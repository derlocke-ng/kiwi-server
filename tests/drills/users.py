#!/usr/bin/env python3
"""Synthetic users: WireGuard clients of the master, run here, each doing every minute what a
person does — names through the Pi-hole, every service's page, a file to Nextcloud and back —
and what each got and how fast. Run it across a backup, an update or a restart to see what
people would see.

    tests/drills/users.py ~/kiwi/fleet.yaml sorbet --users 3 --minutes 20 \\
        [--backup-at 5 papaya] [--restart-at 3 km-vpn-server.service] [--partner ~/kiwi2/fleet.yaml --partner-ssh core@188.68.38.110]

A partner user (a client of the partner network's master) opens only what is shared with it.
The users are devices `drill-user-N` (made once, fetched after); their files go to Nextcloud's
admin under kiwi-users/ and are removed at the end. Needs podman with /dev/net/tun.
"""
import argparse
import os
import re
import statistics
import subprocess
import sys
import threading
import time

import yaml

from drill import kiwi, say, setup
from kiwiserver import cli, roles as rolesmod

extra = argparse.ArgumentParser()
extra.add_argument("--users", type=int, default=3)
extra.add_argument("--minutes", type=float, default=20)
extra.add_argument("--every", type=int, default=60)
extra.add_argument("--backup-at", nargs=2, metavar=("MINUTE", "HOST"))
extra.add_argument("--restart-at", nargs=2, metavar=("MINUTE", "UNIT"), help="restart a unit on the master then, as an update does")
extra.add_argument("--partner", help="the partner network's fleet file: one user of it opens what is shared")
extra.add_argument("--partner-ssh", help="how to reach the partner's master")
opts, rest = extra.parse_known_args()
sys.argv = [sys.argv[0]] + rest
args, fleet, m = setup(__doc__.splitlines()[0], roles=("master",))
master = m["master"]
_fleet, roles, ms = cli.load(args)
dns_ip = master.host.module_settings["vpn-server"].get("server_ip") or "10.8.0.1"
r = cli.Renderer(_fleet, roles, cli.out_dir_for(argparse.Namespace(output_dir=None), _fleet), None, ms, None)
records = r.dns_records()
services = sorted({(n, ip) for ip, n in records if n.count(".") >= 2 and n.endswith("." + (fleet.defaults.get("domain") or "home"))})
hosts = sorted({n for _ip, n in records if n.count(".") == 1})
cloud = None
for h in _fleet.hosts.values():
    if h.role in roles and roles[h.role].node_type != "master":
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
        except Exception:     # noqa: BLE001 — a host that does not resolve is no cloud host here
            continue
        if "cloud" in (h.modules or []):
            name = "cloud.%s" % h.hostname
            ip = next((i for i, n in records if n == name), None)
            if ip:
                cloud = (name, ip, h.module_settings["cloud"].get("admin_password") or "")
                break

SCRIPT = r"""
for n in $DNS; do
    t=$(dig +time=3 +tries=1 @$DNSIP $n +noall +answer +stats 2>/dev/null | sed -n 's/;; Query time: \([0-9]*\) msec/\1/p')
    if [ -n "$t" ]; then echo "dns $n 200 $(awk "BEGIN{print $t/1000}")"; else echo "dns $n 000 3"; fi
done
for u in $URLS; do
    n=${u%%:*}
    echo "https $n $(curl -sk -o /dev/null -w '%{http_code} %{time_total}' --max-time 15 --resolve "$u" "https://$n/")"
done
if [ -n "$NC" ]; then
    head -c 1048576 /dev/urandom > /tmp/f
    d="https://$NC/remote.php/dav/files/admin/kiwi-users"
    curl -sk -u "admin:$NCPASS" -X MKCOL -o /dev/null --max-time 15 --resolve "$NC:443:$NCIP" "$d" || true
    echo "put $NC $(curl -sk -u "admin:$NCPASS" -T /tmp/f -o /dev/null -w '%{http_code} %{time_total}' --max-time 60 --resolve "$NC:443:$NCIP" "$d/$ME.bin")"
    echo "get $NC $(curl -sk -u "admin:$NCPASS" -o /tmp/g -w '%{http_code} %{time_total}' --max-time 60 --resolve "$NC:443:$NCIP" "$d/$ME.bin")"
fi
"""


