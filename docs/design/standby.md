# A standby master (17 November 2026)

A second machine that holds the master's place when the master is gone. Step 6 of
[recovery.md](recovery.md); the ROADMAP's "standby master".

## What it is

The same master on a second machine: the same role, stack, settings and WireGuard identity
(the server key and every peer, the copy the fleet keeps in `secrets/wg-easy/`). Its disk,
its address and its unlock are its own. In the fleet file it is a block of the master:

```yaml
  crumble:
    role: master
    standby:
      crumble-b:
        target: debian
        network: { dhcp: false, address: 192.168.2.70/16, gateway: 192.168.2.1 }
        # endpoint: 203.0.113.8    # where the nodes reach it, when not its network.address
```

`crumble-b` is a machine of the fleet like any other for `build`, `next`, `apply`, `unlock` and
`status`, but not a second master: it renders as `crumble` (same name on the mesh, same
address 10.10.0.1, same secrets). An apply of the master applies its standbys too.

## How the network moves over

Both run. A peer talks to the one it is pointed at; the same key answers on both.

- **Nodes** know both addresses. A node whose tunnel stays down for three minutes moves to the
  next address, and back to the first once that answers again for ten (`kiwi-master-switch`,
  a timer on the node).
- **Phones and laptops** have one address in their config. They follow when `wg_host` is a
  name the operator points at the standby (a dynamic DNS name). Without one they wait for the
  master.

## Not load balancing

Two masters with the same identity split the mesh: a peer on one cannot reach a peer on the
other. So a standby is for when the master is gone, not to share the load. Two masters that
share the load are two networks with a federation link.

## Limits

- The standby is only as new as the last apply or enroll: a device added in wg-easy's own
  page reaches it with the next apply.
- When the master comes back after the nodes moved, the devices still on the master do not
  reach the nodes until the nodes move back (ten minutes).
- A standby at home has no Tor: Tor does not leave from a home connection.

## Steps

1. The `standby` block, the render as the master, the identity, apply. Nodes switch.
2. A drill: master off, nodes on the standby; master back, nodes back.
3. The control node and the page: the standby in the machine list, its state.
4. Phones and laptops: a dynamic DNS name the control node points at the standby.
