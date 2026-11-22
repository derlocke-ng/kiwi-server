# Moving the owner's network to v2 (plan)

Written 22 November 2026 for a move in the week after. The owner decides every step on the live
v1 network (its master, the M1, sh3, the old kiwi-node); nothing there is touched without his yes.

## Where it ends up

| Machine | Hardware | Runs |
|---|---|---|
| master A | VPS (the network's own) | VPN server, exit through Mullvad, the relay to the rings at home |
| master B | VPS (v1's master once v1 is off) | a second network of its own, linked to A: the second exit, and to try things on |
| gw1 | Rock Pi E (2 ports) | gateway, Pi-hole, the moving address (vip), a ring, half of DHCP |
| gw2 | Odroid M1 | gateway, Pi-hole, vip, a ring, half of DHCP, control node, cache, status page |
| sh3 | x86, 16 cores, 60 GB | cloud, vault, opencloud, immich, paperless, git (VPN + Tor, no Kanidm), downloader, games, backups |

Later: a backup server with the 18 TB disk, an offsite node.

## What works today

| Need | State |
|---|---|
| VPN, phones, laptops | built; drilled with VMs and stand-in clients, never with a real phone over 5G |
| Master's backup and rebuild | built; RAM master rebuilt with the same keys (drilled 11 and 17 Nov) |
| Two exits to choose from | one exit per network today; a linked network as the exit is drilled, a choice per group or device is not built |
| Two gateways, one address (vip) | drilled on lime + papaya; DHCP from both never ran on a real LAN |
| M1 / Rock Pi E | Armbian images exist for both (trixie); every image the gateway and control modules use has arm64 builds (checked 22 Nov); the encrypted Armbian image is built by hand |
| Control + ring + cache on one machine | done on papaya (x86), not on an ARM board |
| sh3's services | every module built; git and Nextcloud logins drilled through Tor; Vaultwarden and OpenCloud are not offered as hidden services |
| Backups, restore, offsite | drilled (restore onto another machine, offsite against stand-ins); the backup before an update was broken until 2.300.0, now seen working on quince and lime |
| Unlock | two rings, either enough, drilled; manual unlock over SSH before boot drilled |
| Taking over v1's data | not built, not rehearsed |

## sh3 today (looked at 22 Nov, read only)

- Debian 12. Root on the 466 GB NVMe (222 GB used, most of it the test VMs), asks its passphrase
  (dropbear); the three data disks open with key files from the root disk.
- nvme_2tb (1.8 T, 270 G used): Nextcloud's data (docker/knnc-data), share (118 G), virt.
- hdd_5_2 (4.5 T, 1.8 T used): nc_dlcom_data, share. hdd_18 (17 T): 99 % full.
- Running: Nextcloud AIO (mastercontainer), Vaultwarden (~/docker/knvault, 1 MB), gluetun,
  nginx, portainer, Enshrouded. Kept but stopped: Abiotic Factor (5.6 G), Satisfactory (4.3 G),
  three Minecraft worlds (4 G), mods. Also ~/build (Armbian build tree), ~/dev, ~/tangpod, and
  the VMs: the owner's id-* VMs and the test machines medlar, quince, lychee, crumble-b, damson.
- v2's cloud module runs the same AIO images and volume names without the mastercontainer, and
  the games module knows all five games. Whether each takes v1's data as it is: to be rehearsed.

## Before the move (the owner)

1. Recycle hdd_18 until everything on sh3 fits on it, then copy everything there and take it out:
   the cold copy while sh3 is reinstalled, later the backup server's disk.
2. Decide the disks for the new sh3 (the spare 5 TB with or instead of the 4.5 TB).
3. A Nextcloud AIO backup and a Vaultwarden export, on the cold copy too.

## What I close this week, in order

1. DHCP from both gateways and a device behind the moving address: drilled on a LAN of its own
   (VMs on an isolated network on sh3, not the house LAN, where v1's DHCP runs).
2. The rehearsal: v1's data into v2 on a VM with copies (Nextcloud's volumes and data folder,
   Vaultwarden's folder, a game save, Forgejo), each checked after.
3. The exit per VPN group: a group of master A's network that leaves through master B (the
   linked network's exit, which works for a whole network today). Until then a device can hold
   both networks' WireGuard configs and switch in the app.
4. The encrypted Armbian image for the Rock Pi E and the M1, as a step of kiwi-server.
5. The control node, ring and cache together on an ARM board (the Rock Pi E first).
6. The store's lock for people, not for the sync (notes/TODO.md).

## The move

1. Master A on its VPS, a new fleet (new keys, a new 10.X subnet, so v1's 10.8 and `.kiwi` stay
   apart and a device can hold both).
2. gw1 (Rock Pi E) next to v1: gateway, Pi-hole, ring, no DHCP yet.
3. sh3 reinstalled (Debian 13, encrypted, rings on gw1 and later gw2 plus its key by hand), its
   parts applied, the data taken back from the cold copy as rehearsed.
4. DHCP: v1's DHCP off, gw1's on.
5. gw2: the M1 reinstalled as gateway, control node, cache and status; the vip pair; DHCP halves.
6. The devices enrolled in A's VPN; v1's VPN stays until the last one has moved.
7. v1's master off; its VPS becomes master B, linked to A.

## The owner decides

1. Which VPS becomes master A: sorbet (the test network then uses crumble) or a new one.
2. The network's domain: `home` (the default) or another; not `kiwi` while v1 runs.
3. The two exits: Mullvad on both (two countries) or B's own address.
4. After a power cut every machine waits for a ring. One ring has to come up by itself: a small
   board without disk encryption holding a ring, a ring away from home through the master's
   relay, or a key typed by hand on one gateway (`kiwi-server unlock … --manual`).
5. sh3's VMs (id-*, the test machines): kept, moved, or made again after the reinstall.
