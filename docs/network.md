# The network

The machines and your devices share one WireGuard mesh behind the master.

## Joining the mesh

One command per client, through the master's wg-easy API over an SSH tunnel
to the master:

```bash
kiwi-server enroll fleet.yaml sh3                     # a fleet host: secrets/sh3.home.conf, picked up at the next render
kiwi-server enroll fleet.yaml phone --group devices --qr   # a device: secrets/devices/phone.conf, and the QR code
kiwi-server enroll fleet.yaml laptop --group devices --split   # only the mesh through the tunnel, its own internet otherwise
kiwi-server enroll fleet.yaml fritzbox --group routers        # for a router — see routers.md
kiwi-server enroll fleet.yaml sh3 --existing          # the config of a client that exists, again
```

- `--group` moves the client into that group's range (`vpn-server.groups`). Without it a new
  device goes into `family`, or the first group without the admin pages: a device outside every
  group reaches everything, admin pages included. The control page and the app preselect the
  same group.
  By default, in your mesh 10.X.0.0/16: 10.X.1.0/24 guests, 10.X.2.0/24 pentest,
  10.X.3.0/24 devices, 10.X.4.0/24 routers, 10.X.5.0/24 family. The master's
  firewall rules go by these.
- `--address` sets one by hand.
- A fleet host's client is named after its hostname, so the wg-easy page and
  the fleet agree.
- Nodes keep everything in the tunnel; `--split` is for devices that should
  reach the network but keep their own exit. Such a config carries this mesh and the meshes of
  the linked networks, nothing else.
