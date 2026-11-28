# Moving the owner's network to v2 (plan)

Written 22 November 2026 for a move in the week after. The owner decides every step on the live
v1 network (its master, the M1, sh3, the old kiwi-node); nothing there is touched without his yes.

**28 Nov 2026:** the map of machines, clients and services, and the order, are in
[migration-map.md](migration-map.md). Nextcloud starts fresh there and the old data is imported by
hand; the AIO takeover below stays as a record.

## Where it ends up

| Machine | Hardware | Runs |
|---|---|---|
| master A | VPS (the network's own) | VPN server, exit through Mullvad, the relay to the rings at home |
| master B | VPS (v1's master once v1 is off) | A's standby: takes over when A is down; its own Mullvad exit while it does |
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
| Taking over v1's data | Nextcloud and Vaultwarden rehearsed on a VM with stand-ins (27 Nov), all four kinds of game server on lemonaide (a modded Minecraft, Enshrouded, Abiotic Factor, Satisfactory) |

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

## Taking over v1's data (plan, 27 Nov; Nextcloud and Vaultwarden rehearsed that day)

Looked at on sh3 on 27 Nov, read only: names, paths and versions, no content and no secret.

**Vaultwarden.** v1: the folder `~/docker/knvault` (SQLite, `rsa_key.pem`, attachments, sends),
image `vaultwarden/server:latest-alpine` (1.37.5 that day). v2's `vault` module runs the same
image on `<docker folder>/kn-vault`. The step: stop v1's container (its write-ahead log is
written out), copy the folder as it is, apply. What changes is the name: `vault.sh3.kiwi` becomes
`vault.<machine>.home`. A security key or passkey used as second factor is bound to the name and
stops working; the authenticator app and the recovery code keep working. `vault.vault_domain`
keeps the old name if that matters. Check after: sign in, open an item.

**Nextcloud.** v1: AIO 15.1.1 with its master container, Nextcloud 35.0.3, PostgreSQL 18, the
name `cloud.sh3.kiwi`. Its parts: Collabora, Talk, Imaginary, full-text search and Whiteboard are
on; ClamAV, OnlyOffice and Talk's recording are off. Data folder
`/mnt/nvme_2tb/docker/knnc-data`; volumes `nextcloud_aio_nextcloud`, `_database`,
`_database_dump`, `_apache`, `_redis`, `_elasticsearch`. Upload limit 50G, memory 8192M.
v2's `cloud` module runs the same containers on volumes of the same names, without the master
container. What has to match:

1. *The secrets.* AIO made its own (in its `configuration.json`: the database's and Redis's
   passwords, Talk's, Whiteboard's, Imaginary's and the search's secrets, the admin password);
   v2 makes its own from the seed. The database and Nextcloud's config hold v1's. So v1's go into
   the new fleet's store as the `cloud` module's settings before the first apply, piped from the
   file into the store, never shown (`kiwi-server secrets set`, pull request 409, not merged).
2. *The version.* v2's `cloud.image_tag` must give the same AIO release as v1 ran last, or a
   newer one; never an older one (Nextcloud and PostgreSQL do not go back).
3. *The volumes.* Docker's volumes on Debian become Podman's on uCore: copied with owners and
   modes kept (root on both sides), each under its own name.
4. *The name.* `cloud.sh3.kiwi` becomes `cloud.<machine>.home`; the containers set the new name
   at their start. The devices' Nextcloud apps are pointed at the new name once.
5. *The settings.* `cloud.datadir`, the upload limit, the parts that are on, as v1 has them.

Check after: sign in as the admin and as a user, a file opens, a file uploads, Talk and Collabora
open, `occ status` and `occ db:add-missing-indices` say nothing is wrong.

*Rehearsed on 27 Nov 2026* on mulberry, a throwaway uCore VM of the test fleet, with a stand-in
for v1 (tests/drills/v1-standin.sh: AIO's containers under Docker, the same parts on, Nextcloud
35.0.3, its own secrets, a user with files). What was done, in this order:

1. v1's containers stopped and removed; the volumes and the data folder stay.
2. The cold copy: each of the six volumes as one archive, as root:
   `tar --numeric-owner --xattrs --acls -cpf COLD/<volume>.tar -C /var/lib/docker/volumes/<volume>/_data .`
3. On the new system: `podman volume create <volume>` and `podman volume import <volume>
   COLD/<volume>.tar` for each. Owners and modes arrive as they were; Podman labels the files.
4. The machine in the fleet file with the `cloud` module and v1's settings (`nextcloud_datadir`,
   `talk`, `fulltextsearch`, `whiteboard`), not applied yet.
