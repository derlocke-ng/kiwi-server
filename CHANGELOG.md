# Changelog

## 2.35.0 — what a night of pulling plugs found

- `kiwi-server status` says when a machine is waiting at its locked disk, and with which
  command to open it, instead of calling it unreachable. On CoreOS the initramfs answers on
  the system's own SSH port, where a command used to suggest removing the host key.
- `status` shows how long the public certificates are good for, and calls it a problem when a
  certificate should have been renewed by now: the job that renews them does not fail.
- `prune` removes the locks of dead restic processes first. One left by a machine that lost
  power stopped it for good.
- kiwi-stack 0.4.0: a podman machine switches off a Docker daemon that runs nothing. It kept
  the published ports from the network after a reboot while every container looked healthy.
- kiwi-modules 0.19.0: a backup that dies or hangs lets its databases go, tries again an hour
  later and clears dead locks; a machine restored with two ACME accounts can renew again.

## 2.34.0 — the certificates come back too

- kiwi-modules 0.18.0: the backup holds a public site's Let's Encrypt certificates and
  ACME account, so a rebuilt machine does not have to ask again.

## 2.33.0 — status knows about backups and disks

- `kiwi-server status` says when a machine with the `backup` module last backed up, warns
  after a missed night and calls a week without one bad, and says whether the disk is
  unencrypted or opens only with a typed key. The app shows the same.
- A machine keeps its reboot window when rings it reaches by itself are enough to open
  its disk, even if another ring has to be forwarded in.

## 2.32.0 — a ring on this machine

- The ring named **`operator`** is served by kiwi-server itself, from the operator's machine,
  only while it is needed and only on that machine's loopback, with its keys in the secret
  store. It is for what no other machine can open: a fleet with one master, the machine
  that serves the other rings. `unlock.via` forwards it in.
- `kiwi-server vps install` gives every upload a name of its own: netcup booted the previous
  ISO of the same name, so a machine came up without the encryption it was built with.
- It also refuses to encrypt a CoreOS root on a server with too little memory. The first
  boot copies the whole root into memory, about 4 GiB is needed, and with 2 GiB it stops in
  emergency mode with nothing to see from outside.

## 2.31.0 — a master comes back with its identity

- A master's WireGuard state is kept with the fleet's secrets, refreshed by `apply` and
  `enroll`, and put back on a machine that has none before its stack starts. A rebuilt
  master keeps its server key and every peer, with nobody re-enrolling. Its data backup
  could not do that: it goes over the mesh, and the mesh is that identity.
- kiwi-modules 0.17.0: `kiwi-stack restore vpn-server` says why it will not, and a
  restore starts the whole stack again afterwards.

## 2.30.0 — an encrypted install with no console

- A Debian host with `unlock.ssh` and `debian.encrypt` has SSH in its initramfs from the
  first boot on, so a machine at a provider needs no web console: `kiwi-server unlock
  <fleet> <host> --manual` hands the one-time key over. The host key is taken at that
  first contact, for a key that dies at the first apply, and is pinned from then on.
- `kiwi-server unlock --manual` tries again when the connection fails: only a key the
  initramfs refused counts as a try.
- A machine whose ring has to be forwarded in does not reboot by itself any more: it
  would wait at boot until someone opened it.

## 2.29.0 — the relay

- kiwi-modules 0.16.0. **`vpn-server.unlock_relay`** lets a machine behind NAT ask the
  master's public port for its ring, past a gate that holds only the addresses enrolled
  machines last connected from: an `unlock.via` of `http://<master>:<port>`. The `tang`
  module's rings are reachable at the node's mesh address for it.

## 2.28.0 — the initramfs key can do one thing

- The initramfs has a key of its own, `secrets/unlock_ed25519`, which opens no running
  machine, and its line in `authorized_keys` allows what unlocking needs and nothing else:
  a forced command, and on CoreOS one forwarded port and `unlock.ssh_from`. Dropbear has no
  `from=` and ignores a key that names one, so `ssh_from` is refused on Debian.
- CoreOS's initramfs sshd takes forced commands only and forwards inwards only.

## 2.27.0 — CoreOS through the tunnel too

- `unlock.ssh` works on CoreOS: `dracut-sshd`, layered, with a host key of its own, and
  `kiwi-server unlock` forwards a ring in through it. Its host key goes on record at the
  apply, and the reboot after that apply is the first with SSH in the initramfs.
- A CoreOS machine whose ring is only reached through a forwarded port can install:
  Ignition sizes the root partition, because the growing at the first boot takes a ring
  that is not there yet, and the ring is forwarded in to add the key for the console.
