#!/usr/bin/env python3
"""Which sites work as hidden services? Every hidden service of a network, opened through Tor.

    tests/drills/onion-sites.py ~/kiwi/fleet.yaml --master core@5.45.103.177 [--master-port 41022] [service.machine ...]

For each hidden service (`kiwi-server onion`): its front page through Tor, following where
it sends the visitor; whether the visitor stays at the .onion address (a service that sends
them to its name on the VPN is no use to a stranger); and whether the files the page asks
for (scripts, styles, pictures) come from that address too. It reads only: nothing is logged
in to, nothing changed. Tor runs on the master, in a container that is removed again; no Tor
traffic leaves from the computer this runs on.

The answer is a table to decide by which sites a module marks as usable over Tor.
"""
import argparse
import json
import os
import subprocess
import sys

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("fleet")
ap.add_argument("only", nargs="*", help="service.machine, as `kiwi-server onion` lists them (default: all)")
ap.add_argument("--master", required=True, help="user@address of the master, for ssh")
ap.add_argument("--master-port", default="22")
args = ap.parse_args()
ks = os.environ.get("KIWI_SERVER", "kiwi-server")
fdir = os.path.dirname(os.path.abspath(args.fleet))
r = subprocess.run([ks, "onion", args.fleet, "--porcelain"], capture_output=True, text=True)
try:
    rows = json.loads(r.stdout)["services"]
except (ValueError, KeyError):
    sys.exit("drill: kiwi-server onion: %s" % (r.stderr or r.stdout).strip()[-200:])
rows = [x for x in rows if not args.only or "%s.%s" % (x["service"], x["host"]) in args.only]
open_ones = [x for x in rows if not x["clients"]]
for x in rows:
    if x["clients"]:
        print("SKIP  %-18s only for %s: a key would be needed" % ("%s.%s" % (x["service"], x["host"]), ", ".join(x["clients"])))
if not open_ones:
    sys.exit("drill: no hidden service to try")

script = r'''D=$(mktemp -d); mkdir -p $D/data
printf 'SocksPort 127.0.0.1:9150\nDataDirectory /data\n' > $D/torrc
chown -R 101:102 $D/data
podman rm -f kiwi-drill-tor >/dev/null 2>&1
podman run -d --rm --name kiwi-drill-tor --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z \
    -v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null
for i in $(seq 30); do podman logs kiwi-drill-tor 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done
get() { curl -s --max-time 90 --socks5-hostname 127.0.0.1:9150 "$@"; }
while read -r name addr; do
    [ -n "$name" ] || continue
    for try in 1 2 3 4; do
        first=$(get -L -o $D/body -w '%{http_code} %{url_effective} %{num_redirects}' "http://$addr/")
        [ "${first%% *}" != 000 ] && break
        sleep 30
    done
    echo "PAGE $name $first"
    grep -oE '(src|href)="[^"#]+"' $D/body | sed -E 's/^(src|href)="//; s/"$//' | grep -E '\.(js|mjs|css|png|svg|ico|woff2?)(\?|$)' | sort -u | head -8 |
    while read -r a; do
        case $a in
            http://$addr/*|/*) u=$a; [ "${a#/}" != "$a" ] && u="http://$addr$a"; echo "FILE $name $(get -o /dev/null -w '%{http_code}' "$u") here" ;;
            http://*|https://*|//*) echo "FILE $name - $(echo "$a" | sed -E 's#^(https?:)?//([^/]+).*#\2#')" ;;
            *) base=$(echo "$first" | cut -d' ' -f2); echo "FILE $name $(get -o /dev/null -w '%{http_code}' "${base%/*}/$a") here" ;;
        esac
    done
done
podman rm -f kiwi-drill-tor >/dev/null 2>&1; rm -rf $D
'''
feed = script.replace("done\npodman rm -f kiwi-drill-tor >/dev/null 2>&1; rm -rf $D", "done <<'LIST'\n%s\nLIST\npodman rm -f kiwi-drill-tor >/dev/null 2>&1; rm -rf $D"
                      % "\n".join("%s.%s %s" % (x["service"], x["host"], x["address"]) for x in open_ones))
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
       "-o", "LogLevel=ERROR", "-o", "UserKnownHostsFile=%s" % os.path.join(fdir, "secrets", "known_hosts"),
       "-i", os.path.join(fdir, "secrets", "admin_ed25519"), "-p", str(args.master_port), args.master, "sudo sh -s"]
out = subprocess.run(ssh, input=feed, capture_output=True, text=True, timeout=3600).stdout
pages, files = {}, {}
for line in out.splitlines():
    w = line.split()
    if w[:1] == ["PAGE"] and len(w) >= 5:
        pages[w[1]] = (w[2], w[3], w[4])
    elif w[:1] == ["FILE"] and len(w) >= 4:
        files.setdefault(w[1], []).append((w[2], w[3]))
bad = 0
for x in open_ones:
    name = "%s.%s" % (x["service"], x["host"])
    code, where, hops = pages.get(name, ("000", "", "0"))
    fl = files.get(name, [])
    stays = x["address"] in where
    away = sorted({h for c, h in fl if h != "here"})
    broken = [c for c, h in fl if h == "here" and not c.startswith(("2", "3"))]
    ok = code.startswith(("2", "401")) and stays and not away and not broken
    bad += not ok
    why = []
    if not code.startswith(("2", "401")):
        why.append("the page answers %s%s" % (code, " at %s after %s redirects" % (where.replace(x["address"], "<address>"), hops)
                                               if where else ""))
    if where and not stays:
        why.append("it sends the visitor to %s" % where.split("/")[2])
    if away:
        why.append("files come from %s" % ", ".join(away))
    if broken:
        why.append("%d of its files do not load" % len(broken))
    print("%-5s %-18s %s" % ("PASS" if ok else "FAIL", name, "; ".join(why) or "page %s after %s redirects, %d files from the address itself"
                             % (code, hops, len(fl))))
print("%d of %d would not do as a hidden service" % (bad, len(open_ones)))
sys.exit(1 if bad else 0)
