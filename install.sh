#!/bin/sh
# Устанавливает hyper_ctl в /opt/hyper_ctl (нужен sudo) и включает systemd user service.
# Настройки хранятся в ~/.config/hyper_ctl и не затрагиваются. После установки репозиторий можно удалить.
set -e
cd "$(dirname "$0")"
PREFIX="${PREFIX:-/opt/hyper_ctl}"

sudo install -d -m 755 "$PREFIX"
sudo install -m 644 hyper_ctl.py effects.py hyper_ui.py "$PREFIX/"
sed "s|@PREFIX@|$PREFIX|" hyper-ctl.service | sudo tee /etc/systemd/user/hyper-ctl.service > /dev/null
sudo install -m 644 70-hyperx.rules /etc/udev/rules.d/70-hyperx.rules
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw

# убрать старую установку в домашней папке, если была
OLD_UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hyper-ctl.service"
if [ -f "$OLD_UNIT" ]; then
    systemctl --user disable --now hyper-ctl 2>/dev/null || true
    rm -f "$OLD_UNIT"
fi
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/hyper_ctl"

systemctl --user daemon-reload
systemctl --user enable hyper-ctl
systemctl --user restart hyper-ctl
echo "Установлено в $PREFIX, настройки: ~/.config/hyper_ctl, UI: http://127.0.0.1:8075/"
