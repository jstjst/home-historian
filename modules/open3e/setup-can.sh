#!/usr/bin/env bash
# Brings up the CAN interface of a real adapter for open3e, at the bit rate
# of the Viessmann E3 CAN bus (250 kbit/s).
#
#     modules/open3e/setup-can.sh [interface]        # default: can0
#
# open3e does not set the bit rate: it takes the interface as the host has
# configured it, and fails if the interface is down.
#
# The interface has to exist already. A USB adapter creates it when plugged
# in, an SPI HAT needs its overlay in config.txt (see README.md,
# "Commissioning").
#
# Run this again after every reboot, or let systemd-networkd do it
# permanently (README.md, "Bring the interface up").
set -euo pipefail

IFACE=${1:-can0}
BITRATE=250000
# After a bus-off the controller stays off the bus for this long, then tries
# again by itself. Without it, the interface is dead until someone takes it
# down and up by hand.
RESTART_MS=10000

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "[!] There is no interface $IFACE. CAN interfaces of this host:" >&2
  ip -brief link show type can >&2 || true
  exit 1
fi

echo "[*] Configuring $IFACE: $BITRATE bit/s, restart $RESTART_MS ms after bus-off ..."
# The bit rate can only be set while the interface is down.
sudo ip link set "$IFACE" down
sudo ip link set "$IFACE" up type can bitrate "$BITRATE" restart-ms "$RESTART_MS"

echo "[*] Done. Status:"
ip -details -statistics link show "$IFACE"
