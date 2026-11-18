# Groups of the home network, and VLANs (19 November 2026)

The owner, 18 November: "many companies use those, so we should keep that in mind to
integrate/support that (kinda like the subnet-group filter)". Step 1 is built in 2.217.0
(kiwi-modules 0.96.0, kiwi-stack 0.9.17); steps 2 and 3 are proposals.

## What there is now

Who may reach what knows two kinds of "who": a group of the VPN (a range of client addresses
on the master) and a linked network. The home network itself is trusted as a whole: a client at
home reaches every node's services across the switch, by the node's LAN address (docs/network.md,
DNS), and only the master's firewall and the nodes' nginx filter, both on VPN addresses.

## Step 1: a group of the home network

A third kind of "who", in the same words as the other two:

```yaml
defaults:
  lan:
    groups:
      iot:    { subnet: 192.168.20.0/24, reach: [cloud.papaya] }
      office: { subnet: 192.168.10.0/24, reach: [apps], admin: false }
```

- What is not in a group stays as today: the home network reaches everything. A fleet without
  `lan.groups` changes nothing.
- No VLAN hardware needed: any subnet the home routes is a group, a guest Wi-Fi with its own
  range as much as a VLAN.
- Enforced where the packets are: each node's nginx (the same allow and deny lines as for a VPN
  group, `reverse-proxy.access`), and each node's nftables table `kiwi_lan` (kiwi-stack) for the
  ports, looked at before a published container port's DNAT. Not yet: the gateway's forwarding
  for what it routes from the home network into the mesh (masters, machines at another site).
- Proven on a kernel by `tests/drills/lan-netns.py`: rootless namespaces, a node with the table
  kiwi-stack writes, a client in a group and one in none, a published port.
- `kiwi-server access FLEET iot cloud.papaya`, the page's and the app's *Who may reach what*:
  one more row per group, as for the VPN's groups. `tests/drills/access.py` gets a client in a
  group's range.

## Step 2: a machine on several VLANs

```yaml
network:
  address: 192.168.1.30/24          # untagged, as now
  vlans:
    - { id: 20, address: 192.168.20.5/24, name: iot }
```

kiwi-server makes the tagged interfaces (NetworkManager keyfiles on CoreOS and uCore, networkd
on Debian), with no default route on them. A VLAN with a group of the same name is that group's
range. A service can be bound to one VLAN only (its module's `listen`), for example a Pi-hole for
the IoT network that knows no other names.

## Step 3: the gateway per VLAN

The gateway module routes between VLANs only where *who may reach what* allows it, and serves
DHCP and DNS per VLAN when the home's router does not.

## Tests without a managed switch

As `tests/drills/translate-netns.py` does for the link: rootless network namespaces on this
computer, a Linux bridge with `vlan_filtering`, VMs or namespaces on tagged and untagged ports,
the node's rendered firewall and nginx lines.
