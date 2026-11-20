# What is open (one list)

Compiled on 20 November 2026 at 2.267 from the roadmap, the review notes and the work notes,
each item checked against the code. Worked from the top; an item that is done is struck here
and gets its line in the changelog. New ideas go to the end, not to the front.

## A. Shipped, never tried on real machines

1. ~~`names: service` on a live machine.~~ Done 20 Nov on quince: `ffsync.home` and `pay.home` resolve, answer with a valid certificate, the old name sends on.
2. Wake-on-LAN waking real hardware.
3. The starter stick and "No master yet" on a real machine.
4. The guided session from the docs (notes/guided-session.md: its remarks are empty).
5. Phones following the standby master by DDNS.
6. A real mail from the notifications.
7. The public relay: a listed key posting from outside; a renewal that is due.
8. Authenticator account and code, Firefox sync from a browser, Jabber clients.
9. ~~The agent on the control node's own machine.~~ Done 20 Nov: it was installed, but its reports got 403 (the control page's proxy did not admit the machine itself); `/api/report` needs no address rule now (kiwi-modules). papaya reports 200.

## B. Half-built

10. The agent reports only: orders and the neighbours' vote are missing.
11. A standby follows folders, not named volumes.
12. A switch takes six to eight minutes: parts kept ready, the name on both machines, the
    switch by itself (docs/design/scale.md).
13. Moving a whole network: a plan only.
14. UI steps 3 and 5 to 7 (docs/design/ui.md): one store, `/api/v1`, pushed changes, the app on
    the kit (the job strip is there).
15. Boards: kiwi-server builds the encrypted Armbian image, first unlock, LUKS grown.
16. netcup: rescue system, snapshots, user images.
17. VLANs: the gateway's DHCP, DNS and routing per VLAN.
18. Install media reinstall at every boot: no guard, no `--reinstall`. (Looked at on 20 Nov: bigger than it reads. It needs a mark on the target's boot partition written by both installers, a check in both, and the stick's boot loader starting the disk when the mark is there; with VM tests for uCore and Debian, UEFI and BIOS.)
19. `kiwi-stack health`: clock step, fragmentation.
20. kiwi-app uses status, machines, activity and the registry only.

## C. The page and the app lack what the command line has

21. ~~`move PART`, `standby`, `switch`, `keep_ready`.~~ Done: the machine's page (Services: plan, Move it, Keep it ready, Switch), the app (Move…, Switch; through the control node), the setting "Parts kept ready on another machine". Not yet clicked on the live control page.
22. ~~`names: service`: no settings field.~~ Done: "Names of its parts" in the shared settings (page and app).
23. App: settings edits through the control node's API.
24. App: a machine's History and status chips.
25. ~~App: open the game page.~~ Done: "Open the game page" beside "Open the status page" (the network's settings).
26. ~~Page: link a new network by card.~~ Done: Linked networks, "Link a new network": download this network's card, paste the other's.
27. Page and app: open an unsigned copy; page: a `.tar.enc` copy, a synced folder.

## D. Asked for, not started

28. Netboot (PXE).
29. A backup store per service; DNS, DHCP and the gateway twice; site controllers; high
    availability at one site.
30. The join token; the phoenix image with the container images inside.
31. More hosters (cloud-init, Hetzner first), IPv6, NFSv4 with kernel TLS.
32. `ssh-config`, `probe`, `build --boot`, `images.lock`, `migrate`, Pi-hole DNSSEC, `master.exit`.
33. Data kept across a reinstall on one disk (four questions wait for the owner,
    docs/design/data-partition.md).
34. Backups of desktop and phone, the kiwi-desktop client, Windows.
35. Mastodon, Matrix, email modules (the owner: not yet).

## E. Docs that are stale

36. ROADMAP's top says 2.239: no agent, names by service, part move, standby, switch, power,
    Machines table.
37. ROADMAP "Open, in order": item 2 is done, item 8 ignores the part move, scale.md's order
    is absent.
38. ROADMAP's lower half still says "not built" of federation, Wake-on-LAN, the sleeping node,
    leash and ring rotation, games, netcup booting the RAM master.
39. notes/REVIEW-page-parity.md ends at 2.239.
40. design/website.md "designed, not built"; design/ram-master.md "Not tried: a VPS";
    guided-session.md names 2.165; README lacks part move, standby, agent.
