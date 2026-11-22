# The network's basics twice (design, 22 November 2026)

Step 6 of [scale.md](scale.md). The owner, 22 November: for DNS, the VPN client and the gateway a
second machine that takes over is fine; they keep no data that two versions could split. Steps 1
and 2 are built (2.296.0, kiwi-modules 0.117.0); step 3 runs on the test network: lime and papaya
share 192.168.2.70. Drill (tests/drills/vip.sh): the address went to papaya about a second after
lime's keepalived stopped and back in four, and a name asked of it every second was answered
through both switches.

## What a site gets

Two machines at a site run DNS (Pi-hole) and the gateway, both at once. A **moving address**
(VRRP, keepalived) belongs to one of them at a time; every device on the LAN gets that address
as its DNS server and its default gateway, and keeps it when one machine stops. The switch takes
three to four seconds; connections through the gateway that were open start again.

```yaml
hosts:
  lime:   { node: { modules: [dns, gateway, vip], vip: { address: 192.168.2.70 } } }
  papaya: { node: { modules: [dns, gateway, vip], vip: { address: 192.168.2.70 } } }
```

- The pair: the machines of one site whose `vip.address` is the same. The first by name holds
  the address while it is well; the other waits.
- Well means: its Pi-hole answers a name, and its gateway routes. A machine that fails its check
  gives the address up at once; the other takes it within seconds.
- The two talk to each other by unicast at their own LAN addresses (no multicast on the home's
  LAN), with a password from the seed.
- DHCP: only the machine that holds the moving address hands out leases: its dhcp-relay relays the
  LAN's DHCP to its Pi-hole while it holds the address (kiwi-server fills in `dhcp-relay.follow`).
  Both Pi-holes have the whole range, with the moving address as router and DNS server; the one
  that takes over gives a device the address it asks for again, since Pi-hole's DHCP is
  authoritative. Halves of the range on both at once were the first plan: each Pi-hole then
  refused the other's leases and a device got another address at nearly every restart
  (tests/drills/dhcp-pair.sh, 22 November 2026: two addresses and 14 refusals in 7 asks; with the
  relay following the address, the same address through every switch).
- The moving address is written in the fleet file: kiwi-server never looks for a free one on a LAN.
  On the test network: 192.168.2.70 (the test machines' range starts at .60).

## Not here

- The master: a standby master already moves the devices (standby.md).
- Parts with data: they are moved by a person (authority.md, the owner's decision of 22 November).

## Order

1. The `vip` module (kiwi-modules): keepalived in a container on the host's network, its check,
   unicast peers, the password; a drill in network namespaces on one machine.
2. kiwi-server: the pairs by site, the split DHCP range, the moving address as the router and the
   DNS server handed out; the page and the app show which machine holds it.
3. On the test network, lime and papaya at 192.168.2.70; drill: one switched
   off, a device on the LAN keeps resolving and reaching the Internet.