- `kiwi-server unlock` knows it got in from what the session prints, not from how long
  it lasted; `--port` is for an initramfs reached through another port.

## 2.26.0 — a disk that opens by itself

- kiwi-modules 0.15.0: the **`tang`** module serves rings.
- A host's **`unlock:`** block names the rings that may open its disk. On Debian `apply`
  binds the disk with Clevis and gives the initramfs network; its key stays in another
  keyslot for the console. On CoreOS Ignition binds it at install, and the first `apply`
  adds a key for the console.
- `unlock.via` names the way to a ring that is not on the LAN. `unlock.ssh` puts an SSH
  server in the initramfs (Debian), with its host key pinned while the machine can vouch
  for it. **`kiwi-server unlock <fleet> <host>`** forwards a ring in through it, and
  `--manual` hands over the disk's key instead.

## 2.25.0 — old backups go

- **`kiwi-server prune <fleet>`** removes old snapshots from the backup nodes and frees
  the space: seven days, four weeks and six months are kept, and `--keep-daily`,
  `--keep-weekly` and `--keep-monthly` change that; `--dry-run` only says what would go.
  It runs from the operator's machine because a machine can only add to its backups and
  the node has no keys. The password reaches the node on stdin, for that one run.

## 2.24.0 — backups of a machine's data

- kiwi-modules 0.14.0 and kiwi-stack 0.3.0. The module **`backup`** keeps a machine's
  data with restic, encrypted, on a node running the new module **`backup-server`**:
  every night and with `kiwi-stack backup`. `kiwi-stack restore MODULE...` puts the
  newest back and stops only those modules; `kiwi-stack snapshots` lists what is stored.
- Each machine has its own repository and can add to it but not delete. Passwords come
  from the fleet's seed, so the seed brings the backups back.
- A module says what it wants kept in a `backup:` block (docs/modules.md).

## 2.23.0 — folders the apps share

- kiwi-modules 0.13.0. **`cloud.shared_folders`** and **`cloud.read_only_folders`** put
  folders into every user's Nextcloud that other apps on the host can use.
  **`immich.external_dirs`** mounts one read-only for an External Library.
  **`paperless.user_id`** lets Paperless share a consume folder with Nextcloud, and
  **`paperless.filename_format`** gives its archive readable names.
- kiwi-modules 0.12.1: Paperless's login page no longer answers 403.

## 2.22.0 — OpenCloud, Immich and Paperless-ngx to try out

- kiwi-modules 0.12.0: three new modules for a node, `opencloud`, `immich` and
  `paperless`, each behind the reverse proxy with a setting for where its data lives.
  None is in a preset: add them to `modules:`.
- A new Nextcloud installs end-to-end encryption by default.

## 2.21.0 — a calendar's way to Nextcloud

- kiwi-modules 0.11.0. **`hub.caldav`** lets a hub's calendar reach a Nextcloud through
  the hub, from the mesh only, on a public hub too. **`cloud.cors_origins`** lets hubs on
  other addresses, such as Armory, use CalDAV from the browser.
- **Nextcloud sees who its clients are.** It recorded every client as `127.0.0.1`, so
  brute-force protection counted nothing. The gateway no longer hides LAN clients of
  published ports behind the bridge address.
- A master's containers can reach the mesh: the answers were being dropped.

## 2.20.0 — local traffic stays local

- **A node's Pi-hole and hosts file answer with the LAN address of the nodes on its
  own LAN**: machines with a static address in its subnet, worked out from the fleet
  file. A client at home went through the tunnel to the master and back to reach a
  machine on its own switch; on the test network a page went from 176 ms to 48 ms.
  Mesh clients still ask the master's Pi-hole and get mesh addresses.
- kiwi-modules 0.10.0, and with it 0.9.1: **the `gateway` module works for LAN
  clients**. Two of its rules were invalid and firewalld refused forwarded traffic, so
  it had never carried a real LAN client, and the rule keeping LAN clients off the
  container network was missing. A health check of the form `[CMD, ...]` now runs
  without a shell, so Collabora no longer reports unhealthy while working.

## 2.19.0 — a public relay knows its members

- kiwi-modules 0.9.0: `nostr-relay` authenticates connections (NIP-42), and who
  counts as a member is a setting, `members: approved | registered`. A signed-in
  member may post what other keys signed, as a hub's boards, groups and gift wraps
  are; anyone may send a gift wrap to a member or sign up with proof of work; bans
  beat membership. It runs our own strfry image, release 1.1.3 with a small patch,
  because stock strfry does not tell the write policy who a connection is.
