# What is open (one list)

Merged on 22 November 2026 at 2.292.0 from every note in notes/, docs/ROADMAP.md, every design in
docs/design/ and the working notes, each item checked against the code. Worked from the top; a done item
is struck here and gets its line in the changelog. S: under an hour, M: a few hours, L: days.
New ideas go to the end of their section.

## 1. No decision needed (worked in this order)

Small:
1. ~~Machines table: a "last apply" column (ui.md).~~ Done (2.293.0).
2. ~~The app: link a new network by card (`federate --invite/--accept`).~~ Done (2.293.0).
3. ~~A computer's first sync hands over what it switched off or held back.~~ Done (2.293.0).
4. ~~power-cut drill: a hypervisor that does not answer counted every machine as real.~~ Done (2.293.0).
5. A sudo that wants a password: a `sudo -n` check before an apply, with a clear word (ROADMAP problem 35). S
6. A literal secret in the fleet file: the warning still says "until there is a secret store". S
7. The role script on the machine (`/var/lib/kiwi-server/role.sh`) holds every file of the bundle and stays. S
8. Certificates of the network's own CA renew only with an apply: the control node applies a machine whose certificate runs out within 14 days (undoable). S-M
9. Removing a controller (a computer) says nothing about rotating what it could read. S
10. Docs that call built things "not built": public-services.md, scale.md, starter.md, local-first.md, controllers.md; REVIEW-2.1.0.md cut to its status table; the old item 10 (the vote exists now). S
11. The Devices table by site, as Machines (ui.md). S-M
12. `/api/v1/machines/{name}` and `/api/v1/jobs` (ui.md step 5; only `/api/jobs` exists). S-M

Medium:
13. The permission drills that are missing: the power cut with the control node away, the machine's network cut while it runs, a witness away (authority.md §2). M
14. The diagnosis asks the provider: switched off there means start it, not a power cycle (authority.md §4). M
15. The control node's own update: check `/healthz` after, fall back to the image before. M
16. The agent takes orders: restart a service, update now (scale.md). M
17. `backups --offsite`: restore from the offsite copy without logging in to the store's machine. M
18. A "through the VPN" switch for any module, instead of hand-written `network_mode` (onion.md). M
19. The VPN name of a public instance, `<site>-public.<machine>.home` (public-services.md). S-M
20. With an exit through a partner, DNS lookups still leave from the network's own master (federation.md). M
21. `unlock.opens`: by itself, by a person, at home, as a setting (unlock.md step 2). M
22. The cache keeps install images (FCOS, Debian netinst) for the network (local-first.md §1). M
23. An upgrade rehearsal drill: the previous release on a throwaway VM, data, the new one, `check` (ROADMAP). M
24. Debian: a host firewall and a sysctl baseline by default (ROADMAP problem 57). M
25. The master's `stream` door: its refused connections into a fail2ban jail (the nodes ban by address already). S-M
26. Nostr as a message channel (recovery.md). S-M
27. The page restores a copy of the older passphrase kind (`.tar.enc`). M
28. Rotate steps for the generated secrets that have none yet (controllers.md; cloud, immich, paperless have them). M per module
29. The app on the kit: remembered size, shared header, About, Preferences; split the 7400-line file (ui.md step 6). M
30. The app: the Machines and Devices tables by site with ticks and filters (ui.md). M-L
31. Render only the machines a change reaches (scale.md). M-L
32. What a move leaves behind: the old data and the old backup store stay until someone deletes them. S, but how long to keep them is the owner's

Large:
33. A backup store per service (scale.md step 3).
34. DNS, DHCP and the gateway twice, with a moving address (scale.md step 6).
35. Moving a whole network (`move --mesh/--domain`): both meshes while it runs, pins, the CA's name constraint, host certificates, partners' cards (federation.md).
36. The emergency channel over Tor: onion SSH and control API on every machine, nodes asking where their master is (recovery.md steps 4-5).
37. The diagnosis for the other events: a part down, a disk filling, a backup overdue, a certificate running out, a failed apply, each with its drill (authority.md §4).

## 2. Needs the owner

Decisions:
- Step 4 of the rebuild: a second control node on his computer, a person's forced action (pull request 379).
- data-partition.md's four questions (only new installs? 64 GB or asked? machines without rings? CoreOS first?).
- local-first.md: should the cache also mirror Fedora/uCore OS updates?
- no-master.md: should a node keep answering on its home address once a master comes?
- controllers.md: may a site controller enroll devices?
- recovery.md: who may answer an `ask` from a phone? `recovery: reinstall` (cannot be undone: by itself only if he turns it on).
- scale.md: which parts get live replication first (proposed: accounts and git, then files)? Teams and users: admins who log in as themselves, rights per site, WebAuthn.
- public-services.md: authentik or a second Kanidm per public door, when a public side needs accounts.
- A new CA and new WireGuard keys (every device imports again; controllers.md, rotate.py).
- The stack networks sit in public address space (172.64/172.128): fixing it recreates them everywhere, v1 included (ROADMAP problem 66).
- "Act after a time" when nobody answers, per part (off unless he turns it on).
- The page taking a disk key by hand (a secret passing through a page); a synced folder on the page; the app's settings through the control node's API (authority.md calls it not needed).
- Firefox policy in the device bundle (CA in, DNS over HTTPS off): needs root on the device.
- Rootless podman; Mastodon, Matrix, email modules ("not yet"); the org move ("later, months"); the live v1 network's move.
- The phoenix bootc image, its cosign key on a hardware token, the join token and the generic ISO (his repository and registry).
- A store key on a hardware key (age-plugin-yubikey).
- Separate public instances beyond Jabber; OpenCloud's shared folders and which cloud owns the files.

His hardware, accounts or time:
- The starter stick on a real machine; Wake-on-LAN on a board that wakes; the guided session (notes/guided-session.md).
- A real mail from the notifications; phones following the standby master by DDNS; an authenticator account; Firefox Sync from a browser; Jabber clients; BTCPay on mainnet; MTU on a phone over 5G.
- The encrypted Armbian image and the first unlock at the DHCP address (a board flashed anew; the key decision for a CI-built image).
- Netboot (a PC on one LAN), a Raspberry Pi starter and CoreOS aarch64, VLANs per gateway (a second router), the desktop ring policy (his desktops), Windows.
- papaya's plain data disk, single rings on lychee and sorbet, `secrets copy-key` for ~/kiwi and ~/kiwi-2, the room on crumble's RAM master, the NAT test network's range.

## 3. Known gaps, kept on purpose

- `apply` on a machine met for the first time wants `--ssh` even when the fleet file has its address: `remote.reach`
  never sends a role script to an address whose host key is not on record (it could be someone else's machine).
- `from=` on the keys of Debian's boot system: Dropbear has no such option.
- A RAM master as witness forgets its refusals at a reboot (it keeps its folders hourly); the window is the minute
  between a take and its record.
- Relay members need `ghcr.io/derlocke-ng/strfry:1.1.3-kiwi2` until upstream releases the patch.
