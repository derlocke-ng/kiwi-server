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
  By default: 10.8.1.0/24 guests, 10.8.2.0/24 pentest, 10.8.3.0/24 devices,
  10.8.4.0/24 routers. The master's firewall rules go by these.
- `--address` sets one by hand.
- A fleet host's client is named after its hostname, so the wg-easy page and
  the fleet agree.
- Nodes keep everything in the tunnel; `--split` is for devices that should
  reach the network but keep their own exit.
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
  (Quad9 only while the master is unreachable).
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
that arrives whole.

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

- `all`: everything on the VPN (a group only).
- `nodes`: every node.
- `apps`: every app: the web pages of every node, and nothing else of them. No
  SSH, no other port, no admin pages (a group only).
- `papaya`: one machine, all of it.
- `cloud.papaya`: one service, its web address only.
- `papaya:22`: one port of a machine.
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

On the control page this is "Who may reach what", in the app the same entry in
the menu: a tick per machine and service.

To see that the rules hold, with a real VPN client per group:

```bash
python3 tests/drills/access.py ~/kiwi/fleet.yaml sorbet
```

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

Each network keeps its fleet, seed, CA and mesh (every network its own /16).
The masters are linked by WireGuard on UDP 51822, each Pi-hole asks the other
for the other's domain, and a partner reaches only what it is given, in the
same words as a group ([above](#who-may-reach-what)). `federation.yaml` next to
the fleet file holds it as `share`; `from` there limits which addresses of the
partner may come.

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
