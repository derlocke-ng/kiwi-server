# The fleet file

One `fleet.yaml` describes every machine. The full reference is the commented
[examples/fleet.yaml](https://github.com/derlocke-ng/kiwi-server/blob/main/examples/fleet.yaml).

## An example

```yaml
defaults:                          # every host, unless it says otherwise
  target: ucore                    # coreos | ucore | debian | armbian | coreos-ram (a master in RAM)
  role: bare                       # bare | master | node
  domain: home                     # sh3 becomes sh3.home
  names: machine                   # machine: paste.sh3.home | service: paste.home (network.md, Names by service)
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
      preset: gateway                # vpn-client, dns, dhcp-relay, reverse-proxy, gateway, portainer
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

The console password lives in the fleet file only as its hash. A password typed on the
control page or in the app is kept that way; an older fleet file with `password:` in the clear
moves it with

```bash
kiwi-server secrets admin-password ~/kiwi/fleet.yaml
```

which leaves `password_hash` in the fleet file and the password itself in the secret store,
where `kiwi-server logins` shows it as *The machine's screen*.

The modules' passwords go the same way. A password written for a machine's module
(Pi-hole's, Portainer's, the VPN server's, a VPN provider's key, a router's password) moves
into the secret store with

```bash
kiwi-server secrets keep ~/kiwi/fleet.yaml
```

It lands in `secrets/pins.json` under `hostname/module/key` and leaves the fleet file. Every
machine keeps the same password: an apply afterwards changes nothing on them.
`kiwi-server secrets show` and `logins` still show each one. A password typed on the
control page or in the app goes there by itself when the settings are saved. A password
written in the defaults, for every machine of a role, is not moved: give each machine its
own, or leave it out to have one generated, which changes it.

To take a kept password out again, so that the machine gets a generated one with its next
apply: the page's *clear* box, *Forget* in the app, or

```bash
kiwi-server secrets forget ~/kiwi/fleet.yaml papaya portainer.admin_password
```

A password that a database or an account keeps no longer fits after that: change it there too.

## How settings combine

- Hosts override defaults key by key.
- The lists `admin.ssh_keys`, `debian.packages` and the `coreos.*` lists *add*
  to the defaults.
- File settings are paths relative to the fleet file. Their content is
  embedded into the host's role script.
- `kiwi-server show fleet.yaml sh3` prints what a host ends up with, secrets
  masked.
- `kiwi-server roles -v` lists every role and module setting with its
  default.

## The role block

A module role's block holds the **stack settings** at the top and **one block
per module**. The stack settings are `preset`, `vpn_ip`, `pub_iface`,
`docker_dir`, `service_user`, `docker_subnet`, `wg_mtu` and `bridge_mtu`, and
the daily VPN restart and weekly update times.

A node is any module set:

- `preset: gateway | cloud | minimal` picks the usual shape.
- `modules:` replaces the list outright. A cloud node that also downloads is
  `modules: [vpn-client, reverse-proxy, cloud, vault, downloader]` plus the
  downloader's settings.
- The renderer refuses two modules that publish the same host port.
- `node-gw` and `node-cloud` remain as names for the first two presets.

The master runs nginx inside its WireGuard server's network namespace, so
`wg.<hostname>` and `pihole.<hostname>` answer on the mesh address only, for
the client groups that may see them. Roles and modules in detail:
[Roles and modules](roles.md).

## Containers

Containers run as **podman quadlets** by default: every service is a systemd
unit under `/etc/containers/systemd`, ordering and restarts come from
systemd, `podman auto-update` pulls new images, and the compose file stays
next to them as the source and for review. `runtime: docker` in the role
block keeps docker compose, as the v1 setups ran.

The cloud preset runs Nextcloud from the AIO project's own containers without
the AIO master container: no docker socket, podman-native. Collabora, Talk,
Imaginary, ClamAV, full-text search and the whiteboard are switches. Every
database and service secret is generated from `secrets/seed`, so a re-render
never rotates a password.

## Addresses

A machine on tagged VLANs (a managed switch, a trunk to the machine) gets an
interface per VLAN, with no default route on it (docs/design/lans.md):

```yaml
network:
  address: 192.168.1.30/24
  vlans:
    - { id: 20, address: 192.168.20.5/24, name: iot }     # name: a group of the home network
    - { id: 30, address: 192.168.30.5/24, interface: eth1 }
```

A module answers at one of them only with its `listen` setting: its ports are bound
to that address (`node: { paste: { listen: 192.168.20.5 } }`).

Static addresses without typing them: `network: { dhcp: false, gateway: …,
iprange: 192.168.1.20-192.168.1.99 }` in the defaults hands each static host
the next free address in file order; a host's own `address:` is reserved
first.

A node's `vpn_ip` is read from its WireGuard config's `Address` when it is
not set; when both are given and differ, `validate` warns.

## Names and the domain

Names are `service.hostname.domain`, and the domain is yours to choose:
`kiwi-server init` and the GUI ask when they create a fleet, and `home` is the
fallback. `validate` says when a choice is a bad one:

- `.home`, `.corp` and `.mail` are strings ICANN will not delegate as public
  top-level domains, and `.internal` is reserved for private use. A name under
  them can never leak to a public registry or be registered by a stranger
  with a browser-trusted certificate.
- `.kiwi`, `.dev` and every two-letter domain are public, so they can.
- `.local` is mDNS and never reaches a DNS server on Apple devices.
- `.lan` is what OpenWrt and many routers call their own LAN, so a router
  then answers it itself instead of forwarding the fleet's names.

## On the machine

The stack's data directories belong to `service_user` (the admin user unless
set: uid 1000 on a fresh install, matching the containers' PUID defaults);
the rendered files stay root's.

```bash
sudo kiwi-stack start|stop|update|status|verify|backup|snapshots|restore|logs|vpn-restart
```

The VPN restart also restarts the containers that share the VPN client's
network namespace (Transmission, JDownloader).

By hand, never recreate a container that shares another's network namespace
(Pi-hole, Tor and nginx on a master, the downloaders on a node) with
`docker compose up --no-deps`: compose then gives it the literal network mode
`service:km-vpn-server`, which docker refuses to start. `docker rm` it and run
`docker compose up -d` without `--no-deps`, or `kiwi-stack restart`.

Mesh SSH to a node is one DNAT rule: `vpn-client: { extra_dnat_rules:
["2222/tcp:172.128.0.1:22"] }` (the docker gateway is the host).

## Migrating a v1 machine

To migrate a machine that ran a v1 stack in place:

- Set `docker_subnet` to what it used.
- Rename its data directories to the module names (`kmvpn-server` →
  `km-vpn-server`, `knvault` → `kn-vault`, …) before the first start, so
  wg-easy keeps its peers and the services their data.
- The default runtime is podman now. A machine that still runs the v1 docker
  compose stack either keeps it with `runtime: docker` in the role block, or
  has the old stack stopped before the role runs. The role script does not
  stop docker containers.
