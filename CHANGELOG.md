# Changelog

## 2.0.0 — the kiwi-updater rewrite

A new tool under the old name. The 1.x script generated Fedora CoreOS ISOs from
a YAML file; 2.0 builds the machines of a Kiwi Network.

- **kiwi-updater app**: `kiwi.manifest`, `install.sh` (user scope, no root),
  `kiwi-server` CLI with bash completion and `kiwi-server-gui` (GTK4/libadwaita,
  app id `eu.kiwinetwork.KiwiServer`). `kiwi install kiwi-server` once it is in
  the catalog.
- **Three targets**: Fedora CoreOS (stable), uCore (CoreOS that rebases itself
  onto a `ghcr.io/ublue-os/ucore*` image on first boot) and Debian stable
  (trixie netinst, preseeded).
- **Roles** instead of a flat server list: `bare`, `node-cloud` (the kiwi-cloud
  stack: Nextcloud AIO, Vaultwarden, Portainer, nginx behind a gluetun
  WireGuard client) and `master` (wg-easy + Pi-hole baseline). A role is a
  directory with a `role.yaml` settings schema and an `apply.sh`; the schema
  validates the fleet file and builds the GUI form, so the reworked
  kiwi-node/kiwi-master drop in without GUI changes.
- **One role script per host**: self-contained bash, secrets embedded, run by
  `kiwi-role.service` on first boot — or by hand on any installed machine.
- **Unattended install media**: `coreos-installer iso customize --dest-device`
  for CoreOS/uCore; for Debian the preseed goes into the installer's initrd and
  the boot menus start the unattended entry, with `xorriso -boot_image any
  replay` keeping the stock BIOS/UEFI boot setup.
- **Automatic updates with a reboot window** on every target: Zincati
  `periodic` on CoreOS, staged rpm-ostree updates plus a reboot timer on uCore,
  unattended-upgrades plus a reboot-if-required timer on Debian.
- **Toolchain** runs natively when butane, coreos-installer and xorriso are
  installed, otherwise in a podman/docker image (`kiwi-server toolchain build`)
  — nothing is layered onto an ostree desktop.
- **Checks**: `validate` (exit 2 on errors), `doctor`, `show` (effective config,
  secrets masked), `--porcelain` JSON for the GUI; tests shellcheck every
  rendered script, validate every Butane config with `butane --strict`, and
  rebuild a mock Debian ISO.

Dropped from 1.x: the `global:`/`servers:` file format, the `kiwi-server-gen.sh`
wrapper and `iso ignition embed` (which only embedded a live config and still
asked you to run the installer). The per-server Butane extras (`files`,
`directories`, `systemd_units`, `services`, `kernel_arguments`, `luks`,
`boot_device`) live on under `coreos:`.

## 1.0.0

Initial release: Fedora CoreOS / uCore ISO generator from a YAML file.
