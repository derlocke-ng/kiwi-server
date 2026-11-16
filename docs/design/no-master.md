# A network without a master

For a home that starts with one machine and no rented server: a phone user with the
starter stick, or someone trying Kiwi out. The starter design leaves this open
([starter.md](starter.md), "No master yet").

## What happens today

Tried on 16 November 2026 with a fleet of one node (`cloud` preset) and no master:

1. `validate` passes, but says the node waits for `enroll`, "once its master runs".
2. `script` stops: the `vpn-client` part needs the WireGuard config that the master's
   VPN server issues.
3. Without `vpn-client` it stops again: "the stack setting vpn_ip (this host's mesh
   address) is required — cloud, vault are reachable at the mesh address".
4. Without a backup machine, `backup` stops too. That is right: it needs somewhere to go.

So a node is built around its VPN address. Its names (`cloud.mango.home`) point at that
address, and the master's Pi-hole answers for them.

## What is built (kiwi-server 2.149.0, kiwi-modules 0.61.1)

- The stack setting `vpn: false` on a node: no VPN client (left out of the presets and of
  what parts require), and its fixed home address stands in for its VPN address.
- The first-run questions (page, app, `kiwi-server init`): "No master yet". The fleet then
  has no master; every machine at home gets `vpn: false` and needs a fixed address; the
  first one runs `dns` (Pi-hole) and answers for every machine's names with home
  addresses. A gateway is refused: it leads into the VPN.
- The page and the app say what it means: no "master first", and no VPN for a phone (the
  Devices page says how a phone finds the names at home).
- Tried: the questions on a local page, the node's stack rendered (nginx on 80/443 at
  home, Pi-hole's records). Not yet tried on a real machine.

- The home subnet stands in for the VPN's subnet too, so what is only for the network
  itself (the control page) lets the home network in.
- `kiwi-server add FLEET node` in such a network writes `vpn: false` and asks for the
  fixed address. `kiwi-server add FLEET master --address A` puts every machine at home back
  onto the VPN (`vpn: true`, its VPN client back, first in its module list), comments kept;
  then: build and install the master, enroll and apply each machine.
- A drill: `tests/drills/starter-vm.sh alone` (the starter in a local VM, "No master yet",
  "Set it up", the names and the control page from the home network, a hard reset).

Still to build: "Add a master" as a button on the page and in the app.

## Proposal

**The home address stands in for the VPN address.** A fleet with no master gives each
node `vpn_ip` = its fixed address at home. Every part that listens "at the mesh
address" then listens on the home network. Nothing else in the modules changes.

- `vpn-client` is left out of the presets while there is no master. `enroll` is not a
  step, and the questions do not ask "where does the master run".
- **Names:** the first node runs `dns` (Pi-hole) and answers `*.home` with home
  addresses. The router hands it out as the DNS server; the page says how, as it does
  for the gateway today. A device that is not told keeps working by address.
- **Certificates:** the fleet CA, as now. A phone trusts it the same way.
- **From outside:** nothing. That is the trade-off, and the page says it plainly: "Your
  services open at home only. A master makes them open from anywhere."
- **Backups:** to a second machine at home, or a USB disk (`backup-server` on a node),
  as now. With one machine only, the page warns that nothing is kept elsewhere.

**When a master comes** ("Add a master" on the page):

1. The master is added and installed as today (the hoster's sign-in, or install media).
2. Each node is enrolled; `vpn-client` comes back into its parts; `vpn_ip` becomes its
   VPN address.
3. The names move to the master's Pi-hole. The node's own `dns` stays for the home
   network, and now forwards `.home` to the master as other nodes do.
4. Apply everything once, the master first. Bookmarks keep working: the names stay
   the same, only their addresses change.

## Open questions

- Should a node keep answering on its home address after the master comes? That is
  handy at home and is what a gateway does already, but it is one more door.
- Hidden services without a master: Tor runs on the master only today
  ([onion.md](onion.md)). Tor on a node would give access from outside without renting
  anything, but it is a second design.
- The first-run questions get a third answer under "Where it runs": "No master yet".

## What to build, in order

| Piece | Where |
|---|---|
| `vpn: false`: the home address as the VPN address, no VPN client | built: kiwi-modules 0.61.0 |
| "No master yet" in the questions; what it means on the page and in the app | built: 2.149.0 |
| The warning when one machine keeps everything and nothing is backed up elsewhere | the page's "If something breaks" says it already |
| "Add a master" for a network that has none | built in `kiwi-server add`; the page and the app: to build |
| A drill: one node, no master | built: `starter-vm.sh alone` |
