# Testing on real hardware

What the suite cannot prove: a machine that boots, installs and runs a stack.
This runbook is for a session or a person with SSH to the test machines (a
VPS and a LAN box or VM). Every step says what "good" looks like; a step that
fails is a finding for docs/ROADMAP.md, not something to work around.

## Machines

- **VPS** (the master): Debian 13 or Ubuntu 24.04 with podman ≥ 4.4, root over
  SSH. No LUKS until the Tang work exists.
- **A LAN box or a bridged VM** (a gateway or cloud node), reachable from the
  VPS through a reverse tunnel (`ssh -N -R 2222:localhost:22 user@vps` on the
  box; then `ssh -p 2222 user@localhost` on the VPS).

## 1. The operator machine

```bash
git clone https://github.com/derlocke-ng/kiwi-server.git && cd kiwi-server
./install.sh install && kiwi-server doctor
mkdir ~/kiwi && cd ~/kiwi && kiwi-server init fleet.yaml --domain home
```

Fill in the master (the VPS: `target: debian`, `role: master`, `wg_host` the
VPS address, a Mullvad or other provider key for the exit, `pihole_password`,
`wg_password`) and one node. `kiwi-server validate fleet.yaml` must pass.

## 2. Apply the master onto the installed VPS

```bash
kiwi-server apply fleet.yaml gate --ssh root@<vps>
```

Good: every image pulled with its name on the console; "checking that every
container is running"; `kiwi-server status fleet.yaml gate` shows
`applied:` with a date and `all km-vpn-client, km-vpn-server, … running`.
On the VPS: `sudo kiwi-stack verify`, `podman ps` with every container Up,
`ls -l /etc/containers/systemd` all 0600, `grep -c PASSWORD
/etc/containers/systemd/*.container` = 0, `/home/user/docker/kiwi/env/`
root-only. Pi-hole answers on 10.8.0.1 from a WireGuard client;
`https://pihole.gate.home` and `https://wg.gate.home` open from the mesh
with the fleet CA imported.

Bad, and what it means: "cannot pull" = the host has no Internet or the
image name is wrong; "not running: km-nginx (failed)" = read
`kiwi-stack logs km-nginx`; `role.failed` present = the step named in it.

## 3. Enroll and apply a node

```bash
kiwi-server enroll fleet.yaml m1            # writes secrets/m1.home.conf
kiwi-server apply fleet.yaml m1 --ssh core@<node>
```

Good: the node's WireGuard comes up (`podman exec kn-vpn-client wg show`),
`ping 10.8.0.1` from the node, `dig @127.0.0.1 cloud.sh3.home` on a gateway
node answers the mesh address, a container on the node resolves through
Pi-hole (`podman exec kn-portainer cat /etc/resolv.conf` shows the Pi-hole
address first), `https://portainer.m1.home` from a mesh client shows the
client's own 10.8.x address in the nginx access log, not the VPN client's.

## 4. A fresh install from an ISO

Only where the provider or the VM can boot a custom ISO.

```bash
kiwi-server build fleet.yaml m1 && ls -l output/m1/m1.iso
sudo dd if=output/m1/m1.iso of=/dev/sdX bs=4M status=progress oflag=sync   # a raw copy, nothing else
```

Write the stick **raw** (`dd`, GNOME's Disk Image Writer, Fedora Media
Writer). Ventoy, Rufus in ISO mode and similar tools rewrite the image, and
the Fedora CoreOS live system then cannot find its root: the console ends in
`Failed to isolate default target` before anything is installed (found on
the first mini PC, 30 October 2026; the same ISO installed cleanly in a
QEMU VM). Before blaming the media, boot the ISO in a VM:

```bash
qemu-img create -f qcow2 disk.qcow2 20G
qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 -machine q35 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd \
  -drive file=disk.qcow2,if=none,id=d0 -device nvme,drive=d0,serial=test \
  -cdrom output/m1/m1.iso -netdev user,id=n0 -device virtio-net-pci,netdev=n0
```

(an `nvme` device when the host's `disk:` is `/dev/nvme0n1`, `virtio-blk`
for `/dev/vda`). A good run installs and powers off within a minute.

Boot it, walk away, come back: the machine has installed and **powered
off** (`install.on_done: poweroff`, the default). Remove the stick or detach
the ISO, power it on: `kiwi-role.service` runs (`journalctl -u kiwi-role
-f`), and step 3's checks pass. The install guard against a second boot of
the media is still a roadmap item (problem 14); the power-off is what keeps a
forgotten stick from reinstalling.

## 5. Re-apply, remove, switch

- Change a setting, `apply` again: the container with the changed file
  restarts, the others do not; `kiwi-stack verify` passes.
- Remove a module from the host's block, `apply`: its container is stopped
  and gone from `podman ps`, its ports free.
- Set `runtime: docker` (or back), `apply`: the old stack stops before the new
  one starts; no port conflict.

## 6. The dead-node drill

Destroy the node (reinstall or delete the VM). Time from "gone" to "back":
re-enroll is not needed (its config is in `secrets/`), a fresh install plus
`apply` brings the stack back; what is lost is the data the backup module
will cover. Record the time and every manual step in docs/ROADMAP.md.
