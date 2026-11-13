# The fleet file

The fleet file, usually `fleet.yaml`, describes every machine of your network.
kiwi-server builds the install media from it, and it stays the source for the
running machines: you change a setting here and apply it. This page explains
its structure. The commented
[examples/fleet.yaml](https://github.com/derlocke-ng/kiwi-server/blob/main/examples/fleet.yaml)
is the full reference, and `kiwi-server roles -v` lists every role and module
setting with its default.

## Defaults and hosts

A fleet file has two parts. `defaults:` applies to every host, and `hosts:`
has one entry per machine. A host overrides the defaults key by key.

```yaml
defaults:                          # every host, unless it says otherwise
  target: ucore                    # coreos | ucore | debian | coreos-ram (a master in RAM)
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
      vpn-server: { wg_host: vpn.example.org }
  m1:                              # a gateway node
    role: node
    node:
      preset: gateway              # vpn-client, dns, dhcp-relay, reverse-proxy, downloader, gateway, portainer, sftp
      vpn_ip: 10.8.0.6
      pub_iface: eth0
      downloader: { download_dir: /mnt/data/downloads }
  sh3:                             # a cloud node
    role: node
    ucore: { image: ghcr.io/ublue-os/ucore-hci:stable }
    node:
      preset: cloud                # vpn-client, reverse-proxy, cloud, vault, portainer
      cloud: { nextcloud_datadir: /mnt/nvme_2tb/docker/knnc-data, memory_limit: 8192M }
```

Without a `defaults:` value, a host gets `target: ucore`, `role: bare`,
`domain: home` and `timezone: UTC`.

Some lists *add* to the defaults instead of replacing them: `admin.ssh_keys`,
`debian.packages`, `debian.kernel_arguments` and the `coreos.*` lists
(`kernel_arguments`, `services`, `files`, `directories`, `units`).

Settings that name a file, such as a WireGuard configuration or a
certificate, are paths relative to the fleet file. Their content is embedded
into the host's role script.

`kiwi-server show fleet.yaml sh3` prints what a host ends up with, with the
secrets masked.

## Targets

The *target* is the system the ISO installs:

| Target | System | Updates |
|---|---|---|
| `coreos` | Fedora CoreOS, stable stream | Zincati |
| `ucore` | Fedora CoreOS that rebases itself onto a `ghcr.io/ublue-os/ucore*` image | rpm-ostree, staged |
| `debian` | Debian stable (trixie) from the netinst image, with a preseed file | unattended-upgrades |
| `coreos-ram` | Fedora CoreOS live, running from RAM, for masters only; experimental | a new ISO |

`kiwi-server targets` prints the same list. [First boot and
updates](updates.md) explains the update windows and the master in RAM.

## The role block

A host's role is set by `role:`. For the module roles, `master` and `node`,
the role's settings live in a block named after the role (`master:` or
`node:`). [Roles and modules](roles-and-modules.md) describes the roles.

The block holds the **stack settings** at the top and **one block per
module** below them:

- `preset` picks a node's usual module set: `gateway`, `cloud` or `minimal`.
- `modules:` replaces the preset's list outright. A cloud node that also
  downloads is `modules: [vpn-client, reverse-proxy, cloud, vault, downloader]`
  plus the downloader's settings.
- `vpn_ip` is the host's mesh address. When it is not set, it is read from the
  `Address` line of the host's WireGuard configuration; when both are set and
  differ, `validate` warns.
- `pub_iface` is the host's LAN interface.
- `docker_dir` is where the stack keeps its data, and `service_user` owns it.
  The service user is the admin user unless you set it, which is uid 1000 on
  a fresh install and matches the containers' default user IDs. The rendered
  files themselves belong to root.
- `docker_subnet` is the stack's bridge network: `172.64.0.0/24` on a master
  and `172.128.0.0/24` on a node unless set.
- `wg_mtu` (1376) and `bridge_mtu` (1500) are the tunnels' MTU and the stack
  network's MTU. [Manage running machines](managing.md#measure-a-tunnels-mtu)
  shows how to measure the right value.
- `runtime` is `podman` (the default) or `docker`; see below.
- `daily_vpn_restart` and `weekly_update` set when the VPN client restarts and
  when the images are updated.

kiwi-server refuses two modules that publish the same host port.

## Containers: podman or docker

By default the services run as **podman quadlets**. Each service is a systemd
unit under `/etc/containers/systemd`, systemd handles the order and the
restarts, and `podman auto-update` pulls new images. The compose file stays
next to the units, as their source and for you to review.

`runtime: docker` in the role block runs the stack with docker compose, as the
first Kiwi Network setups did. [Migrate a v1 machine](migrating-from-v1.md)
explains when you need it.

## Names and the domain

Every host and service has a name of the form `service.hostname.domain`, for
example `cloud.papaya.home`. The domain is yours to choose: `kiwi-server init`
and the app ask for it, and `home` is the default. `validate` tells you when a
choice is a bad one:

- `.home`, `.corp` and `.mail` will never be delegated by ICANN as public
  top-level domains, and `.internal` is reserved for private use. A name under
  them cannot leak to a public registry, and nobody can register it with a
  certificate that browsers trust.
- `.kiwi`, `.dev` and every two-letter domain are public, so both can happen.
- `.local` is refused. It is used by mDNS, and Apple devices and
  systemd-resolved never ask a DNS server for it.
- `.lan` is what OpenWrt and many routers use for their own LAN. Such a router
  answers `.lan` names itself and does not forward the fleet's names.

The fleet's machines get the fleet's names in their hosts files, and every
Pi-hole in the fleet resolves them. [DNS and networks](network.md) explains
how.

## Static addresses

A host with `network: { dhcp: false, address: …, gateway: …, dns: [...] }` gets
a static address. To avoid typing each one, set a range in the defaults:

```yaml
defaults:
  network: { dhcp: false, gateway: 192.168.1.1, iprange: 192.168.1.20-192.168.1.99 }
```

Each host with a static address then gets the next free address in the range,
in the order of the file. A host's own `address:` is reserved first.

## Generated passwords

Service passwords that you leave out of the fleet file are generated from
`secrets/seed`, the same at every render, so the fleet file does not need to
hold them. `kiwi-server secrets show fleet.yaml <host> [setting]` prints one,
and `validate` warns about passwords that are still written in the file.
[Security](security.md#generated-passwords-and-the-seed) has the details.

## Editing the file in the app

The app writes plain YAML. Comments in a fleet file that you wrote by hand are
lost when the app saves it.
