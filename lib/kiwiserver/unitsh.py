"""Shell lines for the scripts kiwi-server runs on a machine to put a unit of its own there.

`systemctl daemon-reload` makes systemd read every unit again: a quarter of a second on a small
virtual machine, a second and a half on a Rock Pi. `systemctl enable` and `disable` do the same each
time, changed or not. An apply that changed nothing did it seventeen times (28 Nov 2026), six of
them from these scripts. With the lines below a unit is written only when it differs, systemd reads
its units again only then, and a unit is enabled only when it is not yet.

No percent sign in here: the scripts these lines go into are %-formatted.
"""

PRELUDE = r'''
kiwi_changed=
kiwi_unit() {       # NAME: what comes on stdin becomes /etc/systemd/system/NAME, unless it is that already
    mkdir -p "$(dirname "/etc/systemd/system/$1")"
    cat > "/etc/systemd/system/$1.new"
    if [ -f "/etc/systemd/system/$1" ] && [ "$(cat "/etc/systemd/system/$1.new")" = "$(cat "/etc/systemd/system/$1")" ]; then rm -f "/etc/systemd/system/$1.new"
    else mv "/etc/systemd/system/$1.new" "/etc/systemd/system/$1"; kiwi_changed=1; fi
}
kiwi_reload() { if [ -n "$kiwi_changed" ]; then systemctl daemon-reload; fi; kiwi_changed=; }
kiwi_enable() {     # [--now] UNIT: enabled, and started, when it is not yet
    kiwi_now=; if [ "$1" = --now ]; then kiwi_now=1; shift; fi
    systemctl is-enabled -q "$1" 2>/dev/null || systemctl enable -q "$1"
    if [ -n "$kiwi_now" ]; then systemctl is-active -q "$1" 2>/dev/null || systemctl start "$1"; fi
}
'''
