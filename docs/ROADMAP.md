# Roadmap and known issues

The state of kiwi-server after 2.4.0, from a verified review of the whole
tree (six areas, every finding checked against the code by a second reader),
merged with the direction agreed in October 2026: kiwi-server becomes a
control plane that can run on a node, with the desktop app as one of its
clients. [design/control-plane.md](design/control-plane.md) describes that
design; this file says what is done, what is wrong, and in which order the
rest comes.

## Done

- Targets: CoreOS/uCore (Butane 1.6.0, Ignition, Zincati windows, uCore
  autorebase, staged reboots), Debian (full preseed, late command, unattended
  upgrades), one first-boot unit that retries every boot until the marker
  exists, unattended ISOs for both (CoreOS signature-checked, Debian
  checksum-checked), the toolchain natively or in a container.
- Roles and modules: bare, master, node with presets gateway/cloud/minimal
  (node-gw and node-cloud as aliases); twelve modules rendered from one
  compose fragment each into podman quadlets (default) or docker compose;
  settings schema with validation, generated secrets from the fleet seed,
  file settings with a conventional fallback, module `validate:` rules, port
  collision check, static container addresses, DNAT through the VPN client,
  nginx per service name with fleet-CA certificates, one resolver chain
  (node Pi-hole → master Pi-hole → Quad9), Mullvad SOCKS5 records refreshed
  by a host timer, client groups on the master.
- Management: apply, status, enroll (hosts, phones, routers; groups, split
  tunnel, QR), fleet backup to the nodes and restore from one, CA, OpenWrt
  scripts, doctor, a GTK app over the CLI, bash completion, the kiwi-updater
  integration with the kiwi-icons artwork, 84 unit tests and a CLI smoke test.
- Every decision about addressing, naming, DNS, podman and enrollment is
  implemented. 72 of the 75 findings of the 2.1.0 review are closed.

## Not yet proven

No target has booted end to end from this pipeline on real hardware, and
the first-boot library is parsed and shellchecked but never executed by the
tests. Everything under "Problems" that concerns the machine side was found
by reading, not by booting. A real first boot of each target and a
dead-node drill come before any new feature.

## Problems

Ordered by what breaks first. Each item names the file so it can be fixed
without re-reading the review.

Stack and first boot:

1. The first boot cannot fail on the stack. `kiwi-stack start` returns 0
   whatever the container units do, so the role writes `role.done` and
   `status` says applied while nothing runs; cold image pulls under the 900 s
   unit timeout make the cloud preset the likely victim. Pre-pull the images
   with progress, check every service after the start, fail loudly, and set
   `StartLimitIntervalSec=0` or `Restart=on-failure` in the quadlets.
   (roles/common/lib.sh, lib/kiwiserver/quadlet.py)
2. Secrets world-readable under podman: every quadlet is installed 0644 with
   the environment inline, while the compose file is 0600. Write quadlets
   0600 and move secret-typed settings into a per-service `EnvironmentFile`
   (0600). (lib.sh, quadlet.py, modules.py)
3. The master's Pi-hole and nginx share the WireGuard server's network
   namespace and both bind 80/443. Add a dns `web_port` setting rendered as
   `FTLCONF_webserver_port` (8081 when a namespace is shared), point the
   proxy block and the localhost publish at it, and let the renderer refuse
   two services in one namespace with the same listening port.
   (modules/dns, modules/reverse-proxy, modules/vpn-server, modules.py)
