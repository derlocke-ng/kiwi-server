# Boards

arm64 boards (Odroid M1 and N2+, Banana Pi, a Pi) run as nodes. Two ways:

| | When |
|---|---|
| [A ready image](#a-ready-image) | the board can be flashed (SD card, eMMC) |
| [Adopt](#adopt-a-running-board) | it already runs Debian or Armbian and should keep it |

Every module image has arm64 builds.

## A ready image

```yaml
n2plus:
  role: node
  target: armbian
  armbian: { board: odroidn2 }    # its name at dl.armbian.com: odroidm1, odroidn2, ...
  network: { dhcp: true }
  node:
    modules: [vpn-client, reverse-proxy]
```

```bash
kiwi-server enroll ~/kiwi/fleet.yaml n2plus      # its place in the mesh
kiwi-server build ~/kiwi/fleet.yaml n2plus       # output/n2plus/n2plus.img
sudo dd if=output/n2plus/n2plus.img of=/dev/sdX bs=4M status=progress oflag=sync
```

`build` takes the board's Armbian image (checksum checked) and writes the
first boot into it; no root needed. On first boot the board makes the admin
user with the fleet's keys, locks root, turns off Armbian's console autologin
and runs the base role. Then:

```bash
kiwi-server apply ~/kiwi/fleet.yaml n2plus --ssh core@<its address>
```

| Setting | Default | |
|---|---|---|
| `armbian.board` | — | required |
| `armbian.release` | `trixie` | |
| `armbian.branch` | `current` | |
| `armbian.variant` | `minimal` | or `server` |
| `armbian.image_url` | — | another `.img.xz`, its `.sha` beside it |

Odroid N2/N2+: the boot switch must be on MMC, not SPI.

## Adopt a running board

The host is `target: debian` with `debian: { arch: arm64 }` (or
`target: armbian`).

```bash
kiwi-server enroll ~/kiwi/fleet.yaml n2plus
kiwi-server adopt ~/kiwi/fleet.yaml n2plus --as root@192.168.2.40   # the board's own login
```

`adopt` checks the fleet, puts on the admin user, the fleet's keys, the sshd
and update settings, then applies the role. Disk and network stay as they
are. A password login works; sudo may ask for it.

- **Stock Armbian:** log in once by hand first; it asks for a new root password.
- **Flashed again?** New host key: add `--new-host-key`.

## Not yet

A board with a fixed address in the fleet file (`network.address`, `dhcp: false`) gets that
address written at its first start, or with `adopt` for its next restart: a file of
systemd-networkd's that comes before netplan's, or the card's connection in NetworkManager.

## An encrypted board

A board's disk is encrypted when its image was built that way: with Armbian's build framework
(`CRYPTROOT_ENABLE=yes`), which makes the separate `/boot` and the boot system a locked root
needs. kiwi-server builds that image on a machine with Docker that you name, the builder:

```yaml
rockpi:
  target: armbian
  armbian: { board: rockpi-e, encrypt: true, builder: sh3 }
  network: { dhcp: false, address: 192.168.2.67/16, gateway: 192.168.2.1 }
  unlock: { ssh: true }
```

```bash
kiwi-server build ~/kiwi/fleet.yaml rockpi
```

- `builder` is the machine's SSH name as your computer's `ssh` knows it. There kiwi-server
  keeps a checkout of Armbian's framework (`builder_dir`, default `/var/tmp/kiwi-armbian-build`,
  at `build_ref`, default `main`) and one privileged container of its own, `kiwi-armbian-build`.
  Armbian's own Docker mode is not used: it prunes the machine's images. Nothing on the builder
  is removed; the images stay in `output/images` there.
- The builder gets the board's one-time disk key on a tmpfs in the container, removed after the
  build, and public keys: the admin's, for the boot system's SSH on port 2222. With a fixed
  address, the boot system takes it, so the first unlock is at that address. Nothing else of the
  fleet goes there.
- On an x86 builder the framework runs ARM programs through QEMU: their handlers are registered
  with the builder's kernel until its next restart.
- A build takes about five minutes when Armbian's cache has the board's kernel (the Rock Pi E,
  22 November 2026).
- Flash the image. At its first start: `kiwi-server unlock <fleet> rockpi --manual` (the key in
  `secrets/luks/rockpi.once`), then `kiwi-server adopt` with the board's own login, then `apply`.
  The first `apply` replaces the one-time key with one of its own, binds the disk to the rings
  and grows the encrypted volume to the disk's size.

A board whose image was made by hand is told so with `encrypt: true` and no builder; its key is
`secrets/luks/<name>.key`:

```yaml
lime:
  target: armbian
  armbian: { board: odroidn2, encrypt: true }
  unlock: { rings: [infra, house], ssh: true }
```

- `apply` binds the disk to the rings, writes the board's fixed address into its boot system and
  kiwi-server's keys for it (`kiwi-server unlock`, the openers).
- Proven on an Odroid N2+ (19 November 2026): flashed anew, its first start brought its
  services and its ring's keys back from the backup by itself; bound to three rings it opens by
  itself 48 seconds after a restart, and opens the way for the master as an opener.
- The first `apply` grows the encrypted volume to the disk's size (Armbian's first start
  grows the partition only), with the disk's key and once: a volume of full size is left
  alone.
