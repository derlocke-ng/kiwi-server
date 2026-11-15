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

## Game servers

Add `games` to a node's modules and list the servers, each with a name of its
own and its game:

```yaml
hosts:
  medlar:
    node:
      modules: [vpn-client, reverse-proxy, games]
      games:
        eula: true                 # Minecraft's licence: https://aka.ms/MinecraftEULA
        servers:
          survival: { game: minecraft, memory: 4G, name: Our world }
          doom: { game: zandronum }
          lab: { game: abiotic-factor, port: 7800, password: "..." }
```

The games it knows: `minecraft`, `minecraft-bedrock`, `enshrouded`,
`abiotic-factor`, `zandronum`, `valheim`, `factorio`, `satisfactory`,
`palworld`, `terraria`. `catalog:` in the same block takes more: an image, its
ports and where it keeps its worlds. Minecraft and Zandronum were run on a test
machine through the fleet (15 November 2026): Minecraft answered at the machine's
address at home and at its VPN address. The others are set up as their images
document it and are not tried.

- **Where players connect.** At home: the machine's address and the game's port
  (`medlar.home:25565`). From the VPN: the same name; give the group the port in
  "Who may reach what" (`medlar:25565`,
  [network.md](network.md#who-may-reach-what)). Nothing is opened to the
  Internet: a friend plays as a device on your VPN, in a group that reaches that
  one port and nothing else.
- **Two servers of games that share a port**: `port:` moves one of them. Its
  other ports move with it.
- **What is kept.** A server's worlds are in `kn-games/<server>` on the machine
  and in its backup. What a game downloads (Enshrouded, Abiotic Factor and
  Valheim fetch several GB at the first start) is in `kn-games-files/<server>`
  and is not backed up.
- **Zandronum** needs a game file: put `freedoom2.wad` (free, from
  freedoom.github.io) or your own `doom2.wad` into `kn-games/<server>/data`;
  `args:` is the server's command line.
- The big games need a machine for it: Enshrouded wants about 16 GB of memory,
  Abiotic Factor and Palworld about 8.

## Jabber

Add `xmpp` to a master's modules and your people have a Jabber (XMPP) server:
chats, group rooms, files, and message history that a new device shows. Any
XMPP app works: Conversations (Android), Monal (iPhone, Mac), Dino, Gajim.

```yaml
hosts:
  sorbet:
    master:
      modules: [vpn-client, vpn-server, dns, reverse-proxy, xmpp, onion]
      xmpp: { xmpp_domain: chat.example.org }      # the Internet's door
      onion: { services: { jabber: {} } }          # Tor's door
```

It is one service with up to three doors:

- **The VPN** (always): `jabber.<master>`, like `jabber.sorbet.home`. Without a
  public name that is also what comes after the @ (`anna@jabber.sorbet.home`),
  and the certificate is the fleet's.
- **The Internet**: `xmpp_domain`, a name that points at the master in DNS as a
  plain record (no proxy in front: Jabber is not web traffic), with
  `reverse-proxy: { public: true }`. That name then comes after the @
  (`anna@chat.example.org`), the certificate is Let's Encrypt's, people log in
  from anywhere on 5222 and write with people on other servers. An app on the
  VPN can keep `jabber.<master>` as the server it connects to.
- **Tor**: `onion: { services: { jabber: {} } }`; `kiwi-server onion` shows the
  address. An app connects to `<address>.onion`, port 5222, and the page is
  there too. Someone who cannot check a certificate can have an account
  `@<address>.onion`.

**Two apart.** `xmpp: { xmpp_domain: …, separate: true }` keeps the VPN's
service to itself (accounts `@jabber.<master>`, nobody outside reaches it) and
runs a second one for the public name. Their accounts are apart and the two
exchange no messages. The public one's hidden service is `xmpp`, not `jabber`.

**Accounts.** `kiwi-server logins` shows the admin's. Everyone else makes their
own: in the app ("create a new account" on the server), or on the page
`https://<name>/register`, each after a captcha. One new account per address in
ten minutes; an address that keeps typing wrong passwords is shut out for an
hour. `registration: false` leaves accounts to the admin (the app's server
commands, or `/admin` on the page).

Rooms are at `rooms.<name>`. A file may be up to `upload_mb` (100) MB.

Tried on the test master (15 November 2026): the admin logs in over the VPN,
the page shows a captcha and refuses a wrong answer, the app's registration
form carries one, and through Tor the page opens and a Jabber server answers
on 5222. Not tried: a public name, two apart, an app.

## Pastebin

Add `paste` to a machine's modules: a page to put a text or a file on and pass
its link along (PrivateBin). A paste is encrypted in the browser; the server
keeps what it cannot read, and the link carries the key.

- **On the VPN**: `https://paste.<machine>`.
- **For the Internet**, on a master: `paste: { paste_domain: paste.example.org }`
  with `reverse-proxy: { public: true }`.
- **For strangers without a public name**: `onion: { services: { paste: {} } }`,
  a hidden service ([network.md](network.md#hidden-services)).

`title`, `default_expire` (a week), `size_mb` (10) and `file_upload` are its
settings. The pastes are in the machine's backup.

## Payments (BTCPay Server)

Add `btcpay` to a node's modules and you take Bitcoin payments yourself: BTCPay
Server with its own Bitcoin node, invoices, and a point-of-sale page that works
as a small shop.

```yaml
hosts:
  papaya:
    node:
      modules: [vpn-client, reverse-proxy, btcpay, onion]
      btcpay: { data_dir: /mnt/data/btcpay }
      onion: { services: { pay: {} } }       # a hidden service, for strangers
```

- **The page** is `https://pay.<machine>` on the VPN; `kiwi-server logins` has the
  admin's login (a mail address and a password, made at the first start; nobody
  else can register). With `onion`, `kiwi-server onion` gives the address
  strangers open in Tor Browser ([network.md](network.md#hidden-services)).
- **The Bitcoin node** talks to the Bitcoin network through the VPN, not through
  your home's line, and accepts no connections. It checks the whole chain once,
  which takes days on a small machine, and keeps the newest `prune_mb` (5000 MB)
  of it; give it a disk with about 20 GB (`data_dir`). Until it is done BTCPay
  says that it is synchronizing.
- **Trying it first**: `btcpay: { network: regtest }` is a chain of one's own,
  ready in a minute, with coins worth nothing; `signet` and `testnet` are public
  test networks. Change `network` back and it starts on real Bitcoin, with a
  fresh database for that network.
- **The backup** holds the stores, invoices and wallet keys, not the chain.
- Tried on the test network in regtest (15 November 2026): the admin was made
  and logs in over the VPN, an invoice was paid and settled, a shop page opened
  without a login, and the hidden service opened through Tor. A new hidden
  service needs a few minutes before it is found. Not tried: mainnet's first
  sync. Lightning is not part of it.

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
