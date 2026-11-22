# Moving the owner's network to v2 (plan)

Written 22 November 2026 for a move in the week after. The owner decides every step on the live
v1 network (its master, the M1, sh3, the old kiwi-node); nothing there is touched without his yes.

## Where it ends up

| Machine | Hardware | Runs |
|---|---|---|
| master A | VPS (the network's own) | VPN server, exit through Mullvad, the relay to the rings at home |
| master B | VPS (v1's master once v1 is off) | a second network of its own, linked to A: the second exit, and to try things on |
| gw1 | Rock Pi E | gateway, Pi-hole, the moving address (vip), a ring, DHCP while it holds the address |
| gw2 | Odroid M1 | gateway, Pi-hole, vip, a ring, DHCP while it holds the address, control node, cache, status page |
| sh3 | x86, 16 cores, 60 GB | cloud, vault, opencloud, immich, paperless, git, downloader, games, backups |

Later: a backup server with the 18 TB disk, an offsite node.

Through Tor: git only, nothing else, and only for named keys (`kiwi-server onion FLEET git.sh3
--client NAME`: Tor's client authorization; without the key the service cannot even be found).
Git's own accounts, no Kanidm. Everything else over the VPN only.

## What works today

| Need | State |
|---|---|
| VPN, phones, laptops | built; drilled with VMs and stand-in clients, never with a real phone over 5G |
| Master's backup and rebuild | built; RAM master rebuilt with the same keys (drilled 11 and 17 Nov) |
| Two exits to choose from | a linked network as the exit is drilled; per VPN group built (2.302.0), not yet tried on the masters |
| Two gateways, one address (vip) | drilled on lime + papaya; DHCP only from the holder, drilled in podman, never on a real LAN |
| M1 / Rock Pi E | every image the gateway and control modules use has arm64 builds (checked 22 Nov); the encrypted image is built by kiwi-server on sh3 (2.303.0), not yet booted on a board |
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

1. ~~DHCP from both gateways~~: halves on both broke devices' addresses; now only the holder of
   the moving address hands out leases (kiwi-modules 0.120.0), drilled 14 of 14
   (tests/drills/dhcp-pair.sh). Open: a device behind the moving address on a real machine.
2. The rehearsal: v1's data into v2 on a VM with copies (Nextcloud's volumes and data folder,
   Vaultwarden's folder, a game save, Forgejo), each checked after.
3. ~~The exit per VPN group~~: `federate A B --exit family,phones` (2.302.0); a device chooses
   its way out by its group. Open: tried on the two test masters.
4. ~~The encrypted Armbian image as a step~~: `armbian.builder: sh3` and `kiwi-server build`
   (2.303.0); built for the Rock Pi E in five minutes. Open: flashed and first started (yours:
   the SD card), then the pair with lime on real ARM boards.
5. The control node, ring and cache together on an ARM board (the Rock Pi E first).
6. ~~The store's lock for people, not for the sync~~ (2.303.0).

## The move

1. Master A on its VPS, a new fleet (new keys, a new 10.X subnet, so v1's 10.8 and `.kiwi` stay
   apart and a device can hold both).
2. gw1 (Rock Pi E) next to v1: gateway, Pi-hole, ring, no DHCP yet.
3. sh3 reinstalled (uCore, encrypted, rings on gw1, gw2 and the Banana Pi, plus its key by hand),
   its parts applied, the data taken back from the cold copy as rehearsed.
4. DHCP: v1's DHCP off, gw1's on.
5. gw2: the M1 reinstalled as gateway, control node, cache and status; the vip pair.
6. The devices enrolled in A's VPN; v1's VPN stays until the last one has moved.
7. v1's master off; its VPS becomes master B, linked to A.

## Decided (the owner, 22 Nov 2026)

1. Which VPS becomes master A: later. Master B is a network of its own, on another subnet, whose
   web services A's devices reach too (the link's `share`): for hosting and trying web services.
2. The domain: `home`.
3. Both exits through Mullvad, each rotating among different servers: `server_cities` per master,
   a random one of them at every connect, a new connect every morning. v1's master does it with
   `00 07 * * * docker restart kmvpn-client` and six cities (checked 22 Nov); v2: vpn-client
   `reconnect`.
4. After a power cut: a Banana Pi M1/M2 on a battery holds a ring. It is encrypted, on a LAN
   cable, and unlocked by hand once (over SSH at its boot system, `kiwi-server unlock --manual`,
   or a keyboard); the battery keeps it up through a power cut, so it opens the others when the
   power is back. Unlocked by hand again only when the battery ran empty. No Wi-Fi (its password
   would lie open in the boot partition). The second way is a person, on the control page's
   "Unlock it" over the VPN or at home. Tang for 32-bit ARM: kiwi-modules 0.121.1. Open: the
   first encrypted `bananapi` image, and Docker on 32-bit Armbian.
5. sh3's VMs: moved, and backed up first.
6. The new sh3 (or sh4, or a fruit's name) runs uCore, not Debian: an encrypted install bound to
   the rings, its VMs on libvirt there. Its disk opens by SSH today already (dropbear).
7. Slowly: one step at a time, each checked before the next.