5. v1's eleven secrets into the store, each piped, none shown:
   `… | kiwi-server secrets set FLEET MACHINE cloud.<setting>` for `admin_password`
   (NEXTCLOUD_PASSWORD), `database_password`, `redis_password`, `turn_secret`,
   `signaling_secret`, `talk_internal_secret`, `recording_secret`, `onlyoffice_secret`,
   `imaginary_secret`, `fulltextsearch_password`, `whiteboard_secret`. On the real v1 they are
   in the master container's `/mnt/docker-aio-config/data/configuration.json` under `secrets`.
6. `kiwi-server apply`. All eleven containers were healthy within a minute, on the first try.
7. Talk keeps v1's name in three lists beside the new one, and v1's comes first:
   `occ talk:signaling:delete https://<old name>/standalone-signaling/`,
   `occ talk:stun:delete <old name>:3478`, `occ talk:turn:delete turn <old name>:3478 udp,tcp`.
   After that no setting names v1 any more.

Checked after: the same installation id, 35.0.3 with no upgrade pending, both users there; the
user signs in with her v1 password, her note reads, the 3 MB file has the same checksum, a wrong
password gets 401, a new upload works; the admin signs in with v1's password; Collabora, Talk's
signalling and Whiteboard answer under the new name; `occ setupchecks` shows nothing red and no
index is missing; after a restart of the machine everything is healthy again in about a minute;
a second `plan` changes nothing.

A second pass the same day with server-side encryption switched on in the stand-in, as v1 has
it (default module, master key): a file v1 had encrypted reads under v2 with the same checksum,
a file from before the encryption too, and a new upload under v2 is encrypted on disk. The
master key lies in the data folder (`files_encryption/`) and opens with the `secret` in
`config.php`, which comes along in the `nextcloud_aio_nextcloud` volume: both are taken as they
are, so nothing has to be done for it.

Not in the rehearsal: the secrets read from a real master container's file; a data folder of
270 GB (it stays on its disk, but its first start under v2 gives every file the containers'
label, which takes time); v1 on another AIO release than v2 pulls that day; phones and desktop
apps pointed at the new name.

For the real move the owner's part is step 2 on sh3 (root, or `docker run` with the volume),
before anything is wiped. `kiwi-server secrets set` is in pull request 409, not merged.

*Vaultwarden, rehearsed on 27 Nov 2026* on mulberry: a stand-in vault with v1's image and a plain
data folder, an account with a login item in it (tests/drills/vault-client.py `make`, a small
Bitwarden client). Then: the container stopped, the folder as an archive, unpacked into
`<docker folder>/kn-vault` before the first apply, the `vault` module applied. The same account
signs in under the new name with its master password, and the item's password is the one that
was kept (`vault-client.py check`). The write-ahead files (`db.sqlite3-wal`, `-shm`) come along
with the folder; v2 starts with sign-ups off.

**Game saves.** Enshrouded runs; Abiotic Factor, Satisfactory and three Minecraft worlds are kept
stopped. v1 keeps one folder per server under `~/docker/games`; the `games` module keeps the
save apart from the game's files, under `<docker folder>/kn-games/<server>/`. Where each goes
(folder names read on sh3 on 27 Nov, the paths from the module's catalog; all four kinds
rehearsed that night with stand-in servers, see below):

| Game | v1's folder | goes into `kn-games/<server>/` |
|---|---|---|
| Enshrouded (8.3 G with the game) | `game-enshrouded/server/savegame`; the rest of `game-enshrouded` | `savegame`; the rest into `kn-games-files/<server>/game` |
| Minecraft, three worlds | `game-minecraft*/data`, `game-minecraft*/modpacks` | `data`; the pack into `kn-games-files/<server>/modpacks` |
| Abiotic Factor | `game-abiotic/data`; `game-abiotic/gamefiles` | `saves` (v1's `data` is the game's `Saved` folder: it holds `SaveGames`, `Config`, `Logs`); the game into `kn-games-files/<server>/game` |
| Satisfactory | `game-satisfactory` (backups, gamefiles, logs, saved) | `gamefiles` into `kn-games-files/<server>/gamefiles`; the rest is `config` |

