# Federation (proposal, 11 November 2026)

Two networks give each other access to some of their services, nodes or people. Not a
merger: each keeps its fleet, seed, CA, master and mesh; each decides alone what it shares.
Multi-hop (one network leaving the internet through another) rides the same link since
2.91.0 (The exit, below).

## The link

One WireGuard tunnel between the two masters, its own interface beside wg-easy (wg-easy routes
only a client's own address to it; the link carries the other mesh's /16). A small
`federation` module: per partner a key pair, the partner's master as endpoint, `AllowedIPs` its
mesh. Meshes never overlap: every network has its own /16 (10.9, 10.10, 10.11 …).

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
```

The sharing master's firewall lets the partner's `from` reach only the shared nodes, and only
on 443 for a service; the node's nginx admits the partner only to the shared names. Nothing is
shared that is not listed.

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
shared yet. Between operators: `federate A/fleet.yaml --invite` writes a file for the other
side, `--accept FILE` answers it.

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

## Later: multi-hop per client

Like Mullvad's multihop: a client picks its entry and its exit (its own master, a partner's,
or a provider location), instead of the master deciding for every client.
