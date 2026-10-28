#!/bin/sh
# Удаляет сервис, программу из /opt и udev-правило. Настройки в ~/.config/hyper_ctl остаются.
set -e
PREFIX="${PREFIX:-/opt/hyper_ctl}"
systemctl --user disable --now hyper-ctl 2>/dev/null || true
sudo rm -f /etc/systemd/user/hyper-ctl.service /etc/udev/rules.d/70-hyperx.rules
sudo rm -rf "$PREFIX"
sudo udevadm control --reload
systemctl --user daemon-reload
echo "Удалено. Настройки остались в ~/.config/hyper_ctl"
