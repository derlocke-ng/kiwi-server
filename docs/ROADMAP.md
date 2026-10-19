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
    stays first in the boot order. Add an install guard to both ISOs with a
    `--reinstall` override.
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
3. **Port ranges, the `backup:` key and the game module contract.** Module
   `backup: {paths, volumes, exclude, hooks}`, the restic backup module on a
   backup node with per-node credentials from the seed, `kiwi-stack backup`
   and `restore`, restore on first boot for a rebuilt node; then the `game`
   module that reserves ports, addresses, names, data and the socket.
4. **The control module.** `kiwi-server serve`: FastAPI over the existing
   core, Jinja pages with HTMX, packaged as a container module that runs on
   a node with the fleet directory as its data; users with two roles (fleet
   admin, node admin bound to hosts); the GTK app and the CLI as clients.
5. **Join tokens and generic ISOs.** One bootstrap ISO per target published
   by CI, a one-time token that lets a machine fetch its role at first boot,
   `curl … | sudo bash` for hand-installed machines; per-host ISOs stay for
   offline installs.
6. **Workloads.** Runtime management of containers through podman's remote
   API over SSH (docker likewise), the kiwi-game plugin (catalog, idle
   shutdown, wake on first packet), the GTK app talking to the API.

After that, in the order the owner set: Tang/Clevis nested SSS with the
reverse-tunnel unlock and `kiwi-server unlock`; the standby master (needs
the wg-easy peer database in the backup module and `wg_host` as a name);
rootless podman for the minimal preset only; the OpenWrt package once the
endpoint is a name; federation; the Bluefin client (first step: an enrol
bundle for the desktop apps).

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
