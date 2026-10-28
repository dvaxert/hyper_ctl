#!/bin/sh
# Удаляет сервис и программу. Профили в ~/.config/hyper_ctl остаются.
set -e
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/hyper_ctl"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hyper-ctl.service"
systemctl --user disable --now hyper-ctl 2>/dev/null || true
rm -f "$UNIT"
systemctl --user daemon-reload
rm -rf "$DEST"
echo "Удалено. Профили остались в ~/.config/hyper_ctl, udev-правило — в /etc/udev/rules.d/70-hyperx.rules"
