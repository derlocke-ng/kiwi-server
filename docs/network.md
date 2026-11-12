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

- `--group` moves the client into that group's range (`vpn-server.groups`).
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

## Federation

Two networks can share chosen services with each other
([design](design/federation.md)):

```bash
kiwi-server federate ~/kiwi/fleet.yaml ~/kiwi2/fleet.yaml   # the link, nothing shared yet
$EDITOR ~/kiwi/federation.yaml                             # share: [cloud.papaya], from: []
kiwi-server plan ~/kiwi/fleet.yaml && kiwi-server apply ~/kiwi/fleet.yaml
```

Each network keeps its fleet, seed, CA and mesh (every network its own /16).
The masters are linked by WireGuard on UDP 51822, each Pi-hole asks the other
for the other's domain, and a partner reaches only what is listed under
`share`, from the addresses under `from`.
