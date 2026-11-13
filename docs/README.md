# kiwi-server documentation

kiwi-server builds the machines of a [Kiwi Network](https://kiwi-network.eu).
You write one `fleet.yaml`; it makes an unattended install ISO per host and
then sets up and maintains each machine over SSH. Its command is
`kiwi-server`; its desktop app is **Kiwi Server** (`kiwi-server-gui`).

## Pages

| Page | For | What it covers |
|---|---|---|
| [Install](install.md) | everyone | kiwi or the checkout, what it needs, the ISO toolchain |
| [Getting started](getting-started.md) | everyone | a new fleet, the first ISO, apply, offline media, netcup servers |
| [The fleet file](fleet.md) | everyone | the settings, the role block, names and the domain, `kiwi-stack` on the machine |
| [Roles and modules](roles.md) | everyone | master and node, presets, a public hub, the LAN cache, writing a role |
| [The network](network.md) | everyone | joining the mesh, DNS, the MTU, federation |
| [Machines](machines.md) | everyone | targets, the output, first boot, updates, a master in RAM, running machines |
| [Backups](backups.md) | everyone | the fleet directory, each machine's data, restore, prune |
| [Security](security.md) | everyone | the secret store, the seed, encrypted disks and unlock at boot, SSH |
| [The desktop app](gui.md) | everyone | Kiwi Server, the overview, a host's pages |
| [Routers](routers.md) | everyone | a whole LAN on the mesh through its router |
| [Modules](modules.md) | module authors | the module reference: `module.yaml`, templates, adding one |
| [Development](development.md) | contributors | tests, kiwi-modules and kiwi-stack, releases |
| [Testing on real hardware](testing-hardware.md) | contributors | the runbook for real machines |

## Design and plans

| Page | What it covers |
|---|---|
| [Roadmap](ROADMAP.md) | what is done, what is wrong, in which order |
| [The control plane](design/control-plane.md) | where kiwi-server is going |
| [A master in RAM](design/ram-master.md) | `target: coreos-ram` |
| [Federation](design/federation.md) | two networks sharing services |
| [Local first](design/local-first.md) | the LAN cache, network installs |
| [Handoff](HANDOFF.md) | notes for a session on the test hardware |
| [Review of 2.1.0](REVIEW-2.1.0.md) | findings of the 2.1.0 review |

The source of these pages is the `docs/` folder of
[derlocke-ng/kiwi-server](https://github.com/derlocke-ng/kiwi-server).
The pages link to each other with relative links, so the folder can be built
on its own or copied into a larger site.
