#!/usr/bin/env python3
"""Who may reach what, tried from real VPN clients.

`kiwi-server access` says what each group of the VPN and each linked network may reach. This
makes one WireGuard client per group (a device `drill-in-<group>`, made once), one outside
every group and, with --partner, one in the partner network, and tries from each of them what
it should reach and what it should not: every machine's SSH port, every service's page, the
ports a rule names, the master's admin pages, another device, the internet. It changes
nothing but those devices.

    tests/drills/access.py ~/kiwi/fleet.yaml sorbet [--partner ~/kiwi2/fleet.yaml --partner-ssh core@188.68.38.110]

Needs podman with /dev/net/tun. Exit 1 when one try came out other than the rules say.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

from drill import kiwi, say, setup
from kiwiserver import cli

extra = argparse.ArgumentParser()
extra.add_argument("--partner", help="the partner network's fleet file: one client of it tries what is shared")
extra.add_argument("--partner-ssh", help="how to reach the partner's master")
extra.add_argument("--only", nargs="*", default=[], help="only these groups (and `none` for the client outside every group)")
opts, rest = extra.parse_known_args()
sys.argv = [sys.argv[0]] + rest
args, fleet, m = setup(__doc__.splitlines()[0], roles=("master",))
master = m["master"]
rc, out = kiwi("access", args.fleet, "--porcelain")
if rc != 0:
    sys.exit("drill: kiwi-server access: %s" % " ".join(out[-2:]))
facts = json.loads("\n".join(x for x in out if not x.startswith(("::", "warning"))))
_fleet, roles, ms = cli.load(args)
server_ip = master.host.module_settings["vpn-server"].get("server_ip") or "10.8.0.1"
mesh = (fleet.defaults.get("master") or {}).get("mesh_subnet") or "10.8.0.0/16"
domain = fleet.defaults.get("domain") or "home"
nodes = [x for x in facts["machines"] if not x["master"]]
ssh_port = {h.name: int(h.admin.get("ssh_port") or 22) for h in _fleet.hosts.values()}
INTERNET = "1.1.1.1"
admin_page = "wg.%s" % next(x["hostname"] for x in facts["machines"] if x["master"])       # wg-easy, through the master's proxy
# admin areas inside a site (kiwi-modules 0.67.0: `admin: true` on a location): only for the groups with the admin pages
ADMIN_AREAS = {"jabber": "admin/", "xmpp": "admin/", "vault": "admin"}

PROBE = r"""
while read kind a b; do
  case $kind in
    tcp)  nc -z -w 4 "$a" "$b" 2>/dev/null && r=open || r=closed ;;
    web)  r=$(curl -sk -o /tmp/body -w '%{http_code}' --max-time 8 --resolve "$a:443:$b" "https://$a/")
          [ "$r" = 403 ] && grep -q '<center>nginx' /tmp/body && r=refused ;;
    admin) h=${a%%/*}; r=$(curl -sk -o /tmp/body -w '%{http_code}' --max-time 8 --resolve "$h:443:$b" "https://$h/${a#*/}")
          [ "$r" = 403 ] && grep -q '<center>nginx' /tmp/body && r=refused ;;
    ping) ping -c 1 -W 3 "$a" >/dev/null 2>&1 && r=open || r=closed ;;
    dns)  dig +time=3 +tries=1 @"$b" "$a" +short 2>/dev/null | grep -q . && r=open || r=closed ;;
    net)  r=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$a/") ;;
  esac
  echo "$kind $a $b $r"