4. A node's own service names resolve to 127.0.0.1 inside its containers
   (podman copies the host's /etc/hosts), so Nextcloud cannot reach
   Collabora, Talk or notify-push through its own URL. Emit `AddHost=` /
   `extra_hosts` with the proxy's container address; mount the fleet CA into
   the Nextcloud container (`TRUSTED_CACERTS_DIR`). (modules.py, quadlet.py,
   modules/cloud)
5. Port ranges are unsupported in DNAT rules, `open_ports`, the collision
   check and the firewall opener (firewalld wants `a-b`, ufw `a:b`). This is
   the first prerequisite of the game module.
6. A Debian node gets podman without `catatonit`, so every `init: true`
   container (all of Nextcloud) refuses to start; a bookworm host gets a
   podman without quadlets. Install catatonit, assert podman ≥ 4.4.
7. A failed first boot leaves no diagnosis: no ERR trap, no failure marker,
   `status` says "never". Write `role.failed` with the step and the last log
   lines; show it in status and the GUI.
8. Switching `runtime:` on a running machine rewrites stack.env before the
   old stack is stopped, so both stacks fight for the ports. Read the old
   runtime first and stop the old stack.
9. Under podman a module removed from the stack has its quadlet deleted but
   its container keeps running and its ports. Stop the old services before
   rewriting stack.env (the docker path has `--remove-orphans`).
10. The DNAT masquerade makes every mesh client appear as the VPN client's
    address, so Nextcloud's and Vaultwarden's rate limits act on one source.
    Drop the masquerade where the mesh route exists.
11. The Nextcloud data directory mount has no SELinux label.
12. On CoreOS/uCore the host's resolv.conf points at the upstream resolvers,
    so the host and (under podman) every container bypass the local Pi-hole
    and never see the fleet's split-horizon names.
13. Every image floats on `latest` with `AutoUpdate=registry`: unattended
    major upgrades on every node weekly. Pin by major tag per module, make
    auto-update a stack setting, pin `weejewel/wg-easy:7` today.
14. The install media reinstalls the machine on every boot while the stick
    stays first in the boot order. *Since 2.7.0 the media power the machine
    off when the install is done (`install.on_done: poweroff`), so the stick
    is removed before the next boot.* Still open: an install guard in both
    ISOs (a fingerprint of a kiwi install on the target disk, checked by a
    `--pre-install` script and the preseed's early command) with a
    `--reinstall` override, for media that cannot be removed.
15. The role script with every secret stays at /var/lib/kiwi-server/role.sh
    for the machine's lifetime; the Debian LUKS passphrase also survives in
    d-i's saved debconf database. Shred after a successful run, clean
    /var/log/installer.
16. uCore's first boot runs Zincati beside the unsigned rebase, and
    `updates.enabled: false` is applied by an Ignition preset the uCore image
    probably overrides. Write the Zincati disable file too and handle the
    rpm-ostreed timer in the role.

Management and security:

17. SSH host keys are never provisioned: the first apply, status, backup or
    enroll to a fresh machine fails under BatchMode. Use
    `StrictHostKeyChecking=accept-new` with a known_hosts under secrets/,
    recognise the "changed" message after a reinstall. (remote.py)
18. The fleet seed is created silently on first use, and generated secrets
    include the host key and role name in their derivation, so a rename, a
    second checkout or an older backup rotates every database password and
    the next apply breaks a running cloud. Freeze the derivation to
    (hostname, module, key) with a compatibility path, make seed creation
    explicit, record a seed id in stack.env and refuse a mismatch. This must
    happen before the split. (config.py, roles.py)
19. The CA has no lifecycle: an expired CA is reused, old leaves are reused
    after a rotation, leaves renew only when someone happens to render in
    the last 30 of 825 days. Verify leaves against the CA, report days left
    in status, warn under 60 days, add `ca --rotate`. (certs.py)
20. `apply` has no lock, no record of what is on the machine, no diff or
    dry-run; a renamed host targets a name that does not resolve. Add a
    state file, `apply --diff`, and a `rename` command.
21. The fleet backup is encrypted but not authenticated, and it is restored
    from the nodes it will later trust. Document the two bootstrap secrets
    now; move to `age` if the format is touched.
22. `enroll <name>` is unvalidated and used in a path and as the wg-easy
    client name; validate against a DNS label.
23. Mesh address collisions: `enroll --address` is not checked, hand-set
    `vpn_ip` values are not compared.
24. The OpenWrt script is written with the router's private key to the
    working directory; warn or take an output path written 0600.

UX and ecosystem:

25. A misspelled top-level fleet key is silently ignored, so `rol:` turns a
    master into a bare machine. Check keys against the defaults tree with
    "did you mean".
26. `kiwi-server <cmd> --help` prints the usage twice and exits 2; the
    per-option help strings are never shown. Give every subparser its own
    usage; document the exit codes.
27. The first `validate` after `init` fails without an ed25519 key and
    without jinja2; the README's `pip install --user` refuses on Debian.
    Pick an existing key in `init`, per-OS install lines.
28. The GUI lags the CLI: no enroll, backup, restore, openwrt or ca, no
    address on apply/status, no tls or backup groups, validation only after
    save; its Stack button shows the compose file, not the quadlets the
    machine runs. All of this is porcelain plumbing.
29. Not every command has `--porcelain`; the exit-code contract is
    undocumented. The API in the control plane design replaces this.
30. docs/REVIEW-2.1.0.md should be retired to its status table.
31. The kiwi-network site describes 2.2.0 (tracked in a separate report).
32. The mesh subnet is spread over six settings that do not follow each
    other: the stack's `mesh_subnet`, the vpn-server's `server_ip`,
    `wg_subnet`, `wg_default_address` and `groups` ranges, and tor's
    `socks_policy_accept`, all defaulting to 10.8.x. A network beside
    another one (the test network on 10.9.0.0/16 next to a live 10.8) has to
    set all six by hand, and one that is missed leaves part of the stack on
    10.8. Derive them from `mesh_subnet` (the server at .0.1, the groups at
    .1 to .4) unless set, and let `validate` refuse a setting outside it.
    (roles/common/stack.yaml, modules/vpn-server, modules/tor, resolver.py)
33. `init` writes `~/.ssh/id_ed25519.pub` into the fleet file whether or not
    it exists; an operator whose keys live only in an agent gets a file that
    fails its first `validate`. Pick a key that exists (`ssh-add -L`, any
    `~/.ssh/*.pub`) or write a clear placeholder. (cli.py, the init
    template; part of problem 27)
34. `validate` accepts `wg_host: TODO-vps.invalid` and
    `wireguard_addresses: TODO/32`: strings are not checked for shape, so a
    passing `validate` does not mean the placeholders are filled in. Check
    addresses, subnets, hostnames and `x.x.x.x/nn` settings by type.
    (schema.py, modules.py)
35. `apply` runs `sudo sh -c` under `BatchMode=yes`: an admin user whose
    sudo wants a password (an Ubuntu VPS set up by a provider) fails with
    sudo's prompt in the error, not with what to do. Probe `sudo -n true`
    first and name the sudoers line. (remote.py)
36. No `disks:` setting: a node with a system disk and a data disk (the mini
    PC: NVMe plus a 1 TB SATA SSD) has to get the second disk formatted,
    mounted and in fstab by hand before the first apply, and the ISO path
    cannot do it at all. A per-host list of {device, mount, fs, options},
    rendered into the preseed's late command, the Butane filesystems and
    the role script (mkfs only when the disk has no filesystem).
    (targets/debian.py, targets/coreos.py, roles/common/lib.sh)
38. *Found on hardware, fixed in 2.7.0.* A first boot on Debian ran apt
    before the resolver was ready: `network-online.target` was satisfied,
    `apt-get update` failed with "Temporary failure resolving" and the bare
    role went on without updates; a stack role would have hit the same wall
    at its first pull. `ks_wait_dns` in lib.sh now waits up to two minutes
    for a name to resolve before the first apt or pull. Open: the role unit
    could order itself after `systemd-resolved` / `dhcpcd` where they exist.
39. *Found on hardware, fixed in 2.7.0.* `apply` offered the operator's
    agent keys to the machine, not the key the fleet gave it: the first
    apply to the VPS failed with "Permission denied (publickey)" because the
    test network's own key lived in `secrets/` and not in the agent.
    `remote.configure` now takes the private keys found next to the
    `admin.ssh_keys` `.pub` files and uses them exclusively. (remote.py,
    cli.py)
41. *Found on hardware, fixed in 2.7.0.* The first podman apply on a fresh
    machine failed in the quadlet cleanup: `grep -l … /etc/containers/systemd/*.container
    | xargs -r rm -f` ran over globs that matched nothing, grep exited 2 on
    the literal patterns, and `pipefail` made that a failed apply — reported
    as "fleet CA trusted", the last step announced, with `xargs` as the
    culprit. Now one file at a time, and `role.failed` names the function
    the command sat in. Open: a test that *executes* lib.sh's functions in
    a scratch root (the library is parsed and shellchecked, never run).
42. *Found on hardware, fixed in 2.7.0.* On the first master (Debian 13, an
    nf_tables host) wg-easy's `wg-quick up` failed: every `iptables` call of
    the master's `start.sh` is the image's *legacy* xtables, and inside the
    container those return "Permission denied (you must be root)" on every
    table, whatever modules the host has loaded, while `iptables-nft` in
    the same image works. `start.sh` now uses `iptables-nft` where the
    image has it, and `WG_POST_DOWN` is a no-op instead of wg-easy's
    legacy default. gluetun was never affected: it picks nft itself.
43. *Found on hardware, fixed in 2.7.0.* The LUKS prompt of an encrypted
    Debian read a German keyboard as US (y/z swapped, no dash): the
    initramfs does not load the console keymap unless
    `KEYMAP=y` in `initramfs.conf`. The late command sets it and rebuilds
    the initramfs when the disk is encrypted.
44. `kiwi-stack verify` ran right after the start and passed while wg-easy
    was still about to fail its `wg-quick up`; a minute later three
    containers were down and the role had said "applied". Verify should
    look twice, with a grace period (30 s) and `RestartCount` in between;
    and when the container that owns a shared namespace restarts, podman's
    `--replace` fails while the dependents still run ("has dependent
    containers which must be removed before it") until systemd has stopped
    them — `BindsTo=` on the dependents, or stopping them first, would make
    the restart clean. (roles/common/kiwi-stack, lib/kiwimodules/quadlet.py)
45. A Debian host has no `iptables` command (trixie installs none); the
    gateway module's `gw.sh` runs on the host and uses it. Install
    `iptables` (nft-backed) with the gateway module on Debian, or write
    `gw.sh` in nft. Not hit yet: the gateway node of the round is uCore.
46. `disk:` is a guess until the machine is seen: the third VPS had no
    `/dev/vda` (another virtualization than its sibling at the same
    provider) and coreos-installer stopped in the live ISO with "getting
    absolute path of /dev/vda: No such file or directory". `kiwi-server
    probe` would have told; for media that cannot be probed first, a
    `disk: auto` that lets the live ISO pick the only (or the largest
    non-removable) disk through a `--pre-install` script writing the
    installer config, and the preseed's `partman-auto/disk` from an early
    command the same way. (iso.py, targets/debian.py)
47. *Fixed in 2.7.0.* When the live ISO's install failed, its emergency
    shell was useless: the live system's root is locked and no user exists.
    The live environment now gets its own Ignition (`--live-ignition`): root
    and the admin user with the fleet's console password and keys, and a
    unit that prints `lsblk` to the console before the installer looks for
    `disk:`. Open: SSH into the live system over the provider's network for
    a VPS whose console is slow. (iso.py)
48. *Found on hardware, fixed in 2.7.0.* On the first gateway+cloud node
    the VPN client, Pi-hole, Vaultwarden and Nextcloud's apache never
    started: "IPAM error: requested ip address 172.128.0.2 is already
    allocated". The Nextcloud AIO containers have no fixed address, so
    netavark gave them the first free ones, .2, .3, .4, .6, .7, .8 — the
    very addresses the modules reserve — and whichever started first won.
    The stack network now has an `IPRange` (the third quarter of the
    subnet, 172.128.0.128/26 of a /24) that containers without a fixed
    address draw from, the renderer refuses a fixed address inside it, and
    the compose file carries the same `ip_range`. (modules.py,
    quadlet.py, modules/networks.yml.j2)
49. *Found with 48, fixed in 2.7.0.* A changed `.network` quadlet changed
    nothing on the machine: its generated service only creates the network
    when it is missing. The apply now remembers the installed network
    units' checksums and, when one changes, stops the stack and removes the
    podman network; and because the quadlet's generated oneshot stays
    "active (exited)" after that and would not run again (every container
    then failed with "network not found"), it stops that unit too, so the
    start recreates the network first. (roles/common/lib.sh)
50. *Found on hardware, fixed in 2.7.0.* Pi-hole never started on the
    gateway+cloud node: "listen tcp4 :53: bind: address already in use".
    podman's own resolver (aardvark-dns) listens on the stack network's
    gateway address, port 53, and a container publishing 53 on every address
    collides with it. When the stack runs a resolver, the role writes
    `dns_bind_port = 5533` into `containers.conf.d`; podman moves aardvark
    and redirects the containers' lookups itself. (roles/common/lib.sh)
51. *Found on hardware, fixed in 2.7.0.* **Nothing behind a VPN client had
    NAT out of the tunnel.** In `post-rules.txt.j2` the comment above the
    masquerade ended in an inline `{% if %}…{% endif %}`, and Jinja's
    `trim_blocks` swallowed the newline after it, so the rendered line read
    `# NAT: … tunneliptables -t nat -A POSTROUTING -o tun0 -j MASQUERADE` —
    a comment. gluetun's own traffic worked (the healthcheck, DoT), every
    forwarded packet left with a private source and never came back: on
    the master, the nodes' and clients' internet; on a node, the LAN
    clients the gateway routes. The golden files carried the bug, which is
    what a golden test does; found by `wget` from inside the master's
    WireGuard server. (modules/vpn-client/post-rules.txt.j2, tests/golden)
52. *Found on hardware, fixed in 2.7.0.* On the gateway node the VPN client
    never got past its handshake: the master's replies arrived on the LAN
    NIC and were dropped by the gateway module's own host rule, `-I FORWARD
    -i <LAN> -d <docker subnet> -j DROP`, which stands above the
    established-accept and so killed every reply to every connection a
    container started — and every connection netavark DNATs from the LAN
    to a published port (Pi-hole, nginx). The rule meant "LAN clients may
    not reach the containers' own addresses"; it now matches
    `--ctstate NEW ! --ctstatus DNAT` only. Review finding R28 had put the
    DROP on top on purpose; under podman that was the bug. Proven with the
    rule's packet counter and a capture inside the VPN client's namespace.
    (modules/gateway/gw.sh.j2, tests/golden/m1)
53. *Found on hardware, fixed in 2.7.0.* A gateway node's Pi-hole answered
    the node's own names (`cloud.papaya.home`, `papaya.home`) with the
    reverse proxy's *container* address, 172.128.0.5, to every LAN client:
    the `AddHost=` entries meant for the containers went into Pi-hole's
    container too, and dnsmasq serves its `/etc/hosts`. The master's Pi-hole
    answered the mesh address correctly. Pi-hole's container gets no
    `AddHost=` now and serves the fleet's records — and dnsmasq runs with
    `no-hosts`, because podman copies the *host's* `/etc/hosts` into the
    container, where the node's own names point at 127.0.0.1 (the role
    writes them so), and Pi-hole served that too. (modules.py,
    modules/dns, tests/golden/m1 and gate)
40. **MTU.** The stack's 1412 fits DSL; on 5G and other mobile paths (often
    IPv6-only with 464XLAT, nested encapsulation) the owner sees it fail.
    Measure instead of guessing: a path-MTU probe in `enroll` and in the
    client config it writes (`--mtu`, a lower default such as 1280 for
    phones), MSS clamping to the path MTU on the master's `wg0` forward
    chain so TCP works whatever the client's MTU, and `kiwi-stack health`
    reporting fragmentation. (modules/vpn-server, cli.py enroll, routers.md)
37. A node publishes its services on the host (80/443 of the reverse proxy,
    the sftp port, what `published_ports` lists) because a node lives on a
    LAN; on a public host that is the internet. A master is safe there (its
    nginx and Pi-hole live in the WireGuard server's namespace), so today
    a VPS has to be a master. Add `reverse-proxy.network_mode: vpn-client`
    (the proxy inside the VPN client's namespace, reachable at the mesh
    address only, as on a master) or a stack setting `exposure: lan | mesh`
    that binds every published port to the mesh, and let `validate` warn
    when a node has a public address and publishes ports.
    (modules/reverse-proxy, modules.py, roles/common/stack.yaml)

## Todo, in order

The six steps agreed with the owner, with the items above folded in.

1. **Bug round one** (problems 1 to 13, 17, 18, 22, 25, 26): the stack must
   be trustworthy before anything is built on it. Then a real first boot of
   each target and a dead-node drill, recorded in the README.
2. **The split.** Freeze the generated-secret derivation and the three
   silent contracts (stack.env keys, the quadlet marker, the
   kiwi-stack.service name). kiwi-modules takes modules/, the renderer trio
   (modules.py, quadlet.py, schema.py), a fleet-free settings resolver and
   the stack profiles, plus `kiwi-modules render <one-machine.yaml>`;
   kiwi-stack takes lib.sh's machine side, the kiwi-stack CLI as a real
   file, the unit templates and `kiwi-stack apply DIR`. kiwi-server vendors
   both as subtrees pinned by tag (submodules and pip do not survive the
   ostree desktop kiwi-updater installs onto). A golden-output test of every
   preset comes first so no machine applied by 2.4.0 changes.
   *Status: steps one to four done after 2.5.0 — the on-disk bundle and
   `kiwi-stack apply DIR`, the golden test, the `kiwimodules` package with
   the one-machine render, and the two repositories populated
   (kiwi-modules 0.1.0, kiwi-stack 0.1.0, tagged and released by each
   repository's release workflow on the first push to main). kiwi-server is still
   the place both are developed: `tools/split-sync.sh` copies them over and
   `--check` reports drift. The flip to vendored subtrees pinned by tag
   waits for the first hardware round, so a fix found there lands in one
   tree.*
3. **Port ranges, the `backup:` key and the game module contract.** Module
   `backup: {paths, volumes, exclude, hooks}`, the restic backup module on a
   backup node with per-node credentials from the seed, `kiwi-stack backup`
   and `restore`, restore on first boot for a rebuilt node; then the `game`
   module that reserves ports, addresses, names, data and the socket.
4. **The control module.** `kiwi-server serve`: FastAPI over the existing
   core, Jinja pages with HTMX, packaged as a container module that runs on
   a node with the fleet directory as its data; users with two roles (fleet
   admin, node admin bound to hosts); the GTK app and the CLI as clients.
5. **The image and the join token.** One bootc image in the phoenix-server
   repository (plain uCore plus kiwi-stack, kiwimodules and dracut-sshd,
   signed with cosign), installed from the stock Fedora CoreOS live ISO with
   an Ignition that writes the policy and switches once, from a
   bootc-image-builder disk image where a provider imports one, or from a
   rescue system; a one-time token that lets a machine fetch its role at
   first boot, `curl … | sudo bash` (from cloud-init user-data on a VPS) for
   hand-installed machines; per-host ISOs stay for offline installs.
   (design/control-plane.md, The image)
6. **Workloads.** Runtime management of containers through podman's remote
   API over SSH (docker likewise), the kiwi-game plugin (catalog, idle
   shutdown, wake on first packet), the GTK app talking to the API.

After that, in the order the owner set: the unlock (design/control-plane.md,
Unlock: the `tang` module with rings, `vpn-server.published_ports` and
`unlock_relay:` with the endpoint gate in `start.sh`, the house share
written by `kiwi-server openwrt`, the `unlock:` block rendered into Clevis
trees for Ignition and the Debian role script, `kiwi-server tang serve`,
`kiwi-server unlock --manual` over dracut-sshd and dropbear-initramfs, the
leash timer, ring rotation as a controller job); the standby master (needs
the wg-easy peer database in the backup module and `wg_host` as a name);
rootless podman for the minimal preset only; the OpenWrt package once the
endpoint is a name; federation; the orchard (design/control-plane.md,
Managed masters); the Bluefin client (first step: an enrol bundle for the
desktop apps, which also binds a desktop's disk to its rings).

## New items worth adding

Not on the agreed list, implied by "self-hosting for a family, run by a
non-expert":

- Fleet health and alerting: healthchecks in every fragment,
  `kiwi-stack health`, a push to the master (ntfy or Gotify as a module).
- Automatic certificate renewal and a visible expiry.
- A node replacement runbook: host keys, `enroll --renew`, restore.
- A fleet file version and a `migrate` command; today's renames are
  handled by aliases.
- Secrets rotation that knows the cloud database is initialised once.
- A security baseline per target: a host firewall on Debian by default, a
  brute-force guard on SSH, a sysctl baseline.
- An audit of what leaves the network: the host's own traffic (updates,
  image pulls, the Mullvad list, backups) uses the raw ISP link; make that
  visible and optionally route it too.
- Pi-hole DNSSEC as a one-line setting.
- Image digests recorded per render (`images.lock`) so a restore reproduces
  what it backs up.

Asked for by the owner in October 2026, for the test round and after:

- **NFSv4 with kernel TLS instead of the sftp container** for the file
  shares: a native mount on every Linux client, the fleet CA's certificates
  for the transport (RFC 9289, `xprtsec=mtls`, `tlshd` from ktls-utils on
  both sides, kernel 6.5 or newer), far faster than SFTP and settable per
  export. nfsd lives in the kernel, so this is the first module that is a
  host unit and an exports file rather than a container (the gateway's
  `kn-gateway.service` is the pattern); the sftp module stays for
  non-Linux clients.
- **OpenCloud** as a second cloud module, beside or instead of Nextcloud: the
  owner's own configs and Flatpak data, code and Documents from the phone;
  one Go binary, little RAM, WebDAV. The `cloud` module's names are
  Nextcloud's; a `cloud:` kind or a module of its own.
- **Immich** for photos (its ML container wants RAM; a node setting to turn
  it off), **Paperless-ngx** for OCR, scans and documents, with a consume
  folder and the archive reachable from Nextcloud and OpenCloud (external
  storage or WebDAV) — the office and small-business side.
- **`kiwi-server ssh-config`**: an `Include`d file under `~/.ssh/config.d/`
  with a `Host` per machine (admin user, port, `IdentityFile`, the fleet's
  `secrets/known_hosts` as `UserKnownHostsFile`) and tunnel aliases per
  service (`<host>-pihole`, `<host>-portainer`, `<host>-wg` with
  `LocalForward` to the service's localhost port), regenerated on every
  render — the emergency path when the mesh is down. The owner's own ssh
  config for the live master is the model: `<host>-portainer` and
  `<host>-wg-easy-ui` with a `LocalForward`, `<host>-restart-vpn` with
  `RemoteCommand kiwi-stack vpn-restart` and `RequestTTY force`.
- **IPv6, end to end.** Today everything is IPv4: the stack network has
  `enable_ipv6: false`, the mesh is 10.x, wg-easy 7 knows no IPv6 clients,
  Pi-hole serves A records only. The owner has a public IPv6 prefix at home
  and wants to expose services over it. In order: a dual-stack stack
  network (netavark `IPv6=true`, a ULA per stack), the mesh with a ULA /48
  beside 10.x (a newer wg-easy, or wg-easy's successor, for v6 clients),
  Pi-hole AAAA records for the fleet's names, nginx listening on v6,
  gluetun's IPv6 through the provider (Mullvad hands one out already), the
  masters' public v6 as a second `wg_host`; at home, pinholes on the
  router for the services to expose and AAAA records through dynamic DNS.
  Each step keeps v4 working; `validate` says which hosts are dual-stack.
- **`kiwi-server probe <ssh target>`**: what a machine is, as JSON, before
  the first apply (OS, podman or docker, catatonit, `sudo -n`, disks,
  interfaces, listening ports, running containers, a DHCP server on its
  LAN); fills `disk`, `pub_iface` and `address`, warns about problems 35
  and 36 before they bite. `init` runs it locally for the LAN's subnet,
  gateway and resolvers. A shell script, so it runs on CoreOS.
- **cloud-init user-data** for a VPS the provider installs: today the key,
  NOPASSWD sudo and podman ≥ 4.4 (the by-hand steps behind problem 35),
  later the join line. **`build --boot`**: boot the ISO it made in a local
  QEMU VM (UEFI, a disk device matching `disk:`) and report whether it
  installed and powered off — the test that told a broken stick from broken
  media on the first mini PC; by hand today (docs/testing-hardware.md).
- **Random names** (design/control-plane.md, Names): fruit dishes for
  masters, fruits for nodes, from two lists of DNS-safe words, in `init`,
  the control plane and `enroll`; a name stays settable (part of problem 33).
- **Exit modes for a master** (design/control-plane.md, Exits):
  `master.exit: provider | direct | master`. `direct` works today with
  `modules: [vpn-server, dns, tor, reverse-proxy]` (the double hop is
  skipped, groups masquerade out of `eth0`, Pi-hole asks its fallbacks); the
  setting names it, turns `mullvad_socks` off when the provider is not
  Mullvad, and `validate` says where a master's clients exit. `master` is
  multi-hop through another network's master and waits for federation's
  cross-network enrolment. Together with federation and the orchard: a VPN
  service of one's own.
- **The phoenix-server repository** for the bootc image: Containerfile, CI build,
  cosign signing, published to ghcr.io; kiwi-server pins it by tag like
  kiwi-stack and kiwi-modules (todo step 5).
- **Two-factor codes with a phone app and a web UI.** Vaultwarden already
  does it: the Bitwarden apps generate TOTP codes and sync them, the web
  vault shows them, and Bitwarden Authenticator imports an Aegis export.
  Beside it, a module for people who keep 2FA apart from passwords: Ente
  Auth (self-hostable server, Android, iOS and web, imports Aegis) or
  2FAuth (web only, imports Aegis); either way Aegis's own encrypted
  auto-backup goes into a folder the cloud module syncs, which is the
  backup. More server-and-app pairs worth a module when someone asks:
  Immich and Paperless-ngx (above), ntfy (alerting, already planned),
  Jellyfin, Navidrome, Audiobookshelf (media), Linkwarden (bookmarks),
  Memos or Joplin Server (notes), Miniflux (feeds), Vikunja (tasks),
  Syncthing (sync without a cloud), Home Assistant (home). Each is a
  compose fragment and a module.yaml; the catalog decides which are
  presets.