- A split config still names the master as its DNS server. Imported as it is into
  NetworkManager, every name the computer looks up goes there, the computer's own LAN names
  included. `enroll --split` prints the line that changes this:
  `nmcli connection modify laptop ipv4.dns-search ~home,~internal ipv4.never-default yes`
  (your domain and the linked networks' domains). After it, only those names go through the
  tunnel. `kiwi-server connect` does the same for the computer it runs on.
- `vpn-server.wg_password` is what the API logs in with.

Routers join the mesh with `kiwi-server openwrt` or the steps in
[Routers](routers.md).

## The way to the Internet

Phones, laptops and the home's machines reach the Internet through the master.
From there they leave one of three ways:

- **Riseup** (the first run's suggestion): they come out at a Riseup server.
  Free, with no account; Riseup is run by volunteers, so it is slower than a paid
  provider. See [Riseup](#riseup).
- **The master's own connection**: they come out at the master's address. This is
  the simplest; nothing to sign up for.
- **A VPN provider**: they come out at the provider's servers, and the master's own
  address stays out of what they do. Every provider gluetun runs with WireGuard
  works: Mullvad, Proton VPN, NordVPN, Surfshark, AirVPN, Windscribe, IVPN and
  FastestVPN. Any other provider works through its WireGuard config.

The first run (in the app, on the page or in the terminal) asks for it. Each
provider is asked only for the lines it needs from a WireGuard config made in your
account there:

| Provider | From its WireGuard config |
|---|---|
| Mullvad, Surfshark, IVPN, FastestVPN | PrivateKey, Address (its IPv4 part) |
| Proton VPN, NordVPN | PrivateKey |
| AirVPN, Windscribe | PrivateKey, Address, PresharedKey |
| Another provider | PrivateKey, Address, the PublicKey and Endpoint of its [Peer] part, PresharedKey when it has one |

In the fleet file it is the master's `vpn-client` block:

```yaml
    master:
      vpn-client: { vpn_provider: protonvpn, wireguard_private_key: …, server_countries: Switzerland }
```

`server_countries`, `server_regions`, `server_cities` and `server_hostnames`
choose the provider's servers.

### Riseup

```yaml
    master:
      vpn-client: { vpn_provider: riseup }            # or: { vpn_provider: riseup, server_cities: amsterdam }
```

Riseup gives anyone a client certificate for its OpenVPN servers, with no account
and nothing to type. The computer that applies fetches one from riseup.net into
the fleet's secrets (`secrets/riseup/<machine>.ovpn`, sealed with the other keys)
and renews it when fewer than ten days are left of its five weeks; the control
node looks every six hours and applies the machine whose certificate runs out. It picks the first of Riseup's servers that answers, in the
city of `server_cities` when one is given. A node without a master can use it as
its exit too (`vpn: false`). Without a `vpn-client` block the master is its
own exit. A second network can be the exit too
([Leave to the Internet through a partner](#leave-to-the-internet-through-a-partner)).

## DNS

DNS is one chain for the whole network:

- VPN clients ask the master's Pi-hole.
- A gateway node's Pi-hole serves its LAN and asks the master's Pi-hole first
  (a public resolver, Cloudflare's, only while the master is unreachable).
- Names under the fleet's domain go to the master and never leave it.
- Every host carries the fleet's names in its hosts file plus a route into
  the mesh through its VPN client.

A node's Pi-hole and hosts file answer with the LAN address of the nodes on
its own LAN: a machine with a static address in the node's subnet. A client
at home reaches `cloud.papaya.home` across the switch and not through the
master. A device on the mesh, which asks the master's Pi-hole, gets the mesh
address wherever it is, so a device with its tunnel on at home still goes
through the master. Names of masters, and of nodes on DHCP or another LAN,
stay at their mesh addresses.

A public name (`status.example.org`, `cloud.example.org`) is never one of the
network's own: the Pi-holes and hosts files do not know it, so it resolves to
the master's public address at home and on the VPN as on the Internet. What is
public is reached the way strangers reach it, through the master; what the
network keeps for itself has a name under the fleet's domain. A service you
want both ways is better two: a public one and an internal one.

The chain ends at a public resolver that answers every name: Cloudflare's, asked
over TLS by the master's VPN client through the exit (`vpn-client.dot_providers`;
`google` is another). What is blocked is what the Pi-hole's blocklist says. Until
2.269.0 the default was Quad9, which leaves out names it holds to be malicious: a
site was blocked that no blocklist of the network named.

Nothing about who asks is passed on: Cloudflare sends the sites' name servers no
client subnet (ECS). Google does, and through a VPN exit that is the exit's subnet,
which shows a VPN to a site you visit over your home line. A subnet of your own
choosing cannot be sent instead: the VPN client's forwarder drops it (tried on 20
November 2026). One leak test still shows the exit's subnet with Cloudflare: Akamai's
`whoami.ds.akahelp.net`, the one name Cloudflare makes an exception for.

Signed names (DNSSEC) are checked where the chain ends, by that resolver. A badly
signed name gets no address there, and so nowhere in the network.
Pi-hole's own check stays off: that resolver hands out answers, not the keys a
second check needs, and with the check on every public name failed (tried on a
node and on a master, 20 November 2026). A master with `upstream_dns` of its
own gets whatever those resolvers do.

### Names by service

By default a part is named after its machine: the pastebin on quince is `paste.quince.home`.
With `names: service` a part that runs on one machine only is named for itself:

```yaml
defaults:
  names: service        # paste.home, cloud.home, git.home (default: machine)
```

- The name stays when the part moves to another machine: links, bookmarks and the server
  address in a client do not change.
- `names:` can also stand on one machine, to try it there first.
- A name you set by hand in the part's own settings always counts.
- A part that runs on two machines keeps its machines' names on both.
- Two parts can never have one name: kiwi-server names both and stops.
- The control page and the backup server keep their machine's name: computers that joined
  and the backups point at it.
- After changing it, apply the machine and the machines that answer for names
  (`kiwi-server apply … --changed`). The old name keeps opening for Nextcloud, Forgejo,
  Vaultwarden, the pastebin and Firefox Sync (it sends the browser on); for the other parts
  only the new name answers.

### Mullvad's SOCKS5 proxies

Every Mullvad server runs a SOCKS5 proxy, reachable through the master's
Mullvad exit. The master's Pi-hole answers for them as
`de-fra-wg-socks5-001.relays.mullvad.net` and `de-fra-001.mullvad.home`
(`dns.mullvad_socks`, on in the master preset). gluetun refuses these
private-range answers, so without the records the names would not resolve
reliably in the mesh. A host timer refreshes them every 6 hours from the
[mullvad-socks5](https://github.com/derlocke-ng/mullvad-socks5) list, which
also has FoxyProxy imports that use these names.

## This computer on the network

```bash
kiwi-server connect ~/kiwi/fleet.yaml
```

puts the computer you sit at on your network's VPN. It gets a place of its own (a
device named after the computer), and where NetworkManager runs, a connection called
`kiwi-<name ending>` that:

- carries only your network: its machines and services, nothing else;
- asks your network's DNS only for names with your ending (`.home`), so the computer's
  other names and its Internet stay as they are;
- comes up by itself after a restart.

Your browsers learn to trust the network's certificates (Chromium and its kin, each
Firefox profile, Flatpak browsers), without an administrator. For every other program
the command prints one line to run with `sudo`. The app has the same under
"Connect this computer…". `kiwi-server connect <fleet> --remove` undoes it.

Run it on the computer itself, in its own desktop session: over SSH NetworkManager
refuses to add a connection.

Two computers must not share one device: each would cut the other off. When a device of
this computer's name exists and the computer has no connection yet, the command stops and
asks: `--existing` when it is this computer, `--name` for another one of the same name.

## A machine's address at home

A machine installed from install media gets its fixed address from the installer. A
board (Armbian) starts from an image and asks the router. `network.address` in the
fleet file, or "Its address at home" in the settings, reaches such a machine with the
next apply: the machine changes its own address, checks that the router is there and
that nobody else has the address, and takes back what it had when that fails. Where
the address cannot be changed after the install (CoreOS, uCore, Debian), the apply
says what the machine has and what the fleet file wants.

## A machine at another site

A node need not stand at home: at a friend's, in an office, behind someone else's
router. It joins the mesh like any node, through the master, and the others reach it
over the VPN. Machines with fixed addresses in one LAN reach each other there
directly (their DNS answers with the LAN address); one at another site is never taken
for a neighbour. Give it its site in the fleet file, or "Site" in its settings:

```yaml
hosts:
  medlar:
    network: { dhcp: false, address: 192.168.178.20/24, gateway: 192.168.178.1, site: friend }
```

Without `site` every machine is at `home`. It matters when the other place's LAN has
the same numbers as the home's (two routers of one make give out the same range):
without it, home would send the machine's name to an address in its own LAN.

Nothing at home reaches a machine there before it is on the VPN, so its first setup
cannot come over SSH from home. Either a computer at its site does it (`apply … --ssh`),
or the machine comes onto the VPN by itself first:

```yaml
hosts:
  medlar:
    install: { over_vpn: true }      # or "It stands at another site" in its settings
```

`kiwi-server build` then makes a one-time join peer on the master (its own key and
address, not the machine's) and puts its WireGuard config on the install media. The
installed machine comes onto the VPN with it at first boot; `kiwi-server next` (or the
control node) finds it there and sets it up over the mesh. As soon as it answers at its
own mesh address, the join tunnel is switched off on the machine and the peer is removed
from the master. Until then the stick carries that key: keep it safe. Nothing more is
opened on the master than the VPN it has anyway.

An encrypted machine there opens its disk at boot from a ring at its own site, or
from a ring at home through the master: the master only passes the ring's port on over
the VPN and keeps no key, and it lets in only the addresses the network's machines last
connected from. kiwi-server sets that up by itself (`vpn-server.unlock_relay` and the
machine's `unlock.via`); apply the master once after the machine joins. A ring at home is no
help to it directly: its initramfs has no VPN yet.

A ring served by the master itself (`tang` on the master) works too, but then its keys
lie on a rented server: whoever copies them there and also gets hold of the machine can
open its disk. kiwi-server does not suggest it; the relay is the way.

## Phones and laptops: 1280

A phone or laptop is in a different network every day: mobile data, a hotel, another
VPN around it. Many of those carry smaller packets than a home line, and a packet that
is too big vanishes without a word: pages open, uploads and photo syncs hang. So the
config `kiwi-server enroll` (and the page's "Add a phone or laptop") makes for a device
says `MTU = 1280`, which fits everywhere. A machine of the network keeps the master's
`wg_mtu`: its path is fixed and measured.

```bash
kiwi-server enroll fleet.yaml laptop --mtu 1340      # another size for this one
```

```bash
kiwi-server enroll fleet.yaml phone --existing --qr  # a device made before 2.84.0: its config anew, with 1280
```

## Measuring the MTU

To find out what a path really carries, connect the node or device and
measure:

```bash
kiwi-server mtu fleet.yaml m1 --ssh core@<address>          # a node
kiwi-server mtu fleet.yaml phone --ssh core@<master>        # a device from secrets/devices/
```

It sends don't-fragment pings through the tunnel and reports the largest size
that arrives whole. For a node it then measures the path outside the tunnel, to
the master's public address, and says how large the tunnel's MTU could be (the
path less 60 bytes over IPv4, 80 over IPv6). A larger MTU on the master reaches
every node, so measure each before raising it. A machine's page in the app and
on the control page has the same measurement as a button.

- A device's path is small: change that device's own config and leave
  everyone else alone.
- A node's path is small: `wg_mtu` has to come down on it and its master;
  `--write` puts that in the fleet file.

A packet split on the way out and put together by the far end cannot be seen
from here.

## Who may reach what

A device on the VPN belongs to a group: you choose it when you add the device
(`enroll --group`, or "Who is it for?" on the page). A group is a range of
addresses on the master. Each group reaches what it is given, and nothing else.
A new network has five:

| Group | For | Reaches | Other devices | Admin pages |
|---|---|---|---|---|
| `devices` | your own devices | everything | yes | yes |
| `family` | the people of the house | every app | yes | no |
| `routers` | a router that brings a whole LAN | everything | yes | no |
| `guests` | visitors | every node | no | no |
| `pentest` | someone testing the network | nothing | no | no |

**Admin pages** are the network's controls, not apps: the control page, Portainer,
Pi-hole, the VPN's device page, the backup server, the cache. A group opens them
only with its admin switch on, or when one is named for it. "Everything" without
the switch is everything but those. A linked network opens an admin page only
when it is named. `kiwi-server access` lists the admin pages of your network.

A device outside every group reaches everything. The master is always
reachable: it answers the names.

```bash
kiwi-server access ~/kiwi/fleet.yaml
```

That lists every group and every linked network with what it reaches. To change
one, name it and what it gets:

```bash
kiwi-server access ~/kiwi/fleet.yaml guests cloud.papaya paperless.papaya
```

What a group or a linked network can be given:

- `all`: everything on the VPN, and what the linked networks open to this one
  (a group only).
- `nodes`: every node.
- `apps`: every app: the web pages of every node, and nothing else of them. No
  SSH, no other port, no admin pages (a group only).
- `papaya`: one machine, all of it.
- `cloud.papaya`: one service, its web address only.
- `papaya:22`: one port of a machine.
- `kiwi2`: a linked network by its name: what it opens to this one (a group
  only). A group without `all` reaches nothing of a linked network otherwise.
- `nothing`.

`--add` and `--remove` change the list by single entries. `--new` makes a group
with the next free range. `--internet no` keeps a group off the Internet through
the VPN, `--peers no` away from other devices, `--admin yes` lets it open the
admin pages.

```bash
kiwi-server access ~/kiwi/fleet.yaml family --new papaya lychee:22
```

The change is in the fleet file (`vpn-server.groups`); the lines around it and
their comments stay. It reaches the machines with the next apply: the master
always, and a node when single services of it are named. The command says which.

Two places enforce it. The master's firewall lets through what is given and
drops the rest. All services of a node share one address and port, so the node's
own web server does the rest: it refuses a group the services it was not given,
and the admin pages when it has not the switch.

On the control page this is "Access rules", in the app the same entry in
the menu: a tick per machine and service.

To see that the rules hold, with a real VPN client per group:

```bash
python3 tests/drills/access.py ~/kiwi/fleet.yaml sorbet
```

### Groups of the home network

At home, the network trusts the whole LAN: a client there reaches every node
across the switch. A part of the home network can be a group too: a guest Wi-Fi,
a camera network, a VLAN, anything with a subnet of its own. It is given things
in the same words as a VPN group:

```bash
kiwi-server access ~/kiwi/fleet.yaml iot --new --lan 192.168.20.0/24 cloud.papaya
```

It lands in the fleet file as `lan.groups` in the defaults. The master never
sees that traffic, so each node keeps the rules itself: its web server refuses
the names the group was not given, and its firewall (the nftables table
`kiwi_lan`, which `kiwi-stack` writes) the ports. A gateway node does the same for
what it routes from the home network into the mesh. DNS, DHCP and ping stay open
to everyone. A group of the home network has the admin switch, but not the VPN's
"other devices" or "the Internet": the home's router decides those. Apply the
nodes after a change. On the page and in the app, a new group with a subnet in
*At home* is a group of the home network. Plans for VLANs on the machines
themselves: [design/lans.md](design/lans.md).

## Hidden services

A hidden service gives one service of a machine an address that anyone opens with
Tor Browser: no public name, no open port at home, no address of yours in
anyone's log. It is for people who are not on your VPN, strangers included
([design](design/onion.md)).

```bash
kiwi-server onion ~/kiwi/fleet.yaml git.papaya
```

That makes `git.papaya` a hidden service and prints its address
(`http://….onion`). Apply the machine, and the address works. Anyone who has it
gets in and meets the service's own login.

For named people only:

```bash
kiwi-server onion ~/kiwi/fleet.yaml cloud.papaya --client anna
```

It prints a key. Anna opens the address in Tor Browser and types the key when
it asks. Without a key the service cannot even be found.

- `kiwi-server onion <fleet>` lists the hidden services with their addresses.
- `--show anna` prints anna's key again.
- `--remove --client anna` takes anna's key away; `--remove` alone takes the
  hidden service away.

**A new address.** An address was given out too widely, or should change for another reason:

```bash
kiwi-server onion ~/kiwi/fleet.yaml git.medlar --new-address
```

or **New address** next to it on the page and in the app. The old address stops
answering with the machine's next apply; who should keep reaching the service
needs the new one. Its people and their keys stay.

### Which services

Not every service works at a `.onion` address: some send a visitor on to their
name on the VPN, which a stranger cannot reach, or refuse a login that comes
from another address than their own. So a module marks the sites that were
tried through Tor, and `kiwi-server onion`, the control page and the app offer
those:

| Works, tried through Tor | What was tried |
|---|---|
| Git (Forgejo) | the pages, their files, the login form, and a login by its API |
| Payments (BTCPay) | the pages, their files, the login form, a payment |
| Jabber | the page for new accounts, logins on port 5222 |
| Pastebin, the hub page, the relay, the status page | the page and its files |
| Files (Nextcloud) | the page, its files, what its app is told, and the files opened with a login (WebDAV) |
| Photos (Immich) | the page, its files, what its app is told; its login answers there (no account was at hand to log in with) |
| Documents (Paperless) | the page, the login form and a login by its API (it is told its `.onion` address, or it would refuse a login sent from there) |

Not marked: the admin pages (they are not for strangers); OpenCloud (its app is
sent to the name on the VPN to log in); Passwords (Vaultwarden: the server tells
its app the name on the VPN, and it was not seen to work without it); Firefox
Sync (no page for people); the downloaders; and what was not tried yet. `kiwi-server onion
<service>.<machine> --anyway` makes a hidden service of one of those all the
same; a hidden service that existed before stays, with a warning at each
render. `tests/drills/onion-sites.py` is the check: every hidden service of a
network opened through Tor.

### https at the address

Tor encrypts the way to your machine by itself, so `http://….onion` is not
plain text on the way. With `onion: { tls: true }` each hidden service answers
`https://….onion` as well. The certificate is your network's CA's when the CA
may vouch for such a name; a CA limited to your network's names
(`tls.name_constraints`) may not, and then the certificate is the machine's
own. A stranger's browser warns in both cases: it knows neither. This is for
programs that insist on https.

By default Tor never leaves through your connection at home. The Tor process of
a node shares its VPN client's network: what it sends goes to the master and
leaves there, through the master's VPN exit or, when it has none, from the
master's own address. Your internet provider at home sees the VPN to your master
and nothing else. Without a master, a commercial VPN (Mullvad, Proton, …) can be
the machine's exit instead.

A machine without any VPN can still run hidden services when you choose it:
`onion: { without_vpn: true }`. Tor then leaves through the machine's own
connection, and your internet provider can see that you use Tor; kiwi-server
says so whenever it sets one up.

The address and the keys come from the fleet's seed. They are known before the
machine is set up, and a machine installed anew keeps its addresses.

Mind: a service open to anyone is open to anyone. Keep its own sign-up off
unless strangers should have accounts.

## Federation

Two networks can share chosen services with each other
([design](design/federation.md)):

```bash
kiwi-server federate ~/kiwi/fleet.yaml ~/kiwi2/fleet.yaml   # the link, nothing shared yet
kiwi-server access ~/kiwi/fleet.yaml kiwi2 cloud.papaya     # what the partner may reach here
kiwi-server plan ~/kiwi/fleet.yaml && kiwi-server apply ~/kiwi/fleet.yaml
```

Each network keeps its fleet, seed, CA and mesh. `init` gives a new network a
/16 at random, 10.X.0.0/16 with X from 10 to 250, not the home's LAN and not a
mesh of the other fleets on that computer (its advanced question *The VPN's
addresses* shows it).

Two networks with the same mesh or the same domain link all the same: each
side sees the other under an alias that `federate` picks and writes in its
`federation.yaml` as `seen_as`, a free /16 and `<its name>.<its domain>`. Your
devices reach the partner's `papaya.home` as `papaya.kiwi2.home` at an address
in the alias /16, while your own `papaya.home` stays yours. The master maps the
alias onto the partner's real addresses at the link, a small DNS on the master
(`<prefix>-fed-dns`) gives the partner's names under the alias, and the
partner's machines answer the alias name and carry it in their certificates.
Nothing to set by hand; only `share`, `from` and the rest stay in the partner's
own words. Two partners that have one mesh between them cannot both be linked. The home
LANs behind them do not matter: only the meshes go over the link.
The masters are linked by WireGuard on UDP 51822, each Pi-hole asks the other
for the other's domain, and a partner reaches only what it is given, in the
same words as a group ([above](#who-may-reach-what)). `federation.yaml` next to
the fleet file holds it as `share`; `from` there limits who of the partner may
come: its groups by name, or single addresses.

```yaml
kiwi2:
  share: [cloud.papaya, lychee, papaya:22]   # a service, a whole node, one port of a node
  from: [devices, 10.10.0.3]                 # their group devices and one device; empty: all of them
```

When the two networks have two operators, each gives the other a card instead
(nothing secret on it: the link key's public half, the master's address, the
mesh, the domain, the groups, the CA's certificate and a certificate request per
machine):

```bash
kiwi-server federate ~/kiwi/fleet.yaml --invite kiwi.card.yaml   # send this file to the other
kiwi-server federate ~/theirs/fleet.yaml --accept kiwi.card.yaml # they take it; it writes their card beside it
kiwi-server federate ~/kiwi/fleet.yaml --accept theirs.card.yaml # and you take theirs
```

### A partner's certificates on your devices

A partner's pages carry certificates of its own CA. Your devices trust them once they
import that CA. Since 2.222.0 `federate` keeps the partner's CA in `federation.yaml`
(`ca:`). It is offered to your devices only when its name constraint permits nothing
but the partner's own domain, so it can sign nothing of yours:

- the control page: *Linked networks*, a download with the CA's mark;
- the app: *Federation…*, *Its certificates*, *Save…*;
- `kiwi-server federate ~/kiwi/fleet.yaml --ca kiwi2` prints it;
- `kiwi-server connect` puts it into this computer's browsers, beside your own CA.

A partner with your own domain (translated at the link) gets its names signed by your
CA instead: `federate` signs the names your devices use for its machines, and its
machines show that certificate on them. Apply its machines afterwards. Between two
operators (2.227.0) the cards carry the requests: `--accept` of a partner's card writes
`<you>.signed-for-<partner>.yaml` beside it; send it back, and the partner takes it with
`kiwi-server federate THEIR-FLEET --accept` that file. Its own CA is not offered: it could
sign your names. Networks linked before 2.222.0 bring their CA when they are linked
again.

### Leave to the Internet through a partner

One network can use the other as its way to the Internet: a device of the first comes
out at the second's master (multi-hop).

```bash
kiwi-server federate ~/kiwi/fleet.yaml ~/kiwi2/fleet.yaml --exit
```

The first network leaves through the second. The command writes `exit: true` for the
partner into the first network's `federation.yaml` and `internet: true` into the
second's, which is what lets the first one out. Apply the second network's master, then
the first's.

- It rides the link that is there. The first master is not a client of the second's
  VPN server, and needs no VPN client and no second tunnel for it.
- The second network sees the first one's master, not which of its devices asks.
- The first network gets out to the Internet only: never to a private address of the
  second network. What it may reach there is still `share`.
- With the link down, the first network's devices have no Internet. They do not fall
  back to their master's own connection.
- A network has one such exit, and two networks cannot leave through each other.
- The names a device asks for are still answered by its own master's Pi-hole, which
  asks its resolvers from the first master, not through the partner.

`kiwi-server federate … --no-exit` puts it back. `kiwi-server validate` says which way
a network's devices leave.
