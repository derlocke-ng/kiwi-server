# Changelog

## 2.7.1 — the enroll tunnel beside an operator's ssh multiplexing

- **`enroll` works on an operator whose `~/.ssh/config` multiplexes sessions**:
  the tunnel to wg-easy is a channel-less `ssh -N -L`, and through an
  existing ControlMaster it ended at once with "Broken pipe" and exit 0, so
  `enroll` reported "the tunnel to port 51821 failed: ssh exited" while
  `apply` worked. Every connection kiwi-server opens now says
  `ControlMaster=no` (problem 54, found on the first device enrolment).
- **Clients in a group get their answers**: inside the WireGuard server the
  whole mesh is routed into `wg0`; before, only the server's own /24 was,
  and a device in the `devices` group received nothing back (problem 56,
  found with the first device). tests/golden/gate: one line of `start.sh`.
- **A node with only Portainer behind its proxy gets the mesh DNAT too**
  (`portainer.needs_vpn_dnat: true`, problem 58). Found with the same
  device: on uCore a mesh client's connection to a node's proxy died on the
  host's conntrack, which saw only the reply of the DNAT'd flow and dropped it
  as invalid — netavark's forward chain does that on every podman host,
  firewalld on top of it (problem 57). The role now tracks bridged container
  traffic (`br_netfilter`, `bridge-nf-call-iptables`) on every podman host
  with a mesh, and where firewalld runs trusts the mesh subnet like the stack
  network, so the client's own address still reaches nginx; the gateway
  module's masquerade for LAN clients leaves the stack network's and the
  mesh's traffic alone, which the tracking had exposed it to.
- Runbook step 5 passed on a node: a module added and removed again, the
  runtime switched to docker and back, every time with `verify` green and
  the old stack stopped first; noted that a re-apply restarts every
  container of the stack, not only the changed one (problem 55).

## 2.7.0 — install media without the stack

