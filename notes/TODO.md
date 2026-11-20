# What is open (one list)

Compiled on 20 November 2026 at 2.267 from the roadmap, the review notes and the work notes,
each item checked against the code. Worked from the top; an item that is done is struck here
and gets its line in the changelog. New ideas go to the end, not to the front.

## A. Shipped, never tried on real machines

1. ~~`names: service` on a live machine.~~ Done 20 Nov on quince: `ffsync.home` and `pay.home` resolve, answer with a valid certificate, the old name sends on.
2. ~~Wake-on-LAN waking real hardware.~~ Tried 20 Nov on lime (ODROID-N2+, Armbian, the only real board at home): the card is armed at the apply and after a restart, the packet from papaya arrives (3 of 3), and the board stays off (the owner switched it off and had to use its button). The kernel has no wake-up line for its card (`eth_wake_irq not found`); docs/machines.md says so. Open: a PC or another board that does wake.
3. The starter stick and "No master yet" on a real machine. In a VM on sh3 20 Nov (`VMHOST=sh3 tests/drills/starter-vm.sh`): install 17/17, "No master yet" 15/15 on 2.270.0 (it found two bugs, fixed in 2.270.0). A real machine still to do: with the owner.
4. The guided session from the docs (notes/guided-session.md: its remarks are empty).
5. Phones following the standby master by DDNS.
6. A real mail from the notifications.
7. ~~The public relay: a listed key posting from outside~~; a renewal that is due. The relay: done 20 Nov (tests/drills/relay-public.py, 0 wrong three times): a listed key posts, strangers are refused, the members' list works. It found that the default `keep_days: 0` refused every note not dated this very second (fixed in the module). Still open: a certificate renewal that is due (none is 60 days old yet).
8. Authenticator account and code, Firefox sync from a browser, Jabber clients.
9. ~~The agent on the control node's own machine.~~ Done 20 Nov: it was installed, but its reports got 403 (the control page's proxy did not admit the machine itself); `/api/report` needs no address rule now (kiwi-modules). papaya reports 200.

## B. Half-built

10. The agent reports only: orders and the neighbours' vote are missing.
11. ~~A standby follows folders, not named volumes.~~ Done 20 Nov: named volumes are followed (the pastebin, medlar to lychee: fetched within 60 s, switch with nothing lost). The drill found that a public name stayed dead after a move or switch: the proxy's `public` now goes along, the old one closes when nothing public is left, and the certificate is asked for again after the master's apply.
12. A switch takes six to eight minutes: parts kept ready, the name on both machines, the
    switch by itself (docs/design/scale.md). Measured 20 Nov (vault, quince to medlar): the part
    itself is away 55 seconds, and `switch` says so; its name follows as the machines that
    answer for names are applied (the master next, the home's after it), which is the rest of
    the minutes. The name on both machines is what would take those away.
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
