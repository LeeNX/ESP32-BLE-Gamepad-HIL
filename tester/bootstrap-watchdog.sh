#!/usr/bin/env bash
# TESTER role, PRIVILEGED, optional: reboot the tester host when its USB controller wedges, and enable the
# hardware watchdog for full hangs. Run once by a host admin; idempotent.
#
#   sudo tester/bootstrap-watchdog.sh            # install in log-only mode: journal says "would reboot"
#   sudo tester/bootstrap-watchdog.sh --arm      # also actually reboot
#   sudo tester/bootstrap-watchdog.sh --remove
#
# Why: on the Raspberry Pi 3B+ every USB port and the Ethernet share one dwc_otg controller. When it wedges
# (2026-09-11, 2026-10-04) the kernel logs bursts of `dwc_otg_hcd_urb_dequeue ... Timed out`, eth0 loses carrier and
# the boards' USB serial devices return EIO or vanish -- the host stays up but the rig is useless, and nothing
# recovers it short of a reboot. A reboot resets the controller; it does not power-cycle a wedged hub or boards
# (a smart plug on the hub/Pi supply does -- see README "Wish list").
#
# What it installs (all root-owned, self-contained -- it does not run anything from the test user's checkout):
#   /usr/local/sbin/hil-usb-watchdog         the check, run every minute by hil-usb-watchdog.timer
#   /etc/default/hil-usb-watchdog            settings (ARMED, IFACE, thresholds); edit and it applies next minute
#   /etc/systemd/system.conf.d/hil-watchdog.conf   RuntimeWatchdogSec: the SoC watchdog reboots a hung kernel
#
# A minute is "bad" when it adds >= BURST new dwc_otg warnings, or IFACE (if set and present) has no carrier.
# STRIKES bad minutes in a row trigger a reboot, at most one per MIN_INTERVAL seconds (no reboot loops).
# Watch it: journalctl -t hil-usb-watchdog
set -euo pipefail

ARM=0
case "${1:-}" in
  --arm) ARM=1 ;;
  --remove)
    systemctl disable --now hil-usb-watchdog.timer 2>/dev/null || true
    rm -f /etc/systemd/system/hil-usb-watchdog.{service,timer} /usr/local/sbin/hil-usb-watchdog \
          /etc/systemd/system.conf.d/hil-watchdog.conf
    systemctl daemon-reload
    echo "removed (kept /etc/default/hil-usb-watchdog and /var/lib/hil-usb-watchdog)"
    exit 0 ;;
  "") ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

echo "== /etc/default/hil-usb-watchdog"
if [[ ! -f /etc/default/hil-usb-watchdog ]]; then
  cat > /etc/default/hil-usb-watchdog <<'EOF'
# hil-usb-watchdog settings (tester/bootstrap-watchdog.sh). Applied on the next run (every minute).
ARMED=0            # 1 = actually reboot; 0 = only log "would reboot"
IFACE=eth0         # must have carrier if present; empty = don't check the link
BURST=5            # new dwc_otg dequeue timeouts in one minute that count as a bad minute
STRIKES=3          # consecutive bad minutes before rebooting
MIN_INTERVAL=3600  # seconds; never reboot more often than this
EOF
fi
sed -i "s/^ARMED=[01]/ARMED=$ARM/" /etc/default/hil-usb-watchdog
grep -E '^(ARMED|IFACE)=' /etc/default/hil-usb-watchdog

echo "== /usr/local/sbin/hil-usb-watchdog"
cat > /usr/local/sbin/hil-usb-watchdog <<'EOF'
#!/bin/sh
# Root-owned; run every minute by hil-usb-watchdog.timer (tester/bootstrap-watchdog.sh).
set -u
ARMED=0 IFACE=eth0 BURST=5 STRIKES=3 MIN_INTERVAL=3600
[ -r /etc/default/hil-usb-watchdog ] && . /etc/default/hil-usb-watchdog
state=/var/lib/hil-usb-watchdog
mkdir -p "$state"
log() { logger -t hil-usb-watchdog "$*"; }

count=$(dmesg | grep -c dwc_otg_hcd_urb_dequeue || true)
boot=$(cat /proc/sys/kernel/random/boot_id)
prev_boot=$(cat "$state/boot" 2>/dev/null || echo)
prev=$(cat "$state/count" 2>/dev/null || echo 0)
[ "$boot" = "$prev_boot" ] || { prev=$count; echo 0 > "$state/strikes"; }   # new boot: fresh baseline
echo "$boot" > "$state/boot"; echo "$count" > "$state/count"

why=""
[ $((count - prev)) -ge "$BURST" ] && why="$((count - prev)) new dwc_otg timeouts"
if [ -n "$IFACE" ] && [ -e "/sys/class/net/$IFACE" ]; then
  [ "$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null)" = 1 ] || why="${why:+$why; }$IFACE has no carrier"
fi
strikes=$(cat "$state/strikes" 2>/dev/null || echo 0)
if [ -z "$why" ]; then
  [ "$strikes" -gt 0 ] && log "healthy again after $strikes bad minute(s)"
  echo 0 > "$state/strikes"
  exit 0
fi
strikes=$((strikes + 1)); echo "$strikes" > "$state/strikes"
log "bad minute $strikes/$STRIKES: $why"
[ "$strikes" -ge "$STRIKES" ] || exit 0

last=$(cat "$state/last-reboot" 2>/dev/null || echo 0); now=$(date +%s)
if [ $((now - last)) -lt "$MIN_INTERVAL" ]; then
  log "would reboot, but the last watchdog reboot was $((now - last))s ago (< $MIN_INTERVAL): leaving it"
  exit 0
fi
if [ "$ARMED" = 1 ]; then
  log "rebooting: $why"
  echo "$now" > "$state/last-reboot"
  systemctl reboot
else
  log "would reboot (ARMED=0): $why"
fi
EOF
chmod 0755 /usr/local/sbin/hil-usb-watchdog

echo "== systemd timer"
cat > /etc/systemd/system/hil-usb-watchdog.service <<'EOF'
[Unit]
Description=HIL rig USB watchdog (tester/bootstrap-watchdog.sh)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/hil-usb-watchdog
EOF
cat > /etc/systemd/system/hil-usb-watchdog.timer <<'EOF'
[Unit]
Description=Run the HIL rig USB watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF

echo "== hardware watchdog (RuntimeWatchdogSec)"
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/hil-watchdog.conf <<'EOF'
# tester/bootstrap-watchdog.sh: systemd pets the SoC watchdog (bcm2835_wdt); a hung kernel reboots in 15 s.
[Manager]
RuntimeWatchdogSec=15s
RebootWatchdogSec=2min
EOF
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now hil-usb-watchdog.timer
systemctl list-timers hil-usb-watchdog.timer --no-pager | head -3
echo "== done ($( ((ARM)) && echo ARMED || echo "log-only; re-run with --arm to enable reboots" )). Watch: journalctl -t hil-usb-watchdog"
