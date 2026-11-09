# Multi-slot OTA flashing

Every rig board's flash holds **four apps** — the run's hil_runner profiles and the Bluepad32 observer — each in its
own OTA slot behind a shared bootloader and partition table (`firmware/multislot/partitions.csv`).
`tester/flash.py` treats the slots as a cache: an app already in a slot is *booted*, not written again; a new one
is written into the least recently used slot, ~0.6 MB, never a whole board image. A board changes profile or role
(gamepad ↔ observer) in ~1.3–8 s, and on the C3/S3 — 115200 baud — without minutes of flashing.

Why it matters on this rig: flash wear (each write erases whole sectors), run time (an app takes 20 s at 460800 on
the esp32, 35 s+ on the S3's bridge at 115200), and USB churn on the Pi 3B+, whose single USB controller (shared
with Ethernet) logs `dwc_otg_hcd_urb_dequeue` warnings — and has wedged — around esptool sessions. On the rig those
warnings follow serial-port *sessions* (open / reset / close), not bytes: a switch over an already-wired console
needs no esptool session at all.

## Why it works

Both firmwares are ESP-IDF 5.x (hil_runner on Arduino-ESP32 3.x — IDF 5.4/5.5 —, the observer on IDF 5.5), so
either one's bootloader boots the other's app. An app finds its partitions through the table at 0x8000 at run time,
by label and subtype, never by a baked-in offset, and app images are relocatable across 64 KB-aligned slots (that's
what OTA is). So an unmodified bundle's `firmware.bin` runs from any slot of a table that has the partitions it
looks for (`nvs`, `coredump`). Bundles keep their own layout; `flash.py` only takes their app (and, converting a
board, their bootloader).

| | hil_runner bundle | observer bundle | multi-slot (`firmware/multislot/partitions.csv`) |
|---|---|---|---|
| nvs | 0x9000 +0x5000 | 0x9000 +0x6000 | 0x9000 +0x6000 |
| otadata | 0xe000 | 0xf000 | 0xf000 |
| phy_init | – | 0x11000 | 0x11000 |
| apps | ota_0 0x10000, ota_1 0x150000 (1.25 MB) | factory 0x20000, ota_0/1 | ota_0..3 at 0x20000, 0x110000, 0x200000, 0x2f0000 (960 KB each) |
| other | spiffs, coredump 0x3f0000 | – | coredump 0x3f0000 |

No factory partition: with a blank otadata the bootloader starts ota_0. Images today are 0.57–0.73 MB; the esp32
observer, the largest, fills ~74% of a slot. `flash.py` refuses an image that doesn't fit.

## What `tester/flash.py` does

Multi-slot is the default (`HIL_MULTISLOT=0` or `--no-multislot`: the legacy whole-bundle flash; a composed
multi-slot bundle from `builder/make_multislot.py` is also flashed the legacy way). Given a bundle, `--port` (esptool)
and `--console-port` (the firmware's console, `hil_config` `[board.*].port` — test.sh, conftest.py and test-matrix.sh
pass it):

1. **Console.** Ask the running firmware `SLOTS?`: the running / next-boot slot and every slot's image SHA-256,
   digested on the chip. The bundle's app is in slot *n* → `BOOT <n>` (with `WIPE` when the settings must go) and
   wait until `SLOTS?` reports slot *n* running with that digest. No esptool, no bulk transfer — on the C3 not even
   its native USB port is touched. Even "already running" restarts the chip, as a legacy flash's esptool reset did.
