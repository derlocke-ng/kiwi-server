# Roles and modules

A **role** says what a machine becomes. A module role is a set of
**modules**, one service each, rendered on your machine into a container
stack the host starts on first boot.

**The master routes; your data lives on the nodes.** The master is usually a rented server you
need not trust: by default it runs only what moves traffic (the VPN, the names, the Tor proxy,
a proxy that hands public names on to nodes without reading them). Every service that keeps
data runs on a node and is reached over the VPN, or from the Internet through the master
([A node's service, open to the Internet](#a-nodes-service-open-to-the-internet)). A service can
run on the master if you want it there; none does by default.

## Roles

| role | what the machine becomes | modules |
|---|---|---|
| `bare` | the base system: admin user, SSH, updates | — |
| `master` | the kiwi-master: WireGuard entry point (wg-easy) whose default route is the VPN client (gluetun to Mullvad or any provider — the double hop), Pi-hole and Tor on the server's network stack, isolated-client subnet; nginx for the admin pages and to hand public names on to the nodes. It keeps no data of yours | vpn-client, vpn-server, dns, tor |
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

## A hub

A hub (a nostr relay and a hub built on kiwi-framework) runs on a node, so its
messages stay at home. As a private hub it needs only the two modules. To make
it public, give it public names, point their DNS records at the master (DNS
only, if the provider proxies), and open the node's proxy:

```yaml
hosts:
  medlar:
    node:
      modules: [vpn-client, reverse-proxy, nostr-relay, hub]
      reverse-proxy: { public: true }
      hub: { hub_domain: hub.example.org }
      nostr-relay: { relay_domain: relay.example.org, write_pubkeys: [<64 hex characters>] }
```

The master hands the names on to the node as below; the node holds their Let's
Encrypt certificates. Every other site keeps answering the VPN only. Anyone may
read the relay; the VPN may post, and from the Internet only the keys in
`write_pubkeys`. On a master the same two modules work too, with
`reverse-proxy: { public: true, acme_email: … }` on the master: then the hub's
data is on the rented server.

## A node's service, open to the Internet

A service need not live on the master to be reached from outside. Give it a
public name on a node, point the name's DNS record at the master, and open the
node's proxy:

```yaml
hosts:
  medlar:
    node:
      modules: [vpn-client, reverse-proxy, xmpp]
      reverse-proxy: { public: true }
      xmpp: { xmpp_domain: chat.example.org }
```

Nothing is opened at home. The master hands the name on to the node through the
VPN, still encrypted: the node fetches and holds the Let's Encrypt certificate,
and the master cannot read what passes. The node learns each visitor's address
from the master, so its limits count the visitor, not the master. Apply the
master first, then the node: the node asks for its certificate when it is
applied, and the question has to find its way through the master. On the VPN the name leads straight to the node, for the
devices that may reach that machine ([who may reach what](network.md#who-may-reach-what)).

A site is public when its name is outside the fleet's domain; every other site
of the node stays on the VPN.

**Open to**, in the app and on the page, at each module of a node: per web site a
switch for Tor (a hidden service of the machine's onion part, which comes along)
and a public name. A public name sets the module's own name setting and opens the
node's proxy through the master; point the name's DNS record at the master and
apply the master, then the node. Admin pages are offered neither. Ports that are not web traffic are handed on when
the module says so (Jabber's 5222, 5223 and 5269).

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
  [network.md](network.md#who-may-reach-what)). Unless you say so, nothing is
  opened to the Internet: a friend plays as a device on your VPN, in a group
  that reaches that one port and nothing else.
- **Open to the Internet**: `public: true` at a server
  (`survival: { game: minecraft, public: true }`). Players then join at the
  master's address and the game's port; the master hands the ports on to the
  machine, TCP and UDP. Nothing is opened at home, and the game sees the
  master's address for everyone. Apply the master and the machine. Tried with
  Minecraft and Zandronum on a test node (`tests/drills/game-ports.py`).
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
- **A server that sleeps while nobody plays**: `sleep: 30` stops it after 30
  minutes (5 or more) in which no player sent it anything, and frees the memory.
  The first player who tries to join wakes it; they get in a minute or two later
  (Minecraft: refresh the server list, then join). The machine's page and
  `kiwi-server status` say "asleep", not "down". A server you stop by hand stays
  stopped. Leave `sleep` out for its first start, while a game downloads.
- **The people who play start and stop them** at the game page, without being
  admins: [control.md](control.md#the-game-page).

## Jabber

Add `xmpp` to a machine's modules and your people have a Jabber (XMPP) server:
chats, group rooms, files, and message history that a new device shows. Any
XMPP app works: Conversations (Android), Monal (iPhone, Mac), Dino, Gajim.

```yaml
hosts:
  medlar:
    node:
      modules: [vpn-client, reverse-proxy, xmpp, onion]
      reverse-proxy: { public: true }              # for the Internet's door
      xmpp: { xmpp_domain: chat.example.org }      # the Internet's door
      onion: { services: { jabber: {} } }          # Tor's door
```

It runs on a node or on the master. On a node the public name comes in through
the master ([above](#a-nodes-service-open-to-the-internet)); the service, its
accounts and its certificate stay on the node.

It is one service with up to three doors:

- **The VPN** (always): `jabber.<machine>`, like `jabber.medlar.home`. Without a
  public name that is also what comes after the @ (`anna@jabber.sorbet.home`),
  and the certificate is the fleet's.
- **The Internet**: `xmpp_domain`, a name that points at the master in DNS as a
  plain record (no proxy in front: Jabber is not web traffic), with
  `reverse-proxy: { public: true }`. That name then comes after the @
  (`anna@chat.example.org`), the certificate is Let's Encrypt's, people log in
  from anywhere on 5222 and write with people on other servers. An app on the
  VPN can keep `jabber.<machine>` as the server it connects to.
- **Tor**: `onion: { services: { jabber: {} } }`; `kiwi-server onion` shows the
  address. An app connects to `<address>.onion`, port 5222, and the page is
  there too. Someone who cannot check a certificate can have an account
  `@<address>.onion`.

**Two apart.** `xmpp: { xmpp_domain: …, separate: true }` keeps the VPN's
service to itself (accounts `@jabber.<machine>`, nobody outside reaches it) and
runs a second one for the public name. Their accounts are apart and the two
exchange no messages. The public one's hidden service is `xmpp`, not `jabber`.

**Accounts.** `kiwi-server logins` shows the admin's. Everyone else gets theirs
on one page, `https://<name>/join`, in the ways switched on (below). An address
that keeps typing wrong passwords is shut out for an hour. The admin manages
people and rooms in a Jabber app (its server commands) or on the admin panel
`https://<name>/admin`, which answers the VPN only. Server settings changed
there are gone at the next start: kiwi-server writes ejabberd's configuration.

Rooms are at `rooms.<name>`; `room_creators: admins` lets only the admins make
them (`admins: [anna, …]` names more admins than the first one). A file may be
up to `upload_mb` (100) MB and is deleted after `file_days` (30) days.

Tried on a test node behind the master (15 November 2026, `tests/drills/jabber-doors.py`):
with one service, logins from the Internet (5222 and 5223), from the VPN and
through Tor; the page for new accounts with its captcha through all three; a
wrong password refused; the service sees the visitor's own address. With two
apart: the public one from the Internet and through Tor, the internal one
through Tor and its page. Not tried: an app, rooms, files, another server
writing in.

On a node Jabber answers at the machine's VPN address: a device at home reaches
it through the VPN or the home's gateway. With two apart on a node, a device on
the VPN that asks for the public name is led to the node, where the internal
service answers on Jabber's ports: for the public one, use it from outside the
VPN, or through its hidden service.


**How people get an address** is chosen per network, any mix of three, all on `/join`:
- `invites: true` (the default): with an invite code, choosing name and password;
- `requests: true` (the default): by asking with a few words; accepted on the control page
  (Accounts, Jabber), the person gets a password, shown once with their request code;
- `registration: true` (off by default): anyone, after a picture test on the page, or in their
  app after ejabberd's own. One new address per Internet address in ten minutes (over Tor that
  is one for all of Tor).

Invite codes and requests are made and answered under Accounts on the control page, or with
`kiwi-server accounts <fleet> --jabber …`. The rules on the page: `terms`. These Jabber addresses
are not the network's accounts (the accounts module), and nothing joins the two yet.

## Pastebin

Add `paste` to a machine's modules: a page to put a text or a file on and pass
its link along (PrivateBin). A paste is encrypted in the browser; the server
keeps what it cannot read, and the link carries the key.

- **On the VPN**: `https://paste.<machine>`.
- **For the Internet**: `paste: { paste_domain: paste.example.org }` with
  `reverse-proxy: { public: true }` on the machine, and the name pointing at
  the master. On a node the master hands the name on
  ([above](#a-nodes-service-open-to-the-internet)); tried so on a test node.
- **For strangers without a public name**: `onion: { services: { paste: {} } }`,
  a hidden service ([network.md](network.md#hidden-services)).

`title`, `default_expire` (a week), `size_mb` (10) and `file_upload` are its
settings. The pastes are in the machine's backup.

## Accounts

Add `accounts` to a machine's modules: one account for the network's services,
kept by [Kanidm](https://kanidm.github.io/kanidm/stable/) at `https://id.<machine>`,
or at a public name (`accounts: { accounts_hostname: id.example.org }`) when
strangers should sign up.

- **The sign-up page** is `https://<name>/join`. A person gets an account in one
  of two ways:
  - with an invite code: the account is made at once;
  - by asking, with a few words: the people who run the network accept or deny the request.

  The person keeps a request code and comes back with it. `accounts: { signup: false }` leaves only the people who run the network to
  make accounts.
- **No email.** A new account comes with a link that sets its password or a
  passkey, good for a day. A lost password: the people who run the network make
  a new link. The page says so.
- **A password alone will do** unless `second_factor: true`: then every account
  needs a passkey, or a password and a code from an authenticator app.
- **Jabber** keeps its own accounts. `ldap: true` makes Kanidm answer LDAP on the
  stack network, but nothing uses it yet.
- **Forgejo signs people in with these accounts** after `kiwi-server accounts
  <fleet> connect <host> git` (or Connect under Accounts on the control page) and an
  apply of that machine: "Sign in with accounts", and a person's first sign-in makes
  their Forgejo user. The same for Nextcloud with `connect <host> cloud`: "Log in with
  accounts", next to the admin's own password. Paperless: `connect <host> paperless`.
  Vaultwarden: `connect <host> vault`, then "Use single sign-on" (the email it asks
  first is only a hint: the account comes from the sign-on); the person still
  sets a master password, which unlocks the vault and never leaves their device. A
  vault user made earlier with an email and password stays as it is. Immich:
  `connect <host> immich`; its admin page stays editable, kiwi sets only the sign-in
  part of its settings, and the phone app signs in the same way.
- **Over Tor** a person sets a password at the accounts' page; a passkey belongs to
  the usual name and does not work at the hidden address. A service opened as a
  hidden service sends "Sign in with accounts" to the accounts' usual name: that
  works over Tor only when the accounts have a public name.
- **On the control page, Accounts** shows the requests to accept or deny, makes
  invite codes, and makes accounts and new password links; `kiwi-server accounts
  <fleet> …` does the same from a terminal.
- On the machine, `sudo sh ~/docker/kiwi/accounts.sh` makes accounts and their
  links (`create`, `reset`, `list`, `delete`, `jabber NAME on|off`). The sign-up
  page does it with a token of its own, which may make people and nothing else.
- The rules on the sign-up page are `accounts: { terms: "…" }`, a paragraph a
  line.

Tried on a test node (16 November 2026):
- Kanidm behind the machine's proxy.
- Accounts made, listed, joined to Jabber, deleted.
- In a real browser: a person set a password with the link and signed in, and a wrong password was refused.

Tried too (16 November 2026): a stranger signed up with an invite code and by
asking, in a real browser. Signing in to Forgejo with an account
worked too, in a real browser, and so did Nextcloud (16 November).

## Authenticator

Add `authenticator` to a machine's modules: the six-digit codes of your
two-factor logins, in a page of your own at `https://auth.<machine>` instead of
an app on one phone. It is [2FAuth](https://docs.2fauth.app): scan a QR code or
type a secret, see the codes, sort them into groups, bring them in from Google
Authenticator, Aegis, 2FAS and others.

- **For the VPN only.** A public name is refused: the secrets of your logins do
  not belong on a page the Internet reaches.
- **The first account made on the page is the admin's.** Make it at once, then
  close registration (Admin, Auth) unless more people of your network should
  have their own.
- In its settings, switch on **Protect sensitive data**: the secrets are then
  encrypted in its database with the module's key, which comes from the fleet's
  seed. The database is in the machine's backup.
- It may ask the Internet for an account's logo and for new versions; both are
  switches in its settings.

Do not keep the second factor of the password manager that holds your first
factors in the same place, and keep the recovery codes of your accounts on
paper: a machine can be off when you need a code.

Tried on a test node (15 November 2026): the page, its login and its sign-up
form open over the VPN's name. Not tried: an account, a code, an import.

## Firefox Sync

Add `firefox-sync` to a machine's modules and your Firefox browsers keep their
bookmarks, tabs, passwords and history on that machine, not at Mozilla. It is
Mozilla's own sync server with a database of its own.

In every Firefox that should use it, before signing in to sync: `about:config`,
then `identity.sync.tokenserver.uri` =
`https://ffsync.<machine>/1.0/sync/1.5`. A browser that syncs already is signed
out of sync first.

- **The login** still goes through a Mozilla account: the server asks Mozilla
  who someone is. What is synced is encrypted by the browser and stored here.
- **The certificate**: Firefox keeps its own list of authorities. The fleet's CA
  goes into it (Settings, Certificates, Import), or the device trusts the CA and
  Firefox is told to follow (`security.enterprise_roots.enabled`).
- The device must be on the VPN to sync, as for every service.
- Mozilla's image is for x86-64 machines only, not for the small boards.
- Tried on the test network (15 November 2026): the server says that it and its
  database are fine, and it asks for a login. A browser syncing was not tried:
  that needs a Mozilla account.

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
