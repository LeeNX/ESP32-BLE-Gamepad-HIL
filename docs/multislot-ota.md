# Multi-slot OTA bundles (proof of concept)

One flash, up to three apps — DUT profiles and/or the Bluepad32 observer — each in its own OTA slot behind a shared
bootloader and partition table. A board changes profile or role with an OTA reboot (`BOOT <n>`) instead of a
reflash: no ~600 KB write per switch (flash wear, and ~30 s+ on the 115200-baud C3/S3), and the role is picked by a
4 KB otadata write the chip does itself.

Status: **PoC**. Nothing in CI, `test-all.sh`, `test-matrix.sh` or the release uses it yet.

## Why it works

Both firmwares are ESP-IDF 5.x (hil_runner on Arduino-ESP32 3.x — IDF 5.4/5.5 —, the observer on IDF 5.5), so
either one's bootloader boots the other's app. An app finds its partitions through the table at 0x8000 at run time,
by label and subtype, never by a baked-in offset, and app images are relocatable across 64 KB-aligned slots (that's
what OTA is). So an unmodified bundle's `firmware.bin` runs from any slot of a table that has the partitions it
looks for (`nvs`, `coredump`).

| | hil_runner bundle | observer bundle | multi-slot (`firmware/multislot/partitions.csv`) |
|---|---|---|---|
| nvs | 0x9000 +0x5000 | 0x9000 +0x6000 | 0x9000 +0x6000 |
| otadata | 0xe000 | 0xf000 | 0xf000 |
| phy_init | – | 0x11000 | 0x11000 |
| apps | ota_0 0x10000, ota_1 0x150000 (1.25 MB) | factory 0x20000, ota_0/1 | ota_0 0x20000, ota_1 0x160000, ota_2 0x2a0000 (1.25 MB each) |
| other | spiffs, coredump 0x3f0000 | – | coredump 0x3f0000 |

No factory partition: with a blank otadata the bootloader starts ota_0. Images today are 0.6–0.73 MB, under 60% of a
slot.

## Pieces

- **`firmware/multislot/partitions.csv`** — the shared table.
- **`firmware/include/hil_slots.h`** — `SLOTS?` / `BOOT <n> [WIPE]`, shared by both firmwares (hil_runner replies
  `SLOTS …` / `OK` / `ERR <reason>`; the observer `HIL slots …` / `HIL ok boot <n>` / `HIL err <reason>`).
  `SLOTS?` reports `running=<n> boot=<n> count=<n>` and per slot `<i>=<sha256>/<state>/<project>/<version>`; the
  sha256 is `esp_partition_get_sha256()`, i.e. the image's appended digest, verified on the chip.
  `BOOT` calls `esp_ota_set_boot_partition()` (which validates the image first), optionally erases NVS, restarts.