- kiwi-stack 0.2.4: behind chrony a stack waits with `chrony-wait.service`.
  `systemd-time-wait-sync` sat out its 90 seconds at every boot there and failed.

## 2.18.0 — the app shows what the machines say

- **The app has an overview**: machines online, services running, what needs a
  look, a card per machine with memory and disk. Each host has an *Overview* tab
  beside its settings: system, load, memory, disks, every service, the tunnel, the
  web interfaces, when the role was applied. The sidebar row says whether the
  machine answers, how long it has been up and how many services run. It asks once
  a minute while it has the focus, and opens the fleet you had last.
- **`kiwi-server status --porcelain`** returns `state`, `problems`, `notes` and
  `facts` per host, from one read-only probe, for every host at once. What counts as
  a problem is decided there: a service down, a tunnel missing, a VPN client that
  does not forward, a disk filling up, a clock that is off. A machine that booted a
  moment ago is late, not broken, and a healthcheck still starting is not a failed
  unit.
- **`apply` and `status` find the machine**: its name, its static address, the
  master's public address or its mesh address, whichever answers. Only addresses
  whose host key is already on record count, so a stranger at the same address on
  another LAN never gets a role script; `--ssh` still says exactly where.
- `kiwi-server secrets status --porcelain`, with when the timer locks the store;
  the app has *Unlock* and *Lock*. `list --porcelain` adds the web names of a host,
  its update window and the fleet's domain and mesh.
- The app showed no help for settings whose text has `<hostname>` in it: fixed.

## 2.17.0 — secrets/ as one encrypted file

- **`kiwi-server secrets init|unlock|lock|status <fleet>`**: `secrets/` becomes
  `secrets.age`, encrypted with `age` to one key in the GNOME Keyring and one
  recovery key shown once. `init` checks that both open the file before it
  removes the plain files.
- `unlock` opens it into memory, in `$XDG_RUNTIME_DIR`, with `secrets` a link
  there, so everything else works as before. A timer locks it again after 8 hours
  (`--for`). A command that changed something saves it before it exits, so a
  power cut cannot take a new disk key with it.
- **A locked store stops every command that reads the fleet**, so nothing can
  make a new seed in place of the one locked away.
- Stores nothing by itself: a fleet stays plain files until `secrets init`.
- Not yet: `output/` in memory, an encrypted fleet file, adding a way of
  unlocking to an existing store, and the fleet backup in `age`.

## 2.16.0 — a public hub, and nothing else public with it

Carries kiwi-modules 0.8.0.

- **`reverse-proxy.public`**, for a master, publishes 80 and 443. A site whose
  name is outside the fleet's domain (`hub_domain`, `relay_domain`) is public;
  every other site, the admin pages among them, answers the mesh only, enforced
  in nginx per site.
- **Let's Encrypt** certificates for the public names, renewed by a host timer.
  nginx starts on the fleet's certificate until the first one is issued, and
  `acme_staging` tries it out on the test service.
- **A public relay is not an open one**: anyone may read, the mesh may post, and
  from the internet only the keys in `nostr-relay.write_pubkeys`. `open_writes`
  is the old behaviour, by name.
- **The relay reads the client's address from `X-Real-IP`**, not
  `X-Forwarded-For`, which a client can pre-fill.
- Run on a test master with real names. From outside: the hub and relay answer
  with a trusted certificate, `wg.` and `pihole.` answer 403, a signed post from
  a stranger is refused even with a forged address, and only 22, 80 and 443 are
  open.

## 2.15.0 — hubs live on masters

- **`hub` and `nostr-relay` are for masters now** (kiwi-modules 0.6.0): a hub may
  be public, and a master is the machine made to face the internet. A node that
  lists them is refused, and the node role's `hub` preset is gone; a master
  lists them in its `modules:`.
- Run on a test master: a device on the mesh gets the hub's page and config, the
  relay's NIP-11 document and a websocket, with the fleet CA verifying.
- Public hubs are not here yet: they need mesh-only rules on the master's other
  sites before 443 can be published (ROADMAP).

## 2.14.0 — measure the MTU instead of guessing it

- **`kiwi-server mtu <fleet> <node|device|address>`** sends don't-fragment
  pings through a tunnel and finds the largest multiple of 16 that arrives
  whole. A node measures from its own VPN client, because the client's firewall
  drops pings coming in; a device is measured from the WireGuard server.
- A reply that had to be reassembled does not count as fitting: WireGuard's
  outer packets may be split on the way even when the ping comes back.
- It says what to set. For a device, that device's own `MTU =`, with nobody
  else affected. For a node, `wg_mtu` on it and on its master; `--write` puts
  that in the fleet file, changing only those lines.

