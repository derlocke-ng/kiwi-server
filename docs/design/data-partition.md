# Keeping the data on a one-disk machine across a reinstall (design, not built)

An idea from KIWI NG (`rd.kiwi.install.retain_last`): a reinstall replaces the system and keeps
the last partition.

## Today

- A machine with a second disk keeps its data there (`mounts:`, docs/machines.md): a reinstall
  erases the system disk only and mounts the data disk again.
- A machine with one disk keeps everything on it, the services' data under the admin user's
  `docker/` folder on the encrypted root. A reinstall erases all of it. What comes back comes
  from the backups ("Bring everything back"), which takes hours for a big Nextcloud or Immich.

## Proposal (CoreOS and uCore)

- A setting `disk_layout: { system_gb: 64 }`. Without it, nothing changes.
- With it, the first install makes the root partition `system_gb` big and a fifth partition
  with the label `data` from the rest. The services' folders (`docker_dir`) move there, and the
  machine mounts it at every boot (the same unit as a data disk).
- **Encrypted like the root.** It must be: today that data is on the encrypted root. The
  `data` partition gets LUKS with the same unlock rings (Tang), through Butane's `luks:` with
  `wipe_volume: false`.
- **A reinstall keeps it.** The install media of a machine that has the layout pass
  `--save-partlabel data` to coreos-installer, and Ignition does not wipe the partition or its
  LUKS volume. The new system opens it with the rings, as before.

## What has to be decided

1. **Only new installs.** A machine that runs today cannot be split without a reinstall that
   brings everything back from the backups once. Fine?
2. **The size of the system part.** 64 GB covers the system, the images and the caches, with
   room. Should it be one number for everybody, or asked?
3. **Machines without unlock rings** have a root that is not encrypted (no TPM, by decision). Their
   data partition would be plain too, as the root is today. Same rule as now, or refuse the
   layout there?
4. **Debian:** its installer cannot keep a partition in the same way (partman recipes). Only
   CoreOS/uCore first?

## How it would be tried

A drill in a local VM (as `starter-vm.sh`): install with the layout, write a marker into a
service's data, rebuild the install media, reinstall, and find the marker again, with the
partition opened by the ring and no backup used.
