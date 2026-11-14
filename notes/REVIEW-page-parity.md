# The control page next to the app (14 November 2026, kiwi-server 2.80.2)

Asked by the owner: what the app can do, the control node's page should do too, the first
run included. Compared command by command and setting by setting.

## What the page lacks, most missed first

| # | The app has | The page | State |
|---|---|---|---|
| 1 | The first run: the questionnaire, the recovery key | nothing; a node waits for `control push` | **built** in 2.81.0: a machine with nothing on it shows the questionnaire, guarded by a setup code; 2.82.0: a new password by recovery key |
| 2 | Every setting: machine, role, preset, modules and their settings, groups, exit, backup retention, defaults | reads only; Help says "use the app" | **built** in 2.83.0 from one shared list of fields (`form.py`). Left: the VPN groups are a text box and not the app's list editor, a file setting is a typed path, `unlock.control`, removing a machine |
| 3 | Add a machine, make its install media, set it up the first time ("Connect it", "Set it up") | shows the next step as text | **built** in 2.82.0: "Still to set up" with one button per step, media made in memory and fetched from the page, "Add a machine at home" |
| 4 | Restore: snapshots, one module, "Restore all", "Start new" | points to the app | open |
| 5 | Unlock a machine whose disk waits at boot | a hint to go to the computer | open |
| 6 | A copy of the network (`backup --local`), restore a network from a copy, a synced folder | "Save it now" only | open |
| 7 | Federation; SSH certificates | nothing | open |
| 8 | Prune now, full status, role script, doctor | nothing | open |
| 9 | Sign in at the hoster and pick the server (wizard) | the address is typed | open |

`check` and `watch` are allowed actions of the page without a button.

## What the app lacks

| The page has | State |
|---|---|
| Preview (`plan`) | open |
| Update everything (`apply --update`) | open |
| Remove a phone or laptop (`enroll --remove`), show its QR code again | open |
| "Leave NAME out" while keys are replaced (`rotate --waive`) | open |
| Activity: tasks and who did what | open |

## Neither has

`vps login/list/probe/install` (the terminal's `next` does it), `adopt`, `validate`, `mtu`,
`openwrt`, `secrets show/join/add/remove` by name.
