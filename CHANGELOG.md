# Changelog

## 2.2.0 — Mullvad's SOCKS5 proxies by name

- **`dns.mullvad_socks`**: Pi-hole answers for every Mullvad SOCKS5 proxy, as
  `de-fra-wg-socks5-001.relays.mullvad.net` and the short
  `de-fra-001.mullvad.<domain>` (`mullvad_socks_domain` to change it). gluetun
  refuses these private-range answers, so the names only resolved through the
  fallback resolvers until now. **On in the master preset**, whose exit is
  Mullvad; nodes ask the master. Set `mullvad_socks: false` in the master's
  `dns:` block to keep it off; the next apply removes the timer again.
  A master that got these records by hand from the mullvad-socks5 README
  (`pihole-mullvad-socks.timer`, `FTLCONF_misc_etc_dnsmasq_d`,
  `etc-dnsmasq.d/90-mullvad-socks.conf`) should undo that first: its
  [upgrade steps](https://github.com/derlocke-ng/mullvad-socks5#upgrading-from-the-earlier-instructions);
  kiwi-server does not remove units it did not install.
- A host timer (`km-mullvad-socks.timer`, every 6 hours) fetches the list from
  [mullvad-socks5](https://github.com/derlocke-ng/mullvad-socks5)
  (`mullvad_socks_url`) and swaps the file in `<docker_dir>/km-pihole/mullvad-socks/`,
  which Pi-hole sees **read-only** at `/etc/mullvad-socks` (`hostsdir`) and
  reloads without a restart. The directory is root's and outside Pi-hole's
  writable `/etc/dnsmasq.d`, so nothing in the container can swap a file under
  the refresh, which runs as root. Only `*.relays.mullvad.net` names at 10.x
  addresses are taken from the list, so it can never redirect another name; a
  download that is not such a list keeps the current records.
- **A re-apply removes host units it no longer renders**: a module dropped from
  a role, or a setting turned off, no longer leaves its units running outside
  `kiwi-stack`'s control.
- Modules: `when:` on `outputs` entries (templates and file settings) and on
  `storage.bind_mounts`, like the nginx blocks have; `pattern:` on a setting
  checks a string value against a regular expression at validate time.
- `kiwi-server roles -v` shows a role's preset value (`module_defaults`) where
  it has one, not the module's own default.
- `dns.extra_env: { FTLCONF_misc_dnsmasq_lines: … }` is added to the module's
  own dnsmasq lines; before, it replaced them (and with them `strict-order`
  and the fleet's `local=`/`server=` line), since one variable is set once.

## 2.1.2 — one resolver chain for the whole network

- **Nodes resolve through the master.** A node's Pi-hole asks the master's
  Pi-hole first, so the network shares one blocklist and every lookup leaves
  through the master's exit; Quad9 follows in strict order and only answers
  while the master is unreachable (`dns.fallback_dns`). Names under the
  fleet's domain are forwarded to the master and never leave it; the master
  marks the domain local. The master's address comes from the fleet's master
  host or the new stack setting `master_ip`.
- **A route into the mesh on every host.** `kiwi-stack start` routes the mesh
  subnet (`mesh_subnet`, default 10.8.0.0/16) through the VPN client, or
  through the WireGuard server on a master, so the host and its containers —
  the Pi-hole among them — reach the other nodes. The master masquerades that
  traffic as its mesh address.
- **The fleet's names in /etc/hosts** on every stack host, its own names
  pointing at itself, so backups and renewals resolve without a Pi-hole.
- gluetun's DNS-over-TLS provider defaults to Quad9; the gateway module's
  mesh subnet defaults to the stack's.
- **Names are `service.hostname.home`.** The example fleet moves from
  `.kiwi`, a real public TLD anyone can register names under, to `.home`,
  which ICANN will not delegate; the CA is constrained to it. No place label.
- **`kiwi-server openwrt`** writes the uci script that joins an OpenWrt router
  to the mesh: a WireGuard client with the mesh routed (or everything, with
  `--full`), the firewall zone, and the dnsmasq forward of the fleet's names
  to the master's Pi-hole; or, with `--via`, a static route to a gateway
  node. [docs/routers.md](docs/routers.md) covers FritzBox and other routers.

## 2.1.1 — the review fixes

What the 2.1.0 review ([docs/REVIEW-2.1.0.md](docs/REVIEW-2.1.0.md)) found,
fixed. Nothing changes in the fleet file format; new settings have defaults.

- **First boot** (`roles/common/lib.sh`): `kiwi-stack start` no longer
  restarts the host units from inside its own service (a node-gw first boot
  hung forever); the docker group is copied from `/usr/lib/group` on Fedora
  CoreOS before `usermod`; a re-run restarts the stack instead of a no-op
  `enable --now`; the rendered files stay root's and only the data
  directories belong to the service user; `kiwi-stack vpn-restart` also
  restarts the containers in the VPN client's namespace; the timers' services
  wait for docker and the stack.
- **Renderer**: container addresses are computed inside the network (any
  subnet, not only /24), `container_ip` overrides are checked, a missing
  `vpn_ip` is reported as the setting to set — and read from the WireGuard
  config's `Address` when it is empty; `$` in settings survives compose
  interpolation; `key:` with no value means the default; `post_script` is
  pasted as written; the reverse-proxy `hsts` setting works; module.yaml
  mistakes are errors with a place, not tracebacks.
- **Stacks**: nginx resolves backends per request (`resolver` + variable), so
  it starts before Nextcloud AIO's containers exist; Pi-hole gets the fleet's
  names as `FTLCONF_dns_hosts` (Pi-hole 6 ignores a written `custom.list`),
  no `hostname:` together with `network_mode`, a DHCP server (`dhcp_*`) and
  a local-only web UI by default; the master's isolation rules come before
  the wg0 accept-all and isolated clients never reach the admin pages
  (`admin_from_mesh`); gw.sh's DROP is really first; gluetun's post-rules no
  longer accept every new forwarded flow; `label:disable` on the containers
  that mount the docker socket (SELinux); the AIO port and Pi-hole UI bind to
  127.0.0.1; JDownloader's web UI has a login; Portainer's admin password can
  be set before the first start; the SFTP password moved from the command line
  to `users.conf`; `extra_env` on every service module.
- **CLI / targets**: `script` prints only the script; TLS settings that cannot
  work fail `validate`; the fleet CA exists before the first script is
  rendered; host certificates last 825 days (`tls.cert_days`) and the CA can
  carry name constraints (`tls.name_constraints`); ISOs and the preseed copy
  are private; `updates.days: []` means every day inside the window;
  `debian.release` other than the current stable needs `debian.iso_url`; the
  preseed honours `admin.groups`; `service_user` defaults to the admin user.
- **GUI**: the module list applies on Enter, the fleet file keeps its mode,
  a failed save keeps the changes, the build dialog shows the disk from the
  file, the narrow layout shows the form, host names are checked, the script
  renders off the main thread, the missing-CLI dialog is visible.
- **Docs and tests**: firewall `scope` semantics documented, migration and
  mesh-SSH notes, bash completion for `ca`/`modules`; tests for the fleet
  scan (DNS records, CA, warnings), the kiwi-stack helper, the example fleet
  and every fix above.

## 2.1.0 — the kiwi-v2 modules, and GPL-3

- **Modules**: the kiwi-v2 module system lives in `modules/` and the renderer
  is part of the library (`kiwiserver.modules`): compose fragments are merged
  as YAML, the reverse proxy aggregates the blocks the other modules announce,
  module firewall rules flow into gluetun's post-rules, and a *bundle* of
  files, directories, ports, sysctls and host units is embedded into the role
  script. The host needs docker, nothing else.
- **Roles are module presets**: `master` (vpn-client, vpn-server, dns, tor),
  `node-gw` (vpn-client, dns, dhcp-relay, reverse-proxy, downloader, gateway,
  portainer, sftp), `node-cloud` (vpn-client, reverse-proxy, cloud, vault,
  portainer) — the live kiwi-master, kiwi-node-gw and kiwi-cloud, module by
  module. `modules:` in a host's block changes the list.
- **Settings from one place**: each `module.yaml` carries a `settings:` schema;
  validation, `kiwi-server roles -v` and the GUI's per-module groups come from
  it. Presets seed module settings with `module_defaults:`.
- **Filled in from the v2 TODO**: `start.sh` (double hop, isolated-client
  subnet) and `torrc` for the master, nginx server blocks per module, the
  gateway script with the live routing rules (table 128 with `throw`, LAN
  kept off the docker subnet), Pi-hole 6 variables, conditional WireGuard
  env vars for a custom config, DNAT to the proxy's real address.
- **New modules** the live setups ran but v2 lacked: `portainer`, `sftp`,
  `dhcp-relay`.
- **Fleet-wide knowledge**: every Pi-hole serves every host and service name
  at its mesh address (split-horizon DNS), the master's `start.sh` lets
  isolated clients reach every node, and one fleet CA (`kiwi-server ca`)
  issues `*.<hostname>` certificates for the reverse proxies and is trusted on
  every machine built.
- **On the host**: `kiwi-stack` (start, stop, restart, update, status, logs,
  vpn-restart), `kiwi-stack.service`, the daily VPN restart and weekly update
  timers the v1 cron jobs did, systemd-resolved's stub listener turned off
  where Pi-hole needs port 53, `docker compose config` validation at render.
- **License**: GPL-3.0-or-later, like the rest of the Kiwi Network apps.


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
`directories`, `units` (was `systemd_units`), `services`, `kernel_arguments`,
`luks`, `boot_device`) live on under `coreos:`.

## 1.0.0

Initial release: Fedora CoreOS / uCore ISO generator from a YAML file.
