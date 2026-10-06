#!/usr/bin/env bash
# Creates the virtual CAN interface vcan0 through which open3e and the
# simulator (virtualE3) talk to each other.
#
# vcan is a kernel module and exists on Linux only (on macOS/Windows, work in
# a Linux VM). Slim cloud and VM images often lack it:
#     sudo apt install linux-modules-extra-$(uname -r)
#
# Run this again after every reboot, or set it up permanently:
#     echo vcan | sudo tee /etc/modules-load.d/vcan.conf
#     printf '[NetDev]\nName=vcan0\nKind=vcan\n' | sudo tee /etc/systemd/network/25-vcan0.netdev
#     sudo systemctl enable --now systemd-networkd
set -euo pipefail

echo "[*] Loading the vcan kernel module ..."
sudo modprobe vcan

if ip link show vcan0 >/dev/null 2>&1; then
  echo "[*] vcan0 already exists."
else
  echo "[*] Creating vcan0 ..."
  sudo ip link add dev vcan0 type vcan
fi

sudo ip link set vcan0 up
echo "[*] Done. Status:"
ip -brief link show vcan0
