# kiwi-server documentation

kiwi-server creates and manages the machines of a Kiwi Network from one file,
`fleet.yaml`. It builds an unattended install ISO for each host, applies each
machine's role over SSH, and then keeps working with the fleet: WireGuard
clients, health, secrets, backups and encrypted disks. A *master* is the only
machine reachable from the Internet; *nodes* run your services at home or in
the office; a *module* is one service of a machine, and a *role* is the set of
modules a machine runs.

| Page | For | What it covers |
|---|---|---|
| [Install](install.md) | everyone | requirements, install, the ISO toolchain, uninstall |
| [Build and install machines](build-and-install.md) | everyone | from the fleet file to an installed machine, offline media, netcup servers |
| [The fleet file](fleet-file.md) | everyone | hosts, defaults, targets, settings, names and addresses |
| [Roles and modules](roles-and-modules.md) | everyone | what a machine can become, every module, writing a role |
| [Manage running machines](managing.md) | everyone | apply, plan, status, check, enroll, MTU, `kiwi-stack` |
| [First boot and updates](updates.md) | everyone | the first boot, update windows, a master in RAM |
| [DNS and networks](network.md) | everyone | names, the DNS chain, a public hub, the LAN cache, federation |
| [Routers](routers.md) | everyone | joining a whole LAN to the mesh through its router |
| [Backups](backups.md) | everyone | the fleet backup and the machines' own data |
| [Disk encryption](disk-encryption.md) | everyone | encrypted disks and Tang rings |
| [Security](security.md) | everyone | the secret store, SSH, and what to keep in mind |
| [The app](gui.md) | everyone | the Kiwi Server desktop app |
| [Migrate a v1 machine](migrating-from-v1.md) | owners of v1 setups | taking over a machine that ran a v1 stack |
| [Module reference](modules.md) | module authors | the module format, the template context, how a stack is rendered |
| [Development](development.md) | contributors | the tree, tests, CI, vendored copies, releases, the project notes |
| [Testing on real hardware](testing-hardware.md) | contributors | the runbook for a hardware test round |
| [Roadmap and known issues](ROADMAP.md) | contributors | what is done, known problems, the order of work |
| [Handoff notes](HANDOFF.md) | contributors | the state and decisions for work on the test machines |
| [Review of 2.1.0](REVIEW-2.1.0.md) | contributors | the findings of the review of release 2.1.0 |
| [Design: the control plane](design/control-plane.md) | contributors | kiwi-server as a service on a node; designed, not built |
| [Design: federation](design/federation.md) | contributors | linked networks |
| [Design: a master in RAM](design/ram-master.md) | contributors | the `coreos-ram` target |
| [Design: local first](design/local-first.md) | contributors | the LAN cache, netboot and a control node |

The changes of each release are in
[CHANGELOG.md](https://github.com/derlocke-ng/kiwi-server/blob/main/CHANGELOG.md).
These pages are also on
[kiwi-network.eu](https://kiwi-network.eu/docs/server/), where they are pulled
from this folder at every release.
