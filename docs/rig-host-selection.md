# Choosing tester hardware: host, USB, Bluetooth, power

A buyer's guide for anyone building a tester rig for more than one ESP32.
`docs/rig-build.md` tells the history of this rig (photos, the Pi 3B+ numbers,
why it moved). This page is the advice that came out of that history, plus
what to check before you spend money.

Two kinds of claim appear below, and they're labelled:

- **Seen on this rig**: something that happened on the Pi 3B+ (2026-08 to
  2026-11) or on mimir-u26, the 1-litre PC that replaced it. Dates and log
  lines are real.
- **Typical**: widely published figures or common practice, not measured
  here. Treat these as a starting point and send a PR with your own numbers.

## The short version

1. **CPU and RAM barely matter. USB topology and the Bluetooth radio do.**
   Load average stayed under 1 on both rigs during a 3-board run.
2. **Keep the boards' USB controller to the boards.** No root filesystem, no
   Ethernet, no storage on it. That one rule would have prevented every hard
   hang the Pi rig had.
3. **Use a well-known USB Bluetooth dongle, and disable the onboard one.**
   The onboard Intel radio on mimir wedged under parallel pairing. A TP-Link
   UB500 fixed it. Buy a named model from a reputable brand and seller, not
   the cheapest listing: it saves a lot of tweaking and troubleshooting.
4. **Power the boards from a powered hub, never from the host's own ports.**
5. **Budget for idle power, not peak.** The rig is idle well over 95% of the
   time, so idle watts decide the electricity bill.
6. **A used 1-litre office PC is the best value for 3+ boards.** A Raspberry
   Pi is fine for one or two.

## What the tester actually does

The tester flashes boards (`esptool` over USB serial), pairs them over BLE
(BlueZ), and runs pytest against `/dev/input/event*` and GATT. It never
compiles firmware; the builder does that. So the work is:

- a few USB serial devices at up to 921600 baud, which is not much bandwidth;
- **resets**: every flash and every board reboot drops a device off the bus,
  and native-USB boards (C3/S3 USB-JTAG) re-enumerate each time;
- one BLE radio pairing with and talking to every board.

Resets and the radio are where the failures came from, not throughput.

## Host options

| Host | Boards it handles | Why |
|---|---|---|
| Raspberry Pi 3B+ | 1, maybe 2 | All USB ports, the Ethernet and any USB storage sit behind one `dwc_otg` controller with a shared 5 V budget. With 3 boards it wedged USB and Ethernet together, needing a power cycle (seen on this rig: 2026-09-11, 2026-10-04). 1 GB RAM was enough, but only just. |
| Raspberry Pi 4 (2 GB+) | probably 3 (not tested here) | USB is on its own PCIe VL805 controller, separate from Ethernet. That removes the 3B+'s worst coupling. Use a powered hub: the Pi 4 supplies only 1.2 A in total to its USB ports (typical, per Raspberry Pi docs). |
| Raspberry Pi 5 (4 GB+) | probably 3+ (not tested here) | USB via the RP1 chip, two separate USB 3 controllers. Needs the 5 A PSU to give its USB ports more than 600 mA in total (typical). By the time you've bought the PSU, cooler, case and storage, it often costs more than a used 1-litre PC. |
| Used 1-litre office PC (Lenovo Tiny, Dell OptiMicro, HP Mini/EliteDesk) | 3+ (what this rig runs) | USB, storage (SATA/NVMe) and Ethernet (PCIe) each have their own path. 8 GB RAM, an x86 CPU, and often several USB controllers to spread devices across. No USB controller errors since the move on 2026-11-11 (seen on this rig). |
| Intel N100-class mini PC | 3+ (not tested here) | Like a 1-litre PC, new, with lower idle power. Cheap models vary in BIOS quality and in which Wi-Fi/BT module they ship. Plan to use a USB dongle anyway. |

If you're buying a 1-litre PC, look for:

- **8th gen Intel or newer** (Lenovo M720q/M920q, Dell OptiPlex 3060/5060/7060
  Micro, HP EliteDesk/ProDesk 400/600/800 G4 Mini and later). They idle
  noticeably lower than the 3rd-gen i5-3470T in mimir (see "Power" below).
  Any CPU will do.
- **A SATA or NVMe slot with a drive in it**, so the root filesystem isn't
  on USB.
- **Wired Ethernet on the board.** Every business 1-litre PC has it.
- **The BIOS option "power on after AC loss"**, so the rig comes back by itself
  after a power cut or a smart-plug power cycle.
- More than one USB controller is a bonus. Check `lsusb -t` once you have it
  (see "Know your USB tree" below).

