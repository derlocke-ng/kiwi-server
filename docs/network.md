# DNS and networks

This page explains how names are resolved in a Kiwi Network, how to publish a
hub on the Internet, how a node can keep a cache for its LAN, and how two
networks can share services. The *mesh* is the private WireGuard network that
connects the master, the nodes and your devices.

## The DNS chain

DNS is one chain for the whole network:

- VPN clients ask the master's Pi-hole.
- A gateway node's Pi-hole serves its LAN. It asks the master's Pi-hole first,
  and Quad9 only while the master cannot be reached.
- Names under the fleet's domain go to the master and never leave it.
- Every host has the fleet's names in its hosts file, and a route into the
  mesh through its VPN client.

Every Pi-hole in the fleet answers every host and service name of the fleet,
with the mesh address, except for nodes on its own LAN (see below). The names
come from the fleet file and from the services each module announces.

### Names on the same LAN

A node's Pi-hole and hosts file answer with the LAN address for the nodes on
its own LAN, which are the machines with a static address in the node's
subnet. A computer at home therefore reaches `cloud.papaya.home` across the
switch and not through the master. A device on the mesh asks the master's
Pi-hole and gets the mesh address wherever it is, so a device with its tunnel
on still goes through the master at home. Masters, and nodes on DHCP or on
another LAN, are always answered with their mesh address.

### Mullvad's SOCKS5 proxies

Every Mullvad server runs a SOCKS5 proxy, which the mesh can reach through
the master's Mullvad exit. The master's Pi-hole answers for them under their
own names, such as `de-fra-wg-socks5-001.relays.mullvad.net`, and under short
names such as `de-fra-001.mullvad.home`. The setting is `dns.mullvad_socks`,
which is on in the master role. gluetun refuses these private-range answers,
so without the records the names would not resolve reliably in the mesh. A
timer on the host refreshes them every 6 hours from the
[mullvad-socks5](https://github.com/derlocke-ng/mullvad-socks5) list, which
also has FoxyProxy imports that use these names.

## The admin pages

On a master, nginx shares the WireGuard server's network, so `wg.<hostname>`
and `pihole.<hostname>` answer on the mesh address only, and only for the
client groups that may open them (see
[Join the mesh](managing.md#join-the-mesh)).

## A public hub

A hub belongs on a master. As a private hub, reachable through the mesh only,
it needs only the `nostr-relay` and `hub` modules. To make it public, give it
public names, point their DNS records at the master (DNS only, if your DNS
provider proxies), and open the proxy:

```yaml
master:
  modules: [vpn-client, vpn-server, dns, tor, reverse-proxy, fail2ban, nostr-relay, hub]
  reverse-proxy: { public: true, acme_email: you@example.org }
  hub: { hub_domain: hub.example.org }
  nostr-relay: { relay_domain: relay.example.org, write_pubkeys: [<64 hex characters>] }
```

This opens ports 80 and 443 on the master. The public names get Let's Encrypt
certificates, which a timer renews; `acme_staging: true` tries it out on
Let's Encrypt's test service first. Every other site on the master, the admin
pages among them, still answers only the mesh. Anyone may read the relay. The
mesh may post to it, and from the Internet only the keys in `write_pubkeys`.
With public sites, the `fail2ban` module also bans scanners and bad requests.

## Reach a node's SSH through the mesh

Mesh SSH to a node is one DNAT rule, a rule that forwards a port:

```yaml
node:
  vpn-client: { extra_dnat_rules: ["2222/tcp:172.128.0.1:22"] }
```

`172.128.0.1` is the gateway of the node's stack network, which is the host
itself.

## A cache for the LAN

Add `cache` to one node's modules, and the machines on its LAN fetch
container images and Debian packages from it instead of the Internet:

- **Container images**: a mirror for each registry at `cache.<hostname>`.
  podman on the other nodes asks it first and the registry when it does not
  answer.
- **Debian packages**: a caching proxy on port 3142. apt on Debian nodes uses
  it when it is there and goes direct when it is not.

Every node of the fleet on the same LAN uses it without a setting. The master
and nodes elsewhere do not, because over the mesh every byte would cross the
master. When the cache is off, the machines go to the source by themselves.
`cache: { data_dir: /mnt/data/cache }` puts it on a big disk.

The cache was tried on the test network: papaya as the cache, lychee pulling
an image and updating through it, and both again with the cache stopped. The
plan for netboot and a control node that builds on it is in
[Local first](design/local-first.md).

## Federation

**Status: experimental.** The federation drill (a client of one network
reaches what the other shares, and nothing else) passed between two test
networks in November 2026.

Two networks can share chosen services with each other:

```bash
kiwi-server federate ~/kiwi/fleet.yaml ~/kiwi2/fleet.yaml   # the link, nothing shared yet
$EDITOR ~/kiwi/federation.yaml                             # share: [cloud.papaya], from: []
kiwi-server plan ~/kiwi/fleet.yaml && kiwi-server apply ~/kiwi/fleet.yaml
```

Each network keeps its fleet, seed, CA and mesh, and every network uses its
own /16 range of addresses. The masters are linked by WireGuard on UDP port
51822 (`vpn-server.federation_port`), and each Pi-hole asks the other for the
other network's domain. A partner reaches only what is listed under `share`,
from the addresses or groups listed under `from`. Nothing is shared until you
write it. The app's *Federation…* menu shows the partner networks.

[Federation](design/federation.md) has the design.
