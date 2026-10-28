#!/bin/sh
# Устанавливает hyper_ctl в ~/.local/share/hyper_ctl и запускает systemd user service.
# После установки репозиторий можно удалить.
set -e
cd "$(dirname "$0")"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/hyper_ctl"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$DEST" "$UNIT_DIR"
cp hyper_ctl.py effects.py hyper_ui.py "$DEST/"
sed "s|@DEST@|$DEST|" hyper-ctl.service > "$UNIT_DIR/hyper-ctl.service"
systemctl --user daemon-reload
systemctl --user enable hyper-ctl
systemctl --user restart hyper-ctl
echo "Установлено в $DEST, UI: http://127.0.0.1:8075/"

if [ ! -f /etc/udev/rules.d/70-hyperx.rules ]; then
    echo "Не забудь udev-правило для доступа к клавиатуре:"
    echo "  sudo cp 70-hyperx.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger"
fi
