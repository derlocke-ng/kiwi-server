# Drills

Tests that break real machines on purpose: a power cut, a backup killed or starved, a full
disk, a master gone, a rebuild from nothing. They are not part of `tests/` because they need
the test machines, take minutes to an hour and leave the machines in a state someone chose.
Run them when the code they cover changes, and before a release that touches it.

Every drill takes the fleet file and the hosts it plays with, and refuses a host that is not
listed in `drills.yaml` next to the fleet file:

```yaml
allowed: [sorbet, papaya, lychee]
```

A live network's fleet directory has no such file, so nothing there can be drilled by mistake.
The drills reach machines the way kiwi-server does (the fleet's admin key and known hosts,
the addresses in the fleet file) and run this tree's `bin/kiwi-server`, so they test what is
checked out. `drill.py` holds what they share.

| Drill | What it does | Covers |
|---|---|---|
| `exit-server-stuck.py FLEET MACHINE` | Makes DNS-over-TLS fail inside a commercial VPN exit, as through a bad server, and waits for the container to be started anew (about eight minutes, the exit is down most of them) | vpn-client's health check, `HealthOnFailure` |
| `master-down.py FLEET MASTER NODE [--minutes 5]` | Stops the master's WireGuard server for some minutes; records which of the node's containers restart, whether the node's own Pi-hole still answers (an uncached name, 5 s twice, as a client asks) and how fast the node reaches the master again | the node's independence, gluetun's reconnect. 11 Nov 2026: nothing restarted, back after 14 s; a single 3 s lookup missed 3 of 10 (strict-order asks the master first), a client's retry is answered |
| `multihop.py FLEET MASTER [--exit-mesh IP]` | A client of a master whose VPN client leaves through another network's master: its public address is the other master's, the first master's names resolve to its own mesh address, the other mesh stays closed (enrolls `drill-multihop` once) | `vpn_provider: custom` on a master |
| `federation.py A B --shared S [--unshared U] [--closed N]` | A client of network B reaches the service A shares (it answers), gets 403 on another name of the same node and nothing from a node not shared; A's names resolve through B's DNS (enrolls `drill-federation` on B once; B's master in B's `drills.yaml`) | `federate`, the link, its firewall and the nodes' nginx rules |
| `soak.py FLEET [--hours 3] [--every 600]` | Watches `status` for hours, read-only: problems as they start and end, containers that restarted, reboots, backups (needs no `drills.yaml`) | a night as the machines live it |
| `backup-server-hung.py FLEET CLIENT SERVER` | Pauses the server's restic REST container and runs a backup on the client: it must end with an error within the module's limits, leave nothing paused, keep the stack up; with the server back the next run must succeed | the backup module's `ASK_LIMIT`, thaw on failure. 11 Nov 2026 (lychee → papaya): gave up after 420 s, nothing paused, next run 3 s |
| `ram-master-power.py FLEET MASTER` | Powers a `coreos-ram` master off and on through netcup's API, runs `watch --once` until it has its role again, checks the WireGuard key and the stack | `targets/ram.py`, `watch` after a hoster's power cycle. 11 Nov 2026 (crumble): role back 134 s after power on, same key; federation drill passed right after |
| `users.py FLEET MASTER [--users 3] [--minutes 20] [--every 60] [--backup-at MIN HOST] [--restart-at MIN UNIT] [--partner FLEET --partner-ssh T]` | Synthetic users: WireGuard clients of the master run here (gluetun plus an alpine with curl and dig), each round DNS through the Pi-hole, every service's page, 1 MB to Nextcloud over WebDAV and back; per name the checks, failures (and when) and p50/p95. A partner user opens what is shared with it | what people see during a backup, an update, a restart. 11 Nov 2026: 1625 checks in 25 min with a papaya backup, none failed; a WireGuard server restart: sorbet back in 6 s, papaya untouched, lychee ~25 s (keepalive 25 s, now 10 s); the test clients themselves (gluetun) take up to 2 min — plain WireGuard clients would be closer to a phone |
| `ram-master-vm.sh [WORKDIR]` | Builds a `coreos-ram` master in a throwaway fleet, boots it in a local QEMU VM without a disk, lets `watch` send the role, reboots, lets it send it again; checks the master comes back with the same keys and a fresh `/etc` (a few minutes; touches no fleet machine, so it needs no `drills.yaml`) | `targets/ram.py`, `watch`, `apply` on a blank master |

Each drill prints what it measured, one line per step, and puts back what it changed (a
firewall rule, a stopped container) when it ends, also when it fails half way.