- **`builder/make_multislot.py`** — composes a bundle from ordinary ones (slot 0 first). Before writing anything it
  checks: source files vs their manifests' sha256s, one chip for all; the table (64 KB-aligned apps, no overlaps,
  inside the flash size from the bootloader header, ota_0..n with no gaps, no factory, one 8 KB otadata, enough
  slots); each image (ESP magic, chip id, XOR checksum, appended SHA-256, file length == image length, an app
  descriptor present) and that it fits its slot. Writes `bootloader.bin` (from slot 0's bundle by default),
  `partitions.bin` (byte-identical to ESP-IDF `gen_esp32part.py`), `otadata.bin` (`--boot <n>`; otatool's crc),
  `nvs_blank.bin`, `slot<i>.bin`, and a manifest `tester/flash.py` flashes as is, plus a `slots` list (role, profile,
  source bundle, size, headroom, `image_sha256`, IDF version). otadata and NVS are `verify: false`, so a re-flash of
  the same bundle after slot switches is still "already on the chip" — no rewrite.
- **`tester/slot.py`** — `list`, `verify` (exit 1 unless every slot's on-chip digest matches the manifest),
  `boot <n> [--wipe] [--via serial|esptool]`. It refuses to boot a slot whose digest isn't the bundle's, then polls
  `SLOTS?` until the new image answers and checks it runs slot n with every digest intact. `--via esptool` writes
  only the 8 KB otadata, for a slot whose firmware doesn't answer.

```sh
builder/build.sh --boards esp32dev2 --profiles "default sinput"
builder/build-observers.sh --boards esp32dev2
builder/make_multislot.py bundles/esp32dev2-default-<sha> bundles/matrix/esp32dev2-sinput-<sha> \
    bundles/matrix/esp32dev2-bp32obs-<sha>-<rig>            # -> bundles/multislot/esp32dev2-ms-…
tester/flash.py bundles/multislot/esp32dev2-ms-… --port $P --force
tester/slot.py bundles/multislot/esp32dev2-ms-… --port $P verify
tester/slot.py bundles/multislot/esp32dev2-ms-… --port $P boot 2 --wipe    # become the observer
```

## Rollback

Both bootloaders have `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`. `BOOT` from a rollback-enabled app writes the target
as *new*; the bootloader starts it as *pending-verify*; unless the app then marks itself valid, the **next reset
returns to the previous slot**. Arduino-ESP32 3.x does this in `initArduino()`. The observer didn't (its factory app
never needed to) — it now calls `esp_ota_mark_app_valid_cancel_rollback()` once Bluetooth is up, so an observer
image that never gets Bluetooth going is rolled back. An observer bundle older than this change put in a slot would
be rolled back on its second boot. `--via esptool` writes the entry with an undefined state: a plain boot, no
pending-verify.

## Caveats / open points

- **Shared NVS.** All slots use the one `nvs` partition. NimBLE's bonds and BTstack's keys live in different
  namespaces, but DUT profiles all use NimBLE's — today each profile is "a distinct host bond" per flash. Use
  `BOOT <n> WIPE` (or `flash.py --wipe-settings`) on a profile or role change, as the rig does now.
- **Identity.** Every runner image's app descriptor says `arduino-lib-builder` + the core's version, so slots are
  told apart by digest (manifest `slots[].image_sha256`) — `ID?` / `CONFIG?` after the switch still give the profile
  and libsha.
- **One board name per image.** A slot keeps the name it was built with; mixed-board bundles are allowed (with a
  note) but rarely what you want.
- **Bundle provenance.** `CONFIG?`'s `libsha=` check (conftest.py `dut` fixture) still applies per slot; a
  multi-slot bundle's manifest has `lib_sha` empty at the top level, with per-slot `lib_sha`.
- **First flash** must write the whole bundle (`--force`): it replaces the old layout (whose otadata at 0xe000 now
  sits inside nvs, which the blank NVS image overwrites). The coredump partition isn't erased.
- **Wear.** A switch erases and writes one 4 KB otadata sector (the two sectors alternate) and, with WIPE, the 24 KB
  NVS; a reflash writes ~0.6–2.5 MB.

## Results (2026-11-09, rig esp32dev2 — ESP32-D0WD-V3 rev 3.1, CP2102, 4 MB)

Bundle: slot 0 `default` (629 KB, 48% of the slot), slot 1 `sinput` (630 KB, 48%), slot 2 `bp32obs` (725 KB, 55%);
hil_runner built on Arduino-ESP32 3.x / IDF 5.5.5, observer IDF 5.5.5; bootloader from slot 0's bundle.

| Step | Result |
|---|---|
| full flash, `flash.py --force` (2.4 MB incl. 3 apps @ 460800) | 71 s, every image hash-verified |
| `slot.py verify` after flash | 3/3 slot digests == manifest; running 0 |
| `BOOT 1` (default → sinput DUT) | answered in 1.7 s, running 1, state `valid` (Arduino marked it) |
| hard reset (EN) | still slot 1 — no rollback |
| `BOOT 2 WIPE` (→ observer, NVS erased) | answered in 7.8–8.0 s (the observer only reads commands after Bluetooth init), running 2, `valid` |
| hard reset | still slot 2 — the observer's mark-valid works |
| `boot 0 --via esptool` (8 KB otadata write) | answered in 5.3 s, running 0 |
| DUT ↔ DUT switches (×4) | 1.7–1.8 s each |
| bad args, hil_runner | `BOOT 9` → `ERR no_such_slot`; `BOOT x` / `BOOT 1 NOPE` / `BOOT` → `ERR args` |
| bad args, observer | `BOOT 9`, `BOOT 3 WIPE` → `HIL err no_such_slot`; `BOOT 1 NOPE` → `HIL err args` |
| `flash.py` same bundle again (no `--force`) | "already on the chip (verified) — not rewritten", 5 s, despite the slot switches since |
| `slot.py verify` at the end | 3/3 digests intact after 9 switches |

### esp32c3 and esp32s3 (same day, same sequence)

Bootloader at 0x0 (from slot 0's bundle); images 0.57–0.66 MB (43–51% of a slot). C3: console = FT232R on UART0,
flash = native USB-Serial/JTAG. S3: console and flash = the CH340 "UART" port.

| Step | esp32c3 | esp32s3 |
|---|---|---|
| full flash, 3 apps @ 115200 | 30 s (native USB runs well above the nominal baud) | 121 s (CH340 at 115200, ~37 s per app) |
| `verify` after flash | 3/3 | 3/3 |
| `BOOT 1` → sinput, then reset | 1.3 s, kept slot 1 | 1.2 s, kept slot 1 |
| `BOOT 2 WIPE` → observer, then reset | 1.5 s (BLE-only init), kept slot 2 | 1.7 s, kept slot 2 |
| bad args, both firmwares | all rejected as on esp32dev2 | same |
| `boot 0 --via esptool` | 3.7 s (via the native USB) | 4.7 s |
| 5 more switches | 1.3 s each | 1.2–1.3 s each |
| re-flash same bundle | verify skip, 4 s | verify skip, 6 s |
| final `verify` | 3/3 | 3/3 |
| new dwc_otg / cp210x warnings | 0 | 7 — see below |

The S3's seven `dwc_otg_hcd_urb_dequeue` warnings line up with the *resets and port open/closes* of its CH340
(19:00:07 reset, 19:00:20, 19:00:30 ×4 reset + probe, 19:00:58 esptool exit), not with the 121 s, 2.4 MB full flash,
which logged none (as on esp32dev2's CP2102). So on this Pi the warnings track USB serial session churn (a cancelled
URB on close), not bytes written: fewer flashes help through fewer esptool sessions (a verify + write today is two),
and a switch should reuse an open console session rather than open its own.

Lessons from the run, already folded in:

- A `SLOTS?` sent while the chip is still booting (just reset by `flash.py`'s verify or esptool) arrives garbled and
  gets `ERR unknown`; `slot.py` retries an ERR instead of treating it as "no multi-slot support".
- Opening the CP2102's port on the Pi can reset the board even with DTR/RTS deasserted first (the Linux tty raises
  both on open) — same as `SerialDev` assumes. `slot.py`'s retries cover the reboot; the observer's ~8 s Bluetooth
  init is the long pole.

So: switching costs ~2 s (DUT) / ~8 s (observer) and one 4 KB otadata sector, against 20–22 s and ~0.6–0.7 MB of
flash writes per app on esp32dev at 460800 — and several times that on the 115200-baud C3/S3.

## Next steps (not done)

1. CI/test wiring: let `test-all.sh` / `test-matrix.sh` take a multi-slot bundle and `slot.py boot` between profiles
   or roles instead of `flash.py` per bundle (and keep the `CONFIG?` libsha check per slot).
2. A builder entry point (`build.sh --multislot "default specials bp32obs"`?) instead of hand-composing.
3. ~~Try the C3/S3~~ — done, above.
4. Decide whether the observer's `sdkconfig.defaults` should move to the multi-slot table outright (one layout for
   every bundle), or keep both layouts.