2. **esptool verify** (no console, an old firmware that doesn't know `SLOTS?`, or a miss): one `verify_flash`
   session checks the partition table and the app against every slot (MD5 on the chip, reads only).
   - table ours, app in slot *n* → write only otadata (8 KB) selecting *n*;
   - table ours, app nowhere (or `--force`) → write the app into the least recently used slot, plus otadata;
   - table not ours (a legacy-flashed or erased board) → **convert**: bundle's bootloader, the multi-slot table, a
     blank otadata and NVS, the app in slot 0. A legacy flash later (an older rig commit) simply converts it back.
3. With a console, check after an esptool write that the board runs that slot with that digest.

`--wipe-settings` erases just the nvs partition here (BLE bonds), never otadata. An observer bundle, which carries a
blank NVS image, gets it whenever it is switched to from another app — as the legacy flash wrote it with the app.

What each slot holds and when it was last used is recorded per esptool port in
`~/.cache/esp32-hil/slots/<port>.json` (`$HIL_SLOT_STATE_DIR`): it picks the slot to evict and labels
`slot.py list`. It is only a hint — a slot is trusted after an on-chip digest, and the record is corrected from what
the chip says.

## Reporting

- `flash.py` prints `flash: <board> <profile> <lib> -> slot <n> (switched via console, 0 B written)` and a
  machine line `flash-slot: <slot> <action> <method> <bytes>`; actions are `reboot`, `switch`, `write`, `convert`
  (legacy: `unchanged`, `written`, `wiped`).
- `$HIL_FLASH_RECORD` gets that as JSON (test.sh: `results/flash-<board>-<profile>.json`), `$HIL_FLASH_LOG` one line
  per flash (`results/flash-log.jsonl`, `results/matrix/flash-log.jsonl`).
- The run summary (`hil.summarize`) has a **flash** column — `slot 2 · switch (console)`, `slot 1 · write 617 KB` —
  and a totals line: flashes by action, how many went over the console, bytes written. The matrix summary
  (`hil.matrixsummary`) adds a **Slot** column per lane (`pad 1 (switch) · obs 3 (reboot)`, from the verdict line's
  `slots=` field) and the same totals line.

## Reproducible images

A cache only pays if the same source builds the same image. `builder/build.sh` sets `SOURCE_DATE_EPOCH` (GCC's
`__DATE__` / `__TIME__`, i.e. `ID?`'s `built=`) to the newer of the library HEAD and the last `firmware/` commit, and
`builder/build-observers.sh` versions the observer by its own source commit (`firmware/observer`, `firmware/include`,
the script) instead of the rig HEAD. Two clean hil_runner builds a minute apart are byte-identical; a CI re-run, or
a rig PR that doesn't touch firmware, finds every image already in a slot.

## Tools for a person at the rig

- `tester/slot.py --port <console> list | verify <bundle>... | boot <n> [--wipe]` — over the console, no esptool.
- `builder/make_multislot.py <bundle>...` — compose one bundle with apps in fixed slots (the PoC route; flashes as
  is with `flash.py`). It checks chip, image integrity (XOR checksum, appended SHA-256, exact length), slot fit and
  the table first, and its `partitions.bin` is byte-identical to ESP-IDF `gen_esp32part.py`'s.

## Firmware side

`firmware/include/hil_slots.h`, shared by both firmwares: `SLOTS?` → `running=<n> boot=<n> count=<n>
<i>=<sha256>/<state>/<project>/<version> …` (or `<i>=empty`); `BOOT <n> [WIPE]` →
`esp_ota_set_boot_partition()` (which validates the image), optional NVS erase, restart. hil_runner replies
`SLOTS …` / `OK` / `ERR <reason>`, the observer `HIL slots …` / `HIL ok boot <n>` / `HIL err <reason>`.

## Rollback

Both bootloaders have `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`. `BOOT` from a rollback-enabled app writes the target
as *new*; the bootloader starts it as *pending-verify*; unless the app then marks itself valid, the **next reset
returns to the previous slot**. Arduino-ESP32 3.x does this in `initArduino()`. The observer didn't (its factory app
never needed to) — it now calls `esp_ota_mark_app_valid_cancel_rollback()` once Bluetooth is up, so an observer
image that never gets Bluetooth going is rolled back. An observer image older than this change, switched to with
`BOOT`, would be rolled back on its second boot. An esptool otadata write leaves the entry's state undefined: a
plain boot, no pending-verify.

## Caveats

- **Shared NVS.** All slots use the one `nvs` partition. NimBLE's bonds and BTstack's keys live in different
  namespaces, but every DUT profile uses NimBLE's. The rig wipes on a role change (test-matrix.sh) and the
  observer drops its keys at boot anyway; between hil_runner profiles nothing is wiped — as before.
- **Identity.** Every runner image's app descriptor says `arduino-lib-builder` + the core's version, so slots are
  told apart by digest; `ID?` / `CONFIG?` (and the `libsha=` check in conftest.py's `dut` fixture) still say which
  profile and library build is running.
- **Older images** (built before `SLOTS?`) still go into a slot; `flash.py` then uses esptool for that board (their
  console doesn't answer `SLOTS?`) and skips the after-write console check.
- **4 MB flash** assumed (every rig board). The coredump partition isn't erased on conversion.
- **Wear.** A console switch erases one 4 KB otadata sector (the two alternate); an esptool switch writes both
  (8 KB); `WIPE` adds the 24 KB NVS. A legacy flash wrote 0.6–0.7 MB per app, every time the app changed.

## Validation (2026-11-09, rig rp3b-ble-hil, bundles built on a Mac)

Bundles built on a Mac exactly as CI does (`build.sh` esp32dev/esp32c3/esp32s3 × minimal maxfeat sinput, LED pins as
CI's, `build-observers.sh`), run from a worktree of this branch: `test-all.sh --by-board` + the **full** observer
matrix (`test-matrix.sh --lanes parallel`: 3 rotations × minimal maxfeat sinput, 18 lanes), three times — cold
(boards on the legacy layout), warm (same bundles again), and legacy (`HIL_MULTISLOT=0`) as the A/B.

| | r1 multi-slot, cold | r2 multi-slot, warm | r3 legacy (A/B) |
|---|---|---|---|
| suite (`--by-board`) | 6/6 bundles pass | 6/6 | 6/6 |
| matrix lanes | 17/18 (bp32_hil torn-line crash, fixed) | 15/16, then the USB guard aborted rotation 3 (see below) | 16/18 (two lane flakes) |
| flashes (suite + matrix) | 27: 3 convert, 9 write, 15 console | **31, all over the console** | 33: 26 written, 7 verify-skip/wipe |
| esptool sessions | 12 flashes | **0** | 33 flashes (a write is verify + write) |
| bytes written | 7.9 MB (each image once per board) | **0** | 17.2 MB |
| time in flash.py | 483 s | 178 s | 786 s |
| per-bundle flash overhead (suite) | 31–34 s write, ~5 s console | 3–9 s | 14–48 s |
| matrix wall time | 18 min (full) | – (aborted) | 24 min (full) |

- A console switch costs 4.5–6 s (dominated by waiting for the restarted firmware to answer `SLOTS?`; the esp32
  observer's Bluetooth init is the long pole); an esptool write 26–34 s; a legacy write of the same app 26–30 s.
- With the cache warm, every flash of a full run was a console switch: nothing written, no esptool session.
- **USB warnings:** not reduced measurably. r2 (no esptool at all) logged 16 `dwc_otg` warnings in ~23 min, r3
  (33 esptool flashes) 27 in ~34 min — the same rate. The bursts line up with board *resets* — `bp32_hil.py`
  resetting the observer at each lane, the C3's native USB re-enumerating on every chip reset — which both modes
  do. r2's abort was one 12-warning burst at a lane start hitting the matrix guard's threshold (12), not flashing.
- The cell that failed in r1 and r2 (observer esp32c3 / sinput / Bluepad32 lane, gamepad esp32dev) passed 4/4 when
  rerun on its own (one cold conversion + three console reboots), and in r3. Both failures were harness gaps the
  faster switches exposed or happened to hit, fixed in `bp32_hil.py`: a state line torn by the observer reset
  crashed the reader thread (now skipped), and a lost `SPECIAL RELEASE` left a button held (commands are now resent
  once when no reply comes, as `SerialDev` does).
- Suite `test time` (pairing inside pytest) varied 5 s–3 min per bundle in every round, both modes; six samples
  per round don't separate that from BLE pairing variance.

## PoC results (2026-11-09, the first 3 × 1.25 MB layout, composed bundles + `slot.py`)

### esp32dev2 — ESP32-D0WD-V3 rev 3.1, CP2102, 4 MB

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
