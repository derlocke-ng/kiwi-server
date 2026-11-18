# Federation (proposal, 11 November 2026)

Two networks give each other access to some of their services, nodes or people. Not a
merger: each keeps its fleet, seed, CA, master and mesh; each decides alone what it shares.
Multi-hop (one network leaving the internet through another) rides the same link since
2.91.0 (The exit, below).

## The link

One WireGuard tunnel between the two masters, its own interface beside wg-easy (wg-easy routes
only a client's own address to it; the link carries the other mesh's /16). A small
`federation` module: per partner a key pair, the partner's master as endpoint, `AllowedIPs` its
mesh. Meshes never overlap: every network has its own /16 (10.9, 10.10, 10.11 …), and its own
domain, since each side's DNS sends the other's domain to the other (`federate` refuses both).

## What is shared

In the sharing network's fleet, per partner:

```yaml
federation:
  kiwi2:                         # the partner, by any name
    endpoint: 188.68.38.110      # its master's wg_host
    mesh: 10.10.0.0/16
    domain: internal
    share: [cloud.papaya, lychee]   # a service (its name on a node) or a whole node
    from: [devices]              # their groups or addresses that may use it; empty: their whole mesh
    groups: {devices: 10.10.3.0/24}   # their groups, as federate or their card says
```

The sharing master's firewall lets the partner's `from` reach only the shared nodes, and only
on 443 for a service; the node's nginx admits the partner only to the shared names. Nothing is
shared that is not listed.

## What each side sees of the other

A network's `federation.yaml` says what it opens to a partner, not what the partner opens to it:
that is in the partner's file. So each master's DNS says it over the link. It answers a TXT record
`<the partner's mesh>._kiwi-share.<its domain>` (for example `10-9-0-0-16._kiwi-share.internal`)
with the names from that partner's `share` (`-` when it opens nothing), and
`_kiwi-master.<its domain>` with its own name. The partner's status asks both from its master
(the master forwards the partner's domain to the partner's DNS already), and the picture of the
network draws what the partner opens under the partner's box. These are names, not secrets: anyone
on the partner's mesh who asks gets them.

## Names and certificates

Each Pi-hole forwards the partner's domain to the partner's DNS over the link. Services keep
their own fleet's CA; a partner's CA is trusted only with `tls.name_constraints` limiting it to
the partner's domain, and enrol bundles carry the partners' CAs.

## User to user

`from` takes a single address, so one person's device can be let in to one service. Real
identities (a person, not a device) come with the control plane's users, later.

## Setting it up

`kiwi-server federate A/fleet.yaml B/fleet.yaml` when one operator holds both (the test case:
sorbet and crumble): the pair's keys into both stores, the blocks into both fleets, nothing
shared yet. Between operators: `federate A/fleet.yaml --invite CARD` writes a card for the
other side (built in 2.164.0), `--accept CARD` takes theirs and writes this side's card beside
it. A group name in `from` becomes its range through `groups`, which `federate` and the cards
fill in.

## Tests

A drill like `tests/drills/multihop.py`: a client of A reaches the service B shares, not the
one B does not share, not B's other nodes; B's names resolve through A's Pi-hole; the link
comes back after a reboot of either master (crumble is a RAM master).

## The exit

Decided on 14 November 2026, after the owner called the first plan weird: it had the entry
master's VPN client log in to the exit master's VPN server as one of its clients (a group
with no reach, `vpn_provider: custom`). That needed a second tunnel, a VPN client container
on the entry master, and an enrolment across networks.

The link is there already, so the exit rides it. Per partner, two keys:

```yaml
kiwi2:
  exit: true         # in the first network's file: we leave through that partner
```

```yaml
kiwi:
  internet: true     # in the second network's file: that partner may leave through us
```

Entry master: the partner's peer on `wg-fed` also takes `0.0.0.0/0`; what comes in on `wg0`
looks in a table of its own (default over `wg-fed`, the meshes and the machine itself thrown
back to the main table); it is sent on as the master's own address, so the exit sees one
address; a rule drops what the clients send towards the master's own uplink, which is the
kill switch. Exit master: what arrives from the partner's master may go on to any address
that is not private, masqueraded like its own clients. Both keys have to be set for a
packet to pass: `exit` alone leaves the first network without internet, `internet` alone
does nothing.

