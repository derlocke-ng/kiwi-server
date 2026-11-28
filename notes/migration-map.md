# The map for the move (first version, 28 Nov 2026)

What the network has now, what each machine and service becomes, and the order. Written from
the notes, the fleet files and what was read on sh3 and the v1 master; nothing was scanned on the
LAN. Rows with **?** need the owner's word. The steps that were rehearsed, with their commands,
stay in [migration.md](migration.md).

## Decided

- **Nextcloud starts fresh** (the owner, 28 Nov): the `cloud` module on the new sh3 with new
  accounts, and the old files, calendars and contacts are imported by hand. The takeover of
  AIO's volumes (rehearsed 27 Nov) is not used; Vaultwarden's and the games' takeovers are.
- **Clients are managed too**: this desktop, Bluefin on the gaming workstation, laptops, and the
  pentest systems on portable SSDs, by an agent on each (below).
- From 22 Nov: a new network (new keys, a new 10.X mesh, the domain `home`), master B = A's
  standby, both exits Mullvad with rotating cities, the Banana Pi ring on a battery, sh3 on uCore,
  one checked step at a time.

## Machines

### Servers and boxes that run things

| Machine | Hardware | Now | In the new network |
|---|---|---|---|
| v1 master | VPS | v1: VPN 10.8/16 `.kiwi`, Mullvad exit (rotates at 07:00) | master B, A's standby, at the end |
| sorbet | VPS (netcup) | test master (`~/kiwi`) | master A? or a new VPS **?** |
| crumble | VPS (netcup) | test master of `~/kiwi-2`, in RAM | test only, or cancelled **?** |
| M1 | Odroid M1 | v1 gateway, Pi-hole, DHCP for the home | gw2: gateway, control node, cache, status page (moves last) |
| rockpi | Rock Pi E | test gateway, pair with lime (vip .69) | gw1: gateway, Pi-hole, DHCP while it holds the moving address |
| lime | Odroid N2+ | test gateway, ring `home` | a ring? a third gateway? spare? **?** |
| papaya | mini PC, 954 GB SSD | test control node, cloud, cache, ring `infra` | **?** (a second server, the backup server, or test) |
| sh3 | x86, 16 cores, 60 GB, 4 data disks | v1: Nextcloud AIO, Vaultwarden, games, VMs | reinstalled on uCore: the services below |
| Banana Pi | M1/M2, 32-bit ARM | (planned) | a ring on a battery, for power cuts |
| router | 192.168.2.1 | the Internet | stays as it is **?** (its make, and whether it hands out addresses today) |
| OpenWrt box | 192.168.2.251 (in your desktop-manager's config) | **?** | **?** |
| backup server | the 18 TB disk | (later) | the copies of everything |
| offsite node | at someone's place | (later) | backups + a gateway for them |

### Clients (people's computers)

| Client | Where it runs | Now | In the new network |
|---|---|---|---|
| this desktop | Bluefin | v1 VPN?, test fleet's admin computer | a client with the agent, the admin's computer |
| lemonaide | the gaming workstation's Samsung NVMe, uCore | test node: the AI (Studio) | a node in the new network: the AI |
| Bluefin-DX | the same workstation's 2 TB disk | the owner's gaming system | a client with the agent |
| pentest systems | portable SSDs, booted on several machines | **?** (how many, which OS, encrypted?) | clients, known by the system on the SSD, not by the box |
| laptops | **?** | **?** | clients |
| phone pixel-10a | Android | opens the store (an opener) | a device with the app |
| other phones, tablets | **?** | **?** | devices |
| printers, TVs, smart home | **?** | **?** | DHCP, maybe a fixed address |

## Services

What v1 runs today, and what was built and tested. **Keep** is my proposal from your list of 19
Nov ("what he hosts himself"); please strike or add.

| Service | v1 today | Proposal |
|---|---|---|
| VPN, exit through Mullvad, Pi-hole, DHCP | yes (master, M1) | keep |
| Nextcloud (with Collabora, Talk, full-text search, whiteboard) | yes, sh3 | keep, fresh |
| Vaultwarden | yes, sh3 | keep, data taken over |
| Game servers (Enshrouded live; Abiotic, Satisfactory, 3 Minecraft kept) | yes, sh3 | keep, worlds taken over |
| Backups, and an offsite copy | v1: AIO's own borg | keep (restic to the backup server) |
| Jabber (public), nostr relay, pastebin, the dlhost.eu website | no | keep (your list) |
| git (Forgejo) on the VPN and through Tor | no | keep (your list) |
| The local AI | test | keep (lemonaide) |
| Disk unlock rings | test | keep (gw1, gw2, Banana Pi) |
| OpenCloud, Immich, Paperless, downloader, SFTP | test only | **?** |
| Accounts (Kanidm), 2FAuth, Firefox Sync, BTCPay, the hub, smartfritz | test only | **?** |
| Portainer | v1 and test | **?** (the page shows the containers already) |

## The client agent

What it is for (the owner, 28 Nov): see which clients run, and push what they need. From the
design (docs/design/ui.md, "Desktops and laptops as clients"; unlock.md for their disks) and your
desktop-manager plans of April:

- **A client is the installed system, not the box.** It has a key of its own, made when it joins
  (`kiwi-server enroll`, or a link like a phone's). A pentest SSD booted on the workstation shows
  as "pentest-ssd-1, running on lemonaide's hardware"; booted on a laptop, the same client on
  another box.
- **It reports**, as the servers' agent does every minute: which hardware it runs on, its system
  and version, whether its VPN is up and in which mode, its disk's unlock state, pending updates.
  The control page and the app list the clients with that.
- **It takes what the network pushes**, by pulling it from the control node (nothing listens on
  the client): the network's CA, its VPN profile (NetworkManager), the DNS mode of your
  desktop-manager plan (DNS only / full VPN, the killswitch, trusted networks with a kiwi gateway),
  its unlock policy (rings), its updates, its apps (kiwi-updater).
- **A pentest system** gets a VPN group of its own (the access manager): out to the Internet, and
  nothing at home except what you allow. A lost SSD never opens by itself anywhere.

Built so far that it stands on: the servers' agent (signed reports to the control node), the
access manager's groups, the desktop unlock drills (Debian GNOME and Silverblue VMs), the
control node's API. Not built: the agent on a desktop, its orders, the Devices page for clients.
The design with its build order: [docs/design/clients.md](../docs/design/clients.md).

## The order (first version)

1. **The lists**: the machines and services above, with the **?** answered.
2. **Before anything is wiped** (yours, as in migration.md "Before the move"): sh3's data-disk key
   files, the cold copy, AIO's backup and a Vaultwarden export, and from Nextcloud each account's
   calendars and contacts (`.ics`, `.vcf`).
3. **The core next to v1**: master A, gw1 (no DHCP yet), the Banana Pi ring.
4. **sh3 on uCore**: cloud fresh, vault and games taken over, the rest of the list; then the old
   files into the new Nextcloud by hand (copied into each account's folder, `occ files:scan`, or
   through the desktop client).
5. **DHCP**: v1's off, gw1's on. The home's devices move.
6. **The M1 as gw2** with the control node; the moving address shared by gw1 and gw2.
7. **The clients**: the agent is built and tried first on a VM, then this desktop, Bluefin on the
   workstation, the laptops, the pentest SSDs; phones with the app.
8. **v1's master off**, its VPS becomes master B.
9. Later: the backup server, the offsite node.

The test network (`~/kiwi`) stays for tests on VMs only: the boxes in it that are yours (papaya,
lime, rockpi, lemonaide) go into the new network one at a time, each installed anew, so no key of
the test network (some were shown in sessions) comes along.
