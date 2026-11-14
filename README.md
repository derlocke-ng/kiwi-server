# kiwi-server

Build the machines of a [Kiwi Network](https://kiwi-network.eu): write one
`fleet.yaml`, get an **unattended install ISO** per host. Boot it, walk away,
and the machine comes back as a `kiwi-node` or `kiwi-master` on Fedora CoreOS,
uCore or Debian stable. It updates itself and reboots only in the window you
chose.

- **`kiwi-server`**: the command-line tool
- **Kiwi Server** (`kiwi-server-gui`): the desktop app
- **One file for the fleet**: roles, modules, names, certificates, the mesh,
  backups
- **No root on your computer**: installed into `~/.local`; the ISO tools run
  in a container when they are missing

```
fleet.yaml ──render──▶ <host>.role.sh            the role, as one bash script
                       <host>.bu / .ign          CoreOS, uCore: Butane → Ignition
                       <host>.preseed.cfg        Debian: preseed + /kiwi-server files
           ──build───▶ <host>.iso                installs to the given disk WITHOUT ASKING,
                                                 reboots, runs the role script once
```

## Install

With [kiwi](https://github.com/derlocke-ng/kiwi-updater):

```bash
kiwi catalog add https://github.com/derlocke-ng/kiwi-catalog.git   # once per machine
kiwi install kiwi-server
```

Or from the checkout: `./install.sh install`. What it needs, and the ISO
tools: [docs/install.md](docs/install.md).

## Use

```bash
kiwi-server init                             # a new network: a few questions, into ~/kiwi
kiwi-server next ~/kiwi/fleet.yaml           # does the next step; run it again until all runs
kiwi-server list fleet.yaml                  # every host and what comes next
kiwi-server build fleet.yaml                 # the ISOs, in ./output/<host>/
kiwi-server apply fleet.yaml sh3             # set up or change a running machine
kiwi-server status fleet.yaml                # how every machine is doing
kiwi-server logins fleet.yaml                # the address, user name and password of every service
kiwi-server-gui                              # the desktop app
```

Step by step: [docs/getting-started.md](docs/getting-started.md).

## Documentation

| | |
|---|---|
| [Install](docs/install.md) | kiwi or the checkout, what it needs, the ISO toolchain |
| [Getting started](docs/getting-started.md) | a new fleet, the first ISO, apply, offline media, netcup servers |
| [The fleet file](docs/fleet.md) | the settings, the role block, names and the domain |
| [Roles and modules](docs/roles.md) | master and node, presets, a public hub, the LAN cache, writing a role |
| [The network](docs/network.md) | joining the mesh, DNS, the MTU, federation, [routers](docs/routers.md) |
| [Machines](docs/machines.md) | targets, the output, first boot, updates, a master in RAM |
| [The control node](docs/control.md) | kiwi-server on a home node, with a page for the phone |
| [Boards](docs/boards.md) | Odroid and other arm64 boards: `kiwi-server adopt` |
| [Backups](docs/backups.md) | the fleet directory, each machine's data, restore |
| [Security](docs/security.md) | the secret store, encrypted disks, unlock at boot, SSH |
| [The desktop app](docs/gui.md) | Kiwi Server |
| [Development](docs/development.md) | tests, kiwi-modules and kiwi-stack, [the module reference](docs/modules.md) |

Plans and design: [docs/ROADMAP.md](docs/ROADMAP.md) and
[the other pages in docs/](docs/README.md). Release notes:
[CHANGELOG.md](CHANGELOG.md).

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).

The icon is kiwi-server from [kiwi-icons](https://github.com/derlocke-ng/kiwi-icons).
