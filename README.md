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
  through a commercial VPN, Pi-hole, Tor, nginx for the admin pages, and a nostr relay and a hub built on
  kiwi-framework if its `modules:` list them) and
  `node` (any module set behind the VPN client: `preset: gateway` is the LAN
  gateway and download station, `preset: cloud` is Nextcloud and Vaultwarden,
  `preset: minimal` is a start, `modules:` is your own list). The presets are
  the live kiwi-master, kiwi-node-gw and kiwi-cloud setups taken apart into
  **modules** — the kiwi-v2 module system (`modules/`), rendered on your
  machine into a container stack the host starts on first boot.
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

Through [kiwi](https://github.com/derlocke-ng/kiwi-updater), with the Kiwi
Network catalog registered:

```bash
kiwi catalog add https://github.com/derlocke-ng/kiwi-catalog.git   # once per machine
kiwi install kiwi-server
```

kiwi follows the latest version tag (`v2.6.0`), updates it in the background
and shows the app with its icon in `kiwi-gui`. `kiwi info kiwi-server` and
`kiwi diff kiwi-server` show what an install or update would run.

Or straight from the checkout — it is the same installer kiwi runs:

```bash
git clone https://github.com/derlocke-ng/kiwi-server.git && cd kiwi-server
./install.sh install            # ~/.local/bin/kiwi-server, kiwi-server-gui, the library
kiwi-server doctor              # python, pyyaml, build tools, container runtime
```

Needs `python3` with PyYAML and Jinja2 (`pip install --user jinja2` if the
image lacks it), `openssl` and `ssh` (`apply`, `status`, `enroll` and the
fleet backup reach the machines over it); the GUI needs python3-gobject, GTK 4
and libadwaita. Building ISOs needs `butane`,
`coreos-installer` and `xorriso`; on a desktop that does not have them:

```bash
kiwi-server toolchain build     # one image with all three, used automatically
```

## Quick start

```bash
kiwi-server init fleet.yaml     # asks: domain, your key, the master, the machines at home
kiwi-server list fleet.yaml     # every host, where it stands and what comes next
kiwi-server validate fleet.yaml
kiwi-server render fleet.yaml   # scripts + configs into ./output/<host>/
kiwi-server build fleet.yaml    # + the ISOs (base images cached in ~/.cache/kiwi-server)
sudo dd if=output/sh3/sh3.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

`init` asks a few questions in the terminal (Enter takes the suggestion it
found on this computer: time zone, keyboard, the router's network) and writes a
fleet that waits only for its master; `init --example` writes the commented
example instead. The GUI's *New fleet* asks the same questions, each with a (?)
for what it means. New machines are named after fruits, masters after fruit
dishes: `kiwi-server add fleet.yaml node` adds the next free fruit (`add
fleet.yaml master`, or `add fleet.yaml node mybox --preset gateway` for a name
of your own), and `kiwi-server names` prints the suggestions.

Boot the machine from it. CoreOS and Debian install and then **power off**
(`install.on_done: poweroff`, the default), so you remove the stick or the
provider's ISO before the machine ever boots again — media left first in the
boot order would reinstall on every boot; `install: { on_done: reboot }` goes
straight into the installed system instead. Powered on again, the machine
sets up its base system on first boot (hostname, network, the admin user, the
update policy); uCore reboots twice more first (unsigned rebase, then signed).
Watch it with `journalctl -u kiwi-role -f`; `/var/lib/kiwi-server/role.done`
appears when it is finished.

The ISO carries none of the stack's secrets. The role and its stack come
afterwards, over SSH:

```bash
kiwi-server apply fleet.yaml sh3 --ssh core@<address>
```

A node's ISO can be built before `enroll` wrote its WireGuard config; `apply`
checks the role's settings when it brings them.

For a machine that cannot be reached over SSH there are **offline media**:
`kiwi-server build fleet.yaml sh3 --offline` puts the whole role and its
secrets on the ISO, and `kiwi-server script fleet.yaml sh3 --offline > sh3.sh`
writes the same thing as a script to `sudo bash` on any installed Debian /
CoreOS / uCore machine. Whoever holds one holds those secrets: keep it off a
provider's ISO store and delete it once the machine is installed.

A server at netcup needs no trip to the web panel. `kiwi-server vps` drives
netcup's API, and `vps install` wipes the server, so it asks first (`--yes`
for a script):

```bash
kiwi-server vps login fleet.yaml             # prints a page to sign in on; once
kiwi-server vps list fleet.yaml              # your servers
kiwi-server vps probe fleet.yaml crumble     # state, addresses, what disk: has to say
kiwi-server build fleet.yaml crumble
kiwi-server vps install fleet.yaml crumble   # upload, boot it, wait, detach, boot the disk
```

A master is found by its `wg_host`; any other host says `vps: { server: <id,
name or nickname> }`. `vps install` refuses an ISO that carries the role's
secrets, and one built for a different `disk:` than the server really has. It
removes the ISO from netcup's store when it is done, and forgets the machine's
old SSH host keys.

To find out what a path really carries, connect the node or device and measure:

```bash
kiwi-server mtu fleet.yaml m1 --ssh core@<address>          # a node
kiwi-server mtu fleet.yaml phone --ssh core@<master>        # a device from secrets/devices/
```

It sends don't-fragment pings through the tunnel and reports the largest size
that arrives whole. If a device's path is small, change that device's own
config and leave everyone else alone. If a node's is, `wg_mtu` has to come down
on it and its master; `--write` puts that in the fleet file. A packet split on
the way out and put together by the far end cannot be seen from here.

A hub belongs on a master. As a private hub it needs only the two modules. To
make it public, give it public names, point their DNS records at the master
(DNS only, if the provider proxies), and open the proxy:

```yaml
master:
  modules: [vpn-client, vpn-server, dns, tor, reverse-proxy, nostr-relay, hub]
  reverse-proxy: { public: true, acme_email: you@example.org }
  hub: { hub_domain: hub.example.org }
  nostr-relay: { relay_domain: relay.example.org, write_pubkeys: [<64 hex characters>] }
```

That publishes 80 and 443. The public names get Let's Encrypt certificates, which
a timer renews; `acme_staging: true` tries it out on the test service first.
Every other site on the master, the admin pages among them, keeps answering
the mesh only. Anyone may read the relay; the mesh may post, and from the
internet only the keys in `write_pubkeys`.

## The fleet file

```yaml
defaults:                          # every host, unless it says otherwise
  target: ucore                    # coreos | ucore | debian
  role: bare                       # bare | master | node
  domain: home                     # sh3 becomes sh3.home
  timezone: Europe/Berlin
  disk: /dev/sda                   # WIPED by the ISO. Required for build, not for render
  admin:
    user: core                     # passwordless sudo on every target
    password_hash: $6$…            # the console login, from: openssl passwd -6
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
    role: node
    node:
      preset: gateway                # vpn-client, dns, dhcp-relay, reverse-proxy, downloader, gateway, portainer, sftp
      vpn_ip: 10.8.0.6
      pub_iface: eth0
      vpn-client: { wireguard_config: secrets/m1.home.conf }   # the client config wg-easy issued (optional:
                                                              # secrets/<hostname>.conf is found by itself)
      dns: { pihole_password: … }
      downloader: { download_dir: /mnt/data/downloads, transmission_password: … }
      sftp: { sftp_password: … }
  sh3:                             # a cloud node
    role: node
    ucore: { image: ghcr.io/ublue-os/ucore-hci:stable }
    node:
      preset: cloud                  # vpn-client, reverse-proxy, cloud, vault, portainer
      vpn_ip: 10.8.0.25              # optional too: the config's Address is the mesh address
                                     # (no vpn-client line: `kiwi-server enroll sh3` wrote secrets/sh3.home.conf)
      cloud: { nextcloud_datadir: /mnt/nvme_2tb/docker/knnc-data, memory_limit: 8192M }
```

Hosts override defaults key by key; the lists `admin.ssh_keys`,
`debian.packages` and the `coreos.*` lists *add* to the defaults. File
settings are paths relative to the fleet file and their content is embedded
into the host's role script. `kiwi-server show fleet.yaml sh3` prints what a
host ends up with, secrets masked; `kiwi-server roles -v` lists every role and
module setting with its default. The full reference is the commented
[examples/fleet.yaml](examples/fleet.yaml).

Containers run as **podman quadlets** by default: every service is a systemd
unit under `/etc/containers/systemd`, ordering and restarts come from
systemd, `podman auto-update` pulls new images, and the compose file stays
next to them as the source and for review. `runtime: docker` in the role
block keeps docker compose, as the v1 setups ran. The cloud preset runs
Nextcloud from the AIO project's own containers without the AIO master
container: no docker socket, podman-native, Collabora, Talk, Imaginary,
ClamAV, full-text search and the whiteboard as switches, and every database
and service secret generated from `secrets/seed` so a re-render never rotates
a password.

A module role's block holds the **stack settings** at the top (`preset`,
`vpn_ip`, `pub_iface`, `docker_dir`, `service_user`, `docker_subnet`, `wg_mtu`
and `bridge_mtu`, the daily VPN restart and weekly update times) and **one
block per module**. A node is any module set: `preset: gateway | cloud |
minimal` picks the usual shape, `modules:` replaces the list outright — a
cloud node that also downloads is `modules: [vpn-client, reverse-proxy, cloud,
vault, downloader]` plus the downloader's settings. The renderer refuses two
modules that publish the same host port. `node-gw` and `node-cloud` remain as
names for the first two presets. The master runs nginx inside its WireGuard
server's network namespace, so `wg.<hostname>` and `pihole.<hostname>` answer
on the mesh address only, for the client groups that may see them.

Static addresses without typing them: `network: { dhcp: false, gateway: …,
iprange: 192.168.1.20-192.168.1.99 }` in the defaults hands each static host
the next free address in file order; a host's own `address:` is reserved first.

A node's `vpn_ip` is read from its WireGuard config's `Address` when it is
not set; when both are given and differ, `validate` warns. The stack's data
directories belong to `service_user` (the admin user unless set — uid 1000 on
a fresh install, matching the containers' PUID defaults); the rendered files
stay root's. On the machine: `sudo kiwi-stack start|stop|update|status|verify|backup|snapshots|restore|logs|
vpn-restart`; the VPN restart also restarts the containers that share the VPN
client's network namespace (Transmission, JDownloader). By hand, never
recreate a container that shares another's network namespace (Pi-hole, Tor
and nginx on a master, the downloaders on a node) with `docker compose up
--no-deps`: compose then gives it the literal network mode
`service:km-vpn-server`, which docker refuses to start. `docker rm` it and run
`docker compose up -d` without `--no-deps`, or `kiwi-stack restart`.

Names are `service.hostname.domain`, and the domain is yours to choose:
`kiwi-server init` and the GUI ask when they create a fleet, and
`home` is the fallback. `validate` says when a choice is a bad one. `.home`,
`.corp` and `.mail` are strings ICANN will not delegate as public top-level
domains, `.internal` is reserved for private use, so a name under them can
never leak to a public registry or be registered by a stranger with a
browser-trusted certificate. `.kiwi`, `.dev` and every two-letter domain are
public, so they can. `.local` is mDNS and never reaches a DNS server on Apple
devices. `.lan` is what OpenWrt and many routers call their own LAN, so a
router then answers it itself instead of forwarding the fleet's names.
Routers join the mesh with `kiwi-server openwrt` or the steps in
[docs/routers.md](docs/routers.md).

The fleet directory is the one thing the nodes cannot rebuild: the fleet
file, `secrets/`, the CA. With `backup: { hosts: [m1, sh3],
passphrase_file: ~/.config/kiwi-server/backup.pass }` in the defaults, every
successful render or build sends it, encrypted, to those nodes
(`/var/lib/kiwi-server/backups`, the newest ten kept); `kiwi-server backup`
does it by hand or to a USB stick with `--local`. On a fresh machine,
`kiwi-server restore --from core@192.168.1.5 --into ~/kiwi` brings it back
over plain SSH, before there is any mesh. The archive is openssl AES-256 with
a passphrase-derived key, so `openssl enc -d -aes-256-cbc -md sha256 -pbkdf2
-iter 600000 -in FILE | tar xz` opens it without kiwi-server.

A disk can open itself at boot. The `tang` module on one node serves a *ring*, and
another machine's `unlock:` block binds its disk to it:

```yaml
    papaya: { node: { modules: [vpn-client, tang] } }          # serves the ring infra on port 7500
    lychee: { debian: { encrypt: true }, unlock: { rings: [infra], ssh: true } }
```

On a Debian machine `apply` does the binding; a CoreOS machine is bound by Ignition, so
build its ISO with the ring running. Its key stays in another keyslot, in
`secrets/luks/<host>.key`, so a ring that is gone costs a console and not the disk. A
machine that cannot reach the ring at boot, such as a master on the Internet, is bound
through `via: { infra: "http://127.0.0.1:7500" }` and opened with `kiwi-server unlock
<fleet> <host>`, which forwards the ring in over SSH in its initramfs (`--manual`
hands over the key instead, on Debian). `kiwi-server status` says when a machine is waiting
like this, which it knows by the host key its initramfs shows. A reinstalled machine shows a new
host key there: `unlock --manual` gives it the one-time key of its new ISO and nothing else,
and no ring goes to it before its first apply. The login key for all this, `secrets/unlock_ed25519`,
opens no running machine and can only run the unlock; on CoreOS `unlock.ssh_from` also says
where from. A machine behind NAT that nobody can reach at boot asks its master instead:
`vpn-server.unlock_relay: { "7500": "<mesh address>:7500" }` there, and
`via: { infra: "http://<master>:7500" }` on the machine. A machine cannot serve its own ring; the ring named `operator` is served from the machine
running kiwi-server while it is needed, for what no other machine can open. Encrypting a
CoreOS root takes about 4 GiB of memory at the first boot: on less, use `target: debian`. Back up the rings'
keys: the `backup` module keeps them.

A machine's own data is the job of two modules. `backup-server` on a node with
a big disk stores it; `backup` on a machine sends it there with restic,
encrypted, every night and on `sudo kiwi-stack backup`:

```yaml
    lychee: { node: { modules: [vpn-client, reverse-proxy, backup-server],
                      backup-server: { clients: [papaya.home], data_dir: /mnt/backups } } }
    papaya: { node: { modules: [vpn-client, cloud, backup],
                      backup: { servers: [https://backup.lychee.home] } } }
```

With two backup nodes each backs up to the other, and every other machine to both:
`servers: [https://backup.lychee.home, https://backup.papaya.home]` gives each server a
full copy of its own, and a restore takes the freshest. A rebuilt machine finds itself in
the backups at its first apply and restores every module by itself before it backs up
again (`backup.restore: hold` makes it wait for `kiwi-stack restore` instead).

`sudo kiwi-stack snapshots [vault]` lists what is stored and `sudo kiwi-stack restore
vault cloud` puts the newest back, stopping only those modules; `restore --at "yesterday
18:00" vault` takes the newest from that time or before, for a mistake found late (the time
is the machine's own unless it says otherwise, and the list shows UTC). A machine that was rebuilt
never backs up its empty self over its history; `sudo kiwi-stack backup --new` starts a
history of its own instead of restoring. The passwords
derive from the seed, so the seed brings the backups back. A machine can add to
its backups but not delete them, so one that is taken over cannot destroy them;
`kiwi-server prune <fleet>` removes old snapshots (seven days, four weeks and six
months are kept) from the operator's machine, which is where the keys are;
`kiwi-server prune <fleet> --every week` sets up a timer on that machine that does it, and
`kiwi-server status` says on each backup node when it was last pruned and how long its
free space lasts.

A database is paused while restic reads it, for ten minutes at most. A run that fails tries
again an hour later, three more times, and when the backup server is gone or full the run
stops rather than pause each module in turn; `kiwi-server status` says when the last backup
is late.

DNS is one chain for the whole network: VPN clients ask the master's Pi-hole,
a gateway node's Pi-hole serves its LAN and asks the master's Pi-hole first
(Quad9 only while the master is unreachable), names under the fleet's domain
go to the master and never leave it, and every host carries the fleet's names
in its hosts file plus a route into the mesh through its VPN client.

A node's Pi-hole and hosts file answer with the LAN address of the nodes on its own
LAN: a machine with a static address in the node's subnet. A client at home reaches
`cloud.papaya.home` across the switch and not through the master, and a device on the
mesh, which asks the master's Pi-hole, gets the mesh address wherever it is. A device
with its tunnel on at home therefore still goes through the master. Names of masters,
and of nodes on DHCP or another LAN, stay at their mesh addresses.

The master's Pi-hole also answers for Mullvad's SOCKS5 proxies — every
Mullvad server runs one, reachable through the master's Mullvad exit — as
`de-fra-wg-socks5-001.relays.mullvad.net` and `de-fra-001.mullvad.home`
(`dns.mullvad_socks`, on in the master preset). gluetun refuses these
private-range answers, so without the records the names would not resolve
reliably in the mesh. A host timer refreshes them every 6 hours from the
[mullvad-socks5](https://github.com/derlocke-ng/mullvad-socks5) list, which
also has FoxyProxy imports that use these names.

Migrating a machine that ran a v1 stack in place: set `docker_subnet` to what
it used and rename its data directories to the module names (`kmvpn-server` →
`km-vpn-server`, `knvault` → `kn-vault`, …) before the first start, so wg-easy
keeps its peers and the services their data. The default runtime is podman
now: a machine that still runs the v1 docker compose stack either keeps it
with `runtime: docker` in the role block, or has the old stack stopped before
the role runs (the role script does not stop docker containers). Mesh SSH to a node is one DNAT
rule: `vpn-client: { extra_dnat_rules: ["2222/tcp:172.128.0.1:22"] }` (the
docker gateway is the host).

## What you get

`output/<host>/`, which is in the secret store's tmpfs while the store is open (the rendered
files hold the fleet's secrets and are made again by every command, so they never touch the
disk); only the ISO, and beside a bare one a `.bare` mark, stays on disk in `output/<host>/`.
A render removes the plain copies an older release left there.

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

## Managing running machines

Once a machine runs, the fleet file stays the source: change a setting, add
a module, renew a certificate, then

```bash
kiwi-server apply fleet.yaml sh3            # re-render, send the role script over SSH, run it
kiwi-server apply fleet.yaml sh3 --no-run   # only put it at /var/lib/kiwi-server/role.sh
kiwi-server status fleet.yaml               # when each role was applied, uptime, kiwi-stack status
```

SSH goes to the admin user at the host's name with the fleet's own key when
there is one — an `admin.ssh_keys` entry like `secrets/admin_ed25519.pub`
whose private key lies next to it — and with your agent otherwise (nothing
asks for a password; the install gave the admin user passwordless sudo). `--ssh core@192.168.1.7` reaches a machine whose name does not resolve
yet. The script runs with `--force`, so the stack is rewritten and restarted
with whatever changed; the disk and the OS are not touched. The GUI has the
same two actions on the host page.

Joining the mesh is one command per client, through the master's wg-easy
API over an SSH tunnel to the master:

```bash
kiwi-server enroll fleet.yaml sh3                     # a fleet host: secrets/sh3.home.conf, picked up at the next render
kiwi-server enroll fleet.yaml phone --group devices --qr   # a device: secrets/devices/phone.conf, and the QR code
kiwi-server enroll fleet.yaml laptop --group devices --split   # only the mesh through the tunnel, its own internet otherwise
kiwi-server enroll fleet.yaml fritzbox --group routers        # for a router — see docs/routers.md
kiwi-server enroll fleet.yaml sh3 --existing          # the config of a client that exists, again
```

`--group` moves the client into that group's range (`vpn-server.groups`,
10.8.1.0/24 guests, 10.8.2.0/24 pentest, 10.8.3.0/24 devices, 10.8.4.0/24
routers by default), which is what the master's firewall rules go by;
`--address` sets one by hand. A fleet host's client is named after its
hostname, so the wg-easy page and the fleet agree. Nodes keep everything
in the tunnel; `--split` is for devices that should reach the network but
keep their own exit. `vpn-server.wg_password` is what the API logs in with.

## Roles and modules

| role | what the machine becomes | modules |
|---|---|---|
| `bare` | the base system: admin user, SSH, updates | — |
| `master` | the kiwi-master: WireGuard entry point (wg-easy) whose default route is the VPN client (gluetun to Mullvad or any provider — the double hop), Pi-hole and Tor on the server's network stack, isolated-client subnet | vpn-client, vpn-server, dns, tor |
| `node` | a kiwi-node: any module set behind the VPN client. `preset: gateway` is the LAN gateway and download station (Pi-hole with DHCP, policy routing through the VPN, Transmission and JDownloader fail-closed in the VPN client's namespace, nginx, Portainer, SFTP); `preset: cloud` is Nextcloud and Vaultwarden behind nginx; `preset: minimal` is the VPN client and nginx to build on | per preset; `modules:` replaces the list |
| `node-gw`, `node-cloud` | the gateway and cloud presets as roles of their own, for fleet files that use them | as above |

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
from one CA (`kiwi-server ca fleet.yaml` shows it). The WireGuard client
configs come from the master's wg-easy: `kiwi-server enroll <host>` fetches
one into `secrets/<hostname>.conf`, where the vpn-client finds it without a
line in the fleet file (or point `vpn-client.wireguard_config` at any file).

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

**Kiwi Server** in the app grid opens the fleet you had last. On the left the
overview, one row per host and the defaults; each host row says whether the
machine answers, how long it has been up and how many of its services run.

A new fleet starts with **New fleet** in the menu: a few pages of questions
(the fleet, logging in, the master, the machines at home, secrets), each with a
(?) that explains it, then the fleet with its next steps. The **overview** counts
machines, services and what needs a look, with a card per machine, and lists
what comes next for every machine not installed yet: enroll, build, install,
apply. *+* adds a host with the next free fruit (or fruit dish, for a master) as
its name. A **host** has two tabs. *Overview* is what the machine says about
itself: system, load, memory, disks, every service with its state, the tunnel,
and the web interfaces to open from the mesh. *Settings* is the form: every field
shows what it inherits; the role's stack settings and one group per module appear,
built from `role.yaml` and the modules' `module.yaml`; changing the `modules` list
changes the groups.

The machines are asked once a minute while the window has the focus, over SSH and
read-only, through `kiwi-server status --porcelain`, which is also where it is
decided what counts as a problem. A machine is reached at its name, or at another
address of it the fleet has already met, so `.home` names need not resolve on this
computer; `apply --ssh` once introduces a new one. A locked secret store shows a
banner with *Unlock*, and the corner of the sidebar locks it again.

*Apply*, *Build* and *Render* run the CLI and stream its output; *Build* lists
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
- **The secret store** keeps `secrets/` encrypted when you are not using it.
  `kiwi-server secrets init fleet.yaml` turns it into `secrets.age`, puts one key
  in the GNOME Keyring and shows you a recovery key once: save that one somewhere
  safe. After that `secrets unlock` opens it into memory for 8 hours (`--for`,
  `--identity FILE` for the recovery key), `secrets lock` closes it, and a command
  run while it is locked refuses and says so. It needs `age`
  (`brew install age`); `age` and `tar` alone can open it by hand. While it is
  open, anything running as you can read it.
- **The same fleet on another computer of yours**: copy or sync the fleet folder
  (`fleet.yaml`, `secrets.age`, `secrets.recipients`; `output/` is made again), run
  `kiwi-server secrets join fleet.yaml laptop` there, and the line it prints on a
  computer that can open the store; that one encrypts the store for both, and with
  the new `secrets.age` back on the laptop `secrets unlock` works there too.
  `secrets remove fleet.yaml laptop` takes a computer out again (a copy it kept
  still opens with its key: treat a lost computer's secrets as known). A computer
  never saves over a `secrets.age` another one saved since it opened it; it says so
  and keeps its changes in memory until you take the other copy.
- **Service passwords you leave out are generated** from `secrets/seed`, the
  same at every render, so the fleet file need not hold them.
  `kiwi-server secrets show fleet.yaml <host> [setting]` prints one. `validate`
  warns about those still written in the file. Taking one out on a running
  machine changes that password; Nextcloud's and Portainer's are only read at
  their first start.
- **Encrypted Debian disks** install with a one-time key: type the one in
  `secrets/luks/<host>.once` at the first boot, and the first `apply` replaces
  it before anything secret reaches the machine. From then on the disk asks for
  `secrets/luks/<host>.key`; losing that file locks the disk, so keep the fleet
  backed up. A master also gets a new volume key at that apply, which takes
  some minutes (`debian.reencrypt`).
- A role script runs as root and does what the role says. Read
  `kiwi-server script fleet.yaml <host> --offline` before trusting a role you did not
  write, the same way you would read an installer.
- **The machines' SSH host keys** are kept in `secrets/known_hosts`: the first
  contact accepts a new machine's key, a changed key (a reinstall) is refused
  until you drop the old line (`ssh-keygen -R <host> -f secrets/known_hosts`).
- **A failed role says so**: `/var/lib/kiwi-server/role.failed` names the
  step and the command, `kiwi-server status` shows it, and the marker that
  means "applied" is only written once every container of the stack runs.
- **The generated secrets follow `secrets/seed`**: it is made on first use and
  backed up with the fleet; a machine refuses an apply with a different seed
  (its databases keep the passwords they were created with).

## Status

2.7.0 adds install media without the stack (`build --bare`) and the design after the October talks; 2.6.0 split the renderer and the machine side out as kiwi-modules and kiwi-stack (nothing a stack renders changed); 2.5.0 fixed what the review of 2.4.0 found. See [CHANGELOG.md](CHANGELOG.md). The
generators are tested (every rendered script is shellchecked, every Butane
config validated with `butane --strict`, every preset's compose file checked
with `docker compose config`, the Debian ISO rebuild runs against a mock
netinst in CI). What still wants a real machine: the first boot of each
target end to end — the master and gateway stacks are the live v1 setups
rendered from modules, verified file by file against them; the cloud preset
left the AIO master container behind and follows AIO's manual install
instead — none of it booted from here yet —
and `apply`, `status` and `enroll` against a live master, which are tested
against fake SSH runners and a fake wg-easy here.

The renderer and the machine side are repositories of their own:
[kiwi-modules](https://github.com/derlocke-ng/kiwi-modules) renders one
machine's stack from a machine file without a fleet, and
[kiwi-stack](https://github.com/derlocke-ng/kiwi-stack) applies such a bundle
(`sudo kiwi-stack apply DIR`). This tree carries both as vendored copies
pinned by release in `VENDORED` (`tools/vendor.sh`, `--check` in CI); the
stacks they produce are this tree's, byte for byte (tests/golden).

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).

The icon is kiwi-server from [kiwi-icons](https://github.com/derlocke-ng/kiwi-icons).
