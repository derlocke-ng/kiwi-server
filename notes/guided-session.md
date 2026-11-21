# The guided session: a network from the docs alone

The owner sets up a new test network following only the README and docs/, and says what he likes,
dislikes, what works and what does not. Each remark goes into the table under the step it belongs to.
They are fixed afterwards in small releases, not during the session.

State on 20 November 2026: the session has not been held, and the table of remarks at the end
is empty. This page was checked against kiwi-server 2.267.0: the docs pages of the path exist,
and the page's buttons and the drill named below are in the code.

## What can be used

| Machine | For | State | Before |
|---|---|---|---|
| crumble (netcup VPS, 188.68.38.110) | the master | runs ~/kiwi-2's RAM master (Riseup exit, standby crumble-b) | wiping it is allowed (`KIWI_NEVER` keeps the live master and sorbet safe: the drills and kiwi-server's netcup calls both read it) |
| crumble-b, damson (VMs on sh3, 192.168.2.70 / .71) | a node, a standby | ~/kiwi-2's standby and node since 17 Nov | reinstall from new media; they are mine to wipe |
| a new VM on sh3 | the first node | made from the starter stick | `virsh` on sh3, own VMs only; the `id-*` VMs stay untouched |
| starter-test (VM on sh3, 192.168.2.177) | a second node | the starter stick, waiting | its stick is from 2.123.0: make a new one first |
| this computer or the other | the operator | kiwi-server 2.267.0 or later | a fresh folder, e.g. ~/kiwi3 |

Never: the live master 94.16.108.143 (netcup 578583), the v1 network (gate, sh3's own services, m1, 192.168.2.20).

## The path, in the order a newcomer meets it

1. README.md
2. docs/install.md
3. docs/getting-started.md: a new fleet, the first install, apply
4. docs/control.md: the control node, the status page (the start page is one page with it since 2.188.0), Accounts
5. docs/network.md: devices on the VPN, the way to the Internet (Riseup suggested since 2.163.0), who may reach what
   (the control page's menu calls it *Access rules* since 2.259.0)
6. docs/roles.md: what a machine runs (Jabber, accounts)
7. docs/gui.md: the app

## A second way, since 2.149.0

Start with "No master yet" on the starter's first page (the machine at home alone, services at
home only), then "Add a master" on Machines with crumble's address. It tries the path a newcomer
without a rented server takes, and the move onto the VPN afterwards. The drill
`tests/drills/starter-vm.sh alone` does the first half in a local VM.

## Remarks

| # | Step | Said | Kind (works / does not / looks / wording) | Fixed in |
|---|---|---|---|---|
| 1 | | | | |