One exit per network; two networks cannot leave through each other. The older way stays
for an exit that is not a Kiwi network (any WireGuard provider, `vpn_provider: custom`).
Not done: the entry network's DNS lookups still leave from its own master.

## The same mesh or the same domain: translated at the link

Decided on 18 November 2026 (the owner: "translate is the best idea"), built in 2.216.0
(kiwi-modules 0.95.0). Two networks made with the same defaults (both `home`, both 10.8 before
2.215.0 gave each its own /16 at random) can still link, and neither renumbers or renames
anything.

**How each side sees the other.** A partner whose mesh or domain is this network's own gets an
alias here, chosen by this side and written in its `federation.yaml`:

```yaml
kiwi2:
  mesh: 10.9.0.0/16          # theirs, as it is
  domain: home
  seen_as:
    mesh: 10.209.0.0/16      # a free /16 here (not ours, not another partner's, not the LAN)
    domain: kiwi2.home       # <their name>.<their domain>: inside their CA's name constraint
```

`federate` picks the alias (a free /16 at random, the partner's name under the shared domain)
and puts it on the card, so the partner learns how it is seen. Without a collision there is no
`seen_as`, and everything stays as it is today.

**Addresses.** A master translates only the connections it starts towards a partner, into the
partner's real space: the wire carries the partner's real address as destination and the alias
the partner gave this network as source. On A, for a client of A that opens `10.209.0.5`:
PREROUTING maps `10.209.0.0/16` 1:1 onto `10.9.0.0/16` (iptables `NETMAP`) and marks the
packet; the mark routes it into `wg-fed` (a table of its own: A's own 10.9 stays on `wg0`);
POSTROUTING on `wg-fed` maps A's source onto the /16 B sees A as. B receives a packet from an
address of its alias for A, to its own real address: nothing to do there but its rules (`from`
is in that space). The answer goes back untouched; A's conntrack undoes both mappings. The
partner's peer on `wg-fed` allows its real mesh and its alias here.

**Names.** A's Pi-hole forwards `kiwi2.home` to a small DNS proxy on A's master (part of the
link, one container): it asks B's DNS for `x.home` and gives back `x.kiwi2.home` with the
address moved into `10.209`. Pi-hole's own address rewriting (`alias=`) cannot do it: it would
change A's own names too.

**Certificates.** B adds the alias names to the certificates of what it shares with A
(`cloud.papaya.kiwi2.home` beside `cloud.papaya.home`), from A's card. A trusts B's CA for
`kiwi2.home` only, so B's CA can never sign one of A's own names. An alias domain inside the
shared domain stays within B's own name constraint. `federate` refuses an alias that is one of
A's own host names.

**What does not change.** `share` and `from` are written in the partner's own words, as now;
the TXT records, the picture and the access manager show the alias names.

**Tests.** `tests/drills/translate-netns.py` runs the federation part of each master's rendered
start.sh in throwaway network namespaces on this computer's kernel (no root), with a real
WireGuard link: a client of A reaches B's web at 10.209.0.5 while A's own node keeps 10.9.0.5,
web sees B's address for A, a port B does not share stays shut, and A's link DNS gives
`web.gate-b.home` at 10.209.0.5. Two details it found: B answers DNS from 10.9.0.1, which is
A's own address (`accept_local` on `wg-fed`), and A's own lookups need a route for the alias
before NETMAP turns it into the partner's.

## Moving a whole network to another mesh or domain

For just in case (the owner, 18 November): `kiwi-server move FLEET --mesh 10.X.0.0/16`
and/or `--domain D`, as a plan first and then an apply. Not built yet. What it has to carry:

- **Mesh:** every node's and device's address in it, the groups' ranges, the access rules
  written as addresses. While it moves, the master's VPN serves both meshes (each peer allowed
  its old and its new address), so a node is never cut off; devices need their new config
  (a new QR code) once.
- **Domain:** every host name changes, and with it everything derived from a host name: each
  generated password and each data disk's key (`derive_secret(hostname, …)`). Before anything
  else the move pins each of them under the new name with its old value, so no password and no
  disk key changes. Then the certificates (the CA's name constraint too), the SSH host
  certificates, the DNS records, `known_hosts`, the backup repositories named after a host,
  the pins already kept, the partners' cards.

## Later: multi-hop per client

Like Mullvad's multihop: a client picks its entry and its exit (its own master, a partner's,
or a provider location), instead of the master deciding for every client.