## USB: where the resets come from

### Keep the boards' controller to the boards

Seen on this rig: the Pi 3B+ booted from a USB hard drive on the same
controller as the boards. A 3-board BLE reconnect storm produced bursts of
`dwc_otg_hcd_urb_dequeue … Timed out` and took out root-filesystem I/O and
Ethernet together. Moving root back to the SD card stopped that particular
crash. `docs/rig-build.md` "What not to do" has the full story.

On any host: storage, network and anything you can't afford to lose go on a
**different controller** from the ESP32s. On a PC this is usually automatic.
On a Pi 3B+ it's impossible, which is the real reason it tops out at 1–2
boards.

### Powered hubs

- **The boards go on a powered hub**, not the host's ports (README "Still manual").
  An ESP32 radio drawing current while flashing or bringing up BLE can brown
  out a bus-powered port. The symptom is a reset loop that never advertises.
- **Size the hub's PSU for the boards**: about 0.5 A per board as a working
  budget (typical: ESP32 dev boards peak at several hundred mA during RF
  calibration and flash writes). A 4-port hub with a 2 A brick is marginal
  for 4 boards; 3–4 A is comfortable.
- **Check the hub doesn't back-power the host.** Some cheap hubs feed 5 V
  back up the upstream cable, which can stop a Pi from rebooting or powering
  off cleanly. Unplug the host's PSU with the hub still connected: if the
  host's LEDs stay lit, the hub is back-feeding.
- **One 7-port hub beats two chained 4-port hubs.** Every tier is another
  chip, another cable and another power supply that can glitch. Seen on this
  rig: mimir currently has two chained Genesys GL850G hubs (`05e3:0608`).
  They work, but they're the cheapest single-TT hub chip there is.
- **A well-known hub is harder to source than a well-known dongle.** Hub
  listings rarely name the controller chip or the PSU rating. The same
  product name can ship with different chips between batches, and
  brand-name hubs are often rebadged generic ones. What helps:
  - Buy from a seller with a decent return policy, and check the chip
    (`lsusb`, then `bDeviceProtocol` below) as soon as it arrives.
  - Prefer hubs whose chip is documented: `uhubctl`'s supported list names
    exact models and chips. Industrial or lab hubs (with metal cases, a
    screw-terminal or barrel power input, and per-port switching) cost more
    but are made to a spec.
  - Treat the hub's power supply as part of the hub. Replace a weak
    bundled brick with a known-good one of the same voltage and polarity.
  - Once you have a hub that behaves, note the exact model and buy a
    spare.
- **Single-TT vs multi-TT.** Every board's serial bridge is a 12 Mbit/s
  full-speed device. Behind a USB 2.0 hub, a single-TT hub runs all of them
  through one translator; a multi-TT hub gives each port its own. Serial
  bandwidth is far too low for this to matter, but a multi-TT hub also keeps
  one port's misbehaviour from stalling its neighbours (typical, not proven
  here). `cat /sys/bus/usb/devices/<hub>/bDeviceProtocol` gives `1` for
  single-TT and `2` for multi-TT.
- **Per-port power switching is worth paying for.** A hub that `uhubctl`
  supports (its README lists them) lets the rig power-cycle a wedged board or
  Bluetooth dongle over ssh, with nobody walking to the rig. A host reboot
  resets the USB controller, but it does **not** power-cycle the hub or the
  boards (see `tester/bootstrap-watchdog.sh`). The rig doesn't use one yet.
  Front-panel push-button switches don't help: nobody can press them
  remotely.

### Serial bridges and cables

- **Prefer flashing through a UART bridge** (CP2102, CH340, FT232R) over a
  board's native USB. Native USB-JTAG re-enumerates on every reset, which is
  one more chance for the bus to trip. Seen on this rig: the S3's native USB
  cable was removed and it's flashed through its CH340 (README "ESP32-S3
  dual-USB-C setup").
- **Bridge chip brand doesn't matter much; the cable does.** All three bridge
  chips work here. Seen on this rig: the only adapter that logged `-71`
  (EPROTO) errors was a CP2102 on a spare USB-C cable, and the cable or hub
  port was the suspect, not the chip. Short, decent cables, and swap the
  cable first when one lane flakes.
- **Budget ports per board, not per rig**: a C3 with a separate UART bridge
  needs two (README "ESP32-C3 serial bridge").
- Always address boards by `/dev/serial/by-id/`. `ttyUSBn` numbers move
  between reboots (seen on this rig).

## Bluetooth: one radio, many boards

Every board pairs through the one host radio, so a wedge in that radio fails
every lane at once. That makes it the single most important part to choose.

