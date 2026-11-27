#!/usr/bin/env bash
# The reverse proxy is asked before an apply writes anything (module.yaml `preflight`, kiwi-stack's
# KS_STACK_CHECKS). The drill applies a throwaway machine from a copy of this code whose nginx
# template is broken on purpose, and looks at what is left: the apply must stop with nginx's own
# words, the machine's files must be the old ones, its sites must still answer. Then the good
# code is applied and must pass.
#
#   tests/drills/preflight.sh FLEET MACHINE
#   e.g. tests/drills/preflight.sh ~/kiwi/fleet.yaml mulberry
#
# MACHINE: one of drills.yaml's `allowed`, with the reverse-proxy module, already applied once.
# KIWI_SERVER: the code under test, a checkout's bin/kiwi-server (default: the one this drill
# lies in). One PASS or FAIL line per check. The machine is left on the code under test.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}
dir=$(cd "$(dirname "$fleet")" && pwd)
grep -qE "^allowed:.*\b$m\b" "$dir/drills.yaml" 2>/dev/null || { echo "$m is not in $dir/drills.yaml allowed:" >&2; exit 2; }
here=$(cd "$(dirname "$0")/../.." && pwd)
ks=${KIWI_SERVER:-$here/bin/kiwi-server}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "$@"; }       # by `kiwi-server ssh-config --write`
sites() {       # the machine's own sites, asked on the machine: the ones that do not answer (a
                # .onion name is left out: it answers on a listener of its own, behind Tor)
    on 'sudo sh -c "sed -n \"s/^ *server_name  *\([a-z0-9.-]*\);.*/\1/p\" /home/*/docker/k?-nginx/nginx.conf /var/home/*/docker/k?-nginx/nginx.conf 2>/dev/null | sort -u | grep -v "\.onion\$" | while read n; do c=\$(curl -sk -o /dev/null -w %{http_code} --max-time 8 --resolve \$n:443:127.0.0.1 https://\$n/); case \$c in 2??|3??|4??) ;; *) echo \$n:\$c ;; esac; done"' 2>/dev/null | tr '\n' ' '
}
state() { on 'sudo sh -c "cat /home/*/docker/k?-nginx/nginx.conf /var/home/*/docker/k?-nginx/nginx.conf /etc/containers/systemd/k?-nginx.container 2>/dev/null | sha256sum; systemctl show -p ActiveEnterTimestampMonotonic --value k?-nginx.service"' 2>/dev/null | tr '\n' ' '; }

work=$(mktemp -d "${TMPDIR:-/tmp}/kiwi-preflight.XXXXXX"); trap 'rm -rf "$work"' EXIT
cp -a "$(dirname "$(dirname "$ks")")" "$work/bad" 2>/dev/null; rm -rf "$work/bad/.git"
echo 'bogus_directive on;' >> "$work/bad/modules/reverse-proxy/nginx.conf.j2"
[ -x "$work/bad/bin/kiwi-server" ]; check $? "a copy of the code with a broken nginx template"
before=$(state); bad=$(sites)
[ -n "$before" ] && [ -z "$bad" ]; check $? "before: the machine's sites answer${bad:+ (not: $bad)}"

say "== $m with the broken template"
"$work/bad/bin/kiwi-server" apply "$fleet" "$m" > "$work/bad.log" 2>&1; rc=$?
[ "$rc" != 0 ]; check $? "the apply stopped (exit $rc)"
grep -q "would not start with its new settings" "$work/bad.log"; check $? "it says that the proxy would not start"
grep -q 'unknown directive "bogus_directive"' "$work/bad.log"; check $? "with nginx's own words"
grep -E "would not start|emerg" "$work/bad.log" | head -3 | cut -c1-200 | sed 's/^/        /'
after=$(state); bad=$(sites)
[ "$before" = "$after" ]; check $? "its config, its quadlet and its running container are the ones from before"
[ -z "$bad" ]; check $? "its sites still answer${bad:+ (not: $bad)}"
on 'sudo sh -c "ls -d /run/kiwi-preflight.* 2>/dev/null | wc -l"' 2>/dev/null | grep -qx 0; check $? "no copy of the new files is left on the machine"

say "== $m with the code under test"
"$ks" apply "$fleet" "$m" > "$work/good.log" 2>&1; rc=$?
check $rc "the good code applied $m$([ $rc = 0 ] || echo ": $(tail -n 2 "$work/good.log" | tr '\n' ' ' | cut -c1-200)")"
bad=$(sites); [ -z "$bad" ]; check $? "its sites answer${bad:+ (not: $bad)}"
on 'sudo test ! -e /var/lib/kiwi-server/role.failed'; check $? "no failure mark is left"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
