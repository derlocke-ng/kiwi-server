# Groups of the home network, and VLANs (19 November 2026)

The owner, 18 November: "many companies use those, so we should keep that in mind to
integrate/support that (kinda like the subnet-group filter)". Step 1 is built in 2.217.0
(kiwi-modules 0.96.0, kiwi-stack 0.9.17), step 2's tagged interfaces in 2.223.0 (kiwi-modules
0.98.0, kiwi-stack 0.9.18); a service on one VLAN only and step 3 are proposals.

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
  ports, looked at before a published container port's DNAT; and since 2.218.0 the gateway, for
  what it routes from the home network into the mesh (machines at another site, nodes by their
  mesh address): its chain `KIWI_GW_LAN` lets a group through to what it was given only.
- Proven on a kernel by `tests/drills/lan-netns.py`: rootless namespaces, a node with the table
  kiwi-stack writes, a client in a group and one in none, a published port, and the gateway's
  chain in front of a mesh.
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

Built in 2.223.0, the same on every system: `kiwi-stack apply` makes the tagged interface
(`eth0.20`, or `vlan20` where the name would be longer than 15 characters) on the card named
(`interface:` of the VLAN, else the machine's `network.interface`, else the card of the default
route), gives it its address and no default route, and writes `/etc/kiwi-server/vlans`.
`kiwi-vlans.service` makes them again at boot, after their card and before the network and the
containers. A VLAN that is gone or changed loses its interface first. One set of `ip` commands
instead of NetworkManager keyfiles and networkd files: the machines run either, and an interface
made this way is left alone by both.

`validate` checks each VLAN: a number from 1 to 4094, once; a private IPv4 address with its
network (`/24`), not in the machine's own network, the mesh or another VLAN; a name made of
small letters, digits and `-`. A VLAN named as a group of the home network must have its address
in that group's subnet: the group's rules (step 1) are then what the VLAN's devices may open on
the machine.

Proven without a switch in `tests/drills/vlan-netns.py`: a Linux bridge with VLAN filtering, the
node on a trunk port (untagged 1, tagged 20), a device on an access port of VLAN 20 reaches the
node there, an untagged device with an address of VLAN 20 does not, the group's ports hold.

A service answers at one VLAN only since 2.239.0 (kiwi-modules 0.101.0): every module that
publishes ports has the setting `listen` ("Only at this address", Advanced), and its ports are
bound to that address. Left: a second Pi-hole for an IoT network that knows no other names
(a module runs once per machine). On the control page and in the app a machine's VLANs are a
setting (Network, *Tagged VLANs*) since 2.224.0.

## Step 3: the gateway per VLAN

The gateway module routes between VLANs only where *who may reach what* allows it, and serves
DHCP and DNS per VLAN when the home's router does not.

## Tests without a managed switch

As `tests/drills/translate-netns.py` does for the link: rootless network namespaces on this
computer, a Linux bridge with `vlan_filtering`, VMs or namespaces on tagged and untagged ports,
the node's rendered firewall and nginx lines.
