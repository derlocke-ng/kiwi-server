# Roles and modules

A machine's *role* says what it becomes. The roles `master` and `node` are
built from *modules*: a module is one service of a machine, such as the VPN
client, the WireGuard server, Pi-hole, the reverse proxy or Nextcloud.
kiwi-server renders a host's modules on your computer into a container
*stack* (a compose file, config files and systemd units), and the machine
starts it when the role is applied. This page lists the roles and modules and
shows how to write a role. The [module reference](modules.md) explains how a
module is built.

`kiwi-server roles -v` and `kiwi-server modules -v` list everything here with
every setting and its default.

## Roles

| Role | What the machine becomes | Modules |
|---|---|---|
| `bare` | The base system: the admin user, SSH and automatic updates. A clean machine to build on by hand. | none |
| `master` | The kiwi-master, the only machine reachable from the Internet. Its WireGuard server (wg-easy) is the entry point for every client and node. Its default route is the VPN client, which connects to Mullvad or another commercial VPN, so traffic leaves through a second tunnel (the *double hop*). Pi-hole, Tor and nginx share the WireGuard server's network. | vpn-client, vpn-server, dns, tor, reverse-proxy, fail2ban |
| `node` | A kiwi-node: any set of modules behind the VPN client. | per preset; `modules:` replaces the list |
| `node-gw`, `node-cloud` | The gateway and cloud presets as roles of their own, kept for fleet files that use them. | as their presets |

A node picks its usual shape with `preset:`:

| Preset | What it is | Modules |
|---|---|---|
| `gateway` | The LAN gateway and download station: Pi-hole with DHCP, policy routing of LAN clients through the VPN, Transmission and JDownloader inside the VPN client's network, nginx, Portainer and SFTP. | vpn-client, dns, dhcp-relay, reverse-proxy, downloader, gateway, portainer, sftp |
| `cloud` | Nextcloud and Vaultwarden behind nginx, plus Portainer. | vpn-client, reverse-proxy, cloud, vault, portainer |
| `minimal` | The VPN client and nginx, to build on. This is the default. | vpn-client, reverse-proxy |

`modules:` in the role block replaces the list. On a master, keep the
master's modules in the list when you add some, or they are left out.

The presets are the first Kiwi Network setups (the v1 master, gateway node
and cloud node) taken apart into modules. The roles run on `coreos`, `ucore`
and `debian`; `bare` and `master` also run on `coreos-ram`.

## Modules

| Module | Runs on | What it does |
|---|---|---|
| `vpn-client` | master, node | gluetun, the tunnel every node depends on: to the master with a WireGuard configuration, or on a master to a commercial VPN (its exit). |
| `vpn-server` | master | wg-easy, the entry point for every client and node. Its default route points at the VPN client for the double hop. |
| `dns` | master, node | Pi-hole 6, a resolver that blocks ads, for the mesh or the LAN. It forwards to the VPN client's DNS over TLS and answers for the fleet's own names. |
| `reverse-proxy` | master, node | nginx with TLS, with one server block for each service the other modules announce. Certificates come from the fleet CA unless you bring your own. |
| `tor` | master, node | A Tor SOCKS5 proxy for mesh clients, limited to a set of countries. |
| `fail2ban` | master, node | Bans addresses that keep failing to log in over SSH and slows down addresses that open SSH connections too fast; on a master with public sites, also scanners and bad requests. |
| `gateway` | node | Policy routing on the host: LAN clients that use this machine as their default gateway leave through the VPN client. |
| `dhcp-relay` | node | Relays the LAN's DHCP broadcasts to Pi-hole, so Pi-hole can be the LAN's DHCP server. |
| `downloader` | node | Transmission and JDownloader inside the VPN client's network, so they have no traffic without the tunnel. |
| `sftp` | node | An SFTP server on its own port, for fetching downloads from the LAN or the mesh. |
| `portainer` | master, node | Portainer CE, a web interface for the containers on the machine. |
| `cloud` | node | Nextcloud from the Nextcloud AIO project's containers, without the AIO master container. |
| `vault` | node | Vaultwarden, a password manager that works with the Bitwarden apps. |
| `immich` | node | Immich, for photos and videos from your phone. |
| `opencloud` | node | OpenCloud, for files and sharing. |
| `paperless` | node | Paperless-ngx, for scanned documents with text recognition and full-text search. |
| `backup` | master, node | Backs up the machine's data with restic, encrypted, to a backup server every night. |
| `backup-server` | node | Stores the backups of other machines: restic's REST server. |
| `cache` | node | Keeps container images and Debian packages for the machines on its LAN. |
| `tang` | node | Tang servers, which let the fleet's encrypted machines unlock their disks at boot. |
| `nostr-relay` | master | strfry, a nostr relay, which a hub built on kiwi-framework stores its events on. |
| `hub` | master | A hub built on kiwi-framework, such as Armory, as a container behind the reverse proxy. |

[Backups](backups.md), [Disk encryption](disk-encryption.md) and
[DNS and networks](network.md) explain the modules that work across machines.

### The cloud module

The `cloud` module runs Nextcloud from the AIO project's own containers but
without the AIO master container. It needs no docker socket and runs on
podman. Collabora, Talk, Imaginary, ClamAV, full-text search and the
whiteboard are switches. Every database and service password is generated
from the fleet's seed, so rendering again never changes a password.

## What the fleet adds

Everything that crosses machines comes from the fleet file:

- The master lets the client groups reach the nodes they may reach.
- Every Pi-hole answers every host and service name of the fleet.
- The reverse proxies' certificates come from one fleet CA
  (`kiwi-server ca fleet.yaml` shows it). Hosts that run the reverse proxy get
  a `*.<hostname>` certificate, and every machine the fleet builds trusts the
  CA. Import `kiwiCA.pem` on your own devices.
- The WireGuard client configurations come from the master's wg-easy.
  `kiwi-server enroll` fetches one into `secrets/<hostname>.conf`, where the
  VPN client finds it without a line in the fleet file.

## Write a role

A role is a folder `roles/<name>/` with `role.yaml` and `apply.sh`. A *module
role* lists its modules and takes the stack settings; its `apply.sh` is one
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

A role without modules does its own work in `ks_role_apply()`, with the
library in
[roles/common/lib.sh](https://github.com/derlocke-ng/kiwi-server/blob/main/roles/common/lib.sh):
`ks_say`, `ks_warn`, `ks_die`, `ks_apt`, `ks_file`, `ks_write`, `ks_subst`,
`ks_ensure_user`, `ks_ensure_docker`, `ks_git_clone`, `ks_unit`,
`ks_firewall_open` and others.

Settings in `role.yaml` become the variables `KS_ROLE_<KEY>`. The script runs
as root with `set -euo pipefail`, and it has `KS_HOSTNAME`, `KS_ADMIN_USER`,
`KS_TARGET` and `KS_OS` (`debian` or `ostree`). The tests render and
shellcheck every role in the tree.
