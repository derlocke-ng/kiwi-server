# The network's basics twice (design, 22 November 2026)

Step 6 of [scale.md](scale.md). The owner, 22 November: for DNS, the VPN client and the gateway a
second machine that takes over is fine; they keep no data that two versions could split. Nothing
of it is built yet.

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
- DHCP: both Pi-holes hand out leases, each from its half of the range (`dhcp_start`/`dhcp_end`
  split by kiwi-server), with the moving address as router and DNS server. A device that renews
  at the other one may get another address; nothing breaks by that.
- The moving address is one the owner gives: kiwi-server never looks for a free one on a LAN.

## Not here

- The master: a standby master already moves the devices (standby.md).
- Parts with data: they are moved by a person (authority.md, the owner's decision of 22 November).

## Order

1. The `vip` module (kiwi-modules): keepalived in a container on the host's network, its check,
   unicast peers, the password; a drill in network namespaces on one machine.
2. kiwi-server: the pairs by site, the split DHCP range, the moving address as the router and the
   DNS server handed out; the page and the app show which machine holds it.
3. On the test network, with an address the owner gives: lime and papaya; drill: one switched
   off, a device on the LAN keeps resolving and reaching the Internet.
