# kiwi-server

Build the machines of a [Kiwi Network](https://kiwi-network.eu): write one
`fleet.yaml`, get a **first-boot script**, an **Ignition or preseed config**
and an **unattended install ISO** per host. Boot the ISO, walk away, and the
machine comes back as a `kiwi-node` or `kiwi-master` — on Fedora CoreOS, uCore
or Debian stable — with automatic updates that reboot only in the window you
chose.

A [kiwi-updater](https://github.com/derlocke-ng/kiwi-updater) app: a CLI
(`kiwi-server`) and a GTK4 desktop app (**Kiwi Server**, `kiwi-server-gui`),
installed into `~/.local`, no root.

```
fleet.yaml ──render──▶ <host>.role.sh            the role, as one bash script
                       <host>.bu / .ign          CoreOS, uCore: Butane → Ignition
                       <host>.preseed.cfg        Debian: preseed + /kiwi-server files
           ──build───▶ <host>.iso                installs to the given disk WITHOUT ASKING,
                                                 reboots, runs the role script once
```

- **Targets** — `coreos` (Fedora CoreOS stable), `ucore` (CoreOS that rebases
  itself onto a `ghcr.io/ublue-os/ucore*` image, like the uCore project's own
  autorebase example), `debian` (trixie netinst, preseeded).
- **Roles** — `bare`, `master` (WireGuard entry point with a double hop
  through a commercial VPN, Pi-hole, Tor), `node-gw` (LAN gateway through the
  VPN with Pi-hole, DHCP relay, Transmission, JDownloader, SFTP) and
  `node-cloud` (Nextcloud AIO, Vaultwarden). Each is a preset of
  **modules** — the kiwi-v2 module system (`modules/`), rendered on your
  machine into a docker compose stack the host starts on first boot. The
  three presets are the live kiwi-master, kiwi-node-gw and kiwi-cloud
  setups, taken apart into modules.
- **One CA for the fleet** — hosts that run the reverse proxy get a
  `*.<hostname>` certificate signed by it, every machine built trusts it, and
  every Pi-hole in the fleet resolves every host and service name at its mesh
  address.
- **Updates** — fetched continuously on every target; the *reboot* happens in
  your window: Zincati `periodic` on CoreOS, staged rpm-ostree updates plus a
  reboot timer on uCore, unattended-upgrades plus a reboot-if-required timer
  on Debian.
- **No layering** — butane, coreos-installer and xorriso run natively when
  installed, otherwise in a small podman/docker image.

## Install

Through kiwi, once it is in your catalog:

```bash
kiwi install kiwi-server
```

Or straight from the checkout — it is the same installer kiwi runs:

```bash
git clone https://github.com/derlocke-ng/kiwi-server.git && cd kiwi-server
./install.sh install            # ~/.local/bin/kiwi-server, kiwi-server-gui, the library
kiwi-server doctor              # python, pyyaml, build tools, container runtime
```

Needs `python3` with PyYAML and Jinja2 (`pip install --user jinja2` if the
image lacks it), `openssl` and `git`. Building ISOs needs `butane`,
`coreos-installer` and `xorriso`; on a desktop that does not have them:

```bash
kiwi-server toolchain build     # one image with all three, used automatically
```

## Quick start

```bash
kiwi-server init                # writes a commented fleet.yaml
$EDITOR fleet.yaml              # hosts, roles, your ssh key, the install disk
kiwi-server validate fleet.yaml
kiwi-server render fleet.yaml   # scripts + configs into ./output/<host>/
kiwi-server build fleet.yaml    # + the ISOs (base images cached in ~/.cache/kiwi-server)
sudo dd if=output/sh3/sh3.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Boot the machine from it. CoreOS and Debian install, reboot, and run the role
on first boot; uCore reboots twice more first (unsigned rebase, then signed).
Watch it with `journalctl -u kiwi-role -f`; `/var/lib/kiwi-server/role.done`
appears when it is finished.

Or skip the ISO: `kiwi-server script fleet.yaml sh3 > sh3.sh`, copy it to any
installed Debian / CoreOS / uCore machine and `sudo bash sh3.sh`. It is the
same script the ISO runs.

## The fleet file

```yaml
defaults:                          # every host, unless it says otherwise
  target: ucore                    # coreos | ucore | debian
  role: bare                       # bare | master | node-gw | node-cloud
  domain: kiwi                     # sh3 becomes sh3.kiwi
  timezone: Europe/Berlin
  disk: /dev/sda                   # WIPED by the ISO. Required for build, not for render
  admin:
    user: core                     # passwordless sudo on every target
    password: change-me            # hashed at render; or password_hash: $6$…
    ssh_keys: [~/.ssh/id_ed25519.pub]   # keys, or files holding keys
  network: { dhcp: true }          # or dhcp: false + address/gateway/dns, or an iprange
  updates: { days: [Sun], time: "03:30", length_minutes: 90 }
  tls: { auto: true, ca_dir: secrets/ca }   # the fleet CA; import kiwiCA.pem on your devices
  ucore: { image: ghcr.io/ublue-os/ucore:stable }
  debian: { release: trixie, partitioning: lvm }

hosts:
  gate:                            # the master, on Debian
    role: master
    target: debian
    master:                        # the role's block: stack settings, then one block per module
      vpn-client: { vpn_provider: mullvad, wireguard_private_key: …, wireguard_addresses: 10.66.1.2/32 }
      vpn-server: { wg_host: vpn.example.org, wg_password: … }
      dns: { pihole_password: … }
  m1:                              # a gateway node
    role: node-gw
    node-gw:
      vpn_ip: 10.8.0.6
      pub_iface: eth0
      vpn-client: { wireguard_config: secrets/m1.kiwi.conf }   # the client config wg-easy issued
      dns: { pihole_password: … }
      downloader: { download_dir: /mnt/data/downloads, transmission_password: … }
      sftp: { sftp_password: … }
  sh3:                             # a cloud node
    role: node-cloud
    ucore: { image: ghcr.io/ublue-os/ucore-hci:stable }
    node-cloud:
      vpn_ip: 10.8.0.25
      vpn-client: { wireguard_config: secrets/sh3.kiwi.conf }
      cloud: { nextcloud_datadir: /mnt/nvme_2tb/docker/knnc-data, memory_limit: 8192M }
```

Hosts override defaults key by key; the lists `admin.ssh_keys`,
`debian.packages` and the `coreos.*` lists *add* to the defaults. File
settings are paths relative to the fleet file and their content is embedded
into the host's role script. `kiwi-server show fleet.yaml sh3` prints what a
host ends up with, secrets masked; `kiwi-server roles -v` lists every role and
module setting with its default. The full reference is the commented
[examples/fleet.yaml](examples/fleet.yaml).

A module role's block holds the **stack settings** at the top (`vpn_ip`,
`pub_iface`, `docker_dir`, `service_user`, `docker_subnet`, the daily VPN
restart and weekly update times) and **one block per module**. `modules:`
inside it replaces the preset's list — a cloud node that also downloads is
`modules: [vpn-client, reverse-proxy, cloud, vault, downloader]` plus the
downloader's settings.

Static addresses without typing them: `network: { dhcp: false, gateway: …,
iprange: 192.168.1.20-192.168.1.99 }` in the defaults hands each static host
the next free address in file order; a host's own `address:` is reserved first.

A node's `vpn_ip` is read from its WireGuard config's `Address` when it is
not set; when both are given and differ, `validate` warns. The stack's data
directories belong to `service_user` (the admin user unless set — uid 1000 on
a fresh install, matching the containers' PUID defaults); the rendered files
stay root's. On the machine: `sudo kiwi-stack start|stop|update|status|logs|
vpn-restart`; the VPN restart also restarts the containers that share the VPN
client's network namespace (Transmission, JDownloader).

DNS is one chain for the whole network: VPN clients ask the master's Pi-hole,
a gateway node's Pi-hole serves its LAN and asks the master's Pi-hole first
(Quad9 only while the master is unreachable), names under the fleet's domain
go to the master and never leave it, and every host carries the fleet's names
in its hosts file plus a route into the mesh through its VPN client.

Migrating a machine that ran a v1 stack in place: set `docker_subnet` to what
it used and rename its data directories to the module names (`kmvpn-server` →
`km-vpn-server`, `knvault` → `kn-vault`, …) before the first start, so wg-easy
keeps its peers and the services their data. Mesh SSH to a node is one DNAT
rule: `vpn-client: { extra_dnat_rules: ["2222/tcp:172.128.0.1:22"] }` (the
docker gateway is the host).

## What you get

`output/<host>/`:

| file | what |
|---|---|
| `<host>.role.sh` | the role as one script: settings, embedded files (the rendered stack among them), the common library, the role body. Mode 0600 — it holds secrets |
| `<host>.stack/` | module roles: the rendered `docker-compose.yml`, the module configs (nginx.conf, post-rules.txt, gw.sh, start.sh, torrc, Pi-hole's records) and the host units — for review; the script carries a copy |
| `<host>.bu`, `<host>.ign` | CoreOS/uCore: Butane (fcos 1.6.0) and Ignition. Admin user, hostname, static network, time zone, update policy, `kiwi-role.service` with the script, uCore autorebase units |
| `<host>.preseed.cfg`, `<host>.kiwi-server/` | Debian: the preseed, and the files its late command copies in from `/cdrom/kiwi-server` — role script, `kiwi-role.service`, SSH keys, sudoers, sshd and update policy |
| `<host>.iso` | the unattended installer |
| `README.txt` | the same, for that host |

**CoreOS/uCore** media is the stock live ISO after `coreos-installer iso
customize --dest-device <disk> --dest-ignition <host>.ign`: no network is
needed for the install, Ignition is embedded for the installed system.
**Debian** media is the stock netinst with the preseed appended to the
installer's initrd (so it answers from the first question on), the boot menus
rewritten to start the unattended entry after a second, and the host's files
under `/kiwi-server`; `xorriso -boot_image any replay` keeps the original
BIOS/UEFI boot setup.

## First boot

`kiwi-role.service` is a oneshot that runs `/var/lib/kiwi-server/role.sh` as
root after the network is up and never again once
`/var/lib/kiwi-server/role.done` exists. A failed run is retried on the next
boot and shown on the console; the log is `/var/log/kiwi-server-role.log`.
On uCore the unit also waits for `/etc/ucore-autorebase/signed`, so the role
always runs on the final image. `sudo bash /var/lib/kiwi-server/role.sh
--force` runs it again by hand.

## Updates

| target | installs updates | reboots |
|---|---|---|
| coreos | Zincati, continuously | only inside the window (`strategy = "periodic"`, local time) |
| ucore | `rpm-ostreed-automatic.timer` stages them (the image's default) | `kiwi-staged-reboot.timer` reboots into a staged deployment in the window |
| debian | unattended-upgrades, daily | `kiwi-reboot-if-required.timer` reboots in the window when `/var/run/reboot-required` exists |

`updates.days: []` means any day; `updates.enabled: false` turns all of it off.

## Roles and modules

| role | what the machine becomes | modules |
|---|---|---|
| `bare` | the base system: admin user, SSH, updates | — |
| `master` | the kiwi-master: WireGuard entry point (wg-easy) whose default route is the VPN client (gluetun to Mullvad or any provider — the double hop), Pi-hole and Tor on the server's network stack, isolated-client subnet | vpn-client, vpn-server, dns, tor |
| `node-gw` | a kiwi-node in gateway mode: LAN DNS (Pi-hole + DHCP relay), policy routing that sends LAN clients through the VPN, Transmission and JDownloader fail-closed in the VPN client's namespace, nginx, Portainer, SFTP | vpn-client, dns, dhcp-relay, reverse-proxy, downloader, gateway, portainer, sftp |
| `node-cloud` | a kiwi-node in cloud mode: Nextcloud AIO and Vaultwarden behind nginx, reachable on the LAN and at the node's mesh address | vpn-client, reverse-proxy, cloud, vault, portainer |

The modules are kiwi-v2's (`modules/<name>/module.yaml` plus Jinja
templates) with the gaps filled: `start.sh` and `torrc` from the live master,
the gateway script with the live routing rules, nginx blocks announced by each
module and aggregated by the reverse proxy, Pi-hole 6 variables, and three
modules the live setups ran that v2 lacked (`portainer`, `sftp`,
`dhcp-relay`). Rendering happens on your machine; the host gets a compose file
and config files, unpacks them on first boot, and keeps a `kiwi-stack`
command (`start|stop|restart|update|status|logs|vpn-restart`) plus the
daily VPN restart and weekly update timers the v1 cron jobs did.

Everything cross-host comes from the fleet file: the master's `start.sh`
lets isolated clients reach every node, every Pi-hole serves every host and
service name at its mesh address, and the reverse proxies' certificates come
from one CA (`kiwi-server ca fleet.yaml` shows it). What stays manual: the
WireGuard client configs themselves, which the master's wg-easy issues — put
them under `secrets/` and point `vpn-client.wireguard_config` at them.

[docs/modules.md](docs/modules.md) is the module reference — the context a
template sees, every module.yaml key, how to add one. `kiwi-server modules
-v` lists what is there with every setting.

### Writing a role

A role is `roles/<name>/` with `role.yaml` and `apply.sh`. A module role
lists its modules and inherits the stack settings; its `apply.sh` is one
call:

```yaml
name: node-media
title: kiwi-node (media)
targets: [coreos, ucore, debian]
node_type: node
modules: [vpn-client, reverse-proxy, downloader, portainer]
settings_include: [common/stack]
module_defaults:
  downloader: { jdownloader: false }
```

```bash
ks_role_apply() { ks_stack_apply; }
```

A role without modules does its own thing in `ks_role_apply()` with the
library in [roles/common/lib.sh](roles/common/lib.sh): `ks_say/ks_warn/ks_die`,
`ks_apt`, `ks_file`, `ks_write`, `ks_subst`, `ks_ensure_user`,
`ks_ensure_docker`, `ks_git_clone`, `ks_unit`, `ks_firewall_open`. Settings
in `role.yaml` become `KS_ROLE_<KEY>`; the script runs as root with
`set -euo pipefail`, `KS_HOSTNAME`, `KS_ADMIN_USER`, `KS_TARGET` and `KS_OS`
(`debian` or `ostree`). The tests render and shellcheck every role in the
tree.

## The GUI

**Kiwi Server** in the app grid: open or create a fleet, pick Defaults or a
host on the left, edit on the right. Every host field shows what it inherits;
the role's stack settings and one group per module appear, built from
`role.yaml` and the modules' `module.yaml`; changing the `modules` list
changes the groups.
*Render* and *Build ISOs* run the CLI and stream its output; *Build* lists
every host with the disk it will wipe before it starts. The GUI writes plain
YAML — comments in a hand-written fleet file are not kept when it saves.

## Toolchain

| tool | used for |
|---|---|
| `butane` | Butane → Ignition (`--strict`) |
| `coreos-installer` | downloading and verifying the Fedora CoreOS ISO, `iso customize` |
| `xorriso`, `cpio`, `gzip`, `curl` | the Debian netinst rebuild and download |

`kiwi-server toolchain` shows which run natively and which would use the
container image; `--toolchain native` or `container` forces one. Inside the
container a tool sees the output and cache directories at the same paths, so
nothing else changes. On Fedora the three are `dnf install butane
coreos-installer xorriso`; on Bluefin/Silverblue the image is the way.

## Mind

- **The ISOs wipe the disk they are told to**, on boot, without a question.
  Label them. `disk:` is required for `build` for that reason.
- **The output holds secrets**: password hashes, WireGuard keys, TLS keys, the
  LUKS passphrase. Files are written 0600 and `output/` is in `.gitignore`;
  treat the ISOs the same way.
- A role script runs as root and does what the role says. Read
  `kiwi-server script fleet.yaml <host>` before trusting a role you did not
  write, the same way you would read an installer.

## Status

2.1.0 integrates the kiwi-v2 modules; see [CHANGELOG.md](CHANGELOG.md). The
generators are tested (every rendered script is shellchecked, every Butane
config validated with `butane --strict`, every preset's compose file checked
with `docker compose config`, the Debian ISO rebuild runs against a mock
netinst in CI). What still wants a real machine: the first boot of each
target end to end — the stacks are the live v1 setups rendered from
modules, verified file by file against them, not yet booted from here.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).
