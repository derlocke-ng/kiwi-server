# Roles and modules

A **role** says what a machine becomes. A module role is a set of
**modules**, one service each, rendered on your machine into a container
stack the host starts on first boot.

## Roles

| role | what the machine becomes | modules |
|---|---|---|
| `bare` | the base system: admin user, SSH, updates | — |
| `master` | the kiwi-master: WireGuard entry point (wg-easy) whose default route is the VPN client (gluetun to Mullvad or any provider — the double hop), Pi-hole and Tor on the server's network stack, isolated-client subnet; nginx for the admin pages; a nostr relay and a hub built on kiwi-framework if its `modules:` list them | vpn-client, vpn-server, dns, tor |
| `node` | a kiwi-node: any module set behind the VPN client. `preset: gateway` is the LAN gateway and download station (Pi-hole with DHCP, policy routing through the VPN, Transmission and JDownloader fail-closed in the VPN client's namespace, nginx, Portainer, SFTP); `preset: cloud` is Nextcloud and Vaultwarden behind nginx; `preset: minimal` is the VPN client and nginx to build on | per preset; `modules:` replaces the list |
| `node-gw`, `node-cloud` | the gateway and cloud presets as roles of their own, for fleet files that use them | as above |

The presets are the live kiwi-master, kiwi-node-gw and kiwi-cloud setups
taken apart into modules.

## Modules

The modules are kiwi-v2's (`modules/<name>/module.yaml` plus Jinja
templates) with the gaps filled:

- `start.sh` and `torrc` from the live master
- the gateway script with the live routing rules
- nginx blocks announced by each module and aggregated by the reverse proxy
- Pi-hole 6 variables
- three modules the live setups ran that v2 lacked: `portainer`, `sftp`,
  `dhcp-relay`

Rendering happens on your machine. The host gets a compose file and config
files, unpacks them on first boot, and keeps a `kiwi-stack` command
(`start|stop|restart|update|status|logs|vpn-restart`) plus the daily VPN
restart and weekly update timers the v1 cron jobs did.

Everything cross-host comes from the fleet file:

- the master's `start.sh` lets isolated clients reach every node
- every Pi-hole serves every host and service name at its mesh address
- the reverse proxies' certificates come from one CA (`kiwi-server ca
  fleet.yaml` shows it). Hosts that run the reverse proxy get a
  `*.<hostname>` certificate signed by it, and every machine built trusts it.
- the WireGuard client configs come from the master's wg-easy:
  `kiwi-server enroll <host>` fetches one into `secrets/<hostname>.conf`,
  where the vpn-client finds it without a line in the fleet file (or point
  `vpn-client.wireguard_config` at any file)

[Modules](modules.md) is the module reference: the context a template sees,
every module.yaml key, how to add one. `kiwi-server modules -v` lists what is
there with every setting.

## A public hub

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

That publishes 80 and 443. The public names get Let's Encrypt certificates,
which a timer renews; `acme_staging: true` tries it out on the test service
first. Every other site on the master, the admin pages among them, keeps
answering the mesh only. Anyone may read the relay; the mesh may post, and
from the internet only the keys in `write_pubkeys`.

## A cache for the LAN

Add `cache` to one node's modules and the machines on its LAN fetch container
images and Debian packages from it instead of the internet, by themselves;
with the cache off they go to the source. `cache: { data_dir: /mnt/data/cache }`
puts it on a big disk. Design: [Local first](design/local-first.md).

## Writing a role

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
library in [roles/common/lib.sh](https://github.com/derlocke-ng/kiwi-server/blob/main/roles/common/lib.sh):
`ks_say/ks_warn/ks_die`, `ks_apt`, `ks_file`, `ks_write`, `ks_subst`,
`ks_ensure_user`, `ks_ensure_docker`, `ks_git_clone`, `ks_unit`,
`ks_firewall_open`.

Settings in `role.yaml` become `KS_ROLE_<KEY>`. The script runs as root with
`set -euo pipefail`, `KS_HOSTNAME`, `KS_ADMIN_USER`, `KS_TARGET` and `KS_OS`
(`debian` or `ostree`). The tests render and shellcheck every role in the
tree.
