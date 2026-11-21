# What is open (one list)

Compiled on 20 November 2026 at 2.267 from the roadmap, the review notes and the work notes,
each item checked against the code. Worked from the top; an item that is done is struck here
and gets its line in the changelog. New ideas go to the end, not to the front.

## First: the rebuild (the owner, 21 Nov 2026: "this is going out of hands")

No new feature that acts by itself until steps 1 and 2 of docs/design/authority.md are built:
1. One truth: the control node writes, computers send changes; observed and remembered data leave the synced files.
2. Permissions that run out, for parts with a standby (voters: the machine, its standby, another node of the site; the master only as a fallback).
3. One policy table per part (where, copies, machine lost, alone, how much it is needed, no answer) and its sentence on the page;
   the diagnosis before a decision, a machine's last report when it goes down, one record per case.
4. A second control node on the computer that manages the network; a person's forced action counts (a counter, the lower gives way).
Decided 21 Nov: a permission lasts three minutes; modules bring how much a part is needed; nothing that cannot be undone runs by itself.

## A. Shipped, never tried on real machines

1. ~~`names: service` on a live machine.~~ Done 20 Nov on quince: `ffsync.home` and `pay.home` resolve, answer with a valid certificate, the old name sends on.
2. ~~Wake-on-LAN waking real hardware.~~ Tried 20 Nov on lime (ODROID-N2+, Armbian, the only real board at home): the card is armed at the apply and after a restart, the packet from papaya arrives (3 of 3), and the board stays off (the owner switched it off and had to use its button). The kernel has no wake-up line for its card (`eth_wake_irq not found`); docs/machines.md says so. Open: a PC or another board that does wake.
3. The starter stick and "No master yet" on a real machine. In a VM on sh3 20 Nov (`VMHOST=sh3 tests/drills/starter-vm.sh`): install 17/17, "No master yet" 15/15 on 2.270.0 (it found two bugs, fixed in 2.270.0). A real machine still to do: with the owner.
4. The guided session from the docs (notes/guided-session.md: its remarks are empty).
5. Phones following the standby master by DDNS.
6. A real mail from the notifications.
7. ~~The public relay: a listed key posting from outside; a renewal~~. The relay: done 20 Nov (tests/drills/relay-public.py, 0 wrong three times): a listed key posts, strangers are refused, the members' list works. It found that the default `keep_days: 0` refused every note not dated this very second (fixed in the module). Renewal: tried 20 Nov on medlar (paste.dlhost.eu): certbot renewed it (forced once, as none is due yet), the daily kn-acme.service copied it and reloaded nginx, the public name served the new certificate.
8. Authenticator account and code, Firefox sync from a browser, Jabber clients.
9. ~~The agent on the control node's own machine.~~ Done 20 Nov: it was installed, but its reports got 403 (the control page's proxy did not admit the machine itself); `/api/report` needs no address rule now (kiwi-modules). papaya reports 200.

## B. Half-built

