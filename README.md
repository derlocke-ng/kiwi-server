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
- **Roles** — `bare`, `node-cloud` (the
  [kiwi-cloud](https://github.com/derlocke-ng/kiwi-cloud) stack: Nextcloud
  AIO, Vaultwarden, Portainer, nginx behind a gluetun WireGuard client) and
  `master` (wg-easy + Pi-hole). A role is a directory; drop a new one in and
  the CLI, the validation and the GUI form pick it up.
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

Needs `python3` with PyYAML and `git` (all in the Silverblue/Bluefin base
image). Building ISOs needs `butane`, `coreos-installer` and `xorriso`; on a
desktop that does not have them:

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
  role: bare                       # bare | node-cloud | master
  domain: kiwi                     # sh3 becomes sh3.kiwi
  timezone: Europe/Berlin
  disk: /dev/sda                   # WIPED by the ISO. Required for build, not for render
  admin:
    user: core                     # passwordless sudo on every target
    password: change-me            # hashed at render; or password_hash: $6$…
    ssh_keys: [~/.ssh/id_ed25519.pub]   # keys, or files holding keys
  network: { dhcp: true }          # or dhcp: false + address/gateway/dns, or an iprange
  updates: { days: [Sun], time: "03:30", length_minutes: 90 }
  ucore: { image: ghcr.io/ublue-os/ucore:stable }
  debian: { release: trixie, partitioning: lvm }

hosts:
  sh3:
    role: node-cloud
    ucore: { image: ghcr.io/ublue-os/ucore-hci:stable }
    node-cloud:                    # the role's settings, under the role's name
      vpn_config: secrets/sh3.kiwi.conf
      vpn_ip: 10.8.0.25
  gate:
    role: master
    target: debian
    master: { wg_host: vpn.example.org, wg_admin_password: …, pihole_password: … }
```

Hosts override defaults key by key; the lists `admin.ssh_keys`,
`debian.packages` and the `coreos.*` lists *add* to the defaults. File
settings are paths relative to the fleet file and their content is embedded
into the host's role script. `kiwi-server show fleet.yaml sh3` prints what a
host ends up with, secrets masked. The full reference is the commented
[examples/fleet.yaml](examples/fleet.yaml); `kiwi-server roles -v` lists every
role setting.

Static addresses without typing them: `network: { dhcp: false, gateway: …,
iprange: 192.168.1.20-192.168.1.99 }` in the defaults hands each static host
the next free address in file order; a host's own `address:` is reserved first.

## What you get

`output/<host>/`:

| file | what |
|---|---|
| `<host>.role.sh` | the role as one script: settings, embedded files, the common library, the role body. Mode 0600 — it holds secrets |
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

## Roles

| role | what the machine becomes | status |
|---|---|---|
| `bare` | the base system: admin user, SSH, updates | stable |
| `node-cloud` | a kiwi-node in cloud mode: clones kiwi-cloud, writes its `.env`, drops the WireGuard config and TLS certificate in, renders nginx.conf and the gluetun rules, starts the stack through `kiwi-node.sh` as `kiwi-node.service` | wip — follows kiwi-cloud |
| `master` | a kiwi-master: wg-easy (unattended setup through `INIT_*`), Pi-hole as the VPN's DNS, optional Portainer; only the WireGuard port is public | wip — a baseline until the modularized kiwi-master exists |

### Writing one

A role is `roles/<name>/` with two files. `role.yaml` says what it is and
what it asks for:

```yaml
name: my-role
title: My role
description: One line.
targets: [coreos, ucore, debian]
settings:
  - key: endpoint          # becomes KS_ROLE_ENDPOINT in the script
    required: true
  - key: wg_conf
    type: file             # embedded; ks_file wg_conf /etc/wireguard/wg0.conf 0600
  - key: extra
    type: map              # declare -A KS_ROLE_EXTRA
```

Types: `string` (default), `text`, `int`, `bool`, `enum` (+ `options`),
`file`, `list`, `map`, `secret`. `group`, `label`, `help`, `default`,
`placeholder` and `targets` shape the form and the docs. `apply.sh` defines
one function:

```bash
ks_role_apply() {
    ks_ensure_docker                  # Debian: Docker CE; uCore: enable the socket
    ks_git_clone "$KS_ROLE_REPO" main /opt/thing
    ks_file wg_conf /etc/wireguard/wg0.conf 0600
    ks_unit thing.service <<'UNIT'
    ...
UNIT
    systemctl enable --now thing.service
}
```

It runs as root with `set -euo pipefail`, `KS_HOSTNAME`, `KS_ADMIN_USER`,
`KS_TARGET`, `KS_OS` (`debian` or `ostree`) and the library in
[roles/common/lib.sh](roles/common/lib.sh): `ks_say/ks_warn/ks_die`,
`ks_apt`, `ks_file`, `ks_write`, `ks_subst`, `ks_ensure_user`,
`ks_ensure_docker`, `ks_git_clone`, `ks_unit`, `ks_firewall_open`,
`ks_self_signed_cert`. Render a host with the role and
`shellcheck` the result; the tests do that for every role in the tree.

## The GUI

**Kiwi Server** in the app grid: open or create a fleet, pick Defaults or a
host on the left, edit on the right. Every host field shows what it inherits;
the role's settings appear as their own groups, built from `role.yaml`.
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

2.0.0 is a rewrite; see [CHANGELOG.md](CHANGELOG.md). The generators are
tested (every rendered script is shellchecked, every Butane config validated
with `butane --strict`, the Debian ISO rebuild runs against a mock netinst in
CI). What still wants a real machine: the first boot of each target end to
end, and the Debian netinst rebuild against a current 13.x image. The
`node-cloud` and `master` roles follow kiwi-cloud as it is today and will be
replaced by the modularized kiwi-node / kiwi-master when they land — that is
what the role directories are for.

## License

MIT — see [LICENSE](LICENSE).
