#!/bin/sh
# Устанавливает hyper_ctl в /opt/hyper_ctl (нужен sudo) и включает systemd user service.
# Находит клавиатуры с HID LampArray и генерирует под них udev-правило доступа без root.
# Настройки хранятся в ~/.config/hyper_ctl и не затрагиваются. После установки репозиторий можно удалить.
#
#   ./install.sh               установить
#   ./install.sh --print-rule  только показать найденные устройства и правило, ничего не менять
set -e
cd "$(dirname "$0")"
PREFIX="${PREFIX:-/opt/hyper_ctl}"
RULE=/etc/udev/rules.d/70-hyper_ctl.rules
LEGACY_RULE=/etc/udev/rules.d/70-hyperx.rules

die() { echo "Ошибка: $*" >&2; exit 1; }

# Выводит "VID:PID<TAB>имя" для каждого устройства с HID LampArray (дескриптор начинается с 05 59 09 01).
detect() {
    for d in /sys/class/hidraw/hidraw*; do
        desc="$d/device/report_descriptor"
        [ -r "$desc" ] || continue
        [ "$(head -c 4 "$desc" | od -An -tx1 | tr -d ' \n')" = "05590901" ] || continue
        # имя HID-устройства вида 0003:03F0:0EB8.002A (шина:вендор:продукт.номер)
        id=$(basename "$(readlink -f "$d/device")" | cut -d: -f2,3 | cut -d. -f1)
        name=$(sed -n 's/^HID_NAME=//p' "$d/device/uevent")
        printf '%s\t%s\n' "$id" "$name"
    done | sort -u
}

# ID из уже установленных правил, чтобы не потерять устройство, которое сейчас не подключено
installed_ids() {
    cat "$RULE" "$LEGACY_RULE" 2>/dev/null \
        | sed -n -e 's/.*KERNELS=="\*:\([0-9A-F]*:[0-9A-F]*\)\..*/\1/p' \
                 -e 's/.*idVendor}=="\([0-9a-f]*\)".*idProduct}=="\([0-9a-f|]*\)".*/\1 \2/p' \
        | while read -r a b; do
            if [ -z "$b" ]; then echo "$a"; else
                for p in $(echo "$b" | tr '|' ' '); do echo "$a:$p"; done
            fi
        done | tr 'a-f' 'A-F'
}

gen_rule() {
    echo "# Сгенерировано install.sh из hyper_ctl: доступ к hidraw клавиатур с HID LampArray для пользователя сессии."
    echo "# Номер 70 важен: тег uaccess обрабатывается в 73-seat-late.rules."
    for id in $1; do
        echo "KERNEL==\"hidraw*\", KERNELS==\"*:$id.*\", MODE=\"0660\", TAG+=\"uaccess\""
    done
}

command -v python3 > /dev/null || die "не найден python3"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "нужен Python 3.8+"

found=$(detect)
if [ -z "$found" ]; then
    die "не найдено ни одной клавиатуры с поддержкой HID LampArray.
  Подключи клавиатуру (кабелем или донглом) и запусти снова.
  Если она подключена — значит, её прошивка не поддерживает LampArray (Dynamic Lighting),
  и hyper_ctl с ней работать не сможет."
fi

echo "Найдены устройства с HID LampArray:"
echo "$found" | sed 's/^/  /'
ids=$( { echo "$found" | cut -f1; installed_ids; } | sort -u)
rule=$(gen_rule "$ids")

if [ "$1" = "--print-rule" ]; then
    echo
    echo "$rule"
    exit 0
fi

sudo install -d -m 755 "$PREFIX"
sudo install -m 644 hyper_ctl.py effects.py hyper_ui.py "$PREFIX/"
sed "s|@PREFIX@|$PREFIX|" hyper-ctl.service | sudo tee /etc/systemd/user/hyper-ctl.service > /dev/null
echo "$rule" | sudo tee "$RULE" > /dev/null
sudo rm -f "$LEGACY_RULE"
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw
sudo udevadm settle

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
sleep 2
if ! systemctl --user is-active --quiet hyper-ctl; then
    journalctl --user -u hyper-ctl -n 5 --no-pager >&2
    die "сервис не запустился, лог выше"
fi
echo "Установлено в $PREFIX, настройки: ~/.config/hyper_ctl, UI: http://127.0.0.1:8075/"
