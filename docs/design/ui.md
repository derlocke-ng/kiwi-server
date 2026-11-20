# The page and the apps as server management (design, 19 November 2026)

The owner, 19 November: the page, the app and the phone app should work like server management
software does (Foreman was named), stay fast with many machines, and say what is going on with
a machine instead of "unreachable". This design comes from a look at ten such tools and at our
own code. Nothing of it is built yet; the order at the end is the plan.

The size to design for (the owner, 20 November): one company with three masters (one network:
a main one, a fallback, load shared between them, for reaching one headquarters from another),
and per site its machines and its people's devices: a main building with 30 nodes and 200
devices, an IT department with 10 and 20, a second building with 10 and 30, a third with 2
and 4, a headquarters abroad with 20 and 100. About 70 machines and 350 devices at five sites,
and linked networks of other companies beside it. The Devices page needs the same table,
search, groups and sites as Machines.

## What was found in our code

- A machine installed anew waits at boot for its disk's key, and `status` calls it
  "unreachable": it knows "disk locked" only for a boot system whose key is on record, which a
  fresh install's is not. `kiwi-server next` does know this case.
- Hints are plain text in two tables; "If something breaks." has no link, on the page or in
  the app.
- One thing has several names: the module `dns` is "Ad blocker (Pi-hole)" by its title, "dns"
  under Parts, `km-pihole` as a container. Problems name containers ("nextcloud-aio-apache is
  not running"). The page says "Linked networks", the app "Federation".
- Slow: several pages start `kiwi-server … --porcelain` at every view (0.3 to 0.6 s each);
  the look at the machines runs every 10 minutes and is written only when the last machine
  answered or timed out, behind the one job lock; the app probes by itself as well.
- The app: its window forgets its size; its 25 dialogs cannot be resized (libadwaita's dialogs
  never can); text is cut at fixed lengths.

## What others do, and what to take

| From | Take |
|---|---|
| Foreman | one status from sub-statuses; install states by name; filters as text that can be bookmarked |
| Proxmox | the task log always in view; filters `status: node: tag:` |
| Portainer | a health bar above the list; a click on a part of it filters |
| Rancher Fleet | the worst state wins and rolls up; "ready of total" |
| NetBox | what we did and what was seen are two things; a change log per object |
| Checkmk | not reachable behind something that failed is not "down"; a problem someone took note of leaves the overview |
| Tailscale | every filter has a text form |
| AWX, Semaphore | a job's output with a result per machine; run again, filled in |

To avoid: a state without its cause; actions that hit "all shown" unless ticked; several words
for one kind of group.

## The design

**Machines: a table, grouped by site** (`network.site`, in the fleet file already).

```
Machines  [This network v]   25 · 22 fine · 1 waits for unlock · 2 want a look
[search]  state:[all] site:[all] part:[all]      3 ticked: [Check][Preview][Apply]
v Main building (12)   11 fine · 1 waits for unlock
[ ] lime    waits for unlock  node    Files, Git +2    reinstalled 21:40
> Second building (3)  all fine      > CEO (3)  1 asleep      > IT (4)  all fine
```

- Columns: state, name, site, role, parts, the fullest disk, last apply, last backup, last seen.
- The filter is in the address (`site:ceo state:down`), so it can be bookmarked. The counts
  above the list filter on a click.
- The worst state of a site is its state, but "waits for unlock", "asleep" and "installing" are
  their own, never "down".
- Linked networks are a choice at the top. A linked network shows what it opened to us; more
  only if both sides agree to exchange it.

**A machine's page.**

```
lime  [waits for unlock]        Main building · Home server (node)
Reinstalled 21:40 from lime-encrypted.img (admin). First start: waits for its disk's key.
[Unlock it]   Disk unlock   If something breaks
Overview | Services | History | Backups | Settings
```

- A line that tells its story: what was done to it last, and what the last look saw.
- Every hint is text with its actions, from one table; the page, the app and the phone show
  the actions as buttons or links.
- History: applies, rebuilds, looks that changed its state, with who did it.

**A machine's states**, from two sources: what we did, and what the look saw.

| State | How it is known | Missing today |
|---|---|---|
| described | the fleet file | nothing |
| media built | the media exist; the activity log | nothing |
| installing | the provider for a rented server | a mark when an install starts |
| waits for unlock | its boot system answers | the first-boot case that `next` knows |
| setting up | a running set-up or apply | nothing |
| running, asleep | the look | nothing |
| unreachable | no answer, and none of the above | when it was last seen |
| removed | the activity log | its row kept in History |

**One name per thing.** A module's title first, everywhere; its technical name small after it:
"Ad blocker (Pi-hole) `dns`". A container is named under its part: "Files (Nextcloud):
nextcloud-aio-apache is not running". "Part" is a module on a machine; "module" stays for
Module sources. "Linked networks" in the app too.

**Fast.** One store of the machines' state in the page's process. Each machine is asked on its
own timer (a minute when running, 15 seconds while it changes) and written as it answers,
outside the job lock. Pages only read the store. What a command works out (access, unlock,
recovery) is kept until the fleet changes. The page gets changes pushed instead of loading
itself anew every 6 seconds.

**One API under all of it.** `/api/v1/machines`, `/machines/{name}`, `/events`, `/jobs`: the
page renders the same data the phone app and a later Windows app read. The desktop app reads
the control node's store instead of asking the machines itself.

## The desktop app on kiwi-template's kit

[kiwi-template](https://github.com/derlocke-ng/kiwi-template) holds the kit every Kiwi app
shares, and the UI rules. kiwi-server's app moves onto it (the owner, 19 November).

- **How:** the kit as a copy pinned in `VENDORED`, like kiwi-modules and kiwi-stack
  (`kiwi-template=vX.Y.Z`, copied by `tools/vendor.sh`, guarded in CI). The kit is never edited
  in kiwi-server. The one file `gui/kiwi-server-gui` becomes a package with the kit inside.
- **What it gives at once:** a window that remembers its size (rule 7), the job strip with its
  output in place of the small log pane (rule 10), the shared header, menu, About and
  Preferences (rules 2, 3, 5).
- **Cannot resize:** the rules keep dialogs as dialogs, in three widths. What needs room (logs,
  Accounts, Who may reach what, Activity) becomes a page in the main window, which resizes.
- **Cut-off text:** a new line for rule 9: text a person must read wraps; only a name in a
  one-line row may be cut, with the whole in its tooltip.
- **What the kit gets, for every Kiwi app:** status pills, filter chips, the help button, a
  list with search, sort, groups and ticks, a page for one item (pill, status line, actions,
  tabs), a timeline. kiwi-server keeps what is its own: the columns, the states, the settings
  form, the picture of the network.

## Desktops and laptops as clients

A person's computer (a Debian desktop, Fedora Silverblue or Bluefin) is a device of the network
that the fleet also knows as a machine: its place on the VPN, the network's CA, its disk's
unlock policy (docs/design/unlock.md), its updates. It is managed where everything is: the
control node, through the same API. `kiwi-desktop`, the app on that computer (control-plane.md
names it), is a client of that API as the phone app is: it joins the computer, shows its state
and its services. Not a second management tool with a fleet of its own.

## Modules of one's own, on one's own git

A network can keep its own modules on its own Forgejo (the `git` module): fork
kiwi-modules-community or start a repository, and add it as a module source by its link
(`kiwi-server sources`, pinned to a commit). That is the way for software a company needs and
nobody else packages, Windows server software included:

- Windows containers do not run on Linux: a container shares its host's kernel.
- A Windows program in a Linux container with Wine or Proton: works for many, not for what
  needs Windows services, drivers or a domain.
- A real Windows in a container as a virtual machine (QEMU with `/dev/kvm`, as the project
  dockur/windows does): runs everything, costs a Windows licence, 4 GB of memory and a large
  disk per instance.

A module for either is a module like any other. Not built or tried yet.

## Windows

WSL first, for the command line. Later a Windows app like the phone app: it needs a control
node to do everything, and can start a new network with the install media.

## Order

1. `status` knows a first start that waits for its key; the story line; hints with actions.
2. One name per thing.
3. The store, a timer per machine, the kept answers.
4. The Machines table on the page, with sites; no cap at 50. Built in 2.256 (the column
   last apply comes with step 3's store).
5. `/api/v1` and pushed changes; the app reads the store.
6. The app as a package on the kit: remembered size, job strip (built in 2.254), Preferences.
   No new screens.
7. A kit release with pills, chips, list, item page and timeline; the app uses them; linked
   networks as a choice.

## Decided (the owner, 19 November 2026)

- **A site is where machines stand** (`network.site`): a building, a part of one, a remote
  building, a home, a friend's home.
- **A linked network shows only what it allowed us**: as a rule the services we may reach, no
  summary of its machines. One kiwi network is one organisation (a main master, perhaps
  standby masters, many nodes); a linked one is another: a sister company that shares more, a
  competitor sharing resources for a project, a friend's network to play together over the VPN.
- **What is ugly in the app** is the machine's page itself: the size of its Overview, and
  Settings with its module and system tabs under "Machine kiwi-node / master". That page is
  what step 6 and 7 rebuild, not only the dialogs.
- **Who it is for:** people interested in IT, who know what an IP address is or can look it up.
  The page and the apps use the proper words where a thing has one; the docs explain each
  word where it first appears. Not a tool for people who want nothing explained, and not one that hides its terms.
- **Automated to the end:** a network runs without its admin doing anything. Things break; then
  the admin changes the disk or replaces the machine, and the software does the rest. Every
  place where a person still has to act is a gap to close, and until then the page says
  exactly what to do, with the button.