## 2.13.0 — a server at netcup without the web panel

- **`kiwi-server vps`** drives netcup's Server Control Panel API. `vps login`
  signs in through the device flow, so no password passes through here, and
  keeps a refresh token in `secrets/netcup.token`. `vps list` shows your
  servers, and `vps probe` what one really is, the `disk:` it needs among it.
- **`vps install <host>`** does what the first hardware round did by hand:
  upload the built ISO, boot it, wait for the installer's power-off, detach it,
  put the disk first again, remove the ISO from netcup's store, power on, and
  forget the machine's old SSH host keys. It asks before wiping, and refuses
  an ISO that carries the role's secrets or a `disk:` that is not the server's.
- A host names its server with `vps: { server: ... }`; a master is found by its
  `wg_host`.

## 2.12.0 — service passwords no longer have to be written down

- **Service passwords left out of the fleet file are generated** from
  `secrets/seed` (kiwi-modules 0.5.0): `wg_password`, `pihole_password`,
  `transmission_password`, `sftp_password`, and the cloud's and Portainer's
  `admin_password`. A password that is written down is used as before. Portainer
  left empty used to wait on its first-run page; it now starts with a generated
  password.
- **`kiwi-server secrets show <fleet> <host> [setting]`** prints a host's
  password, or the names of those it has. It reads the fleet on this machine.
- **`validate` warns** about passwords written in the fleet file that need not
  be, and about `admin.password`. A warning for now: refusing them waits for the
  secret store.
- `kiwi-server init` writes a fleet file with no passwords in it.
- Taking a password out on a running machine changes it. Nextcloud's and
  Portainer's are only read at their first start, so there the old one stays
  in force.

## 2.11.0 — a disk key that outlives nothing

- **`debian.encrypt` needs no passphrase**: kiwi-server makes a short one-time
  key per install (`secrets/luks/<host>.once`), typed at the first boot, and
  the first `apply` replaces it with a generated one
  (`secrets/luks/<host>.key`) before the role's secrets reach the machine. The
  keys travel on stdin and live on tmpfs on the way. A run that is cut off can
  be repeated.
- **A master also gets a new volume key** at that apply (`cryptsetup
  reencrypt`, some minutes; `debian.reencrypt` for any host). Replacing a
  passphrase keeps the volume key, so someone who held the ISO and the disk
  during the install could otherwise still decrypt later snapshots.
- A literal `debian.passphrase` still works, unchanged, and `validate` now
  warns that it is in the fleet file and on every ISO.

## 2.10.0 — install media carry no stack unless asked to

- **`build` and `script` make the base system by default**: hostname, network,
  the admin user and the update policy, with `apply` bringing the role and its
  stack afterwards over SSH. Before, a plain `build` put every secret of the
  stack on the ISO. `--bare` is still accepted and now only says the default.
- **`--offline` asks for the old behaviour by name**, for a machine that
  cannot be reached over SSH, and says when it is used that whoever holds the
  ISO or script holds those secrets.
- The GUI's Build makes base-system ISOs and says so; its script preview still
  shows the whole role.
- Not yet: a Debian host with `encrypt: true` still has its LUKS passphrase in
  the ISO's preseed, until the one-time key lands (ROADMAP, November wishes).

## 2.9.1 — what a reboot exposed

- **A master's mesh clients have internet after a reboot** (problem 61): the
  VPN client's namespace copied the host's `ip_forward` while it was still 0,
  so nothing could leave through it until the stack was restarted.
  `vpn-client` sets the sysctl itself. A master applied with an earlier
  version needs one re-apply.
- **Tor no longer races the tunnel** (problem 59): where it shares the
  WireGuard server's namespace, it may bind the mesh address before `wg0` has
  it.
- **The stack waits for the clock at boot** (problem 60): a machine that came
  up with its clock ahead and opened its tunnel before NTP stepped it back
  was shut out of the mesh until real time caught up. `kiwi-stack.service`
  starts after `time-sync.target`, bounded at 90 seconds where there is a VPN
  client.
- Carries kiwi-modules 0.4.3 and kiwi-stack 0.2.3 (`VENDORED`).

## 2.9.0 — a hub and its relay

