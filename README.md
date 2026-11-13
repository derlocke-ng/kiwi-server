# kiwi-server

kiwi-server creates and manages the machines of a
[Kiwi Network](https://kiwi-network.eu). You describe every machine in one
file, `fleet.yaml`, and kiwi-server turns it into an unattended install ISO
for each host. You boot the ISO, the machine installs Fedora CoreOS, uCore or
Debian without asking any questions, and kiwi-server then sends it its role
over SSH. The role makes it a *master* (the only machine reachable from the
Internet) or a *node* (a machine at home or in the office that runs your
services). After that, kiwi-server keeps working with the fleet: it enrolls
WireGuard clients, reports each machine's health, keeps the secrets, backs up
the fleet and unlocks encrypted disks.

kiwi-server is for people who run their own Kiwi Network and want every
machine to be rebuilt from one file and a backup. It is the network part of
Kiwi Network. It comes as a command-line tool, `kiwi-server`, and as a GTK4
desktop app, **Kiwi Server** (`kiwi-server-gui`).

- Latest release: **v2.57.0**, in the
  [Kiwi catalog](https://github.com/derlocke-ng/kiwi-catalog). It carries
  kiwi-modules v0.28.0 and kiwi-stack v0.7.0.
- Runs on: Linux with Python 3.9 or newer, as your own user. CI runs it on
  Ubuntu (GitHub's `ubuntu-latest`).
- Installs machines with: Fedora CoreOS (stable), uCore, Debian 13 (trixie).
- License: GPL-3.0-or-later

## Install

With [kiwi-updater](https://kiwi-network.eu/docs/desktop/kiwi-updater/) and
the Kiwi catalog registered:

```bash
kiwi install kiwi-server
```

Or from a checkout, with the same installer that kiwi runs:

```bash
git clone https://github.com/derlocke-ng/kiwi-server.git && cd kiwi-server
./install.sh install
kiwi-server doctor
```

The installer puts everything under `~/.local` and needs no root rights.
kiwi-server needs `python3` with PyYAML and Jinja2, `openssl` and `ssh`. To
build ISOs it also needs `butane`, `coreos-installer` and `xorriso`. If your
system does not have them, `kiwi-server toolchain build` puts them into a
container image. [Install](docs/install.md) has the details.

## Quick start

```bash
kiwi-server init fleet.yaml        # asks for the domain, your SSH key, the master and the machines at home
kiwi-server validate fleet.yaml
kiwi-server build fleet.yaml papaya            # writes output/papaya/papaya.iso
sudo dd if=output/papaya/papaya.iso of=/dev/sdX bs=4M status=progress oflag=sync
kiwi-server apply fleet.yaml papaya --ssh core@192.168.1.25
kiwi-server status fleet.yaml
```

**Warning.** The ISO wipes the disk named in the host's `disk:` setting as
soon as a machine boots from it, without asking. The machine installs and
powers off. Remove the stick and start the machine again. `apply` then sends
it its role and starts its services.
[Build and install machines](docs/build-and-install.md) explains each step.

## Documentation

| Page | What it covers |
|---|---|
| [Install](docs/install.md) | requirements, install, the ISO toolchain, uninstall |
| [Build and install machines](docs/build-and-install.md) | from the fleet file to an installed machine, offline media, netcup servers |
| [The fleet file](docs/fleet-file.md) | hosts, defaults, settings, names and addresses |
| [Roles and modules](docs/roles-and-modules.md) | what a machine can become, every module, writing a role |
| [Manage running machines](docs/managing.md) | apply, status, enroll, MTU, the `kiwi-stack` command on a machine |
| [First boot and updates](docs/updates.md) | the first boot, update windows, a master in RAM |
| [DNS and networks](docs/network.md) | names, the DNS chain, a public hub, the LAN cache, federation |
| [Backups](docs/backups.md) | the fleet backup and the machines' own data |
| [Disk encryption](docs/disk-encryption.md) | encrypted disks and unlocking them at boot |
| [Security](docs/security.md) | the secret store, SSH, and what to keep in mind |
| [The app](docs/gui.md) | the Kiwi Server desktop app |

[docs/README.md](docs/README.md) lists every page, including the module
reference, routers and the development notes. The same pages are on
[kiwi-network.eu](https://kiwi-network.eu/docs/server/), where they are
updated with each release.

## License

GPL-3.0-or-later; see [LICENSE](LICENSE). The icon is kiwi-server from
[kiwi-icons](https://github.com/derlocke-ng/kiwi-icons).