done
"""


class Client:
    def __init__(self, name, fleet_path, ssh, group=None, allowed=()):
        self.name = name
        conf = os.path.join(os.path.dirname(os.path.abspath(fleet_path)), "secrets", "devices", name + ".conf")
        cmd = ["enroll", fleet_path, name] + (["--ssh", ssh] if ssh else []) + (["--group", group] if group else [])
        rc, out = kiwi(*(cmd + (["--existing"] if os.path.isfile(conf) else [])), timeout=300)
        if rc != 0:
            raise SystemExit("drill: enroll %s: %s" % (name, " ".join(out[-2:])))
        with open(conf) as fh:
            text = fh.read()
        self.address = next(x.split("=", 1)[1].strip().split("/")[0] for x in text.splitlines() if x.startswith("Address"))
        self.plain = os.path.join(os.path.dirname(conf), ".%s.wg-quick.conf" % name)
        fd = os.open(self.plain, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:      # no DNS line, and only what the drill asks through the tunnel
            fh.write("".join(("AllowedIPs = %s\n" % ", ".join(allowed)) if x.startswith("AllowedIPs") else x + "\n"
                             for x in text.splitlines() if not x.startswith("DNS")))
        self.box = "kiwi-%s" % name
        subprocess.run(["podman", "rm", "-f", self.box], capture_output=True)
        subprocess.run(["podman", "run", "-d", "--name", self.box, "--security-opt", "label=disable", "--cap-add", "NET_ADMIN",
                        "--sysctl", "net.ipv4.conf.all.src_valid_mark=1",
                        "-v", "%s:/etc/wireguard/wg0.conf:ro" % self.plain, "docker.io/library/alpine:3.20", "sh", "-c",
                        "apk add -q curl bind-tools wireguard-tools iproute2 iptables ip6tables && wg-quick up wg0 && sleep infinity"],
                       check=True, capture_output=True)

    def ready(self):
        for _ in range(40):
            if subprocess.run(["podman", "exec", self.box, "sh", "-c", "wg show wg0 latest-handshakes | awk '$2>0' | grep -q ."],
                              capture_output=True).returncode == 0:
                return True
            time.sleep(3)
        return False

    def ask(self, tries):
        """{(kind, a, b): result} for the tries [(kind, a, b)]."""
        lines = "".join("%s %s %s\n" % t for t in tries)
        r = subprocess.run(["podman", "exec", "-i", self.box, "sh", "-c", PROBE], input=lines, capture_output=True, text=True,
                           timeout=30 + 10 * len(tries))
        got = {}
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 4:
                got[tuple(parts[:3])] = parts[3]
        return got

    def cleanup(self):
        subprocess.run(["podman", "rm", "-f", self.box], capture_output=True)
        if os.path.exists(self.plain):
            os.remove(self.plain)


def expect(row, other):
    """[(what it is, try, the results that are right)] for one group, linked network or None."""
    opened = row["open"] if row else {"all": True, "machines": {}}
    everything = opened["all"]
    is_group = bool(row) and row["kind"] == "group"
    out = []
    yes, no = ("open",), ("closed",)
    for n in nodes:
        mm = opened["machines"].get(n["name"]) or {"whole": False, "services": [], "ports": []}
        whole = everything or mm["whole"]
        port = ssh_port.get(n["name"], 22)
        named = str(port) in mm["ports"]
        out.append(("%s, all of it (port %d)" % (n["name"], port), ("tcp", n["ip"], str(port)), yes if (whole or named) else no))
        for p in mm["ports"]:
            if p != str(port):
                out.append(("%s:%s" % (n["name"], p), ("tcp", n["ip"], p), yes))
        apps = opened.get("apps", False)
        admin = row["admin"] if is_group else row is None       # a linked network: only by name
        for s in n["services"]:
            name = "%s.%s" % (s, n["hostname"])
            page = s in (n.get("admin") or [])
            if s in mm["services"] or ((whole or apps) and (admin or not page)):
                out.append((name, ("web", name, n["ip"]), None))                        # any answer of the service
                if s in ADMIN_AREAS and is_group:       # its admin area: the admins only (a device outside every group is one)
                    out.append(("%s/%s (an admin area)" % (name, ADMIN_AREAS[s]), ("admin", "%s/%s" % (name, ADMIN_AREAS[s]), n["ip"]),
                                None if row["admin"] else ("refused",)))
            else:
                out.append((name + (" (an admin page)" if page and (whole or apps) else " (not given)"),
                            ("web", name, n["ip"]), ("000", "refused")))
    if row is None or is_group:
        admin = row["admin"] if row else True
        out.append(("the master's names (DNS)", ("dns", nodes[0]["hostname"] if nodes else "example.com", server_ip), yes))
        out.append(("the master's admin page", ("web", admin_page, server_ip), None if admin else ("000",)))
        if other:
            out.append(("another device", ("ping", other, "-"), yes if (row["peers"] if row else True) else no))
        out.append(("the internet", ("net", INTERNET, "-"), None if (row["internet"] if row else True) else ("000",)))
    return out


clients, failed, tried = {}, 0, 0
try:
    want = [r for r in facts["who"] if r["kind"] == "group" and (not opts.only or r["name"] in opts.only)]
    say("%d groups of %s, %d machines; making the clients" % (len(want), master.name, len(nodes)))
    plain = Client("drill-user-1", args.fleet, master.target, allowed=[mesh, INTERNET + "/32"])
    clients[None] = plain
    made = {}

    def make(r):
        made[r["name"]] = Client("drill-in-%s" % r["name"], args.fleet, master.target, group=r["name"], allowed=[mesh, INTERNET + "/32"])
    for r in want:          # one after the other: each enroll talks to the master
        make(r)
    clients.update({r["name"]: made[r["name"]] for r in want})
    partner = None
    if opts.partner:
        import yaml
        with open(opts.partner) as fh:
            pmesh = ((yaml.safe_load(fh) or {}).get("defaults", {}).get("master") or {}).get("mesh_subnet") or "10.10.0.0/16"
        # its name here: the entry of this network's federation.yaml with its mesh (the folders may be named otherwise)
        fed = os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "federation.yaml")
        with open(fed) if os.path.isfile(fed) else open(os.devnull) as fh:
            linked = yaml.safe_load(fh) or {}
        pname = next((n for n, p in linked.items() if str((p or {}).get("mesh")) == str(pmesh)),
                     os.path.basename(os.path.dirname(os.path.abspath(opts.partner))))
        partner = next((r for r in facts["who"] if r["kind"] == "network" and r["name"] == pname), None)
        if partner is None:
            sys.exit("drill: %s is not linked to a network called %s" % (args.fleet, pname))
        clients[pname] = Client("drill-partner-1", opts.partner, opts.partner_ssh, allowed=[pmesh, mesh])
    for c in clients.values():
        if not c.ready():
            say("%s: no handshake with its master" % c.name)
    rows = ([] if opts.only and "none" not in opts.only else [(None, None)]) + [(r["name"], r) for r in want] + \
        ([(partner["name"], partner)] if partner else [])
    results = {}

    def run(key, row):
        exp = expect(row, plain.address if key is not None else None)
        results[key] = (exp, clients[key].ask([t for _w, t, _r in exp]))
    threads = [threading.Thread(target=run, args=kr) for kr in rows]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for key, row in rows:
        exp, got = results[key]
        print("\n%s — %s" % (key or "a device outside every group", (row["says"] if row else "everything")
                             + ("" if not row or row["kind"] != "group" else
                                " (other devices: %s, internet: %s, admin pages: %s)" % tuple(
                                    "yes" if row[k] else "no" for k in ("peers", "internet", "admin")))))
        for what, t, right in exp:
            g = got.get(t, "no answer")
            if right is None:
                good = g not in ("000", "refused", "no answer")
            else:
                good = g in right
            tried += 1
            failed += not good
            print("  %-4s %-44s %-8s %s" % ("ok" if good else "FAIL", what, g,
                                             "" if good else "(should be: %s)" % ("an answer" if right is None else " or ".join(right))))
finally:
    for c in clients.values():
        c.cleanup()
print("\n%d tries, %d came out other than the rules say" % (tried, failed))
sys.exit(1 if failed else 0)
