# The control page next to the app (14 November 2026, kiwi-server 2.80.2)

Asked by the owner: what the app can do, the control node's page should do too, the first
run included. Compared command by command and setting by setting.

## What the page lacks, most missed first

| # | The app has | The page | State |
|---|---|---|---|
| 1 | The first run: the questionnaire, the recovery key | nothing; a node waits for `control push` | **built** in 2.81.0: a machine with nothing on it shows the questionnaire, guarded by a setup code; 2.82.0: a new password by recovery key |
| 2 | Every setting: machine, role, preset, modules and their settings, groups, exit, backup retention, defaults | reads only; Help says "use the app" | **built** in 2.83.0 from one shared list of fields (`form.py`). Left: the VPN groups are a text box and not the app's list editor, a file setting is a typed path, `unlock.control`, removing a machine |
| 3 | Add a machine, make its install media, set it up the first time ("Connect it", "Set it up") | shows the next step as text | **built** in 2.82.0: "Still to set up" with one button per step, media made in memory and fetched from the page, "Add a machine at home" |
| 4 | Restore: snapshots, one module, "Restore all", "Start new" | points to the app | **built** in 2.85.0: "Bring something back" per machine (a part to a snapshot, or everything to the newest). "Start new" for a rebuilt machine: 2.144.0, under Backups |
| 5 | Unlock a machine whose disk waits at boot | a hint to go to the computer | **built** in 2.85.0: "Unlock it" on the machine's page (forwards its ring; handing over the key by hand stays in the terminal) |
| 6 | A copy of the network (`backup --local`), restore a network from a copy, a synced folder | "Save it now" only | **built** (not released yet): "Fetch a copy" on Backups downloads the network's copy, which the recovery key opens; a machine with nothing on it takes a copy on its first page ("I already have a network"); "Save it now" needs no passphrase and goes to every machine that is set up. Left: a copy is not signed (ROADMAP 21), the passphrase kind (`.tar.enc`) is restored with `kiwi-server restore` only, a synced folder |
| 7 | Federation; SSH certificates | SSH certificates since 2.143.0 (Computers, "Let a key in for a while"); federation under Linked networks | **built** |
| 8 | Prune now, full status, role script, doctor | prune and doctor since 2.144.0 (Backups, Help); a machine's status is on its page | role script open |
| 9 | Sign in at the hoster and pick the server (wizard) | the address is typed | open |

`check` and `watch` are allowed actions of the page without a button.

## What the app lacks

| The page has | State |
|---|---|
| "Send a test message" for the notifications (2.108.0); the status page's link | **built** in 2.140.0: a "Send a test message" row under the app's message settings (Defaults); the status page's link is still open |
| The picture of the network, the processor's load and the oldest last backup on Home (2.105.0) | **built** in 2.106.0: "How they are linked" on the overview, a CPU line on every card, a tile for the oldest backup |
| Add a computer: the invite link (Computers) | **built** in 2.115.0: menu, Use another computer…, Add a computer (`control invite`); link, copy button and QR code in both |
| A Services page: every service under its machine, with login, answer and hidden-service address (2.117.0); the menu in groups | the app keeps its own layout (the owner prefers it); hidden-service addresses are on a machine's page since 2.118.0 |
| Hidden services: only sites marked usable over Tor are offered (2.120.0) | **built** in 2.120.0 in both: the page's list and the app's dialog show the same sites |
| Take in a waiting starter machine (its address and setup code) (2.123.0) | **built** in 2.141.0: the app's menu, "Take in a waiting machine…", and `kiwi-server take-in`; the handshake is one function for the page and the command |
| Preview (`plan`) | open |
| Update everything (`apply --update`) | open |
| Remove a phone or laptop (`enroll --remove`), show its QR code again | open |
| "Leave NAME out" while keys are replaced (`rotate --waive`) | **built** in 2.142.0: "A computer was lost or stolen…" says which machines still wait and offers "Leave NAME out" for the keys and the secrets |
| Activity: tasks and who did what | open |

## Neither has

`vps login/list/probe/install` (the terminal's `next` does it), `adopt`, `validate`, `mtu`,
`openwrt`, `secrets show/join/add/remove` by name.

## Since then (14 November 2026, 2.86.0 to 2.90.1)

| What | CLI | App | Page |
|---|---|---|---|
| The logins of a machine's services | `logins` | "Logins…" on the machine, under "Open a service" | "Logins" card, password on "Show password" |
| This computer on the network | `connect` | "Connect this computer…", offered on the overview when the services do not open | not needed: the page is opened from a device that is on the network |
| The network's own copy, opened by the recovery key | `backup`, `restore` | "Back up the network now" (its restore dialog still says "passphrase") | "Save it now", "Fetch a copy", "bring it back from a copy" on a fresh machine |
| A machine leaves for good | `remove` | "Remove" on a saved fleet | "Remove this machine" |
| A board's fixed address | reaches the board with an apply | the same, through Apply | the same, through Apply |

Since 2.91.2 the page has "Linked networks": what each partner may reach, who of it may come, and the two
switches for leaving to the Internet through a partner. Linking a new network stays on a computer that holds both.

Still open on the page: the role script, the
hoster's sign-in in the wizard (prune, doctor and "start new": 2.144.0). Still open in the app:
activity. (Preview, "update everything" and removing a phone: built in 2.92.0.)

Since 2.93.0 and 2.94.0, in all three: what is done about a machine that does not answer (Settings, "When a
machine is gone"; `recover`), and who may reach what (`access`; "Who may reach what" on the page and in the app's
menu): the VPN's groups and the linked networks, a tick per machine and service.

Since 2.95.0, in all three: "If something breaks" (`recovery`): could the network come back, and what to do in
each case; and "This machine is lost or was replaced" on a set-up machine (the first install's steps again).

Since 2.96.0, in all three: hidden services (`onion`; on the page and in the app under "Who may reach what").
The app does not show a person's key yet (`onion … --show NAME` in a terminal, or the page).

Since 2.98.0, in all three: admin pages as a class of their own and the level "every app" (`access … apps`).


Since 2.127.0 to 2.133.0 (16 November 2026): **Accounts** (the accounts module and Jabber's sign-up page:
requests, invite codes, accounts and their password links) on the page, in the app (menu, "Accounts…") and
in the terminal (`kiwi-server accounts`). Page only for now: Connect a service to the accounts (`accounts
connect` in a terminal), and the Settings / Admin page links under each service. The start pages and the
front page for strangers are for everyone, not for the people who run the network: neither app nor control page.
