# The first run, reviewed as a beginner (13 November 2026, kiwi-server 2.77.2)

Asked by the owner: the first installation with the wizard, in the terminal and in the app,
has to work for someone who has never done this. Both were run from an empty home, taking
every suggestion; a fresh machine was installed from the result (the VM quince on sh3).

Items 1 to 5 and 7 to 25 are closed by 2.78.0 (the new questionnaire, `disk: auto`,
`kiwi-server next`, the fixes of the install that follows). Open: 6 (pick a netcup server
from a list), 26 and 28.

## What is already right

- One questionnaire: `setup.questions()` is what the terminal asks and what the app shows
  (`init --questions`, `init --answers`). There are not two routes.
- It finds the time zone, the keyboard, the home network and the router by itself.
- It makes the admin key and the secret store, and shows the recovery key once.
- The app goes on after the wizard with one step and one button per machine.

## The questions

| # | What a beginner meets | What it should be |
|---|---|---|
| 1 | No word on what is needed before starting: a rented server or a spare machine, a USB stick, about an hour. | A first page that says so, with no question on it. |
| 2 | "Name ending [home]", asked first, with a help text about DNS. | Not asked. It is `home`; an expert changes it under "More settings". |
| 3 | "Key [1]" with one choice in the terminal (the app skips it). | A question with one answer is never asked, in both. |
| 4 | The master's name is asked. | Suggested and shown, not asked. |
| 5 | The master's address is required, but the app lets you press Next without it and complains at the very end. | Checked where it is asked, in both. |
| 6 | For a netcup server the address and the disk are asked, although kiwi-server can ask netcup for both. | Log in, pick the server from a list; address and disk come from the API. |
| 7 | "Its disk [/dev/vda]: leave it unless the install says it is /dev/sda". Nobody can know this before the install. The same for every home machine (`/dev/nvme0n1`). | `disk: auto`: the installer takes the machine's one disk, and stops with a clear message when there are several. |
| 8 | "Your home network" and "Your router" are asked with the help "Found on this computer. Leave it." | Shown as found. Asked only when nothing was found. |
| 9 | A home machine starts as `gateway`: the preset that takes over the home's DNS and DHCP. | Starts as the one that keeps files and passwords. The gateway is a choice that says what it changes at home. |
| 10 | "Fixed address you know is free": the suggestion (.30) was taken on the test LAN. | The wizard looks (ping and the neighbour table) and suggests one that does not answer. |
| 11 | "System, ucore or debian", "Where downloads go". | Under "More settings". |
| 12 | "Encrypt them (recommended) [Y/n]". | Not a question: the keys are always encrypted. `--no-store` for a test. |
| 13 | The app's "+" for a home machine is small, and "none is fine" invites to skip the one machine that holds the files. | A clear "Add a machine at home" button; a network without one says what it then cannot do. |
| 14 | A wrong key at a choice ends the terminal wizard with an error and loses every answer. | It asks again. |
| 15 | The help of most questions hides behind "?" in the app. | The one sentence that matters stands under the question. |

## Around the wizard

| # | What happens | What it should be |
|---|---|---|
| 16 | `kiwi-server init` writes into the current directory. | `~/kiwi/fleet.yaml` unless a path is given, as the app does. |
| 17 | Without a terminal (a pipe, a script) `init` silently writes the commented example. | It says that it cannot ask, and how to give answers. |
| 18 | The terminal ends with a list of ten commands to type in order. | `kiwi-server next`: does the next step and says what it did, as the app's buttons do. |
| 19 | `kiwi-server validate` on the new fleet: "error: papaya: not enrolled yet". | A next step, not an error. |
| 20 | Nothing asks whether the recovery key was written down (the terminal scrolls on). | It waits for Enter after the key. |
| 21 | The phone is not mentioned until the app's "What next". | The last page says it: "then your phone". |

## The install that follows

Found by installing the VM quince from the wizard's kind of fleet.

| # | What happens | State |
|---|---|---|
| 22 | The install media power the machine off when done. Right for a stick, a surprise on a VM; the setting existed but the app did not show it. | **fixed** in 2.77.2 |
| 23 | A uCore machine starts as Fedora CoreOS and switches by itself in two steps with a restart each. "Set it up" pressed in between is cut off by the restart. | **fixed** in 2.78.0: apply waits for the switch |
| 24 | The second switch step failed and never ran again. First cause: it has no retry, so one failure is final. | **fixed** in 2.78.0: both steps retry |
| 25 | The real cause behind 24: on an encrypted machine with a fixed address, the network connection made for unlocking at boot stays the machine's connection, and it had no DNS servers. No name resolved, so the switch could never finish. | **fixed** in 2.78.0: the boot-time setting carries the DNS servers |
| 26 | A static address together with disk unlock needs `network.interface`; the build says so, but the wizard never asks and cannot know the name. | open: the installer should find the one wired card |
| 27 | `apply <fleet> <host>` on a fresh machine tries its name, which resolves on the VPN only, and "connection refused" says nothing useful. The app passes the address; the terminal did not. | **fixed** in 2.78.0 for `next`; plain `apply` still wants `--ssh` the first time |
| 28 | A reinstalled machine shows a new host key; the apply stops and names the command that forgets the old one. | open: the app and `next` should offer it |

## What can be looked up instead of asked

| What | How | State |
|---|---|---|
| Time zone, keyboard, home network, router | this computer | built |
| Language of the machines | this computer's `LANG` | open |
| A free address at home | ping, then the neighbour table | open |
| A netcup server's address and disk | netcup's API (`vps list`, `vps probe`) | built as commands, not in the wizard |
| The install disk of any other machine | the installer, on the machine | open (`disk: auto`) |
| The network card for unlock at boot | the installer, on the machine | open |
| The master's address when it runs at home | cannot be known: a name from a dynamic DNS service has to be given | asked, with that explanation |
