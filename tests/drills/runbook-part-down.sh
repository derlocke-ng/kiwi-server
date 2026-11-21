#!/usr/bin/env bash
# The runbook's part-down on a real machine (runbook.py, docs/control.md "The runbook"): a line for
# PART on MACHINE (a podman machine) is put into the fleet file and applied; the container is killed
# with systemd's own restart held off for that moment (a drop-in under /run, taken out again: the
# stack's units restart for ever, so "failed" is a unit systemd gave up on); the machine's runbook
# restarts it within its minutes. A person's stop is left alone. Afterwards the line is taken out and
# applied again: the timer is gone. One PASS or FAIL line per check.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/runbook-part-down.sh FLEET MACHINE PART CONTAINER
set -u
fleet=${1:?the fleet file}; m=${2:?a podman machine}; part=${3:?a part}; c=${4:?a container of it}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["defaults"]
print(d["admin"]["user"], d.get("domain") or "home")' "$fleet")
on() {
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@$m.$domain" "sudo sh -c $(printf '%q' "$1")"
}
line() {   # the machine's runbook in the fleet file: the line (on) or none (off)
    PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import fleettext, util
path, m, part, on = sys.argv[1:5]
text = util.read_text(path)
import re, yaml
value = [{"case": "part-down", "for": "part:" + part, "do": "restart", "after": 1, "tries": 2}] if on == "on" else []
new = fleettext.put(text, ("hosts", m, "runbook"), value)
if on != "on":      # out again, not left empty: an older kiwi-server does not know the key (22 Nov 2026)
    new = re.sub(r"(?m)^ +runbook: \[\]\n", "", new)
    assert "runbook" not in (yaml.safe_load(new)["hosts"][m] or {})
util.write_text(path, new, 0o600)' "$fleet" "$m" "$part" "$1"
}
"$KIWI" control pull "$fleet" > control-pull.log 2>&1 || say "note: nothing pulled from a control node"
line on; check $? "the line for $part is in the fleet file"
"$KIWI" apply "$fleet" "$m" > apply-on.log 2>&1; check $? "$m applied"
grep -q "its runbook acts on $c (restart)" apply-on.log; check $? "the apply set up its runbook for $c"
on "systemctl is-active -q kiwi-runbook.timer"; check $? "its timer runs"
# failed: killed while systemd's own restart is held off (a runtime drop-in, gone right after)
drop=/run/systemd/system/$c.service.d
on "mkdir -p $drop && printf '[Service]\nRestart=no\n' > $drop/kiwi-drill.conf && systemctl daemon-reload && systemctl kill -s KILL $c.service"
sleep 3
on "rm -f $drop/kiwi-drill.conf && rmdir $drop 2>/dev/null; systemctl daemon-reload"
on "systemctl is-failed -q $c.service"; check $? "$c is failed"
cut=$(date +%s); back=""
for _ in $(seq 1 20); do
    sleep 10
    if on "systemctl is-active -q $c.service"; then back=$(( $(date +%s) - cut )); break; fi
done
check "$(is [ -n "$back" ])" "$m restarted $c by itself (${back:-never} s after it failed)"
said=$(on "journalctl -t kiwi-runbook --since '-10 min' --no-pager -o cat | tail -2")
printf '%s\n' "$said" | sed 's/^/        /'
grep -q "$c was down for .* s: restarted" <<<"$said"; check $? "its runbook says it restarted $c"
"$KIWI" service "$fleet" "$m" "$c" stop > stop.log 2>&1; check $? "a person stops $c"
sleep 150
! on "systemctl is-active -q $c.service"; check $? "$c stayed stopped (2.5 minutes)"
"$KIWI" service "$fleet" "$m" "$c" start > start.log 2>&1; check $? "a person starts it again"
line off; check $? "the line is out of the fleet file"
"$KIWI" apply "$fleet" "$m" > apply-off.log 2>&1; check $? "$m applied again"
! on "test -e /usr/local/sbin/kiwi-runbook"; check $? "its runbook is gone from $m"
"$KIWI" control push "$fleet" > control-push.log 2>&1 || say "note: nothing pushed to a control node"
say "$fails wrong"
exit $((fails > 0))
