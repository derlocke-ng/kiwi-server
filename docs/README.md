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
| [Using your network](using.md) | everyone whose machines run | your devices onto the network, opening a service, logins, keeping it well |
| [Example setups](examples.md) | everyone | pictures of possible networks, what each machine does, who reaches what |
| [The fleet file](fleet.md) | everyone | the settings, the role block, names and the domain, `kiwi-stack` on the machine |
| [Roles and modules](roles.md) | everyone | master and node, presets, a public hub, the LAN cache, writing a role |
| [The network](network.md) | everyone | joining the mesh, DNS, the MTU, federation |
| [Machines](machines.md) | everyone | targets, the output, first boot, updates, a master in RAM, running machines |
| [The control node](control.md) | everyone | kiwi-server on a home node, a page for the phone, `control push` |
| [Boards](boards.md) | everyone | an Odroid or other arm64 board with its own Debian or Armbian, `adopt` |
| [Backups](backups.md) | everyone | the fleet directory, each machine's data, restore, prune |
| [When something breaks](recovery.md) | everyone | the readiness report, and what to do when data, a machine, the master, a device, a computer or a home is lost |
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
| [Groups of the home network](design/lans.md) | a subnet or VLAN at home given what it may reach |
| [Recovery](design/recovery.md) | what runs by itself when something breaks, and what is planned |
| [Hidden services](design/onion.md) | services for strangers over Tor, never through a home connection |
| [The starter image](design/starter.md) | a network from a phone: one image, the rest in a browser |
| [A network without a master](design/no-master.md) | one machine at home first, a master later |
| [Keeping data across a reinstall](design/data-partition.md) | one-disk machines: a kept, encrypted data partition (not built; questions open) |
| [Controllers](design/controllers.md) | several computers managing one fleet: join, sync, rotation |
| [Local first](design/local-first.md) | the LAN cache, network installs |
| [A standby master](design/standby.md) | the same master on a second machine, which takes over when the first is gone |
| [Encrypted disks by default](design/encryption.md) | every target encrypted, and how each opens at boot |
| [Who opens whom](design/unlock.md) | the ways a disk opens at boot, openers, one picture for page and apps |
| [The page and the apps as server management](design/ui.md) | the machines table by site, a machine's story and states, one name per thing, speed, the app on the shared kit |
| [Many machines, and services that move](design/scale.md) | why not Ansible or one cluster, what 2.259 built, machines that fetch their settings, names by service, moving a service, standby and failover, site controllers |
| [Services for the public](design/public-services.md) | accounts, sign-up, landing page, status |
| [A public website](design/website.md) | the front page from a pinned repository of pages |

The source of these pages is the `docs/` folder of
[derlocke-ng/kiwi-server](https://github.com/derlocke-ng/kiwi-server).
The pages link to each other with relative links, so the folder can be built
on its own or copied into a larger site.