- **A hub and its relay** (kiwi-modules 0.4.2): the `nostr-relay` module
  (strfry behind the reverse proxy at `relay.<host>`, a mesh name the
  fleet's DNS resolves and the fleet CA signs) and the `hub` module (a
  distribution built with kiwi-framework's `container/Dockerfile`, Armory's
  `ghcr.io/derlocke-ng/armory` by default, behind the proxy at
  `hub.<host>`; its relays, policy and public area set from the module's
  settings and written to the page's `hub.local.json` at start). The node
  role's `hub` preset is the two behind the VPN client: a private hub as it
  comes, semi-public with `public_relays` and `public_mounts` under `hub`.
  `hub` also has `account_mounts` and `require_account` (mounts that ask for
  an account, a username and password, not a key alone; for kiwi-framework
  0.19) and `space_relays` (a space's own relays).
- **`tests/check-version.sh` also compares the vendored kiwi-modules** with
  the package's own version: kiwi-modules 0.4.0 and 0.4.1 went out with the
  two disagreeing, and vendoring either would have carried that here.

## 2.8.0 — the stack network and the tunnels get an MTU each

- **The stack network and the tunnels have an MTU each** (problem 40):
  `bridge_mtu` (default 1500) for the stack's bridge and `wg_mtu` (default
  1376) for gluetun's `WIREGUARD_MTU`, wg-easy's `WG_MTU` and the server's
  own `wg0`. One `mtu` of 1412 for all three split every full-size tunnel
  packet in two on the bridge, which carries the encrypted packet, 60 bytes
  bigger over IPv4 (80 over IPv6) — TLS stalls and stuttering streams through
  the VPN on the v1 fleet.
- **WireGuard's padding counts**: a packet is padded to a multiple of 16 as
  far as the sending peer's MTU allows, so a peer with a bigger MTU (the
  commercial exit) grows it; on the v1 master 1352 became 1360 and 1420
  bytes on a 1412 bridge. `validate` refuses a `wg_mtu` whose padded packet
  plus 80 bytes does not fit `bridge_mtu` (at most 1408 under 1500), or one
  below 1280. 1376 is a multiple of 16: 1436 bytes over IPv4 fits a
  1452-byte DS-Lite path, 1456 over IPv6 a 1460 one.
- **The old `mtu`**: a fleet that still sets it gets it as `wg_mtu`, the
  bridge at 1500, and a warning to rename it; `mtu` and `wg_mtu` together
  are an error, and so is the usual `mtu: 1412`, which no longer fits once
  padded — drop it.
- **The WireGuard server's `wg0` is set to `wg_mtu`** in `start.sh`, after
  the iptables-nft shim: wg-quick sized it from the bridge (1332 under a 1412
  bridge) while the clients were told another MTU.
- **The MSS is set, both ways**: wg-easy's `start.sh` and the VPN client's
  post-rules set every forwarded SYN to `wg_mtu - 40` (`--set-mss`, which
  never raises a smaller one) instead of `--clamp-mss-to-pmtu`, which reads
  only the interface a SYN leaves by — on the v1 master the way back was
  wider than the exit and every first full-size upload segment bounced.
- **A node with the gateway module sets the TCP MSS** in its VPN client by
  default (`vpn-client.mss_clamp`, as on a master): LAN clients routed
  through it advertise MSS 1460, more than the tunnel carries. The host's
  own `mss_clamp: false` still wins.
- **Namespace sharers are never recreated alone**: a test keeps `--no-deps`
  out of kiwi-stack, lib.sh and the rendered stack (compose 2.21 gave a
  sharer the literal network mode `service:kmvpn-server`, and the v1
  master's Pi-hole stayed down ten minutes); the README and the runbook say
  how to recreate one by hand.
- **Upgrading a running master**: the new `wg_mtu` recreates wg-easy, and
  Pi-hole, Tor and nginx in its namespace with it (the apply restarts the
  whole stack). Client configs already issued keep their old `MTU =` until
  downloaded again from wg-easy; apply the nodes too, and re-run
  `kiwi-server openwrt` for a router.
- `kiwi-server openwrt` writes the master's `wg_mtu` as the router
  interface's MTU, not whatever an older client config says.
- tests/golden: the MTU lines of every stack, `ip link set dev wg0` in the
  master's `start.sh`, the clamp in m1's `post-rules.txt`.
- Carries kiwi-modules 0.3.0 (`VENDORED`), where the renderer and module
  side of this change is developed.
- Applied to the test fleet's two masters and a node before the release: no
  packet split through the mesh and the exit. The numbers are in ROADMAP
  problem 40, with problems 59 and 60, found on the way.

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
- **The split's flip**: kiwi-modules and kiwi-stack are developed and
  released in their own repositories; this tree carries them as vendored
  copies pinned by tag in `VENDORED`. `tools/vendor.sh` brings a release in
  (from a checkout next to this one or from github.com), `--check` reports
  a tree that differs from its pins and runs in CI; `tools/split-sync.sh`,
  which copied the other way, is gone.
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
