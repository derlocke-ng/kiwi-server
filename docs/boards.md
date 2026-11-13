# Boards

An arm64 board (Odroid M1 or N2+, Banana Pi, a Pi) that already runs its own
Debian or Armbian becomes a node without a reinstall.

## The host

```yaml
n2plus:
  role: node
  target: debian
  debian: { arch: arm64 }
  network: { dhcp: true }      # or the board's fixed address
  node:
    modules: [vpn-client, reverse-proxy]
```

## Take it over

```bash
kiwi-server enroll ~/kiwi/fleet.yaml n2plus                       # its place in the mesh
kiwi-server adopt ~/kiwi/fleet.yaml n2plus --as root@192.168.2.40  # the board's own login
```

`adopt` checks the fleet, then puts on what an install would: the admin user,
the fleet's keys, the sshd and update settings. Then it applies the role. Disk
and network stay as they are. A password login works; sudo may ask for it.

- **Armbian:** log in once by hand first; it asks for a new root password.
- **Flashed again?** The board has a new host key: add `--new-host-key`.
- **Images:** every module image has arm64 builds.

## Not yet

An image per board, built with Armbian's build framework, encrypted disk
included: see the [roadmap](ROADMAP.md).
