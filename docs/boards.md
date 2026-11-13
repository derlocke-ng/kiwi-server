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

An encrypted disk (Armbian's build framework): see the [roadmap](ROADMAP.md).
