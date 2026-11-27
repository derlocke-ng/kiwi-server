# What is open (one list)

Merged on 22 November 2026 at 2.292.0 from every note in notes/, docs/ROADMAP.md, every design in
docs/design/ and the working notes, each item checked against the code. Cut down the same day after
the owner's decision: the focus is easy setup and use of a Linux infrastructure, many servers and
clients orchestrated; no part with data moves by itself (docs/design/authority.md). Worked from the
top; a done item is struck here and gets its line in the changelog. S: under an hour, M: a few hours,
L: days. New ideas go to the end of their section.

## 1. The core, in this order

1. ~~Remove the automatic takeover of data parts (standby's take, switch by itself, holds, `lost:`);
   keep the copy kept ready, `switch` while the machine runs, `move`, the permissions as an unused
   building block.~~ Done (2.294.0).
2. ~~A backup before an update (kiwi-stack update).~~ Done (kiwi-stack 0.9.31, in 2.294.0).
3. ~~The diagnosis asks the provider; the control node's own update falls back when its page does
   not answer; the master's `stream` door into a fail2ban jail.~~ Done (2.294.0).
4. ~~A restore of a data part onto another machine when its machine is dead: `move --gone` from the
   newest backup without the source.~~ Done (2.295.0), drilled: lychee off, authenticator onto medlar and home again.
5. A runbook (docs/design/runbook.md): admins decide beforehand, per machine or part and case, what happens (a machine
   gone, a service down, a disk filling, a backup overdue, an update failed, cut off from the
   network): tell, restart, stop, restore elsewhere, wait for a person. Written in the fleet file,
   shown on the page and in the app. The owner, 22 Nov 2026: every node's agent handles its own
   cases by itself when it has to (no control node needed), on the patterns and permissions set
   before; an action only one machine may take at a time asks for a permission first (lease.py,
   unused until then). Nothing acts beyond what is written. Builds on the diagnosis and its
   record (authority.md step 3) and the agent (scale.md). M-L
   Built: the lines checked and shown (step 1), `part-down` restarted by the machine itself
   (`kiwi-runbook`, drilled on quince), disk-full, a crash loop told, the control node's cases
   (machine-gone, backup-overdue, certificate-due). Open: update-failed on docker machines, cut-off,
   permissions for lines that name voters.
6. ~~Render only the machines a change reaches (scale.md); apply only the stack when only it
   changed (`--stack-only`).~~ Done (2.295.0); stack-only drilled on quince (38 s against 189 s).
7. DNS, DHCP and the gateway twice per site, with a moving address (scale.md step 6,
   design/basics-twice.md). Built (2.296.0): the vip module and the pair; lime and papaya share
   192.168.2.69 (.70 until 22 Nov, crumble-b's too), drilled; said again every 30 s (kiwi-modules 0.117.1); the Machines table names
   who holds it. DHCP from the holder only (kiwi-modules 0.120.0), drilled on a podman network of
   its own (tests/drills/dhcp-pair.sh, 14 of 14). The gateway's switch with a device behind it:
   drilled (2.302.0, tests/drills/vip-gateway.sh: a device in a network namespace on medlar lost 3
   of 58 fetches over two switches). Open: DHCP on a real LAN (at the move). S
8. The app: the Machines and Devices tables by site with ticks and filters (ui.md). M-L
9. ~~The Devices table by site on the page, as Machines (ui.md).~~ Done (2.295.0).
10. The client side: devices enrolled and kept by the network (bundles, the app, phones). The owner,
    23 Nov 2026: his workstation's own system (Bluefin-DX, beside lemonaide's uCore on the same
    machine) is to be managed by kiwi-server later too. M-L
11. The agent takes orders from the control node: restart a service, update now (scale.md; the
    runbook's actions, asked for by a person). M
12. An upgrade rehearsal drill: the previous release on a throwaway VM, data, the new one, `check`. M
14. Disk unlock as the owner asked (23 Nov 2026: "enable/disable it for certain clients, auto-unlock on
    a timer, 5 min for my gaming workstation, a separate ring for dev devices"; docs/design/unlock.md).
    Built (2.307.0): auto-unlock on/off per machine (`unlock.opens`), on the page and in the app.
    Built (2.307.1): the window, the control node types a waiting machine's key for the next N minutes
    (`unlock.window`). Built (2.308.0, the owner: "just stop the tang servers"): switched rings, a
    machine's own ring (`unlock.switch`) stopped and started, or run for a while; drilled on quince.
    Next: desktops
    (the Bluefin disk of lemonaide's workstation, dev devices with a ring of their own) with the
    auto-unlock and emergency rings, which needs item 10. M-L
13. ~~A local AI (docs/design/ai.md, the owner, 23 Nov 2026; a priority: it helps him go through
    his data before the move): the `ai` module with Unsloth Studio (web UI and OpenAI API) on LAN
    and/or VPN, its tools off; a machine that is off on purpose (`on_demand`) shown as no problem;
    `kiwi-server ai bench` to compare models; `ai connect` for opencode. Waits for the owner's
    settings (GPU, models). L~~ Done (2.306.0-2.306.3): lemonaide, an RX 9070 XT, runs Studio.

15. Faster and stronger (the owner, 27 Nov 2026: "make this tool suck less, make it faster and
    stronger"; measured that day, in this order):
    a. Cheap applies. An apply that changes nothing takes 20 s on lychee, under 2 s of it on the
       machine: a disk-key check at every apply while a one-time key for a later install waits
       (luks.swap, 7 s), and the steps after the role sent one by one. A new site restarts every
       container of the machine (each carries the machine's names as AddHost lines): names
       without a restart. Goal: 3 s, and a new site restarts the proxy only. M
    b. A rollout by the control node: a change or a release goes out by itself, the proxy's
       config checked first (nginx-check), one machine first, then the rest, stop and back on a
       failure. Replaces `apply --changed` by hand and the release chain script. M-L
    c. The fleet says which release it needs: an older kiwi-server says "update me first", not
       "unknown setting" (a new key refused the whole fleet for an hour that day), and the
       control node updates itself before it takes the fleet. S-M
    d. Each part says how to tell it is ready (a page, a port, a command): one check for apply,
       update (back on a failure: the runbook's update-failed), the runbook and the status page. M
    e. Read commands in kubectl's way, from the agents' reports: get, describe, logs, explain. M
    Kubernetes (k0s) as the base: still no (scale.md); as a module for one site with three or
    more servers, or an export of the modules as Kubernetes YAML (Podman runs it): later.

## 2. Smaller, no decision needed

- ~~`backups --offsite`: restore from the offsite copy without logging in to the store's machine.~~ Done (2.297.0): `backups STORE --from-offsite`; drilled (tests/drills/backup-offsite.sh, lychee's store, a stand-in on quince).
- A "through the VPN" switch for any module, instead of hand-written `network_mode` (onion.md). M
- The VPN name of a public instance, `<site>-public.<machine>.home` (public-services.md). S-M
- With an exit through a partner, DNS lookups still leave from the network's own master. M
- `unlock.opens`: by itself, by a person, at home, as a setting (unlock.md step 2). M
- ~~The cache keeps install images (FCOS, Debian netinst) for the network (local-first.md §1).~~ Done (2.299.0): the netinst 77 s from the Internet, 4 s from the cache.
- The LAN's DNS pointing Debian's plain-HTTP package servers at the cache, for every device (the owner's idea, 22 Nov): a setting, off at first; drill what a cache that is down does. S-M
- Debian: a host firewall and a sysctl baseline by default (ROADMAP problem 57). M
- Nostr as a message channel (recovery.md). S-M
- Rotate steps for the generated secrets that have none yet (cloud, immich, paperless have them). M per module
- The app on the kit: ~~remembered size~~ (2.299.0), shared header, About, Preferences; split the big file (ui.md step 6). M
- A backup store per service (scale.md step 3). L
- Moving a whole network (`move --mesh/--domain`, federation.md). L
- The emergency channel over Tor (recovery.md steps 4-5). L
- ~~The store's lock is for people, not for the background~~ Done (2.303.0) (the owner, 22 Nov 2026: "a lock that no
  one can use the app/cli tool but not for the daemon/control stuff"): the sync with the control
  node opens what it needs in memory with this computer's key from the keyring (as `pull` does) and
  runs while the store is locked; apply, build and showing secrets still ask to unlock. Today a push
  reads the open store's files, so the app's sync stops while it is locked (the app now waits and
  syncs after the unlock, 2.301.0). M
- What a move leaves behind: the old data and the old backup store stay until someone deletes them
  (how long to keep them is the owner's). S
- The local AI (after 2.310.0): the chosen model as a setting of the `ai` module, loaded by the
  machine itself after a start; choosing and loading a model in the app and on the control page. M
- The local AI: Studio's Top-K fixed for every request (`ai.top_k`, kiwi-modules branch `ai-tune`,
  rebased on 0.125.0): to try on lemonaide when it is free, then release. S
- Taking over a v1 installation's data (notes/migration.md): Nextcloud AIO rehearsed on mulberry
  with a stand-in, with and without server-side encryption (27 Nov). Open: Vaultwarden with an
  account and an item, a game save; then mulberry goes (`kiwi-server remove`, its VM on sh3). M
- cloud: after a change of its name Talk keeps the old name in three lists (signalling, STUN,
  TURN), the old one first; the module should drop the entries that are not its name. S

## 3. Dropped on 22 November 2026

- Failover of data parts by itself, the standby's take, holds, the voters' wiring, `recovery.switch`,
  `defaults.parts.*.lost`, "act after a time" per part, live replication of data parts, step 4 of
  the authority rebuild as a failover (pull request 379 waits as a design for a second control node).
- The permission drills (permissions.sh, permissions-more.sh, part-failover.sh).

## 4. Needs the owner

Decisions:
- Pull request 379: a second control node on his computer, a person's forced action.
- data-partition.md's four questions (only new installs? 64 GB or asked? machines without rings? CoreOS first?).
- no-master.md: should a node keep answering on its home address once a master comes?
- controllers.md: may a site controller enroll devices?
- recovery.md: who may answer an `ask` from a phone? `recovery: reinstall` (cannot be undone).
- scale.md: teams and users: admins who log in as themselves, rights per site, WebAuthn.
- public-services.md: authentik or a second Kanidm per public door, when a public side needs accounts.
- A new CA and new WireGuard keys (every device imports again; controllers.md, rotate.py).
- The stack networks sit in public address space (172.64/172.128): fixing it recreates them everywhere, v1 included.
- The page taking a disk key by hand; a synced folder on the page; the app's settings through the control node's API.
- Firefox policy in the device bundle (CA in, DNS over HTTPS off): needs root on the device.
- Rootless podman; Mastodon, Matrix, email modules ("not yet"); the org move ("later, months"); the live v1 network's move.
- The phoenix bootc image, its cosign key on a hardware token, the join token and the generic ISO.
- A store key on a hardware key (age-plugin-yubikey).
- Separate public instances beyond Jabber; OpenCloud's shared folders and which cloud owns the files.

His hardware, accounts or time:
- The starter stick on a real machine; Wake-on-LAN on a board that wakes; the guided session (notes/guided-session.md).
- A real mail from the notifications; phones following the standby master by DDNS; an authenticator account; Firefox Sync from a browser; Jabber clients; BTCPay on mainnet; MTU on a phone over 5G.
- The encrypted Armbian image and the first unlock at the DHCP address.
- Netboot, a Raspberry Pi starter and CoreOS aarch64, VLANs per gateway, the desktop ring policy, Windows.
- papaya's plain data disk, single rings on lychee and sorbet, `secrets copy-key` for ~/kiwi and ~/kiwi-2, the room on crumble's RAM master, the NAT test network's range.

## 5. Known gaps, kept on purpose

- `apply` on a machine met for the first time wants `--ssh` even when the fleet file has its address: `remote.reach`
  never sends a role script to an address whose host key is not on record (it could be someone else's machine).
- `from=` on the keys of Debian's boot system: Dropbear has no such option.
- Relay members need `ghcr.io/derlocke-ng/strfry:1.1.3-kiwi2` until upstream releases the patch.
- A copy of the older passphrase kind (`.tar.enc`, from before the secret store) comes back with
  `kiwi-server restore` on a computer, not on the page: the page would have to make a store from it.
