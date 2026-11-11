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
| `ram-master-vm.sh [WORKDIR]` | Builds a `coreos-ram` master in a throwaway fleet, boots it in a local QEMU VM on a blank disk, applies, reboots, then boots a new image on the same disk; checks the state comes back and `/etc` does not (a few minutes; touches no fleet machine, so it needs no `drills.yaml`) | `targets/ram.py`, the state partition, `apply` on it |

Each drill prints what it measured, one line per step, and puts back what it changed (a
firewall rule, a stopped container) when it ends, also when it fails half way.