class User:
    def __init__(self, name, fleet_path, ssh, urls, dns_names, dns, nc=None):
        self.name, self.urls, self.dns_names, self.dns, self.nc = name, urls, dns_names, dns, nc
        conf = os.path.join(os.path.dirname(os.path.abspath(fleet_path)), "secrets", "devices", name + ".conf")
        cmd = ["enroll", fleet_path, name] + (["--ssh", ssh] if ssh else []) + (["--existing"] if os.path.isfile(conf) else [])
        rc, out = kiwi(*cmd, timeout=300)
        if rc != 0:
            raise SystemExit("drill: enroll %s: %s" % (name, " ".join(out[-2:])))
        self.box = "kiwi-%s" % name
        for c in (self.box + "-tools", self.box):
            subprocess.run(["podman", "rm", "-f", c], capture_output=True)
        subprocess.run(["podman", "run", "-d", "--name", self.box, "--security-opt", "label=disable", "--cap-add", "NET_ADMIN",
                        "--device", "/dev/net/tun", "-e", "VPN_SERVICE_PROVIDER=custom", "-e", "VPN_TYPE=wireguard",
                        "-e", "WIREGUARD_IMPLEMENTATION=userspace", "-e", "DOT=off", "-e", "FIREWALL_OUTBOUND_SUBNETS=",
                        "-v", "%s:/gluetun/wireguard/wg0.conf:ro" % conf, "docker.io/qmcgaw/gluetun:v3"],
                       check=True, capture_output=True)
        for _ in range(36):
            if "Public IP address is" in subprocess.run(["podman", "logs", self.box], capture_output=True, text=True).stdout:
                break
            time.sleep(5)
        subprocess.run(["podman", "run", "-d", "--name", self.box + "-tools", "--network", "container:" + self.box,
                        "docker.io/library/alpine:3.20", "sh", "-c", "apk add -q curl bind-tools; sleep infinity"],
                       check=True, capture_output=True)
        for _ in range(30):
            if subprocess.run(["podman", "exec", self.box + "-tools", "sh", "-c", "command -v curl && command -v dig"],
                              capture_output=True).returncode == 0:
                break
            time.sleep(4)

    def round(self):
        env = "DNSIP=%s\nDNS='%s'\nURLS='%s'\nME=%s\n" % (self.dns, " ".join(self.dns_names), " ".join(self.urls), self.name)
        if self.nc:
            env += "NC=%s\nNCIP=%s\nNCPASS='%s'\n" % (self.nc[0], self.nc[1], self.nc[2].replace("'", "'\\''"))
        try:
            p = subprocess.run(["podman", "exec", "-i", self.box + "-tools", "sh", "-s"], input=env + SCRIPT,
                               capture_output=True, text=True, timeout=300)
            out = p.stdout
        except subprocess.TimeoutExpired:
            out = ""
        found = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 4:
                kind, name, code, secs = parts
                try:
                    found.append((kind, name, code, float(secs)))
                except ValueError:
                    found.append((kind, name, code, 0.0))
        return found

    def cleanup(self):
        if self.nc:
            env = "NC=%s\nNCIP=%s\nNCPASS='%s'\n" % (self.nc[0], self.nc[1], self.nc[2].replace("'", "'\\''"))
            subprocess.run(["podman", "exec", "-i", self.box + "-tools", "sh", "-s"], input=env +
                           'curl -sk -u "admin:$NCPASS" -X DELETE -o /dev/null --max-time 30 --resolve "$NC:443:$NCIP" '
                           '"https://$NC/remote.php/dav/files/admin/kiwi-users/%s.bin"\n' % self.name,
                           capture_output=True, text=True, timeout=60)
        for c in (self.box + "-tools", self.box):
            subprocess.run(["podman", "rm", "-f", c], capture_output=True)