- **`kiwi-server build --bare`** (and `render --bare`): the ISO carries the
  base system only — hostname, network, the admin user and keys, the update
  policy — and the machine comes up as `bare`; its role and every secret of
  its stack arrive afterwards with `kiwi-server apply` over SSH and stay
  0600 on the machine. For media that leave the house: a provider's ISO
  store, a stick in the post. Only the base system is validated, so a node's
  media can be built before the node is enrolled (its WireGuard config comes
  with `apply`, which checks the role's settings then). The per-host ISO with
  the stack embedded stays the offline path.
- **The install media power the machine off when the install is done**
  (`install.on_done: poweroff`, the default; `reboot` keeps the old
  behaviour): remove the stick or the provider's ISO, then power on. Media
  left first in the boot order would otherwise reinstall on every boot.
  Debian: `debian-installer/exit/poweroff`; CoreOS: the live ISO skips its
  reboot (`coreos.inst.skip_reboot`) and a post-install script turns the
  machine off. The install guard with a `--reinstall` override (ROADMAP,
  problem 14) stays open.
- **The first podman apply on a fresh machine no longer fails in the quadlet
  cleanup**: a grep over globs that matched nothing exited 2 and `pipefail`
  stopped the role (problem 41, found on hardware). `role.failed` and the
  error line now name the function the failing command sat in, not only
  the last step announced.
- **The master's WireGuard server comes up on an nf_tables host**: its
  `start.sh` uses `iptables-nft` where the image has it — inside the wg-easy
  container the legacy xtables fail with "Permission denied" on Debian 13 and
  Fedora hosts whatever modules are loaded (problem 42, found on the first
  master) — and `WG_POST_DOWN` is a no-op instead of wg-easy's legacy
  default. tests/golden/gate changes accordingly: two lines of `start.sh`
  and one environment line; nothing a client sees.
- **An encrypted Debian loads the console keymap into its initramfs**
  (`KEYMAP=y`, `update-initramfs -u` in the late command), so the LUKS
  prompt reads the keyboard the fleet file says, not US (problem 43, found
  on the first encrypted VM).
- **Containers without a fixed address no longer take the fixed ones**: the
  stack network has an `IPRange` (172.128.0.128/26 of a /24; the compose
  file's `ip_range`) for them, and a fixed address inside it is refused. On
  the first gateway+cloud node the Nextcloud AIO containers had taken .2,
  .4, .6 and .7 before the VPN client, Pi-hole, apache and Vaultwarden
  asked for them (problem 48). A changed `.network` quadlet now has its
  podman network recreated at apply (problem 49). tests/golden: the
  network unit and the compose network of every preset gain one line.
- **NAT out of the VPN client, at last**: in `post-rules.txt.j2` an inline
  `{% if %}` at the end of a comment line swallowed the newline before the
  masquerade rule, which became part of the comment in every rendered
  stack. gluetun's own traffic worked, everything forwarded through it —
  the mesh's internet on a master, the LAN's on a gateway node — left with
  a private source and never came back (problem 51, found on the first
  master with `wget` from the WireGuard server). tests/golden: the rule on
  its own line in every preset.
- **Pi-hole and podman's own resolver no longer fight over port 53**: with a
  resolver in the stack the role sets `dns_bind_port = 5533` in
  `containers.conf.d`, where podman moves aardvark-dns (problem 50).
- **The live installer ISO has a shell and shows the disks**: its own
  Ignition gives root and the admin user the fleet's console password and
  keys, and prints `lsblk` before the installer looks for `disk:`; a VPS
  whose disk is not where the fleet file says can be read at its console
  instead of guessed (problem 47).
- **A gateway node's VPN client can finish its handshake**: the gateway
  module's host rule that keeps LAN clients off the containers' addresses
  dropped every reply to what the containers started, the master's
  WireGuard answers among them, and every LAN connection to a published
  port; it now drops new, non-DNAT connections only (problem 52, found on
  the first gateway node). tests/golden/m1's gw.sh changes accordingly.
- **Pi-hole no longer answers a node's own names with a container address**:
  the `AddHost=` entries for the containers stay out of Pi-hole's container,
  which serves the fleet's records with `no-hosts` (problem 53, found on the
  first gateway node: LAN clients were sent to 172.128.0.5, then to the
  host's 127.0.0.1 entries podman had copied in).
- **The first boot waits for name resolution** (two minutes at most) before
  its first apt or pull: on the first machine booted from this tree, a
  Debian VPS, `network-online.target` was satisfied before the resolver
  was, and apt went on without updates (problem 38, found on hardware).
- **`apply`, `status`, `enroll` and the backup use the fleet's own key**:
  an `admin.ssh_keys` entry that is a `.pub` with its private key next to it
  (`secrets/admin_ed25519.pub`) is offered to the machines, and no other
  key then (`IdentitiesOnly`). Before, ssh offered whatever the operator's
  agent held, and the first apply to a VPS failed with "Permission denied
  (publickey)" because the fleet's key was not in the agent (problem 39,
  found on hardware).
- `tests/cli.sh` keeps the caller's Python user site, so a Jinja2 from
  `pip install --user` (the README's own line) is found.
- docs: the design after the October talks — the control node and the
  replaceable master, the bootc image (phoenix-server), names, the unlock
  with Tang rings and a netfilter relay, managed masters — in
  docs/design/control-plane.md; the test network and its machines in
  docs/HANDOFF.md; problems 32 to 36 and the owner's module wishes in
  docs/ROADMAP.md.

## 2.6.0 — the renderer and the machine side as repositories of their own

- **The renderer is its own package, `lib/kiwimodules`** (modules, quadlets,
  the settings schema, the profile loader, settings resolution, the stack
  manifest), the third step of the split (docs/ROADMAP.md). kiwi-server
  imports it; nothing in a rendered stack changed (tests/golden). The secret
  derivation and the seed id live there now, shared with the fleet.
- **`python3 -m kiwimodules render machine.yaml -o DIR`** renders one
  machine's stack from a one-machine file, without a fleet, into a bundle
  directory that `sudo kiwi-stack apply DIR` installs; `modules` and
  `profiles` list what there is. The same seed and settings give the same
  bundle kiwi-server renders for that host (tested byte for byte).
- The installer copies `lib/kiwimodules`; CI compiles it and shellchecks
  `roles/common/kiwi-stack`.
- **kiwi-modules 0.1.0 and kiwi-stack 0.1.0 exist** as repositories of their
  own, populated from this tree by `tools/split-sync.sh` (`--check` reports
  drift): the renderer with `kiwi-modules render` and the machine side with
  `kiwi-stack apply DIR`. kiwi-server stays the place both are developed
  until the first hardware round; the subtree flip comes after it.

## 2.5.0 — the stack is trustworthy before anything is built on it

The first bug round of the roadmap (docs/ROADMAP.md), on the rendered stacks
and the first boot; nothing here has booted on hardware yet.

- **The first boot can fail now.** The role script pulls every image one by
  one with retries before the first start, then `kiwi-stack verify` checks
  that every container runs; a stack that is not up fails the apply with the
  names, instead of "applied" with nothing running. A failed step leaves
  `/var/lib/kiwi-server/role.failed` (step, command, last log lines), which
  `kiwi-server status` shows. Quadlets have no start limit and pause 15 s
  between restarts, so a slow pull recovers without a reboot.
- **Secrets stay out of the unit files**: a secret-typed setting goes into a
  root-only `kiwi/env/<service>.env` referenced by `EnvironmentFile=`; the
  quadlets themselves are written 0600 now.
- **The master's Pi-hole moves to port 8081** inside the WireGuard server's
  namespace, which nginx shares and whose 80/443 it owns (`dns.web_port`,
  `FTLCONF_webserver_port`); the renderer refuses two modules binding the
  same port in one namespace (`network.listen_ports`, `derived:` values in
  module.yaml).
- **A node's own names resolve to the reverse proxy inside its containers**
  (`AddHost=`), the containers use the stack's Pi-hole as resolver (`DNS=`),
  the fleet CA is mounted into Nextcloud (`kiwi/ca/kiwiCA.crt`, the
  `TRUSTED_CACERTS_DIR` AIO reads), and the Nextcloud data directory carries
  its SELinux label. Collabora, Talk and notify-push can reach
  `https://cloud.<host>` from inside now.
- **No masquerade on DNAT'd mesh traffic** where the host routes the mesh back
  through the VPN client: services see the real client address, so rate
  limits and logs work per client.
- **Port ranges** (`27000-27100/udp`) in `extra_dnat_rules`, `open_ports` and
  published ports: iptables, firewalld and ufw spellings, overlap detection.
- **Re-apply hygiene under podman**: a container whose module left the stack
  is stopped and removed, and a changed `runtime:` or `docker_dir` stops the
  old stack before the new one takes its ports. Debian gets `catatonit` with
  podman (every `init: true` container needs it) and a podman older than
  4.4, which has no quadlets, is refused with the alternative named.
- **SSH host keys** live in `secrets/known_hosts`: accepted on first contact,
  a changed key is refused with the line to drop; a device name given to
  `enroll` must be a DNS label.
- **Generated secrets derive from hostname, module and key** — no longer from
  the fleet's host key and the role name, so `node-cloud` → `node` or a
  standalone render keeps every password (nothing had been applied with the
  old derivation). `secrets/seed` announces its creation, its id travels with
  the role script, and a machine refuses an apply under a different seed.
- **A misspelled fleet key is an error** (`rol:`, `netwrok:`, `admin.shh_keys`)
  with a suggestion; `kiwi-server <command> -h` shows that command's own
  usage and options.
- The role script is left 0600 on the machine after a run.
- **`auto_update: registry | off`** as a stack setting (default registry, as
  before); the images this project is sure about are pinned to a line:
  `weejewel/wg-easy:7`, `qmcgaw/gluetun:v3`, `nginx:1`,
  `portainer-ce:lts`. The others still follow `latest` until a verified pin
  list exists (ROADMAP, problem 13).
- **The host resolves through its own Pi-hole** on CoreOS and uCore
  (systemd-resolved: the stack's Pi-hole first, Quad9 behind it), so the
  fleet's names and the blocklist hold for the host itself, not only for its
  containers. Debian without systemd-resolved keeps its resolver.

## 2.4.0 — the fleet talks to its machines

- **A kiwi-updater app, properly**: the icon is kiwi-icons' kiwi-server
  (`svg/classic`, plus the PNG renders at 48 to 512 px), installed into the
  hicolor theme under the app id and shown by `kiwi-gui`, the app grid, the
  window and a new About dialog in the GUI's primary menu; `kiwi.manifest`
  lists `ssh` among the commands the app needs and describes what the app
  does now. Releases are version tags (`v2.4.0`), as kiwi expects.
- This release carries main's 2.2.0 (the Mullvad SOCKS5 records) on the
  podman runtime: the hosts directory is a read-only quadlet volume, the
  refresh timer is a host unit a re-apply removes again when
  `mullvad_socks` is turned off. From the review of that work: the refresh
  service is sandboxed (`ProtectSystem=strict`, one writable directory, no
  new privileges) and belongs to its timer, so an apply no longer waits for
  the download; the list URL is https (or a local file) only;
  `mullvad_socks_url` is required only while the records are on; the
  unchanged check uses coreutils alone; a directory that holds rendered files
  is made root's again on every apply. `kiwi-server doctor` reports the ssh
  client; a hand-installed Debian gets curl with podman.
- Fixed: the gateway preset's host unit (`kn-gateway.service`) required
  `docker.service`, which a podman node does not have, so systemd refused to
  start the LAN routing on every boot and apply; it now follows the runtime.
- **`kiwi-server apply <fleet> <host…>`**: the role script re-rendered and
  run on a running machine over SSH, as root, with `--force` — settings,
  modules and certificates change without a reinstall. `--no-run` only puts
  it at `/var/lib/kiwi-server/role.sh`; `--ssh USER@ADDR` reaches a machine
  whose name does not resolve yet.
- **`kiwi-server status`**: when the role was applied, the uptime and
  `kiwi-stack status` of every machine; `--porcelain` for the GUI.
- **`kiwi-server enroll <fleet> <host|name>`**: a WireGuard client on the
  master's wg-easy through its API, over an SSH tunnel to the master. A
  fleet host's config lands in `secrets/<hostname>.conf`, a device's in
  `secrets/devices/<name>.conf`; `--group` places the client in a client
  group's range, `--address` sets one, `--split` keeps a device's own
  internet, `--qr` prints the code for a phone, `--existing` fetches a
  config again.
- **`secrets/<hostname>.conf` is found by itself**: `vpn-client.wireguard_config`
  falls back to it (`fallback_file` on a file setting), so a node's block
  needs no line for it and its mesh address comes from the config.
- **`validate` renders the stack**: a module's `validate:` rules, a missing
  mesh address, two services on one port are reported by `validate` instead
  of first by `render` or `apply`. vpn-client checks that a custom provider
  has its config and a commercial one its key and addresses.
- GUI: Apply and Status on the host page.
- `lib/kiwiserver/remote.py` is the one place that talks SSH (the fleet
  backup uses it too); `lib/kiwiserver/wgeasy.py` speaks the weejewel
  wg-easy API.

## 2.3.0 — podman, one node role, Nextcloud without the master container

- **Podman quadlets** are the default runtime (`runtime: podman`): the
  rendered compose file is translated into `.container` and `.network` units
  under `/etc/containers/systemd`, started through `kiwi-stack.target`,
  updated by `podman auto-update`; `kiwi-stack` drives systemd instead of
  compose. `runtime: docker` keeps docker compose. Images are fully
  qualified; nginx resolves through the network's DNS; Portainer talks to the
  podman socket; gw.sh finds the bridge by the route.
- **Nextcloud from AIO's own containers**, no AIO master container, no docker
  socket: apache, nextcloud, database, redis, notify-push, and Collabora,
  OnlyOffice, Talk, recording, Imaginary, ClamAV, full-text search and the
  whiteboard as settings. Database and service secrets are generated from
  `secrets/seed` (a `secret` setting with `generate: true`), the same at
  every render. The AIO interface, its updater and its borg backup are gone;
  kiwi-stack and the coming backup module take their place.
- **One `node` role with presets** (`preset: gateway | cloud | minimal`;
  `modules:` replaces the list); `node-gw` and `node-cloud` stay as aliases.
- **The master's admin pages by name**: nginx runs inside the WireGuard
  server's namespace, `wg.<hostname>` and `pihole.<hostname>` answer on the
  mesh address only.
- **Client groups** (`vpn-server.groups`: subnet, reach, peers, internet,
  admin) replace the single isolated subnet, which stays as a legacy group.
- The renderer refuses two services on the same host port; `validate:` checks
  in module.yaml; an `object` setting type for structured settings.
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
- **The domain is a choice, `home` the fallback.** `kiwi-server init
  --domain`, a question in the GUI's new-fleet dialog, and `validate` warns
  about public TLDs and `.lan`, and refuses `.local`.
- **The fleet backs itself up to its nodes.** `backup: { hosts: [...],
  passphrase_file: ... }` sends the fleet directory (fleet file, secrets, the
  CA — never output/ or ISOs), encrypted with openssl, to
  `/var/lib/kiwi-server/backups` on those nodes after every render or build;
  `kiwi-server backup` by hand or `--local` to a disk; `kiwi-server restore
  --from user@node` gets it back on a fresh machine over plain SSH.
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