The game's own files download again by themselves. Checked by starting the server and seeing
the world's name.

*Enshrouded, rehearsed on 27 Nov 2026* on lemonaide (tests/drills/enshrouded-takeover.sh, all
passed). v1 runs the image the catalog uses (mornedhels/enshrouded-server) with one folder
mounted at `/opt/enshrouded`; the world is the file `server/savegame/world0.ksd` (written when
the server stops, older copies in `savegame/backup`). The steps: stop the old server; move
`game-enshrouded/server/savegame` to `<docker folder>/kn-games/<server>/savegame`, and the rest
of `game-enshrouded` (the game, 8 G) to `<docker folder>/kn-games-files/<server>/game`; the
server in the fleet file (`enshrouded: {game: enshrouded, name: …, password: …}`) and apply. The
new server was online 116 seconds after the apply, without downloading the game again, and its
log said it found the old world ("Found file 'world0.ksd'"). Without the game's folder it works
the same and downloads 8 G first. v1's server also has roles with passwords of their own
(`SERVER_ROLE_0_NAME`, `SERVER_ROLE_0_PASSWORD` and so on, read as names only): they go into
the server's `env:` as they are; their values are in v1's container, `docker inspect
game-enshrouded` shows them to the owner.

*Abiotic Factor, rehearsed on 27 Nov 2026* on lemonaide (tests/drills/abiotic-takeover.sh, all
passed), with the catalog's image (ghcr.io/pleut/abiotic-factor-linux-docker), whose two folders
are v1's: stop the old server; move `game-abiotic/data` to `<docker folder>/kn-games/<server>/saves`
and `game-abiotic/gamefiles` (3 G) to `<docker folder>/kn-games-files/<server>/game`; the server
in the fleet file (`abiotic: {game: abiotic-factor, name: …, password: …}`) and apply. The world
is under `saves/SaveGames/Server/Worlds/<name>`; the catalog's name is `Cascade`, another one goes
into `env: {WorldSaveName: …}` (the folder's name on sh3 says which). The new server saved the
old world again five minutes after its start, with the same content, and downloaded nothing.
Two things about this game: it saves by itself every five minutes and does not end when asked
to (a stop ends it after 90 seconds), and without a password its image sets `password`.

*Satisfactory, rehearsed on 27 Nov 2026* on lemonaide (tests/drills/satisfactory-takeover.sh, all
passed), with the catalog's image (wolveix/satisfactory-server), whose one folder `/config` is
v1's `game-satisfactory`. Since kiwi-modules 0.133.0 the game itself is kept apart, so that it
is not in every backup: stop the old server; move `game-satisfactory/gamefiles` to
`<docker folder>/kn-games-files/<server>/gamefiles` and the rest of the folder to
`<docker folder>/kn-games/<server>/config`; the server in the fleet file (`factory: {game:
satisfactory}`) and apply. The stand-in had been claimed with an admin password and had a
session and a save, made through the server's API as the game does it. The new server took
the old admin password, ran the old session by itself and listed the save, 118 seconds after
the apply and without a download. The server's name, its admin password and which session
loads at the start are in `saved/ServerSettings.7777.sav` and come along.

*Minecraft, rehearsed on 27 Nov 2026* on lemonaide (tests/drills/minecraft-takeover.sh, 14 of
14). v1's three folders are servers of the image the catalog uses too (itzg/minecraft-server)
with a mod loader and a server pack, read from the files in `data` (names and versions only):

| v1's folder | Minecraft | loader | pack in `modpacks` |
|---|---|---|---|
| `game-minecraft` (0.7 G) | 1.21.1 | NeoForge 21.1.133 | none left (the folder is empty) |
| `game-minecraft2` (1.0 G + 0.6 G) | 1.21.1 | NeoForge 21.1.214 | `BMC5_Server_Pack_v44.5.zip` |
| `game-minecraft-old` (1.1 G + 0.7 G) | 1.20.1 | Forge 47.3.11 | one archive |

The steps, as rehearsed with a stand-in server made the same way (NeoForge 21.1.214, a small
pack, a marked world): stop the old server; move `data` to `<docker folder>/kn-games/<server>/data`
and `modpacks` to `<docker folder>/kn-games-files/<server>/modpacks` (the catalog's Minecraft has
that folder since kiwi-modules 0.130.0); then the server in the fleet file with the loader it had,
and apply:

```yaml
games:
  eula: true
  servers:
    bmc5: {game: minecraft, memory: 8G, env: {TYPE: NEOFORGE, VERSION: "1.21.1", NEOFORGE_VERSION: "21.1.214", GENERIC_PACK: /modpacks/BMC5_Server_Pack_v44.5.zip}}
```

The new server came up with the old world (same seed, the mark set before), ran the same loader
and did not unpack the pack again (the pack's mark `.generic_pack.sum` in `data` came along).
For the Forge world: `TYPE: FORGE, VERSION: "1.20.1", FORGE_VERSION: "47.3.11"` (the image's
names; not rehearsed). A server whose `modpacks` folder is empty takes no `GENERIC_PACK`: its
mods are in `data/mods`. What v1 had set besides (memory, the players allowed, RCON) is not on
sh3 any more; `data/server.properties`, `ops.json` and `whitelist.json` come along with the folder.

**The rehearsal** (before anything of v1 is wiped): on a throwaway VM, a small Nextcloud AIO
set up the way v1 was (master container, the same parts), with a user and a file; a Vaultwarden
with an account and an item; a game save. Then the steps above onto a uCore VM, and the checks.
Stand-in data only; v1's own data is copied only at the move, by the owner or with his yes.

## Before the move (the owner)

1. Go through the data (the local AI below can read along; nothing leaves the house). What stays,
   what goes, what moves to the backup server later.
2. **The data disks' key files.** nvme_2tb, hdd_5_2 and hdd_18 open with key files that lie on
   the root disk, which the reinstall wipes. Before anything is wiped: a passphrase in a second
   slot of each (`cryptsetup luksAddKey`), or the key files copied into the new fleet's store.
   Without it the 22 TB do not open again.
3. The sizes only root can read (`sudo du -sh`): /mnt/nvme_2tb/docker/knnc-data (Nextcloud's
   files), /mnt/hdd_5_2/nc_dlcom_data, /var/lib/docker/volumes, /var/lib/libvirt/images (the VMs'
   disks, on the root disk: wiped with it unless copied).
4. The cold copy: what lives on the root disk and nvme_2tb (Nextcloud, Vaultwarden, the game
   saves, the VMs' disks, ~/build, ~/docker) onto a disk that stays out during the reinstall: the
   spare 5 TB, or hdd_18 once it has room.
5. A Nextcloud AIO backup (its own, borg) and a Vaultwarden export, on the cold copy too.
6. The disks of the new sh3 (the spare 5 TB with or instead of the 4.5 TB; the 18 TB later in a
   backup server).

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
7. The local AI on the workstation (docs/design/ai.md), for going through the data first: once
   the owner has given its settings.

## The move

1. Master A on its VPS, a new fleet (new keys, a new 10.X subnet, so v1's 10.8 and `.kiwi` stay
   apart and a device can hold both).
2. gw1 (Rock Pi E) next to v1: gateway, Pi-hole, ring, no DHCP yet.
3. sh3 reinstalled (uCore, encrypted, rings on gw1, gw2 and the Banana Pi, plus its key by hand),
   its parts applied, the data taken back from the cold copy as rehearsed.
4. DHCP: v1's DHCP off, gw1's on.
5. gw2: the M1 reinstalled as gateway, control node, cache and status; the vip pair.
6. The devices enrolled in A's VPN; v1's VPN stays until the last one has moved.
7. v1's master off; its VPS becomes master B, A's standby.

## Decided (the owner, 22 Nov 2026)

1. Which VPS becomes master A: later. Master B is A's fallback, on a second VPS that runs all the
   time: when A is down there is no remote access at all, so B takes over (the owner: "second
   master should be a fallback, cause what happens if master a is down? yes, no remote access at
   all, so master b does the job"). Built for this: a standby master (docs/design/standby.md, drilled
   17 Nov): the same master on a second machine, devices follow it by name. Open: phones following
   it through DDNS, never tried live; a standby on a VPS has Tor, one at home has none.
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