def ok(kind, code):
    return code[:1] in "234" and code != "000"


urls = ["%s:443:%s" % (n, ip) for n, ip in services]
users = []
results, lock, events = [], threading.Lock(), []
try:
    say("%d users of %s: %d services, %d hosts, Nextcloud %s" % (opts.users, master.name, len(urls), len(hosts),
                                                              cloud[0] if cloud else "none"))
    for i in range(opts.users):
        users.append(User("drill-user-%d" % (i + 1), args.fleet, master.target, urls, hosts[:3] + ["example.com", "wikipedia.org"],
                          dns_ip, cloud))
    if opts.partner:
        with open(os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "federation.yaml")) as fh:
            fed = yaml.safe_load(fh) or {}
        pname = os.path.basename(os.path.dirname(os.path.abspath(opts.partner)))
        shared = ["%s.%s" % (e, fleet.defaults.get("domain") or "home") for e in (fed.get(pname) or {}).get("share") or [] if "." in e]
        purls = ["%s:443:%s" % (n, ip) for n, ip in services if n in shared]
        with open(opts.partner) as fh:
            pdns = ((yaml.safe_load(fh) or {}).get("defaults", {}).get("master") or {}).get("vpn-server", {}).get("server_ip") or "10.10.0.1"
        users.append(User("drill-partner-1", opts.partner, opts.partner_ssh, purls, shared, pdns))
        say("partner user of %s: %s" % (pname, " ".join(shared) or "nothing shared"))
    t0 = time.time()
    end = t0 + opts.minutes * 60

    def backup():
        minute, host = opts.backup_at
        time.sleep(max(0, float(minute) * 60 - (time.time() - t0)))
        events.append((time.time() - t0, "backup of %s starts" % host))
        rc, _out = kiwi("backups", args.fleet, host, "--now", timeout=3600)
        events.append((time.time() - t0, "backup of %s ends (%d)" % (host, rc)))
    if opts.backup_at:
        threading.Thread(target=backup, daemon=True).start()

    def restart():
        minute, unit = opts.restart_at
        time.sleep(max(0, float(minute) * 60 - (time.time() - t0)))
        events.append((time.time() - t0, "%s restarts on %s" % (unit, master.name)))
        rc, _out = master.run("systemctl restart %s" % unit, 300)
        events.append((time.time() - t0, "%s restarted (%d)" % (unit, rc)))
    if opts.restart_at:
        threading.Thread(target=restart, daemon=True).start()

    def run(u):
        while time.time() < end:
            start = time.time()
            for kind, name, code, secs in u.round():
                with lock:
                    results.append((start - t0, u.name, kind, name, code, secs))
            time.sleep(max(0, opts.every - (time.time() - start)))
    threads = [threading.Thread(target=run, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
finally:
    for u in users:
        u.cleanup()

say("---- %d checks over %.0f minutes" % (len(results), opts.minutes))
by = {}
for at, user, kind, name, code, secs in results:
    by.setdefault((kind, name), []).append((at, user, code, secs))
for (kind, name), rows in sorted(by.items()):
    times = [s for _a, _u, c, s in rows if ok(kind, c)]
    bad = [(a, u, c) for a, u, c, _s in rows if not ok(kind, c)]
    p50 = statistics.median(times) if times else 0
    p95 = sorted(times)[int(len(times) * 0.95) - 1] if len(times) >= 20 else (max(times) if times else 0)
    when = sorted({"%.1f" % (a / 60) for a, _u, _c in bad}, key=float)
    print("  %-5s %-28s %3d ok %3d failed  p50 %5.2fs  p95 %5.2fs%s" % (
        kind, name, len(times), len(bad), p50, p95, ("  at min " + " ".join(when)) if when else ""))
fails = sorted((a, u, k, n, c) for a, u, k, n, c, _s in results if not ok(k, c))
for at, what in sorted(events):
    print("  %5.1f min  %s" % (at / 60, what))
print("  %d failed checks in all" % len(fails))
sys.exit(0)
