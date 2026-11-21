#!/usr/bin/env bash
# Only the stack changed (kiwi-stack --stack-only, cli.stack_only): a part's setting on MACHINE is
# changed in the fleet file and applied; the apply says only its stack changed, restarts only the
# part's container and is quicker than a whole apply. Then the setting goes back the same way.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/stack-only.sh FLEET MACHINE PART SETTING VALUE CONTAINER
#   e.g. quince vault signups_allowed true kn-vault
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}; part=${3:?a part}; key=${4:?its setting}; value=${5:?a value}; c=${6:?its container}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
setting() {   # VALUE or - (the setting taken out again)
    PYTHONPATH="$here/lib" python3 -c '
import sys, yaml
from kiwiserver import fleettext, util
path, m, part, key, value = sys.argv[1:6]
text = util.read_text(path)
data = yaml.safe_load(text)
role = data["hosts"][m].get("role", "node")
block = dict(((data["hosts"][m].get(role) or {}).get(part)) or {})
if value == "-":
    block.pop(key, None)
else:
    block[key] = yaml.safe_load(value)
new = fleettext.put(text, ("hosts", m, role, part), block)
if new is None:
    sys.exit("the fleet file could not be changed in its text")
util.write_text(path, new, 0o600)' "$fleet" "$m" "$part" "$key" "$1"
}
"$KIWI" control pull "$fleet" > control-pull.log 2>&1 || say "note: nothing pulled from a control node"
t0=$(date +%s)
KIWI_FULL_APPLY=1 "$KIWI" apply "$fleet" "$m" > apply-full.log 2>&1; check $? "$m applied in full ($(( $(date +%s) - t0 )) s): its fingerprints on record"
full=$(( $(date +%s) - t0 ))
setting "$value"; check $? "$part.$key: $value"
t0=$(date +%s)
"$KIWI" apply "$fleet" "$m" > apply-part.log 2>&1; check $? "$m applied"
took=$(( $(date +%s) - t0 ))
grep -q "applying: only its stack changed" apply-part.log; check $? "it took the stack's steps only"
grep -q "restarting what changed ($c)" apply-part.log; check $? "only $c restarted"
check "$(is [ "$took" -lt "$full" ])" "quicker than a whole apply ($took s against $full s)"
setting -; check $? "$part.$key as before"
"$KIWI" apply "$fleet" "$m" > apply-back.log 2>&1; check $? "$m applied as before"
grep -q "applying: only its stack changed" apply-back.log; check $? "again the stack's steps only"
"$KIWI" control push "$fleet" > control-push.log 2>&1 || say "note: nothing pushed to a control node"
say "$fails wrong"
exit $((fails > 0))