10. The agent reports, and takes one order (2.277.0: hold back the containers of a part that went to its standby). Other orders
    (restart a service, update now) and the neighbours' vote between agents are missing; the switch by itself asks the standby
    instead (two that agree: the control node's look and the standby).
11. ~~A standby follows folders, not named volumes.~~ Done 20 Nov: named volumes are followed (the pastebin, medlar to lychee: fetched within 60 s, switch with nothing lost). The drill found that a public name stayed dead after a move or switch: the proxy's `public` now goes along, the old one closes when nothing public is left, and the certificate is asked for again after the master's apply.
12. ~~A switch takes six to eight minutes~~: under a minute since 2.275.0, by itself since 2.276.0 (item 57).
13. Moving a whole network: a plan only.
14. UI steps 3 and 5 to 7 (docs/design/ui.md): one store, `/api/v1`, pushed changes, the app on
    the kit (the job strip is there).
15. Boards: kiwi-server builds the encrypted Armbian image, first unlock~~, LUKS grown~~. The volume is grown by the first apply now (unlock.GROW_SCRIPT; on lime, already grown by hand, an apply leaves it alone: 14476 of 14492 MiB). Open: the image itself and the first unlock at the DHCP address. Ideas, not tried (need a board flashed anew): (a) the first unlock without knowing the address: the board's link-local IPv6 address follows from its MAC (fe80::, EUI-64), so a machine of the same LAN can reach its boot system there with no scan and no DHCP lease; (b) the image: one encrypted image per board built once (CI) with a known first key, and the first apply swaps the key, binds the rings and re-encrypts with a new volume key (the published one is known to all). (b) is a decision about secrets at rest: the owner's.
16. netcup: rescue system, ~~snapshots~~, user images. Snapshots done 20 Nov: `vps snapshot|snapshots|revert`; the drill on sorbet (tests/drills/vps-snapshot.sh): taken in 13 s while it runs, revert 30 s, sorbet back by itself 4 min later with its disk as at the snapshot. The rescue system: `vps rescue --name on|off`, tried on sorbet (tests/drills/vps-rescue.sh, 0 wrong: netcup needs the server off first; the rescue system answered on 22 with its own host key after 108 s; back with its pinned key 103 s after off). Open: user images.
17. VLANs: the gateway's DHCP, DNS and routing per VLAN.
18. ~~Install media reinstall at every boot: no guard, no `--reinstall`.~~ Done in 2.269.0: each ISO has an id, the installer leaves it on the installed system's `/boot`, its guard looks for it before anything is erased. Tried in VMs on sh3 (`tests/drills/install-guard-vm.sh`): uCore with UEFI (starts from its disk by itself), uCore with BIOS and Debian (switch themselves off), all passed; media made again install. `vps install` does not send used media twice.
19. ~~`kiwi-stack health`: clock step, fragmentation.~~ Done in every machine's report instead (the agent sends it each minute): a clock set back is a warning until the time has caught up, packets cut into pieces a note for an hour. Tried on quince: its clock set back four minutes was in `status` within the agent's minute; pings too large for the LAN showed as pieces.
20. kiwi-app uses status, machines, activity and the registry only.

## C. The page and the app lack what the command line has

21. ~~`move PART`, `standby`, `switch`, `keep_ready`.~~ Done: the machine's page (Services: plan, Move it, Keep it ready, Switch), the app (Move…, Switch; through the control node), the setting "Parts kept ready on another machine". Not yet clicked on the live control page.
22. ~~`names: service`: no settings field.~~ Done: "Names of its parts" in the shared settings (page and app).
23. App: settings edits through the control node's API.
24. ~~App: a machine's History and status chips.~~ Done: "History" on a machine's page opens the control node's activity for that machine; the chips (set up, last apply, backup, fullest disk) are under its name, by the page's rules (a test holds the two equal).
25. ~~App: open the game page.~~ Done: "Open the game page" beside "Open the status page" (the network's settings).
26. ~~Page: link a new network by card.~~ Done: Linked networks, "Link a new network": download this network's card, paste the other's.
27. ~~Page and app: open an unsigned copy~~; page: a `.tar.enc` copy, a synced folder. Unsigned: done (the app's *Open it anyway*, a tick on the page's form). Still open, and rare: the page taking a copy of the older passphrase kind, and a network kept in a synced folder.

## D. Asked for, not started

28. Netboot (PXE).
29. A backup store per service; DNS, DHCP and the gateway twice; site controllers; high
    availability at one site.
30. The join token; the phoenix image with the container images inside.
31. More hosters (cloud-init, Hetzner first), IPv6, NFSv4 with kernel TLS.
32. ~~`ssh-config`~~ (built: `kiwi-server ssh-config FLEET [--write]`, tried on the test fleet), `probe`, `build --boot`, `images.lock`, `migrate`, ~~Pi-hole DNSSEC~~, `master.exit`. DNSSEC: tried 20 Nov as one setting on lime and on sorbet, taken out again: the exit's resolver already checks (a badly signed name gets no address) and gives out no keys, so Pi-hole's own check failed every public name (docs/network.md, DNS).
33. Data kept across a reinstall on one disk (four questions wait for the owner,
    docs/design/data-partition.md).
34. Backups of desktop and phone, the kiwi-desktop client, Windows.
35. Mastodon, Matrix, email modules (the owner: not yet).

## E. Docs that were stale

36 to 40: done 20 Nov. ROADMAP's top and lower half, its "Open, in order" (now a summary of this list),
notes/REVIEW-page-parity.md, README, design/website.md, design/ram-master.md and notes/guided-session.md
say what 2.267.0 does.

## F. Older open items, found again while the docs were brought up to date

41. An OpenWrt package for the router side.
42. Rootless podman for the parts.
43. The orchard (several networks of one owner side by side).
44. `from=` on the Debian target's keys.
45. The RAM master's room (what it may hold in RAM, and how much).
46. The move to the GitHub organisation (links that name the owner's account, the docs' link to this list among them).
47. ~~gluetun's `DOT_PROVIDERS` is now `DNS_UPSTREAM_RESOLVERS`~~: the vpn-client module sets the new name (kiwi-modules, next release).

48. ~~The master's resolver after a restart of its VPN client~~: nothing to fix in kiwi; the first exit servers failed gluetun's start check (it tries others).

49. ~~A node's Pi-hole while the master is away~~: found 20 Nov, fixed the same day. It answered public names only when a client
    asked a second time (Pi-hole's strict order moves on only then): one question got nothing in 12 s. The master-down drill
    did not see it, as it reached the node by a name the master answers. Now a CoreDNS in front of a node's Pi-hole asks the
    master while it answers and Cloudflare over TLS while it does not; the drill reaches the node by its home address and asks
    once: "answered throughout" (papaya, 3 minutes without the master). The master itself is as before (its VPN client sits
    next to it).
50. ~~The master's lookups while its exit is down~~: left as it is. The devices on the VPN reach nothing then anyway (their traffic leaves through the same exit); only the master's own lookups wait, and its own traffic goes direct.

51. ~~Local time (NTP)~~, the owner 20 Nov: "maybe include local ntp?!". Done 20 Nov (kiwi-modules, kiwi-stack, kiwi-server):
    Pi-hole serves the time (123/udp, DHCP option 42) and never sets the clock (it had set the host's clock hourly beside
    timesyncd/chrony, with SYS_TIME); each machine asks its site's Pi-hole on its LAN first, else the master's on the mesh,
    the pool behind. Tried on the test network: quince (chrony) and medlar (timesyncd) take their time from lime; lime's
    and papaya's servers answer stratum 2 on the LAN; check 43/43.
52. ~~A true offsite copy~~ (the owner 20 Nov: his spare WebDAV at 1&1, encrypted, "don't trust this cloud"): built in 2.273.0
    (backup-server.offsite_url, rclone crypt with a key from the seed, names encrypted too); drill backup-offsite.sh 0 wrong.
    For his network: offsite_url and offsite_user on his backup store, the password through the page or the app (it goes into
    the store). Open: the providers registry for backup places (the owner's idea: 1&1, Nextcloud, storage boxes, later S3/SFTP
    as kinds next to WebDAV), when the second kind comes; `kiwi-server backups --offsite` to restore without logging in to the
    store's machine.
53. ~~After this computer's reboot `ssh sh3` was refused~~: the owner loaded the key again (20 Nov, 17:00); jinja2 for
    Python 3.15 is installed too.
54. ~~Backup places of another kind~~ (the owner: "use the providers repo for backup providers too"): SFTP built (a Hetzner
    Storage Box, a friend's server; the host key kept at the first copy) and a Nextcloud's own WebDAV kind; the places are a
    table in docs/backups.md. The kiwi-providers repository on GitHub is still empty: the hosters (netcup) and the backup
    places move there together, when a second hoster comes (its first commit would have to go to main).
55. ~~Pushed changes~~ (ui.md step 5): /api/v1/events, the pages that follow it, `control watch` for the app.
56. ~~The watchdog held a restart for an hour after one that had worked~~ (master-gone drill, 20 Nov): fixed.
57. ~~A switch in seconds~~ (item 12): 2.275.0. Vaultwarden ran on its standby 6 s after the switch began, the Pi-holes pointed
    its name there after 12–16 s, it answered after 29–37 s (was 6–8 min). By itself: 2.276.0 (recovery.switch, five minutes,
    the standby must not reach the machine either). Tried with power cuts (part-failover.sh): switched after 6.5 min since 2.278.0
    (counted from the last report), the returning machine stops its copy within 13 s (2.277.0, the agent's hold), the two stay a
    pair (2.278.0), a standby that is behind is a warning and not switched to by itself (2.280.0). Open: the control node's own
    machine (its parts switch by hand: nothing decides when the control node is the one gone; a vote between agents, or a second
    control node, both need the owner's word), and what the returning machine had written after the copy: since 2.281.0 kept beside the folder at its first fetch (tried 20 Nov: quince kept kn-vault.kept-20261120-2239, 32 KB with hard links).

