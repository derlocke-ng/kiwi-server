# First boot and updates

This page explains what a machine does at its first boot, and how it keeps
itself up to date afterwards. Updates are fetched continuously on every
target, but a machine only *reboots* for them inside the update window you
set in the fleet file. The last section describes a master that runs from RAM
only.

## The first boot

The install media install a systemd unit, `kiwi-role.service`. It runs
`/var/lib/kiwi-server/role.sh` as root once the network is up, and never again
once `/var/lib/kiwi-server/role.done` exists. A failed run is tried again at
the next boot and shown on the console. The log is
`/var/log/kiwi-server-role.log`, and `journalctl -u kiwi-role -f` follows it.

On uCore the unit also waits for `/etc/ucore-autorebase/signed`, so the role
always runs on the final, signed image. uCore reboots twice before that: once
into the unsigned image and once into the signed one.

`sudo bash /var/lib/kiwi-server/role.sh --force` runs the script again by
hand. Usually you run `kiwi-server apply` instead; see
[Manage running machines](managing.md).

## Update windows

```yaml
defaults:
  updates: { days: [Sun], time: "03:30", length_minutes: 90 }
```

This is the default: reboots happen on Sundays between 03:30 and 05:00, in the
machine's local time. `updates.days: []` means any day, and
`updates.enabled: false` turns automatic updates off.

| Target | Installs updates | Reboots |
|---|---|---|
| `coreos` | Zincati, continuously | only inside the window (Zincati's `periodic` strategy) |
| `ucore` | `rpm-ostreed-automatic.timer` stages them, as the image does by default | `kiwi-staged-reboot.timer` reboots into a staged update inside the window |
| `debian` | unattended-upgrades, daily | `kiwi-reboot-if-required.timer` reboots inside the window when `/var/run/reboot-required` exists |
| `coreos-ram` | a new ISO, built with `kiwi-server build` and booted by hand | booting the new ISO is the update; `watch` then sends the role again |

The containers are updated separately. The stack's weekly update timer
(`weekly_update`, Sundays at 00:00 unless set) runs `kiwi-stack update`, which
pulls new images (with `podman auto-update` on a podman stack) and restarts
the containers that use them.

## A master in RAM

**Status: experimental.** The `coreos-ram` target is a prototype. It was
tried in a local VM without a disk, and on a test VPS at netcup, where the
master got its role back after a power cycle
([drills](https://github.com/derlocke-ng/kiwi-server/blob/main/tests/drills/README.md)).

With `target: coreos-ram`, a master boots the Fedora CoreOS live ISO every
time, keeps everything in RAM and touches no disk. The ISO carries the
master's live Ignition config, but no role and none of its secrets: only the
machine's SSH host key, which lets kiwi-server recognise it. The key is kept in
`secrets/ram/<host>/` and, when the fleet has an SSH CA, signed by it.

After every boot the master waits. On a computer at home,
`kiwi-server watch` sends it its role, and puts back the WireGuard identity and
the certificates the fleet keeps of it:

```bash
kiwi-server watch fleet.yaml               # look every minute while it runs
kiwi-server watch fleet.yaml --once        # one look
kiwi-server watch fleet.yaml --timer on    # a user timer on this computer that looks every minute
```

While the master runs, `watch` refreshes the fleet's copies of its identity
and certificates every hour. After an unplanned reboot the master is down
until `watch` looks again: about a minute with the timer on, longer when the
computer that runs it is off.

`ram.copy_to_ram: true` copies the whole image into RAM, so the boot medium
can be removed. It needs at least 3 GiB of memory; off, the default, the image
is read from the medium. `disk:` and `unlock:` do not apply to this target.

The design, with what was measured and what was considered, is in
[A master in RAM](design/ram-master.md).