- **A USB dongle beats an onboard radio**, mainly because you can replace it. Seen on this rig: mimir's onboard Intel `8087:07da`
  wedged during parallel pairing (`hci0: Controller not accepting commands
  anymore`). A **TP-Link UB500** (Realtek RTL8761BU, BT 5.1, `2357:0604`)
  then ran full bench and matrix runs with zero controller wedges. It also
  gave tighter timing: a 7.5 ms connection interval against the Intel's
  8.75 ms, and better p99 button latency on every board (`docs/bench/`).
  Other RTL8761B/BU dongles (ASUS USB-BT500 and similar) use the same chip
  and should behave the same (typical, not tested here).
- **Buy a known model from a reputable brand and seller, not the cheapest
  listing.** A named product (TP-Link, ASUS and the like) has a known chip,
  in-kernel support and other Linux users who have already hit and
  documented its problems. No-name dongles often ship a different chip from
  what the listing says, or a counterfeit one. The few euros saved go on
  tweaking and troubleshooting. Check the USB ID with `lsusb` when it
  arrives (the UB500 is `2357:0604`).
- **Avoid** no-name "CSR 4.0" dongles (`0a12:0001` clones; Linux has a
  long list of quirks for counterfeit CSR chips) and anything older than
  BT 4.2 (typical).
- **Pi onboard radios** (Cypress/Infineon over UART) work, but seen on this
  rig the 3B+ only negotiated a 48.75 ms interval with the classic esp32dev,
  against 8.75 ms on the PC. Fine for functional tests, not great for the
  latency bench.
- **Firmware**: Realtek dongles need `rtl8761bu_fw.bin` from `linux-firmware`.
  Check `dmesg | grep -i 'bluetooth: hci'` for a firmware load line after
  plugging in.
- **Disable the radio you don't use** (udev `authorized=0`; README "Bluetooth
  adapter") and **pin the one you do** with `HIL_BT_ADAPTER=<BD address>`.
  Seen on this rig: after a reboot the UB500 came up as `hci1`, because the
  Intel briefly took `hci0`.
- **Placement**: USB 3.0 ports and cables radiate noise at 2.4 GHz (typical;
  Intel published a white paper on it). Put the dongle in a USB 2.0 port, or
  on a short USB 2.0 extension away from the PC's metal case and any USB 3
  devices.
- **Autosuspend**: if a dongle drops off after idling, try
  `btusb.enable_autosuspend=0` on the kernel command line (typical; not
  needed here so far).
- The "Rig health" block in each CI run summary counts `hciN: hardware
  error` lines (README "CI"). A non-zero `+N this run` points at the radio,
  not the code.

## Power

The rig is idle almost all the time. A full CI run is about 7 minutes and the
bench is weekly, so even ten runs a day leaves the host idle for more than 95%
of it. **Annual energy ≈ idle watts × 8.76 kWh.**

Typical figures (not measured on this rig; Pi numbers are the bare board with
no USB load, PC numbers are at the wall):

| Host | Idle | Busy | kWh/year at idle | Per year at 0.30/kWh |
|---|---|---|---|---|
| Raspberry Pi 3B+ | ~2 W | ~5 W | ~18 | ~5 |
| Raspberry Pi 4 | ~3 W | ~6–7 W | ~26 | ~8 |
| Raspberry Pi 5 | ~3 W | ~8–9 W | ~26 | ~8 |
| Intel N100 mini PC | ~6–9 W | ~15–25 W | ~53–79 | ~16–24 |
| 1-litre PC, 8th–10th gen Intel "T" CPU | ~5–8 W | ~25–35 W | ~44–70 | ~13–21 |
| 1-litre PC, 3rd gen i5-3470T (mimir's class) | ~10–15 W | ~30–40 W | ~88–131 | ~26–39 |

Plus, the same on every host:

- **The boards**: an ESP32 sitting BLE-connected, not sleeping, draws
  roughly 0.2–0.5 W (typical). Four boards plus bridges plus the hub's own
  overhead comes to about 2–4 W, whatever the host is.
- **PSU losses**: a cheap USB brick or hub supply wastes 0.3–1 W even with
  nothing attached.

What that means:

- A Pi saves about 10–30 a year in electricity compared with a 1-litre PC.
  That's less than one afternoon spent chasing a USB wedge.
- Among PCs, the generation matters more than the brand. An 8th-gen-or-newer
  "T" CPU idles at around half of what mimir's 3rd-gen one does. If you're
  buying one, that's the cheapest efficiency gain on offer.
- In the BIOS, enable C-states and turn off anything you don't use (Wi-Fi if
  you're wired, serial/parallel ports, Wake-on-LAN if you don't need it).
  `powertop --auto-tune` lowers idle further, but **don't** let it enable USB
  autosuspend on the boards' serial bridges or the BT dongle.

