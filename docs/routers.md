# Routers: reaching the mesh from a whole LAN

A device on the mesh resolves every fleet name and reaches every node. A LAN
behind a router can do the same without each device running WireGuard, in one
of two ways:

- **The router is a mesh client.** It runs WireGuard with a config the master
  issued, and routes the mesh subnet (`10.8.0.0/16`) into the tunnel. This is
  the *split* tunnel: only mesh traffic goes through. The *full* tunnel sends
  everything, so the whole LAN leaves the internet through the master's exit.
- **A static route to a gateway node.** The router has no tunnel; it sends the
  mesh subnet to a node on the LAN that runs the `gateway` module, which
  forwards it through its VPN client.

Either way, one DNS rule replaces the per-host overrides you would otherwise
keep by hand: the fleet's domain (`*.home`) is forwarded to the master's
Pi-hole, which knows every host and service name. Nothing else about the
router's DNS changes.

What the LAN cannot do: reach a device *behind* another router by its LAN
address. Mesh addresses only. That is by design.

## OpenWrt

kiwi-server writes the whole configuration as a uci script:

```sh
# the router as a mesh client — the config comes from the master's wg-easy
kiwi-server openwrt fleet.yaml --wireguard secrets/router.conf          # split: only the mesh
kiwi-server openwrt fleet.yaml --wireguard secrets/router.conf --full   # full: everything

# no tunnel on the router: a static route to the gateway node m1 (needs its static LAN address)
kiwi-server openwrt fleet.yaml --via m1
kiwi-server openwrt fleet.yaml --via 192.168.1.5
```

Copy the output to the router and run it:

```sh
kiwi-server openwrt fleet.yaml --wireguard secrets/router.conf > kiwi.sh
scp kiwi.sh root@192.168.1.1:/tmp/ && ssh root@192.168.1.1 sh /tmp/kiwi.sh
```

The script installs the WireGuard package if it is missing, creates an
interface `kiwi` with the router's key and address, one peer for the master
with `route_allowed_ips` so the mesh (or everything) is routed, a firewall zone
`kiwi` that the LAN may forward into with masquerading and MSS clamping, and the
dnsmasq forward for the fleet's domain. With `--full`, dnsmasq sends every
lookup to the master's Pi-hole instead. Every section is named, so running the
script again replaces what it wrote before.

The same by hand, for reference: `network.kiwi` is an interface with
`proto='wireguard'`, `private_key`, `addresses` and `mtu`; `network.kiwi_peer`
is a `wireguard_kiwi` section with `public_key`, `endpoint_host`,
`endpoint_port`, `persistent_keepalive='25'`, `route_allowed_ips='1'` and
`allowed_ips`; `firewall.kiwi` is a zone with `masq='1'` and `mtu_fix='1'`,
plus a forwarding from `lan`; and in `dhcp`, `server='/home/10.8.0.1'` on the
dnsmasq section.

## FritzBox

Fritz!OS has a WireGuard client and static routes, but no per-domain DNS
forwarding, so the DNS rule moves to a node:

1. **Tunnel**: Internet → Permit Access → VPN (WireGuard) → Add Connection →
   connect to another WireGuard service → import the config file the master
   issued. The imported `AllowedIPs` decides split or full.
2. **Or a static route**: Home Network → Network → Network Settings → IPv4
   Routes: destination `10.8.0.0`, subnet mask `255.255.0.0`, gateway the
   gateway node's LAN address.
3. **DNS**: point the LAN at a Pi-hole. Either set the gateway node as the
   local DNS server under Home Network → Network → Network Settings → IPv4
   Settings, or let the node's own DHCP server hand itself out with the
   `dns.dhcp_*` settings and the dhcp-relay module. The node's Pi-hole carries
   the fleet's names and forwards the rest to the master.

Static routes and the tunnel can coexist; the tunnel wins for the mesh.

## Any other router

The same three facts: a WireGuard client config for the router, or a static
route for `10.8.0.0/16` to the gateway node's LAN address; and DNS for the LAN
pointed at a Pi-hole that knows the fleet, which is any node running the `dns`
module. Routers with dnsmasq underneath (DD-WRT, Tomato, pfSense's forwarder)
take the one-line forward `server=/home/10.8.0.1` as well.