**Measure your own.** A plug-in energy meter for a PC, or an inline USB-C
meter for a Pi. Read it idle, during a `--by-board` run, and during
`--bench`. Intel RAPL (`/sys/class/powercap/intel-rapl*`) is root-only on
current kernels and covers only the CPU package, so it can't stand in for a
wall meter. Measured numbers are very welcome as a PR to this table.

## Tracking down a USB reset

When a lane fails with "firmware never answered PING" or a board vanishes
mid-run, work from cheapest to dearest: **cable → port → hub → hub PSU →
host**. Change one thing at a time, and do a full run between changes.

### Make sure the evidence survives

- **Persistent journal**: `mkdir -p /var/log/journal` (with `Storage=auto`,
  the default) or set `Storage=persistent` in `journald.conf`. Seen on this
  rig: the Pi's 2026-09-11 crash log was lost because the journal lived only
  in RAM.
- **Let the test user read the logs**: add it to `systemd-journal` (and, on a
  Pi, to `video` for `vcgencmd`). The rig's health block and
  `results/health-timeline-*.csv` already sample temperature, CPU frequency,
  the Pi's under-voltage alarm and BT hardware errors on every run.
- **Live view while reproducing**: `journalctl -kf` in one terminal and
  `udevadm monitor --udev --subsystem-match=usb` in another.

### What the kernel log lines mean

| Log line | Usually means |
|---|---|
| `status: -71`, `failed to get modem status: -71`, `device descriptor read/64, error -71` | EPROTO: a signal-integrity problem. Cable, connector, or hub port. Swap the cable first. |
| `usb X-Y: USB disconnect` for **one** device, then it comes back | That board reset or browned out. Normal during a flash; a problem if it happens mid-test. |
| `USB disconnect` for **every device behind one hub** at once | The hub lost power or reset. Check its PSU and how many boards it's carrying. |
| `over-current change` on a hub port | That port's power budget was exceeded. Move the board to a powered hub or a different port. |
| `Under-voltage detected!` (Pi), or `rpi_volt` alarm in the health timeline | The host's own supply is too weak. Use the official PSU and take board power off the Pi. |
| `dwc_otg_hcd_urb_dequeue … Timed out` (Pi 3/Zero) | The single USB controller is wedging. `tester/bootstrap-watchdog.sh` can reboot it, but the fix is fewer devices on that controller or a different host. |
| `xhci_hcd … HC died; cleaning up` | A whole controller died. Reboot, then suspect power or a misbehaving device on that controller. |
| `hciN: Controller not accepting commands anymore`, `hciN: hardware error 0x..` | The Bluetooth controller wedged. This is a radio problem, not USB power, if the rest of the bus stays up. Try a different dongle. |
| `Bluetooth: hciN: RTL: firmware file … not found` | Install `linux-firmware`. |

### Know your USB tree

```bash
lsusb -t                                  # which controller and hub each device is on
for d in /sys/bus/usb/devices/*-*; do     # vendor:product, single/multi-TT, declared current
  [ -f "$d/idVendor" ] && echo "$(basename "$d") $(cat "$d/idVendor"):$(cat "$d/idProduct") \
proto=$(cat "$d/bDeviceProtocol") $(cat "$d/bMaxPower") $(cat "$d/product" 2>/dev/null)"
done
```

Check three things:

- Are the boards on a different controller (`Bus 00N`) from storage and
  network?
- How many hub tiers sit between the host and each board?
- What else is sharing their controller? Seen on this rig: mimir had a small
  USB flash drive on the same xHCI root as the boards and the BT dongle. It
  isn't mounted, but every device on that root is something that can
  misbehave during a reset storm.

## Checklist before you buy

- [ ] Host has wired Ethernet and SATA/NVMe storage that aren't on the boards' USB controller
- [ ] At least 2 GB RAM (1 GB proved a hard floor on the Pi 3B+)
- [ ] A powered USB hub with ~0.5 A per board, preferably `uhubctl`-capable, that doesn't back-power the host
- [ ] A well-known USB Bluetooth dongle from a reputable seller (TP-Link UB500, RTL8761BU, worked here), with `linux-firmware` installed
- [ ] One USB port per board, plus one per separate UART bridge
- [ ] Short, decent USB cables, plus a spare to swap in
- [ ] BIOS: power on after AC loss; C-states on
- [ ] Persistent journal, and the test user can read it
- [ ] Optional: a smart plug on the host or hub supply for remote power cycles
