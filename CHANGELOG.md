# Changelog

## 2.306.3 — the local AI's login is listed

- `kiwi-server logins`, the control page and the app list Studio's login (user `unsloth`)
  on a machine with the ai module (kiwi-modules 0.123.4).

## 2.306.2 — a lean gateway; the cache serves no empty layers

- **The local AI starts** (kiwi-modules 0.123.3): the Studio image for AMD cards, Studio started
  without a Cloudflare tunnel and with its tools off (`ai.tools: true` allows them), no JupyterLab.
  First machine: lemonaide, an RX 9070 XT.
- **A gateway has no downloads and no SFTP** any more (the `node-gw` role and the `gateway`
  preset): `downloader` and `sftp` are modules to add where wanted; the setup wizard no longer
  asks where downloads go. Machines with their own module list keep theirs.
- **The LAN cache** (kiwi-modules 0.123.1, 0.123.2): a layer that several images share and that expired
  was served empty, and a new machine could not pull nginx. The mirrors now fetch it again.
- **The secret store stays open while a command runs**, and nothing writes a plain `secrets/` folder
  beside `secrets.age` when it is locked: an apply that pulled a big image for longer than the idle
  lock wrote two files (host keys, MAC addresses) into a new folder on disk.

## 2.306.1 — the local AI passes validate

- `validate` (and the control node) filled in `ai.allow` only at a render, so a machine with the ai
  module failed its check ("nobody may reach it"). The first such machine: lemonaide, the owner's
  workstation as an on-demand server.

## 2.306.0 — a local AI; boards set up without hand work

- **The local AI** (kiwi-modules 0.123.0, docs/roles.md "A local AI"): the `ai` module runs Unsloth
  Studio on a machine with a graphics card (AMD or NVIDIA), its page and its OpenAI-compatible API
  at `ai.<hostname>` for the LAN and/or the VPN only (`ai.listen`). `kiwi-server ai key` keeps its
  API key in the store, `ai connect` writes opencode's provider block, `ai bench` times each model
  and checks a few known answers, kept to compare. `on_demand: true`: a machine that is off unless
  someone starts it is no problem when it is off.
- **Boards built by kiwi-server** come up at their fixed address on the port `network.interface`
  names, in their boot system and their system; root takes the fleet's key and Armbian's
  first-login questions are skipped, so `adopt` needs no password; the volume grows before the
  role (the image's 2 GB root did not hold the role's images). `adopt` offers no agent keys
  before a board's password, and checks only its own sudoers file (Armbian's own failed the
  whole check). The app has a picker of the network's rings and says what a bare machine needs.

## 2.305.0 — what a machine remembers, and its own units

The owner, 23 Nov 2026: failed update units that could not be restarted or read, a History that
was nearly empty, a refresh that reset the view.

- **`kiwi-server history FLEET MACHINE`**: what the machine's own journal says of the last days:
  every apply (each now writes a line: which kiwi-server, from which computer), the nightly
  updates, what its runbook did, its backups and starts. The app's History and the page's History
  tab show it as "On the machine", next to the control node's log.
- **kiwi's own units as services**: `kiwi-server service` takes `kiwi-stack-update`,
  `kiwi-runbook`, `kiwi-role`, … too (start runs one again, logs shows it). The app and the page
  list them, folded away unless one failed. The failed update units of 22 Nov were the bug
  2.300.0 fixed; they ran again on medlar, lychee, papaya and sorbet.
- **The app keeps the view on a refresh**: open sections stay open, the scroll stays.
- **Encrypted boards built by kiwi-server** (2.303.0) get kiwi-server's unlock key in their boot
  system (the admin's was refused), the port by `network.interface` (a board with two ports), and an
  unlock that times out after the disk opened counts as done (Debian 13's `cryptroot-unlock`).
- docs/design/ai.md: the owner's workstation for the local AI.

## 2.304.0 — another exit every day, a ring on a small board

- **vpn-client `reconnect`** (kiwi-modules 0.122.0): `reconnect: "07:00"` connects the VPN client
  anew every day; with `server_countries` or `server_cities` the provider gives a random server of
  those each time, so the exit changes every day. The owner's v1 master does the same with a
  daily restart and six Mullvad cities.
- **Tang for 32-bit ARM** (kiwi-modules 0.121.1, `tang:15-kiwi7`): a Banana Pi M1/M2 on a battery
  can hold a ring. A machine's ring moves to the new image at its next apply.
- notes/migration.md: the owner's answers (the Banana Pi on a LAN cable, encrypted, unlocked by
  hand once; the daily new exit).

## 2.303.0 — encrypted boards built, and a lock for people

- **The encrypted Armbian image, built by kiwi-server**: `armbian: {encrypt: true, builder: sh3}`
  and `kiwi-server build` builds it with Armbian's framework on that machine, in a privileged
  container of its own (Armbian's Docker mode prunes the machine's images: not used; nothing there
  is removed). The builder gets the board's one-time disk key on a tmpfs and public keys only;
  with a fixed address the boot system takes it. The first `apply` replaces the one-time key, as
  for Debian. Built on sh3 for a Rock Pi E in about five minutes; the key in no log or file.
- **The secret store's lock is for people** (the owner, 22 Nov 2026): the sync with the control
  node runs while the store is locked. A merge opens it in memory with this computer's key and
  writes it back encrypted; a pull leaves a locked store locked (it opened it until now). The app
  syncs while locked and waits only when its key has a password of its own.

## 2.302.0 — a way out per group

- **Leave through a linked network, by group** (kiwi-modules 0.121.0): `kiwi-server federate A B
  --exit family,phones` writes `exit: [family, phones]`; only those VPN groups' devices leave to the
  Internet at B's master, each group with its own kill switch, and the others by A's own master.
  A device chooses its way out by its group. The control page and the app have a field beside the
  switch. A group the VPN does not have stops the master's render.
- tests/drills/multihop.py takes `--group` and `--elsewhere` for it. Tried on the two test networks:
  sorbet's group family leaves at crumble's exit, its group guests at sorbet's own.
- The test network's moving address is 192.168.2.69: .70 was also the address of the second test
  network's standby crumble-b. The drills look for a free address in every line of the fleet files.
- tests/drills/vip-gateway.sh: the device behind the moving address is a network namespace on a test
  machine (its own card and address) instead of a VM; through two switches it lost 3 of 58 fetches.

## 2.301.0 — one Pi-hole at a time

- **DHCP from the site's pair, done again** (kiwi-modules 0.120.0): only the machine that holds the
  moving address hands out leases; kiwi-server gives its dhcp-relay `follow` with that address,
  and both Pi-holes keep the whole range. The halves of 2.296.0 broke devices' addresses: Pi-hole's
  DHCP is authoritative, so each refused the other's leases and a device got another address at
  nearly every restart. tests/drills/dhcp-pair.sh (two Pi-holes and a dhcpcd client on a podman
  network of their own): the same address through two switches and with one Pi-hole gone, 14 of
  14; `HALVES=1` shows the old way (two addresses, 14 refusals in 7 asks).
- The app: a locked secret store holds the sync back until it is unlocked, instead of "Not in
  step"; a store that locked itself after the app's idle time says so.
- notes/migration.md: the plan for moving the owner's network to v2.

## 2.300.0 — updates run again

- **The nightly update stopped on every machine with a backup module** since 2.294.0 (kiwi-stack
  0.9.31): the backup before the update called the module's script without its `backup` verb, the
  script printed its usage and the update counted that as a failed backup. kiwi-stack 0.9.33 asks
  for a backup. Seen as a failed `kiwi-stack-update.service` on lime, lychee and quince.
- The drill for a device behind the site's moving address, tests/drills/vip-gateway.sh (written,
  not yet passed: notes/TODO.md).

## 2.299.0 — installs from the LAN

The owner, 22 Nov 2026: the LAN's cache is for updates and installs, "less traffic usage, faster
updates/installs since 1-10gbit/s possible here".

- **Install images from the LAN's cache** (kiwi-modules 0.119.1, the cache's `files`):
  `kiwi-server build` takes the Fedora CoreOS ISO and Debian's netinst from
  `cache.<hostname>/files/` first, on a computer on that LAN or the control node. The netinst: 77 s
  from the Internet the first time, 4 s from the cache after. Each image is checked as before
  (Fedora's signature, the checksum from Debian's own server); a cache that does not answer within
  three seconds means the image's own server.
- Updates already came through the cache, now said in the docs: a uCore machine's new system image
  (rpm-ostree pulls it through the registry mirror), podman's images, Debian's packages.
- **The app remembers its window size** (kiwi-template's kit, ui.md step 6).

## 2.298.0 — one login per machine

- **One SSH login per machine** for the calls of an apply (kept two minutes): an apply of lime went
  from 54 s to 38 s, most calls from about a second to 0.2 s. A probe, the boot system and a test of
  a new key log in on their own. `KIWI_SSH_REUSE=0` turns it off; `KIWI_APPLY_TIMES=1` prints each
  call to a machine with its time.
- The offsite drill: its stand-in on the host's network with its port opened on a firewalld machine,
  the store's reach checked before anything changes, and `--from-offsite` drilled (lychee's store,
  lime's repository back byte for byte).

## 2.297.0 — the runbook on the control node, a store back from its copy

- The moving address is said again every 30 seconds (kiwi-modules 0.117.1), and the Machines table
  names the machine that holds it.
- **The runbook on the control node**: `machine-gone` lines take the place of `recovery:` for the
  machines they fit, `backup-overdue: backup-now` starts a backup that is late, and
  `certificate-due: tell` leaves a certificate to a person.
- **A backup store back from its offsite copy**: a store installed anew after its machine was lost
  gets every repository its copy at the other place holds, and it has not, with
  `kiwi-server backups FLEET STORE --from-offsite` (kiwi-modules 0.118.0, `backup-offsite.sh bring-back`).
- Fixed: a text of several lines (a CA) written into the fleet file by a command could not be taken
  out again without losing the file's comments; it is written as a block of its own now.

## 2.296.0 — a moving address for a site's basics

- **The `vip` module** (kiwi-modules 0.117.0, docs/design/basics-twice.md): two machines of a site
  share an address (VRRP, keepalived); one holds it while its Pi-hole answers and, on a gateway,
  while the master answers through it; the other takes it within seconds. kiwi-server pairs the
  machines of a site with the same `vip.address`, gives both one password, lets the first by name
  hold it, and a Pi-hole that hands out leases gives the moving address as router and DNS server,
  each Pi-hole from its half of the range. Each machine says whether it holds it (its notes).
  Tried in two containers (it moved in about a second and came back); not yet on the test
  network's machines.

## 2.295.0 — a dead machine's part elsewhere, and a runbook

- **`move PART DEAD OTHER --gone`**: a part of a machine that is dead comes from that machine's
  newest backup onto another machine. A person decides it: what came after that backup is lost.
  The dead machine is neither asked nor applied; a plain move points at `--gone` when the machine
  does not answer, and `--gone` is refused while it answers. The recovery case "A machine is dead"
  offers it. Drill: tests/drills/part-gone.sh.
- **The runbook** (docs/design/runbook.md): `defaults.runbook` and `hosts.NAME.runbook` say
  beforehand what happens in which case; checked, and shown per machine (`kiwi-server parts`, the
  page, the app). `part-down` acts: a machine with such a line restarts a failed, crashed or
  unhealthy container of the part by itself (`kiwi-runbook`), at most `tries` times a day, and
  leaves a person's stop alone. `disk-full` acts too: images no container uses, a day old, are
  removed when the disk passes its mark, once an hour at most. What it did is in its notes for an hour.
- **Fixed: a ring away during an apply** put a disk that was bound to its rings to "its key only"
  for good, with no restart for updates any more (lime, while lychee was off). The binding now
  stays as it is, and a bind removes that guard again.
- **Fixed: every apply of a Debian or Armbian machine with rings rebuilt its initramfs** (a
  comparison that never matched); now only when something changed.
- **Only the machines a change reaches are rendered** (docs/design/scale.md): a fingerprint per
  machine of its own settings, what every machine shares and what its last render read of the
  others. A part's setting on one machine renders that machine alone, a comment nothing; each
  answer is the same as a full render's (test). Fixed on the way: a resolved setting could be a
  module's default itself, and a render wrote into it (access rules) for every later machine.
- **Only the stack changed: only the stack's steps** (kiwi-stack 0.9.32, `--stack-only`): when a
  machine's role script differs from its last apply's in the stack's files, containers or names
  only, the apply writes those, restarts what changed and checks the stack; the machine's own
  steps are left out. `KIWI_FULL_APPLY=1` runs the whole role.
- **A crash loop is told**: a container that systemd or docker restarted three times or more in ten
  minutes is a warning ("it does not stay up"), with the way to its log.
- **Devices by site**: the Devices page groups devices by the site of the master they connect to,
  with `site:NAME` in its filter.

## 2.294.0 — no part with data moves by itself

The owner, 22 November 2026: fast recovery with a copy kept ready uses older data, and the
machine, back, holds a second version of the same database. The network is for setting up and
running many servers and clients with ease; DNS, the VPN and the gateway can go over, a part with
data is moved by a person.

- **Taken out**: the standby's take with permissions, the control node's switch by itself
  (`recovery.switch`), the holds after a power off or restart (`standby --hold`, `X-Kiwi-Hold`),
  `switch --taken`, `defaults.parts.*.lost` (left in a fleet file it does nothing). An apply takes
  the permission scripts off the machines. The permissions stay in the code, unused, for
  automation that needs one actor at a time.
- **Kept**: a copy kept ready (`keep_ready`), `switch` and `move`, backups, emergencies and messages.
- **A switch needs the machine it leaves** and loses nothing: the part stops there, the standby
  fetches once more; when that last fetch fails, the part starts there again and nothing is
  switched. A machine that does not answer is not switched away from: installed anew under its
  name, it brings its data back from its backups.
- **A backup before an update** (kiwi-stack 0.9.31): `kiwi-stack update` backs the machine up
  first; when that fails, nothing is updated.
- **The diagnosis asks the provider**: switched off there means it is started, running and silent
  means it hangs.
- **The control node's own update** checks its page after the restart and goes back to the image
  before when the page does not answer.
- **The master's door** (kiwi-modules 0.116.2): an address that keeps opening more connections than
  the limit lets through goes into a fail2ban jail.
- notes/TODO.md cut down to the core; new: a runbook in which admins decide beforehand what happens
  in which case, carried out by each node's agent.

## 2.293.0 — the backlog, first part

notes/TODO.md is one list again, merged from every note, the roadmap and every design, each item
checked against the code; this release works its first items.

- **The role script does not stay on a machine** (kiwi-stack 0.9.30): it carries every secret of
  the machine and removes itself after a run without a fault. An apply sends a fresh copy.
- **The control node renews certificates of the network's own CA**: every six hours it applies a
  machine whose certificate has 30 days or fewer left. Before, only a person's apply did.
- **A sudo that wants a password** is said plainly, with the line that fixes it.
- **The app takes and writes network cards** (a network of another operator), as the page does.
- **The Machines table** has a column for the last apply.
- **After removing a computer** the page says what it still knows and where its keys are replaced.
- **`/api/v1/`** answers for every API route; new: `/api/v1/jobs`, `/api/v1/jobs/ID`,
  `/api/v1/machines/NAME`.
- A computer's first sync hands over the machines it switched off and the parts it held back.
- `standby --hold` says its minutes in words; the power-cut drill stops when the hypervisor does not
  answer; the notes, ui.md, the roadmap and five designs say what is built.

## 2.292.0 — an update's restart does not move a part

- When a machine says it restarts for an update (its last report), the control node tells the
  standbys of its parts with a permission to wait 15 minutes, as `kiwi-server power restart` does.
  A slow update no longer moves the part to its standby. After the leash's restart (a machine
  that may have been carried off) they do not wait.
- `kiwi-server standby <fleet> --hold MACHINE [--minutes N]` does the same by hand.

## 2.291.3 — reminders, not every hour

- What the control node did by itself, or leaves to you (a part a person switches, a machine
  restarted three times today), is told at once and, while it lasts, again after 1, 6 and 24
  hours, then once a day. It was repeated every hour.
- `tools/gui-preview.py stops`: the app's "When it stops" and Home's emergencies with made-up data,
  in a private session (looked at on 22 Nov 2026: both show, "I have it" runs `control ack`).

## 2.291.2 — the machine a part left gets its apply when it is back

- Fixed: after a switch, the apply of the machine the part left did not wait for it when it was
  away (the control node's own try counted only for a machine already in its book of waiting
  applies). Once back, it kept its old role until someone applied it. Found by the permissions
  drill of 22 Nov 2026.
- Fixed: the control node's own machine, which applies itself detached, stayed "Changes not applied
  yet" after every apply: what it was sent is noted when it is sent now.

## 2.291.1 — a switch while a ring is away

- Fixed: when the control node applied its own machine and one of its disk's rings was on a machine
  that was away, the whole apply failed. A switch after a power cut then ended as a failed job
  (drill of 22 Nov 2026), although the part had moved. It warns now, as every other machine does.
- A message about a machine that does not answer counts from when it fell silent (its last report),
  not from the control node's first look after: an emergency comes about three minutes sooner.
- What the network remembers: a change sent late (an outbox sent hours after) gives way to a newer
  one of the same line at the control node.
- A short case closes in words ("it was back before anything was to be done").

## 2.291.0 — what the review of the permissions found

An independent review of 2.286–2.290 found these; each is fixed and tested.

- **A standby that took a part fetched over it**: its next fetch (rsync --delete) could write the
  old machine's folders over the live part. Nothing is fetched over a part after its take.
- **Writing a take down could stop the part** on its new machine: the apply found no permission
  there. The switch now gives the new machine a start permission and sets the witness up anew.
  An apply of a standby waits until its take is written down.
- **A take that did not start lost the part**: both voters refused the old machine. Now the
  standby gives it the permission again, tries every minute, and the control node tells you.
- **The control node's own switch ignored the table and the diagnosis**: it moved parts a person
  switches, and parts of a machine that said it restarts. It follows both now.
- **A power off's hold could end at once**, when a permission was given in the seconds before the
  machine went down. It ends only when the machine answers two minutes after it was set.
- **A whole site away kept a master's devices from going to its standby master**; machines that are
  off on purpose no longer count as away; a power off without a reason is no longer "planned".
- The voters' times count from when a permission arrived, each SSH call has a limit, a boot drops
  the permissions from before it, a voter's key votes only as that voter, old voter lines and a
  part's old permission are cleaned up, lists in what the network remembers change item by item,
  and an emergency told before 2.290 is repeated too.

## 2.290.1 — emergencies reach the app

- Fixed: the app (and `status --porcelain` on a computer joined to a control node) did not get
  the control node's emergencies, so it showed no "I have it". Found by the emergency drill
  (tests/drills/emergency.sh).
- kiwi-modules 0.116.1: on Docker, a part that runs by a permission restarts when it fails, but no
  boot starts it before its permission is asked (`restart: on-failure`).

## 2.290.0 — emergencies, and messages by how much a part is needed

- **Emergency messages** (docs/design/authority.md): a machine that runs a part the network
  needs (a gateway, a site's DNS, an unlock ring, the control node) and does not answer is told
  after two minutes on every way that is set, at ntfy's highest priority, and again every 15
  minutes until someone says "I have it": on Home, in the app, by the link in the message, or
  `kiwi-server control ack <fleet> <key>`. Activity says who has it. `notify: team` (mail, ntfy)
  names people beside you who get the emergencies too; the settings page and the app have the
  fields.
- **Messages follow the table per part**: a machine whose parts can all wait is not told about
  at all (its page says it); one the network needs is told after two minutes.
- **The machine's page and the app show each permission live**: the seconds left, and a part
  its standby took over by itself.

## 2.289.0 — what happens to each part

The rest of step 3 of the rebuild in [docs/design/authority.md](docs/design/authority.md).

- **One table per part** (`policy.py`): whether it can move, whether its standby takes it by
  itself, how much the network needs it. The kinds of parts bring their rows; the fleet file
  changes them with `defaults.parts: { vault: { lost: ask, need: must } }`, checked by `validate`.
- **One sentence per machine** on its page (*Services*, *When it stops*), in the app, and from
  `kiwi-server parts <fleet> [machine]`: "When medlar stops: vault goes to quince after about
  four minutes; tang (the network needs it) and 11 more are away until it is back."
- **Permissions follow the table**: only a part whose standby takes it by itself runs by a
  permission; one a person switches, or that waits, keeps running on a machine that is cut off.
- Drill `tests/drills/last-report.sh` (2.288.0's last report, on the test machines): the reason
  of a `power restart` reached the control node in 7 seconds, the standby did not take the part,
  and the machine held its permission again 142 seconds after.

## 2.288.0 — a diagnosis before a decision

Part of step 3 of the rebuild in [docs/design/authority.md](docs/design/authority.md).

- **A machine says when it goes down.** Its agent sends a last report as the machine shuts
  down: a restart or switched off, and why when kiwi-server's own scripts asked for it (an
  update in its window, `kiwi-server power`, the leash). Activity shows it.
- **One look in a fixed order before anything is done** about a machine that does not answer:
  did it say it goes down (a restart is waited for 10 minutes without a message or a restart at
  the provider; switched off is left alone), is its disk waiting, does any other machine of its
  site answer (none: nothing is restarted, the whole site is away), do its voters or its standby
  still reach it (then only the way from the control node is cut: nothing is done). The message
  says what was found, and when the machine is back Activity has one line for the case.
- **`power off` holds the standby's take** of the machine's parts with a permission while it is
  off; `power restart` for 15 minutes.
- Fixed: a part the standby took was written down twice (a second switch job while the first
  ran), and the take's time was said in the control node's UTC.

## 2.287.0 — a part runs only where it may

Step 2 of the rebuild in [docs/design/authority.md](docs/design/authority.md).

- **Permissions that run out.** A part with a standby runs on its machine only while the
  standby or a witness (another machine at the site, or the master) gave it the permission
  within the last three minutes. Neither an apply nor a boot starts it without one. A machine
  that is cut off stops the part by itself; one that comes back after the part moved gets no
  permission, so nothing runs twice and nobody has to tell it (`kiwi-lease`, `kiwi-vote`;
  kiwi-modules 0.116.0, kiwi-stack 0.9.29).
- **The standby takes the part by itself**, without the control node, when its own and the
  witness's permissions have run out, the switch by itself is on and its copy is fresh: 244
  seconds after the power cut in the drill (tests/drills/permissions.sh). The control node writes
  it down as a switch from the standby's next report (`switch --taken`) and leaves such parts to
  their voters. A part without a possible witness is switched by the control node as before.
- **A switch by hand ends the permission** on the machine the part leaves: its voters give it no
  more, and it drops the part from its list.
- **The app follows the control node**: when the control node says something changed (an apply,
  a job), the app fetches what it holds and what it remembers. An idle sync asks for the revision
  only, and a push with nothing new sends nothing.
- Docs: [backups.md](docs/backups.md#permissions-a-part-runs-only-where-it-may),
  [security.md](docs/security.md#the-voters-keys).

## 2.286.0 — one keeper for what the network remembers

The first step of the rebuild in [docs/design/authority.md](docs/design/authority.md).

- **The control node keeps what the network remembers about its machines** (when each was
  applied and with what, what a machine holds back, which is switched off, where copies of the
  network went). Each computer kept its own before: the app showed lime, quince, medlar and
  sorbet as "Changes not applied yet" though the control node had applied them and `plan` found
  nothing to change. A computer now takes the control node's with every sync and sends what it
  did itself with the next one; the first sync hands over what it knew newer.
- **Fixed: "Not in step … that key does not open .pull.age".** Two syncs ran at once (the app's
  timer and its live watch) and shared one temporary file. One sync of a folder runs at a time
  now, each with its own temporary files.
- The design of the rebuild (one truth, permissions that run out, a table per part, a diagnosis
  before a decision, emergency messages, a second control node) is in docs/design/authority.md.

## 2.285.0 — a switch with your OK; the app keeps working while it waits

- **A machine that does not answer offers to switch its parts kept ready** (its problem row, in
  the app and on the page: "Switch vault to medlar"). The control node's own machine is switched
  away from this way only, with a person's OK (the owner, 21 Nov); when it is the one gone, the app
  runs the switch itself instead of through it.
- **Fixed: "Update everything" in the app ran on this computer**, not as the control node's update
  job: it looked for the list of machines in the wrong place.
- **The app no longer freezes** while a sync choice is made (that can take half a minute) or the
  access settings are saved: both run in the background with a word on screen.

## 2.284.0 — a sync that asks less, and four times faster

- **A sync asks only about what really needs a person.** The store's JSON maps (the machines'
  MACs, the pins) are merged key by key, and of a key both sides changed the later `seen`/`at`
  counts: the owner was asked to choose over two copies of macs.json that differed only in when
  each side last saw two machines. "Compare with the other copy" marks what merges by itself,
  shows the MAC file's entries with the newer side, and its buttons keep one side only for the
  files both changed (`control push --keep here|there`); every other change of both sides stays.
  Before, a choice took one whole copy and lost the other's changes.
- **`power off` holds the switch**: a machine switched off on purpose is left alone (no switch
  of its parts, no restart at its provider) until it answers again or is woken.
- **Faster** (kiwi-modules 0.115.0): compiled templates kept, YAML through libyaml, onion keys
  worked out once: `list`, which the app asks on every refresh, 4.0 s → 0.9 to 1.4 s on the test
  network; every render, `plan` and apply gains the same.

## 2.283.0 — the hold, as it was meant

- **Fixed (2.282.0): the machine a part was switched away from ran it again for minutes** when it
  came back, until its apply. The hold read the control node's listing kept for its pages, which
  is made anew only after a job; while the switch job still ran it said the machine still ran the
  part, and nothing was held back. It reads the fleet file itself now.
- The agent reports 45 s after a start too (and 10 s, and then every minute): a stack that starts
  the part a little later is caught then, not a minute later.
- The failover drill measures that time from the units by name: its pattern matched nothing and
  said 0 s.

## 2.282.0 — the switch, reviewed

A review of the switch by itself and the pair found ways to a part running on two machines,
a lost kept copy and a switch for no reason. Fixed:

- **A part on two machines**: the machine a part left kept running it after its apply, now that
  it keeps it ready (kiwi-stack 0.9.28 stops it); a switch whose old machine answers but whose
  containers there cannot be worked out switches nothing; a failing last fetch no longer counts
  as the old machine not answering; the fleet file is written before the part starts on the
  standby; a hold ends where the fleet file gives the part back to the machine.
- **A lost kept copy**: the copy is made once per first fetch by a marker, again after another
  switch, never skipped because of an older one, and a folder that cannot be copied is not
  fetched over.
- **A switch for no reason**: a machine asleep by its window is not gone; a standby at another
  site counts only while the master answers (it reaches the machine over the VPN only); gone not
  before the last look that saw the machine answer; an unknown policy word means by hand.
- A standby without what the part needs keeps nothing ready, with a warning, instead of failing
  its applies. The address in the standby's reach check is quoted as an argument.
- Tried before this release (2.281.0): the copy kept beside the folder (quince, 32 KB), two
  switches and their pair, 0 wrong each.

## 2.281.0 — what the machine a part left had written is kept

- **A standby's first fetch over a folder that holds data keeps that data beside it first**
  (`<folder>.kept-<date>`, hard links, 14 days): the machine a part left may have written after
  the copy it now fetches, and the fetch replaces the folder with the copy. A switch clears the
  taking machine's mark of a whole copy, so the day it keeps the part ready again this applies.
- Tried on 2.280.0: the power-cut drill (quince off with Vaultwarden): on medlar by itself after
  7 min 36 s with a fresh copy, quince's own copy ran 7 s after its start, the pair kept, 0 wrong.

## 2.280.0 — a standby that is behind says so, and is not switched to by itself

- **A copy kept ready that is more than ten minutes old is a warning** on the standby (page,
  `status`, messages), with what its last fetch said.
- **No switch by itself to a copy that was behind already** (more than ten minutes when its
  machine fell silent): that would lose what came since, and with the pair the machine, once
  back, would take the old copy over its own. It is told, and left to a person.
- The control node's own machine: its parts kept ready switch when you say so (the control node
  is the one that switches by itself); the page and the app say it.
- A switch does not wait on the machine that is gone to point the part's name.
- `kiwi-server standby` says whether a part goes over by itself. The roadmap's current picture.
- Tried on 2.279.0: the master-down drill (papaya's Pi-hole answered throughout).

## 2.279.0 — the switch by itself, told and shown

- **Tried with a power cut on 2.278.0** (tests/drills/part-failover.sh, medlar off with
  Vaultwarden on it): on quince 6 min 38 s after the cut by itself (was 10 min 42 s), its name
  answered there after 7 min 8 s with its data, medlar's own copy stopped 12 s after it started
  again, and medlar keeps it ready since: the two stay a pair.
- **A switch done by itself is in the messages** (mail, ntfy, webhook), as a restart is.
- The page and the app say on a machine's Services that a part kept ready goes over by itself,
  and after how long (`list --porcelain`: `switch_after`); so does the recovery report.
- certbot's image is pulled before each run (kiwi-modules 0.114.0): `latest` stayed the copy
  pulled the first time. A certificate renewal was tried on the test network (paste.dlhost.eu).
- Docs: README, examples, the scale design; the drills.

## 2.278.0 — a pair stays a pair; a failover two minutes sooner

- **After a switch the machine the part left keeps it ready** (`keep_ready` turned round by the
  switch): once it is back it fetches the part's data and has its containers made, so the part
  can fail over back to it. Tried with Vaultwarden from quince to medlar (part-standby.sh, 0 wrong:
  quince keeps it ready afterwards; the name answered on medlar after 36 s).
- **A machine counts as gone from its agent's last report**, not from the look that missed it:
  a failover by itself comes about two minutes sooner. Tried before this release: medlar's power
  cut, Vaultwarden on quince after 10 min 42 s, medlar's own copy stopped 13 s after it started
  again (the agent's hold), and medlar gave the part up with the apply that waited for it.

## 2.277.0 — the machine that was gone holds the part back; fetches during jobs

- **Tried live**: quince (a VM) lost its power with Vaultwarden on it; with nobody doing
  anything the control node switched it to medlar after 10 minutes, its name answered there with
  its data, and quince gave the part up when it was back (tests/drills/part-failover.sh).
- **Fixed: the machine that was gone ran the part again for minutes** beside its standby when it
  started (2 min 20 s in that drill, until the apply that waited for it). A switch made while the
  machine did not answer now keeps its containers to hold back; the agent's report gets them in
  its answer (`X-Kiwi-Hold`) and stops them at once. The agent reports 10 s after a start (was
  20 s).
- **Fixed: a computer's fetch of the fleet timed out while the control node ran a job** (a switch,
  an apply of several machines: minutes). It now waits at most 5 seconds and then gets the copy as
  it rests; what the job changes comes with the next fetch.

## 2.276.0 — a part goes to its standby by itself

- **Failover by itself**: when the machine that runs a part kept ready elsewhere has not
  answered for five minutes, and the machine that keeps it ready cannot reach it either (ping or
  its SSH port, at home or by name), the control node switches the part there as a job, as the
  page's Switch does, and Activity says why. If the standby still reaches it, only the way to the
  control node is cut, and nothing is switched. Once in 30 minutes per part.
  `recovery: { switch: { after: 5 } }`; `{ do: tell }` or `false` leaves it to a person; "When a
  machine is gone" on the page and in the app (docs/control.md).
- The control node's restart hold of an hour no longer holds after a restart that worked (the
  machine answered in between): written in docs/control.md now.

## 2.275.0 — a switch in seconds

- **A part switched to its standby answers within a minute** instead of six to eight
  (kiwi-modules 0.113.0, kiwi-stack 0.9.27). The standby's proxy holds the part's site in a
  second config and its certificate carries the part's name; the machines that answer for names
  keep the names of parts kept ready in a hosts file of their own. A switch starts the part's
  containers kept ready, puts that proxy config in place, and rewrites the names file on every
  machine that answers for names; the applies follow. Tried three times with Vaultwarden between
  medlar and quince (tests/drills/part-standby.sh, 0 wrong each): running on the standby 6 s
  after the switch began, lime's Pi-hole pointed its name there after 12–16 s, the name
  answered there after 29–37 s (docs/backups.md, A standby for a part).

## 2.274.0 — pages that follow changes; a standby with its part made; SFTP for the offsite copy

- **The control page follows changes** (`/api/v1/events`, server-sent events): Home, Machines,
  Services, Activity, Backups and a machine's page take the new state when a look, a job or an
  apply changes something, without loading themselves again; a page someone has ticked or typed
  on keeps it and says "Something changed meanwhile". `/api/v1/status`, `machines`, `activity`
  are the API's first version. A joined app follows the same stream (`kiwi-server control
  watch`) and asks again within ten seconds instead of once a minute.
- **A standby has its part made** (kiwi-modules 0.112.0, kiwi-stack 0.9.26): the machine that
  keeps a part ready pulls its images and prepares its containers with the settings and secrets
  of the machine that runs it, without starting them (`kiwi-stack ready list`). A switch starts
  them at once, before the standby's apply. Tried with Vaultwarden from medlar (Debian) to quince
  (uCore): it ran on quince 6 seconds after the switch began, nothing was lost, 0 wrong
  (tests/drills/part-standby.sh). Its name follows with the apply (217 s here): the name on both
  machines before a switch is the next step.
- **The copy at another place over SFTP** (a Hetzner Storage Box, a friend's server), its host
  key kept at the first copy; a Nextcloud's own WebDAV kind. Tried with stand-in servers
  (tests/drills/backup-offsite.sh … sftp: 0 wrong).
- **Fixed: a master that came back after a restart was not restarted again for an hour** when it
  went away a second time (the master-gone drill: 34 minutes). A machine that answered after the
  restart lifts that hold; three restarts a day stay the limit.

## 2.273.0 — a copy of the backups at another place

- **A copy at another place** (kiwi-modules 0.111.0): a backup store keeps an encrypted copy of
  everything it holds in a WebDAV folder (1&1 SmartDrive, a Nextcloud, a storage box), brought
  up to date every night by rclone and encrypted once more before it leaves, names included,
  with a key from the seed. Tried with a stand-in WebDAV server (tests/drills/backup-offsite.sh,
  0 wrong): 1.8 GB in 44 s, nothing readable at the place, a repository back byte for byte, the
  next night's run 12 s (docs/backups.md, A copy at another place).
- **A machine that is gone is noticed sooner**: when a machine's agent stops reporting, the
  control node looks at once instead of at its next ten-minute look, and the fleet's recovery
  policy (restart at the provider after 3 minutes) acts minutes earlier. The master-gone drill
  took 17 minutes to restart the master before.
- **Fixed: a sync stopped at "secrets/netcup.token changed on both sides"** after the control
  node and a computer had both used the netcup login (its refresh token changes when used).
  The newer is kept; the file is written only when the token changed.

## 2.272.0 — the time from the network; a sync that keeps both sides' settings

- **The time from the network** (kiwi-modules 0.110.0, kiwi-stack 0.9.25). Every Pi-hole answers
  time questions (NTP, port 123); the machines at a site ask the one on their LAN first, the
  others the master's, the public pool behind them; devices on the LAN can ask it too (and get
  it with DHCP where Pi-hole hands out addresses). Pi-hole no longer sets the machine's clock
  itself: it did so every hour beside the machine's own time service. `dns.ntp: false` switches
  a Pi-hole's time server off (docs/network.md, The time).
- **A sync keeps both sides' settings** when the control page and a computer changed different
  settings in the fleet file: it is merged key by key. Only the same setting changed on both
  sides, or a file with comments further down, still asks which copy counts.

## 2.271.0 — a home that goes on without its master; netcup's snapshots and rescue system

- **Fixed: while the master was away, a node's Pi-hole gave the names of the Internet no answer**
  (kiwi-modules 0.109.0). It moved on to its fallback only when a client asked a second time,
  so every lookup took five seconds or failed. Now a small resolver in front of a node's Pi-hole
  (CoreDNS) asks the master while it answers and Cloudflare over TLS while it does not: the first
  question takes about two seconds, the ones after it are as fast as before. Tried with the
  master's VPN switched off for three minutes: the home's Pi-hole answered throughout. The
  master-down drill reaches the node by its home address now; by name it had counted its own
  lost way to the node as a silent Pi-hole.
- **netcup snapshots**: `kiwi-server vps snapshot|snapshots|revert FLEET HOST`. One is taken
  while the server runs (13 seconds on sorbet); going back restarts the server with its disk as
  it was (sorbet answered again by itself 4 minutes later).
- **netcup's rescue system**: `kiwi-server vps rescue FLEET HOST --name on|off`, for a server
  that no longer starts. Its root password goes into the fleet's secrets, not onto the screen.
- Every change at netcup refuses a server that `KIWI_NEVER` names, by id or address (the guard
  the drills use next to a live network).
- The VPN client uses gluetun's current name for its resolver setting (`DNS_UPSTREAM_RESOLVERS`).
- **The app reads the control node's look** instead of logging in to every machine from this
  computer: `status --porcelain` on a computer joined to a control node gives the control node's
  word on each machine (a second, not a minute), as long as it saw each one in the last 15
  minutes; otherwise, or with `--direct`, the machines are asked from here as before.
- **Fixed: the control page warned that clocks were half a minute off** that were right. A
  machine's report, up to a minute old, was measured against the time it was read, not the time
  it came.

## 2.270.0 — "No master yet" opens its control page at home again

Found by running the starter stick's drill on the released version (`tests/drills/starter-vm.sh
alone`, its VM on another machine).

- **Fixed (kiwi-modules 0.108.0): a network without a master refused its own home.** Its
  control page let in the VPN the network has in mind for later and answered 403 to the home
  network, since new networks get a mesh of their own (2.2xx). The home network stands in
  for the mesh on a machine off the VPN, whatever mesh is planned.
- **Fixed: a second "Set it up" while the machine applies itself ended as failed** ("Unit
  kiwi-self-apply.service was already loaded"). It now says that the machine is applying
  itself already and starts nothing a second time.
- Docs: what the resolver passes on about who asks (no client subnet with Cloudflare; Google
  passes the exit's; one of your own cannot be sent), docs/network.md, DNS.
- The starter drill makes its Windows disk where it is started and sends it when the VM's
  host cannot make one.

## 2.269.0 — install media left in a machine do not install it again

Tried in VMs whose firmware starts from the media first (`tests/drills/install-guard-vm.sh`:
uCore with UEFI and with BIOS, Debian).

- **The guard.** Install media wipe the disk without asking; left in a machine that starts
  from them first, they wiped it again at every start. Each ISO now has an id of its own, the
  installer leaves it on the installed system (`/boot/kiwi-installed`), and before anything
  is erased it looks for it there. Found: nothing is installed and the screen says so. A
  uCore machine with UEFI puts its disk first and restarts into it; any other switches
  itself off after a minute. An ISO built again has a new id and installs: that is how a
  machine is installed again on purpose (docs/getting-started.md).
  `kiwi-server vps install` does not send the ISO that installed a server a second time (it
  would switch the server off untouched): it says to build it again, and `kiwi-server next`
  builds it again by itself.
- An encrypted board's volume is grown to its disk by the first apply (Armbian's first
  start grows the partition only).
- `kiwi-server recovery` (and the Recovery page) names the parts of a dead machine that are kept
  ready on another one, and the switch that brings them up there.
- `kiwi-server ssh-config FLEET [--write]`: ssh's own configuration for the machines, with the
  fleet's key and host keys: `ssh papaya`, and `papaya-lan`, `papaya-vpn`, `sorbet-public` for
  when names do not resolve (docs/machines.md, A shell on a machine).
- **The resolvers answer every name** (kiwi-modules 0.107.0, kiwi-stack 0.9.24). The default was
  Quad9, which leaves out names it holds to be malicious: a site was blocked that no blocklist
  of the network named. The VPN client's DNS over TLS now goes to Cloudflare, and so do the
  fallbacks reached directly. A fleet that set `dot_providers` or `fallback_dns` keeps its own.
- **Fixed: `kiwi-server control activity` crashed** as soon as the control node had run a
  task. Its log's times are in this computer's time now, as the tasks' are.
- A copy of the network without a signature (made before 2.170.0) can be opened on purpose
  in the app (*Open it anyway*) and on the control node's first page (a tick), as
  `restore --unsigned` does. A copy with a wrong signature is never opened.
- `kiwi-server switch` says how long the part was away as soon as its standby runs it; what
  follows only tells the other machines where it is.
- Tests: a notification's mail goes through a real SMTP conversation (STARTTLS, login); the
  page preview shows the plan of a move.

## 2.268.0 — move and switch on the page and in the app; three things found on real machines

Tried on the test machines before the release: `tests/drills/part-standby.sh` (a named volume, a
public name), `tests/drills/relay-public.py`, a clock set back on a machine.

- **Move, keep ready and switch on a machine's page and in the app.** The page: Services has
  the plan, *Move it*, *Keep it ready* and *Switch*. The app: *Move…* and *Switch* on a
  machine, through the control node when there is one. Both settings forms have *Names of its
  parts* (`names`) and *Parts kept ready on another machine* (`keep_ready`).
- **A standby follows named volumes too**, not only folders (the pastebin's data).
- **Fixed: a part with a public name was dead after a move or a switch.** The part's settings
  moved, `reverse-proxy: public` stayed behind. The new machine's proxy now opens to the
  Internet as the old one's is, the old one's closes when no public name is left there, and
  the new machine asks for its certificate again once the master hands the name on.
- **Fixed (kiwi-modules 0.106.0): the relay refused every note not dated this very second**
  with the default `keep_days: 0`. Found by posting to the public relay from outside.
- **Fixed (kiwi-modules 0.106.0): the control node's own machine could not report** (403), and
  a public name without a certificate is asked for again in ten minutes, not a day later.
- **A machine reports a clock that was set back** (a warning until the time has caught up:
  the VPN's other ends may refuse it that long) **and packets cut into pieces** (a note for
  an hour: a tunnel's MTU is larger than its path carries). The agent sends both each minute.
- The page links a new network by card (*Link a new network*). The app has a machine's
  *History*, its status chips under its name, and *Open the game page*.
- Wake-on-LAN on an ODROID-N2+ with Armbian: the card takes the setting and gets the packet,
  the board does not start from off (docs/machines.md says so).
- Docs: the roadmap, the README and the page review say what is built; `notes/TODO.md` is the
  one list of what is open.

## 2.267.0 — a part moves, and has a standby: tried on real machines

Everything here was run on the test machines before it was released (a uCore and a Debian
machine, both ways): `tests/drills/part-move.sh`, `tests/drills/part-standby.sh`.

- **A standby for a part** (`keep_ready: { vault: medlar }` on the machine that runs it): the
  standby machine fetches the part's folders every minute with a key that can only read
  them. `kiwi-server standby` says how old each copy is; `kiwi-server switch FLEET PART`
  gives the part to its standby, also while the machine it runs on does not answer. That
  machine then gets the change from the control node the moment it answers again
  (docs/backups.md, A standby for a part).
- **Fixed: a part under a name its machine's own name does not cover had no valid
  certificate.** A machine's certificate held its name and `*.<machine>.<domain>` only. A
  part named by service (`vault.home`, 2.263) or one that moved and kept its name
  (`vault.quince.home` on medlar) answered, and every browser refused it. A machine's
  certificate now holds every name its parts answer to.
- **`kiwi-server move` now looks before it starts and puts things back when it fails.** The
  plan renders both machines as they would be after the move and stops on what an apply
  would refuse (a hidden service on the old machine that still names the part). If the new
  machine cannot be applied or the data does not arrive, the fleet file goes back, the part
  leaves the new machine and starts again where it was. A failed restore shows restic's own
  lines.
- kiwi-modules 0.105.0: a restore leaves SELinux labels alone (a part could not move between
  uCore and Debian), and a clearer message when a backup or a restore runs already.

## 2.266.0 — a part moves to another machine

- **`kiwi-server move FLEET PART FROM TO`** moves a part with its data, its secrets and its
  name: it stops where it is and that machine makes one last backup; its generated secrets are
  pinned to the new machine; the fleet file moves the part and its settings and keeps its
  name; the new machine is applied and gets the data from the old machine's backups; the old
  machine and everyone the change reaches are applied; the new machine starts a backup into
  its own store. Without `--yes` it prints the plan and what stands in the way
  (docs/backups.md, Moving a part to another machine).
- `kiwi-server backups FLEET MACHINE PART --restore --from OTHER`: the copies another machine
  made of a part, put here; its way into its backups goes over for the one run.
- kiwi-modules 0.104.0 (`kiwi-stack restore --from`).

## 2.265.0 — the control node can update itself

- **"Update everything" on the control page brings the control page itself to the newest
  release.** An apply sets the control page's image to the release of the kiwi-server that
  applies it; a control node applying its own machine therefore stayed on its own release for
  ever, and only an apply from a computer with the newer kiwi-server moved it. Now, when the
  control node updates its own machine, the image stays on the major tag and the update pulls
  the newest release of it. A plain apply changes nothing about this.

## 2.264.0 — the agent: machines report by themselves

- **Every machine runs a small agent** (`kiwi-agent`, installed by an apply when the network
  has a control node). Once a minute it sends the control node how the machine is, signed
  with a key of its own. The control node looks from these reports instead of logging in to
  every machine: the pages are a minute behind, not ten.
- **A machine that starts is seen at once**, and an apply that waited for it runs at once
  (before: within a minute, by the control node asking).
- A machine whose agent is silent is still asked over SSH, as before. `kiwi-server status …
  --reports DIR` is what the control node runs.
- Design: the agent, and the order to build in (docs/design/scale.md). This is the agent's
  first step; taking orders and the neighbours' vote for a failover come on top of it.

## 2.263.0 — names by service: paste.home

- **`names: service`** (under `defaults:`, or on one machine): a part that runs on one machine
  only is named for itself, `paste.home` instead of `paste.quince.home`. Its own site answers
  to the name, the machines that answer for names learn it, and it stays when the part moves.
  A name set by hand counts; a part on two machines keeps its machines' names; one name for
  two parts is refused; the control page and the backup server keep their machine's name
  (docs/network.md, Names by service).
- kiwi-modules 0.103.0: every module with a name of its own says which setting holds it
  (`named_by`).

## 2.262.0 — "What differs" opens for secret files too

- **In the app's "This copy and the other", a secret file's row opens.** Before, only the
  settings file showed what differs; a secret's row could not be opened at all. Now it shows
  how big the file is on each side and, where the file is a list of names with values, which
  names were added, removed or have another value. No value is shown. Known host keys (they
  are public) show which hosts each side has more. `kiwi-server control diff` prints the same.
- CI runs once per pull request, not twice (a branch's push started it a second time).
- Design: staying up (what operators do, what fits here) and the new order to build in
  (docs/design/scale.md).

## 2.261.0 — the tests run several at once; one try per waiting machine

- **Fixed: two looks at the same moment could each start an apply for one waiting machine**,
  and a late try could put a machine back among the waiting ones after the control node had
  given up on it. A try is now started under a lock, only the try itself ends it, and a
  machine that left the book stays out.
- **The unit tests run several at once** (`tests/run-parallel.py`, one process per core, the
  tests dealt out evenly): 64 seconds instead of six and a half minutes on a 16-core computer,
  and CI uses it (the tests were nine of its ten minutes).

## 2.260.0 — a waiting apply is picked up within a minute; the design for many machines

- **A machine whose apply waits is asked every minute**, alone, instead of at the next look at
  every machine (ten minutes). What is known of the other machines stays as it is.
- Design: [many machines, and services that move](docs/design/scale.md): why not Ansible or
  one cluster over everything, what 2.259 built, and the order for what comes: machines that
  report in, names by service, moving a service, a standby and failover, a control node per
  site, live replication, teams.
- Drill: `tests/drills/apply-waits.sh` restarts a machine, applies it meanwhile and watches
  the control node finish the apply by itself.

## 2.259.0 — many machines: side by side, all at once, an apply that waits, shorter labels

- **Jobs on different machines run side by side on the control node.** Before, one lock held
  every job behind the one that ran. Now two applies, checks, previews or restarts that name
  different machines run at the same time; a job on the same machine waits its turn; a job
  that changes more than its machines (Update everything, a removal, a rotation, a restore)
  and an apply to the control node's own machine still run alone.
- **An apply works on all its machines at once**, not on six. `--at-once N` or `KIWI_AT_ONCE`
  says fewer for a small control node. The look asks all machines at once as well (was 8).
- **An apply that did not reach a machine waits for it.** A machine that was off no longer
  ends as "failed" and stays so: the look that sees it answer again applies it by itself (a
  job by `control`). Machines shows "apply waits"; a machine that answers and still fails is
  tried three times, then left to a person.
- **A role script is rendered once per change, not at every apply.** The check "which other
  machines does this change reach" kept rendering every machine (0.15 to 0.2 s each). The
  fingerprints are now kept while nothing a script is made from has changed, an hour at most.
- What the fleet remembers (`.kiwi-state.json`) is written under a lock across processes, so
  two applies at once do not lose each other's lines.
- **Shorter labels**: Access rules (was "Who may reach what"), Controllers (was "Computers"),
  Take in a machine, Add a machine, Updates, Lost device.

## 2.258.0 — the look at the machines does not wait for a job

- **The control node looks at the machines while a job runs.** Before, the look waited for the
  one job lock: during a long apply the page showed what was known before it, and "Check now"
  did nothing until the job ended. Now the look asks every machine the job leaves alone and
  keeps what it knew of the job's own machines (one in the middle of an apply would look
  broken). Only a job on every machine ("Update everything") still makes it wait.

## 2.257.0 — masters as their own group, the Devices table, a job that outlives a page restart

- **A job no longer ends with "the control node lost job … (502)" when the control page
  restarts under it.** An apply to the control node's own machine restarts the page, and its
  jobs live in memory. `kiwi-server control run` (what the app's buttons use) now waits for
  the page (up to 5 minutes), goes on if the job is still there, and runs an apply once more
  if the job ended with the page: an apply that has nothing left to do ends at once. The
  page's own view of a job says the same instead of standing still.
- **"If something breaks" is called Recovery**: in the menu, on its page, in the app and in
  the hints' buttons. The command was `kiwi-server recovery` already.
- **The masters and their standbys are a group of their own** at the top of the Machines table,
  not machines of the site "home". A master's own site, if the fleet file names one, shows
  beside its name and still filters (`site:`).
- **Devices is a table**, made for hundreds of devices: name, group, address on the VPN, and
  whether everything or only the network goes through the VPN. Counts by group filter on a
  click; the filter is text in the address (`group:staff`, a part of a name, the start of an
  address). A page with 350 devices is a quarter of the size the old list would have been.
- The page preview's made-up network has devices with addresses and groups
  (`KIWI_PREVIEW_MANY=1`: 350 more).

## 2.256.0 — the Machines page is a table, by site

- **Machines on the control page is a table with a row per machine, grouped by site**
  (`network.site`), made for many machines: state, role, parts, three small meters (CPU,
  memory, fullest disk), last backup, last seen. A site's line sums up its machines
  ("11 fine · 1 waits for unlock"). Before, it was a grid of cards.
- **One state per machine**: fine, wants a look, has a problem, not reachable, waits for unlock,
  being installed, not set up yet, asleep, not checked yet. A machine that waits for its
  disk's key or is being installed is no longer counted as "not reachable" there.
- **Counts above the table filter on a click, and the filter is text in the page's address**
  (`/machines?q=site:main state:down part:dns`), so a view can be bookmarked. Filters:
  `state:`, `site:`, `part:`, `role:`, or a part of a name (docs/control.md).
- **Ticks in the table replace "Several at once"**: tick machines, or a whole site, and Check,
  Preview or Apply them. The limit of 50 machines per action is gone.
- The page preview can show a company's size: `KIWI_PREVIEW_MANY=1` adds 72 made-up machines
  at five sites (`tests/drills/page-preview.py`).

## 2.255.0 — a Debian machine on DHCP opens its disk with its rings

- **Fixed: a Debian machine without a fixed address never asked its rings at boot.** Its boot
  system got `IP=dhcp`, and clevis-initramfs (20) takes the sixth field of that value as the
  network device to wait for: with no colons it waited for a device called "dhcp", gave up,
  and the disk waited for its passphrase. It now gets `:::::DEVICE:dhcp`, with the device of
  its default route, as machines with a fixed address already did. Found by the Debian
  desktop drill (`tests/drills/desktop-clevis-vm.sh`), which now installs GNOME after the
  first start and passes `ip=::::::dhcp` itself.

## 2.254.0 — the app shows its jobs in the kit's strip

- **A running job shows as a strip with the filling kiwi** under the app's window, as in
  kiwi-updater: what runs, and for an apply to several machines how many are done ("2 of 3
  done"). Its output opens from the strip. It is the kit of kiwi-template 0.1.3, now vendored
  in `gui/kit/` and pinned in `VENDORED` (`tools/vendor.sh kiwi-template=vX.Y.Z`); the
  installer copies it beside the app.
- `tools/private-session.sh` no longer prints permission errors for processes that are not its own.

## 2.253.0 — faster pages, and a look that shows each machine as it answers

- **The look at the machines writes each machine as it answers** (`status --stream`): one that is
  off costs its own timeout, and the others are on the page meanwhile. Before, nothing was
  written until the slowest had answered or timed out.
- **Pages that started kiwi-server at every view start it once**: Who may reach what, Disk
  unlock, If something breaks and the hidden services keep what was worked out until the fleet's
  files change or a job ends (0.3 to 0.6 s saved per view, each).
- Design: the size to design for (about 70 machines and 350 devices at five sites), desktops
  and laptops as clients of the same control node, modules of one's own on one's own git.
- Third step of docs/design/ui.md, first part.

## 2.252.0 — one name per thing

- **A machine's parts are named by their title first**, the technical name small after it:
  "Ad blocker (Pi-hole) `dns`" under a machine's details on the page.
- **A container that is down or unhealthy is named under its part**: "Files (Nextcloud):
  nextcloud-aio-apache is not running", in `status`, on the page and in the app (kiwi-modules
  0.102.0 says which module a container is of).
- The app says **Linked networks**, as the page does (it said Federation).
- Second step of docs/design/ui.md.

## 2.251.0 — boards: a fixed address, and rings for an encrypted one

- **A board gets its fixed address** (`network.address`, `dhcp: false`): written at an image's
  first start, or by `adopt` for the next restart. Until now nothing wrote it, and a board
  flashed anew came up wherever DHCP put it.
- **An Armbian board whose image was built encrypted** (`armbian.encrypt: true`) binds rings as
  a Debian machine does: `unlock.rings`, the boot system's fixed address, `kiwi-server unlock`,
  and it can be an opener. Proven on an Odroid N2+: it opens by itself 48 seconds after a
  restart, and opened the master with the control node's opener switched off. kiwi-server does
  not build that image yet (docs/boards.md says what is still by hand).
- "Who opens whom" says "start" for several machines.

## 2.250.0 — a machine's story, and what to do as a button

- **A machine that was installed anew no longer just says "unreachable".** When its install
  media are newer than its last apply and it does not answer, `status`, the page and the app
  say so: "Its install media were made 40 minutes ago (it was set up before: this is a
  reinstall) and it has not answered since: it is being installed, or it waits at boot for its
  disk's key." A first start that waits for its one-time key is named with its address.
- **Every hint has its actions**, from one table for the page, the app and the phone: a job for
  the machine as a button (Unlock it, Apply), a page as a link (Disk unlock, If something
  breaks, Backups). "If it stays away: If something breaks." without a link is gone.
- First step of docs/design/ui.md.

## 2.249.0 — switch off, restart

- **Switch a machine off or restart it**: `kiwi-server power <fleet> <machine> off|restart` says
  first what that means for this machine (the master takes the VPN with it, the control node its
  page, a disk without rings waits for its key, who can switch it on again) and does it only with
  `--yes`. On the page: **Restart** and **Switch off** under "What you can do"; in the app: a
  **Power** group on a machine's page. Both ask first, with the same words.
- **Fixed:** an apply did not update who may reach a machine's boot system (its openers' keys)
  while one of its rings did not answer. Found live: the master's opener was missing while a
  ring's machine was being reinstalled.
- Design: the page and the apps as server management (docs/design/ui.md): a machines table by
  site, a machine's story and states, one name per thing, speed, the app on kiwi-template's kit.

## 2.248.0 — openers

- **Openers**: a master at a hoster that waits at boot for a ring it reaches only through a
  tunnel is opened by the ring's own machine as well, not only by the control node. That
  machine looks once a minute whether the master's boot system answers, and offers it its ring
  with a key that allows nothing else there. Only machines with an encrypted disk are openers.
  `unlock.openers` (optional) names them; `kiwi-server unlock <fleet>`, the Disk unlock page and
  the app show them, and the recovery report's power-cut check counts them: with one, the
  master no longer waits for the control node after a power cut.
- Design: desktops and laptops open by a policy of rings: every health ring and its
  auto-unlock ring, or its emergency ring (**Unlock now**). Proven in VMs on Fedora Silverblue
  (`tests/drills/desktop-silverblue-vm.sh`, 0 wrong); the Debian desktop drill follows.

## 2.247.0 — a log a person can read

- **Fixed (kiwi-modules 0.101.1):** the accounts' sign-up page (kn-signup) stopped at every start
  with "argument --id-onion: expected one argument" once the accounts had no hidden service: an
  empty word vanished from its container's command. Empty words stay words now.
- **A service's log says first, in words, why it stops**: "kn-signup keeps stopping right after
  it starts (24 restarts so far)… Its last words: …". Then only the service's own lines with a
  short time; podman's lines about its container are left out (and counted). In the terminal
  `kiwi-server service … logs` (`--raw` for every line, `--save FILE` for the full log); on the
  page the log is open at once, with **Open as text** and **Download**; in the app **Log** opens
  a large window with **Save…** (the full log) and **Copy**.

## 2.246.0 — who opens whom

- **Disk unlock**: one picture of how each machine's disk opens at boot, with which rings (and
  on which machine each ring runs), how it reaches them, and whether it comes back by itself
  after a power cut: `kiwi-server unlock <fleet>` (no machine), the control page's new **Disk
  unlock** page, `GET /api/unlock` for the Android app, and the app's **Disk unlock…** window.
  Read only; step 1 of docs/design/unlock.md.
- **Previews and tests of the app run only in a private session** (`tools/private-session.sh`):
  a throwaway runtime folder and a session bus that starts no services, no portals, no gvfs.
  `tools/gui-preview.py` refuses to start outside one, and the app-preview drill goes through
  it. Afterwards the desktop's document portal mount is checked and the portal restarted if it
  went missing. Why: twice a preview's own bus started a second document portal on the
  desktop's runtime folder, and stopping it took the desktop's `/run/user/<uid>/doc` along.
- A new drill, `tests/drills/desktop-clevis-vm.sh`: a Debian desktop whose encrypted disk opens
  with rings by a policy (all health rings and its auto-unlock ring, or its emergency ring).
- Designs: which identity server the public side would take (compared), and why the owner's
  network needs none now.

## 2.245.0 — 3-2-1

- `kiwi-server recovery` (and the page, the app) has a line for **3-2-1**: the data, two
  copies of it, one of them elsewhere. It names the machines that keep one copy only, or every
  copy in the same place as the machine. A hint, not a gap: a machine without any backup stays
  the gap it was.
- `kiwi-server check` ends with one line: all N checks pass, or which failed.
- Designs: **who opens whom** (docs/design/unlock.md: ways a disk opens, openers, one picture
  for page and apps, desktops, an encrypted Armbian image), and in public-services.md the
  decision of 19 November: public and internal services may be separate instances; Kanidm
  takes one address only, so it gets no hidden service.

## 2.244.0 — 1 day, not 1 days

- Counts say "1 day", "1 minute", "1 snapshot", "1 machine": in status, recovery, the backup
  notes, the watchdog's messages, the control page and two lines of the app. A prune of today
  says "pruned today".
- README: a board (Odroid, Banana Pi) gets its own Armbian image; docs/roles.md: a role or
  setting for debian counts on armbian.
- `./install.sh --help` prints the usage and exits 0.
- A sentence of the API docs reads plainly ("which of its people this network lets in").

## 2.243.0 — a typo gets a guess

- A mistyped command gets one line and a guess (`statsu` → did you mean status?) instead of
  the whole help; a mistyped machine too (`papya` → did you mean papaya?).
- `kiwi-server status` says which kiwi-server each machine was applied with, beside when.
- `kiwi-server plan` counts a new kiwi-stack tool or library as a change (it said "nothing
  changes" when only those were new), and writes "1 file", not "1 files".
- The last hints that printed the full fleet path name it as typed (`~/kiwi/fleet.yaml`): the
  secret store, sshca, unlock, next, sources, restore. A missing fleet file too.

## 2.242.0 — every command in the help and in completion

- **Fixed:** on an Armbian board a bare machine's **Extra packages** were dropped: the setting
  was for Debian only, and Armbian is Debian on a board. What runs on debian runs on armbian
  now, and `kiwi-server roles` lists armbian where it can run.
- `kiwi-server --help` names `next`, `adopt`, `service` and `sources` too. Bash completion
  knows the 14 commands it was missing (access, connect, recovery, rotate, qr, …) and offers
  rotate's kinds. Tests keep both in step with the commands.
- `kiwi-server recovery`: a long label ("Bring something back on …") on a line of its own,
  so it no longer runs into its command.
- `kiwi-server disks`: the columns as wide as the longest name (LVM names ran into the size).
- Docs: `kiwi-server qr` shows a device's code again, or saves it as an image.
- The stand-in drill counts the ring's requests again; leftovers removed.

## 2.241.0 — hints that work from any folder

- Every "next: kiwi-server …" hint (list, the wizard, recovery, apply, build, access, onion and
  the others) names the fleet as you would type it, `~/kiwi/fleet.yaml`, instead of only
  `fleet.yaml`: copied from any folder, it works. A path with spaces is quoted, the `~/` stays
  outside the quotes so the shell still expands it.
- kiwi-stack 0.9.23: `kiwi-server status` shows each host unit's state without its journal
  lines (the ACME timer's last run filled it). A failed unit still shows its last 10 lines.
- `kiwi-server move`: a warning about the new domain (such as `.lan`) says **Take care**, not
  "To do for people".
- `kiwi-server --help`: one column for what each command does; a long command says it on the
  next line. It also lists rotate, wake, move, `federate --invite|--accept` and `--ca`.
- Docs: no placeholder words left.

## 2.240.0 — connect with a linked network's CA

- **Fixed:** `kiwi-server connect` stopped with an error on a network whose linked network's CA
  is offered to its devices (since 2.222.0): the step that puts that CA into the browsers did
  not know the module it uses. Found by a static check of the code; a test runs that step now.
- **Fixed:** a machine removed on the control page could show in the last look for a moment
  after its job said done; now it is out of it before.
- Leftovers the same check found are gone (imports and variables nothing used).
- ROADMAP: where it stands, as of 19 November.

## 2.239.0 — a service at one address only

- kiwi-modules 0.101.0: every module that publishes ports has the setting **`listen`** ("Only at
  this address", Advanced, on the page and in the app): its ports answer at that one address of
  the machine, a VLAN's, the LAN's or 127.0.0.1. A module on the host's own network is refused.
- The control page shows a machine asleep in its window as "Asleep until …", with *Wake it now*
  when it has Wake-on-LAN.
- Over the VPN first (`install.over_vpn`) works on Fedora CoreOS and uCore too: their images
  carry `wireguard-tools` and `wg-quick@` (checked in Fedora CoreOS's image).
- README and the notes on what the page and the app offer list this week's additions.

## 2.238.0 — asleep by day, awake for the backups

- **`asleep: { from: "06:00", until: "02:30" }`** on a machine (settings: *Asleep from*, *…until*):
  in that window it suspends to RAM and its own clock wakes it at `until` (`rtcwake`), from a
  timer that looks every quarter of an hour. Not while it is set up, its own backup runs or a
  backup comes in to its backup server; then a quarter of an hour later. Wake-on-LAN wakes it
  earlier. Taken out of the fleet file, the next apply removes it.
- **`status`** says "asleep until …" for a machine in its window that does not answer (state
  `asleep`, no problem, no message); the control page and the app show it.
- Tried on a VM (`tests/drills/offsite-vm.sh`, `ASLEEP=1`): at the window's start it suspended
  (`rtcwake -m mem`), did not answer, `status` said asleep; at its end its clock woke it. In QEMU
  the firmware starts afresh on that wake instead of resuming; real hardware resumes. The first
  try found that a finished one-shot unit (the first setup) counted as running: "busy" is now
  what systemd says is running.
- Tests that use the control page wait up to a minute for the look a login starts (one failed
  now and then on a slow run). ROADMAP: papaya has three rings.

## 2.237.0 — a plan for moving the network, just in case

- **`kiwi-server move FLEET --mesh 10.X.0.0/16 --domain D`** (either or both): what moving the
  whole network would change, and nothing changed. Per machine its new address and name, how many
  generated passwords and data disk keys would be kept (pinned before anything else), the
  groups' new ranges, the passwords already kept that move to the new names; for people: the
  devices that need a new config (a new QR code), the linked networks that link again, a new CA
  when the old one may sign only the old domain. It refuses a mesh that overlaps a network at
  home or a group of it, and a domain that cannot work (`.local`). `--porcelain` gives it as JSON.
  The move itself comes later, on this plan (docs/design/federation.md).

## 2.236.0 — a ring at home for a machine at another site, relayed by itself

- **A machine at another site** whose ring stands at home no longer needs `unlock.via` and the
  master's `vpn-server.unlock_relay` written by hand: kiwi-server fills both in. The master passes
  the ring's port on over the mesh to the ring's machine and keeps no key; the port is fixed by the
  ring's name (7600 to 7799), as a disk keeps the address it was bound with. Apply the master once
  after such a machine joins. A way written by hand stays as it is.
- The offsite drill's `ENCRYPT=RING` uses that relay to a ring at home (no ring on the master); it
  applies the master before and after. With `ENCRYPT=infra OVER_VPN=1`: 29 of 29, the disk bound to
  `http://<master>:7793` and opened by itself after a restart.
- docs: the decision on joining (no endpoint on the Internet; a machine at another site comes onto
  the VPN first) in design/control-plane.md and the ROADMAP.

## 2.235.0 — a machine at another site sets itself up over the VPN

- **`install.over_vpn`** ("It stands at another site" in a machine's settings, page and app):
  `kiwi-server build` makes a one-time join peer on the master and puts its WireGuard config on
  the machine's install media (Debian, CoreOS, uCore). The installed machine comes onto the VPN
  with it at first boot (`wg-quick@kiwi-join`, the mesh only, a routing table of its own so the
  role's own VPN client does not cut the setup), `kiwi-server next` or the control node sets it up
  over the mesh, and once it answers at its own mesh address the join tunnel goes and the peer is
  removed from the master. No computer at its site and nothing more opened on the master.
- `tests/drills/offsite-vm.sh` has `OVER_VPN=1`: a VM behind QEMU's NAT, set up from home; 21 of 21
  checks. Its first runs found two things, both fixed before release: wg-easy's `/24` on the join
  address put the mesh on the join tunnel ahead of the machine's own VPN client (now `/32`), and
  the join went before the apply's last steps over it (it goes last now).
- `remove` takes a join peer still there off the master too.
- The federation drill names why a master gave no facts (a store that locked itself).

## 2.234.0 — a ring taken out comes off the disk

- **Fixed:** a machine whose rings were taken out of `unlock.rings` kept its binding to them, so
  its disk still opened by a ring the fleet file no longer named (damson and a ring on the
  master, 19 November). The next apply of a machine with no ring now removes its ring bindings,
  on each disk only after its key on record is checked to open it; without a key on record it
  says so and removes nothing.
- docs: for a machine at another site the master relays a ring at home (`unlock_relay`) and keeps
  no key; a ring on the master itself is possible, but its keys then lie on a rented server.

## 2.233.0 — how many rings open the control node

- **`unlock.control_threshold`**: how many of the rings in `unlock.control` must answer together
  before the control node's key store opens after a restart (clevis sss), as `unlock.threshold`
  does for a disk. 1 (the default) is any one of them, as before. A setting on the page and in
  the app (*Disk unlock*, "…how many of them"); `validate` says when it is more than the rings
  named. Logging in to the page always takes the password: this is only about the key store.

## 2.232.0 — a quieter status

- kiwi-stack 0.9.22: `kiwi-server status` no longer shows podman's
  `conmon … <nwarn>: Failed to open cgroups file` lines (one for every health check), which
  buried what the units really said.
- ROADMAP: the master in RAM has about 160 MB free again (crumble, 83 %).

## 2.231.0 — only the container whose image changed

- kiwi-stack 0.9.21 fixes 0.9.20 (in 2.230.0): on a disk 85 % full or more, making room for a
  changed image stopped every container of the stack, not only the one whose image changed
  (podman shows short image IDs in one place, long ones in the other). On crumble, the master in
  RAM, all seven restarted at the apply of 2.230.0. Machines below 85 % (papaya: 12 %) never ran
  it. crumble's /var went from 95 % to 83 % with nginx on Alpine.

## 2.230.0 — nginx on Alpine

- kiwi-modules 0.100.0: the reverse proxy runs `nginx:1-alpine` (64 MB) instead of the Debian
  `nginx:1` (167 MB): the same official nginx, about 100 MB less on every machine. For the master
  in RAM it is room it lacked: crumble's /var (half its memory) was 94 % full. The next apply
  fetches it and restarts the proxy once. `reverse-proxy.image: docker.io/library/nginx:1` keeps
  the Debian one. `tests/drills/nginx-check.py` checks with the machine's own image, as before.
- kiwi-stack 0.9.20: on a disk 85 % full or more, a container whose image the stack no longer
  names goes, with that image, before the new one is pulled (it comes back on the new one at
  once). Without it the switch above could not be pulled on crumble.

## 2.229.0 — apply says how to reach a new machine

- `kiwi-server apply FLEET HOST` on a machine met for the first time (nothing of it on record,
  its name resolves on the VPN only) stopped with an SSH error that said nothing useful. It
  says now that `kiwi-server next FLEET HOST` finds it at its fixed address, or which `--ssh`
  to give. Found in the first-run review (notes/REVIEW-first-run.md, 27).
- `wake`: tried live, crumble-b sent damson the packet (both test VMs on sh3); its hint names
  the fleet file as `~/…`.

## 2.228.0 — Wake-on-LAN

- **`network.wol: true`**: the next apply switches waking on at the machine's card (a drop-in
  for systemd's `99-default.link`, `WakeOnLan=magic`, and `ethtool` at once where it is there).
- **Every apply learns the machine's card, MAC and LAN address** into the store
  (`secrets/macs.json`); `network.mac` for a machine not applied yet.
- **`kiwi-server wake FLEET HOST`** (`--via`, `--mac`, `--address`): another machine of its LAN
  sends the magic packet, to its address through a neighbour entry naming its MAC for that
  moment, so no broadcast is needed. *Wake it* on the control page and in the app for a
  machine with Wake-on-LAN that does not answer; the setting in both (Network).
- `tests/drills/wake-netns.py`: the packet reaches a card that has no address (4 checks).
  Waking real hardware is not tested yet.

## 2.227.0 — a translated partner's names between two operators

- **Cards carry a certificate request per machine** (public). On `federate FLEET --accept CARD`
  of a partner that has this network's domain, this network's CA signs the names its devices
  use for the partner's machines into `<this>.signed-for-<partner>.yaml`; the partner takes it
  with `--accept` that file. It is taken only when it is for that network, signed by the CA on
  the sender's card, for the machine's key and for its names; what is not is said.
- **A machine keeps its key** when its own certificate is issued again (new names, near its end):
  a certificate a partner signed for it still fits. Before, the first render after linking
  issued a new key and the partner's certificate was left out until linking again.
- A card made before the network's first render carries its CA too: the CA is made then.

## 2.226.0 — a translated partner's pages trusted

- **A partner with this network's domain** (translated at the link) is trusted on your devices
  without importing its CA: `kiwi-server federate A B` has A's CA sign the names A's devices
  use for B's machines (`papaya.kiwi2.home`, `*.papaya.kiwi2.home`) for each machine's own key,
  and the other way round. B's machines show that certificate on those names (kiwi-modules
  0.99.0: the alias names in a server of their own). It is used while it fits the machine's key;
  after a new key the render leaves it out and says to link again. With both fleets on one
  computer; between two operators not yet.
- **Fixed:** a node with admin pages (Portainer, Pi-hole, the control page) lost the name a
  partner of the same domain gives it (`federation.alias`) when its admin pages were closed to
  partners, so it did not answer that name at all (since 2.216.0). The rules keep it now.

## 2.225.0 — a tagged interface at boot

- kiwi-stack 0.9.19: `kiwi-vlans.service` brings the card up before its tagged interfaces. It runs
  before the network, and with the card still down the unit failed (the kernel brought the
  interface up a moment later, so it worked, but the machine showed a failed unit). Found on a
  Debian test machine after a restart; `tests/drills/vlan-netns.py` now starts the script with
  the card down.

## 2.224.0 — the disk unlock and VLANs in the settings

- **Settings, *Disk unlock*** on the control page and in the app: the rings that open a machine's
  disk, how many of them, the way to a ring at another site, SSH while it waits (and from where),
  the rings that open the control node, and the leash. Until now the fleet file only.
- **Settings, Network, *Tagged VLANs*** (a machine's `network.vlans`, as YAML lines).
- The notes on what the page and the app offer are brought up to date: the app has had *A new
  recovery key…* and *Connect a service to the accounts* for a while.

## 2.223.0 — a machine on tagged VLANs

- **`network.vlans`** in a machine's block: `[{id: 20, address: 192.168.20.5/24, name: iot}]`.
  The next apply makes the tagged interface (`eth0.20`) with its address and no default route;
  `kiwi-vlans.service` makes it again at boot (kiwi-stack 0.9.18, kiwi-modules 0.98.0). The card:
  the VLAN's `interface:`, else the machine's `network.interface`, else the card of the default
  route. A VLAN taken out loses its interface with the next apply.
- `validate` checks each VLAN: its number, a private address with its network that is not the
  machine's own network, the mesh or another VLAN's; a VLAN named as a group of the home network
  has its address in that group's subnet, whose rules then hold on the VLAN.
- `tests/drills/vlan-netns.py`: a switch with VLAN filtering, rootless; 12 checks.
- `federate` says "what each shares is as it was" when linking two networks again.

## 2.222.0 — a linked network's certificates on your devices

- **A network's card carries its CA's certificate** (public), and `kiwi-server federate` keeps it
  in `federation.yaml` as `ca:`. A card without one keeps the CA that is there.
- **The partner's CA is offered to your devices** when its name constraint permits only the
  partner's own domain and nothing of yours: the control page's *Linked networks* has a download
  with its mark, the app's *Federation…* a *Save…*, and `kiwi-server federate FLEET --ca PARTNER`
  prints it. `kiwi-server connect` puts it into this computer's browsers too. A CA that could
  sign any name, other names or this network's own (a translated partner) is not offered, and
  the page says why. `/api/federation` gives `ca`, `ca_mark` and `ca_why`.
- Networks linked before this release bring their CA when they are linked again
  (`kiwi-server federate`, or the cards once more); what each shares stays as it is.

## 2.221.0 — new disk keys from the page and the app

- **The control page's *If a computer was lost or stolen*** has *3. The keys that open the disks at
  boot*: each unlock ring with when its keys were last replaced, *Replace*, and *Leave … out* for
  a machine that waits. The app's dialog has *Replace the disk keys* (every ring in turn).
- `kiwi-server rotate FLEET ring all`: every ring the machines name, one after the other; with
  `--waive NAME` only the rings still waiting for that machine, and no new keys.

## 2.220.0 — the leash, and new keys for a ring

- **`unlock: { leash: 10 }`** (docs/design/control-plane.md, ROADMAP's unlock list): a machine that
  saw the rings of its own site since it started, and then has not reached them for that many
  minutes, restarts into its locked state, so a box carried off while running locks itself.
  Opened by hand without its rings it never saw them and stays up; a ring on the master far
  away does not count; `true` is ten minutes, five the least. `apply` puts the timer on the
  machine (`kiwi-leash.timer`) and takes it away again when the setting goes. Off by default:
  a house without its network for that long restarts its leashed machines too.
- **`kiwi-server rotate FLEET ring NAME`**: new keys for an unlock ring (`tangd-rotate-keys`, the
  old ones kept hidden), every disk it opens bound again to them, and the old keys removed only
  when the last machine has changed; a machine that is off meanwhile still opens and is taken by
  the same command later (`--waive` for one gone for good). `rotate FLEET` lists each ring and
  when its keys were last replaced (was: "the key that unlocks disks at boot: not built yet").
  Each disk is bound again in place (`clevis luks regen`) and checked to open. Tried live: the
  ring `away` on sorbet, and the ring `attic` on medlar with papaya's and quince's disks bound
  again; the old keys gone, both disks open.
- **A disk whose new binding does not fit beside the old one is bound all the same** (found by
  that rotation: on CoreOS, Ignition's binding fills the LUKS2 header): the old binding goes
  first, while the key on record opens the disk, then the new one. Before, a change of a CoreOS
  machine's rings failed with "Error adding new binding".

## 2.219.0 — the control node opens by any of several rings

- **`unlock.control` takes several rings** (`[house, attic]`): the control node's key is kept
  for all of them (clevis `sss`, any one opens it), so after a restart it no longer hangs on one
  machine (ROADMAP: "the control node's own unlock, which hangs on one ring today"). A ring that
  does not answer when the key is made is left out with a note. Proven with the real clevis and
  two Tang servers: one stopped, the key still opens; both stopped, it stays locked.
- `kiwi-server recovery` counts any of them when it says which machines come back by
  themselves after a power cut (a ring forwarded by the control node needs one of its rings up).
- The app's control node setup offers *any of lychee, medlar opens it* when several machines
  serve a ring (still opt-in: *I log in on its page* stays the first choice).

## 2.218.0 — the gateway keeps the groups of the home network; a removed machine is gone at once

- **A gateway node keeps the groups of the home network too**, for what it routes from the home
  into the mesh (machines at another site, nodes by their mesh address): kiwi-modules 0.97.0's
  chain `KIWI_GW_LAN` lets a group through to what it was given and no further; a client in no
  group reaches the mesh as before. `tests/drills/lan-netns.py` covers it (13 checks).
- **A machine removed on the control page is gone from it at once** (the owner: kumquat was
  "still half there", and its Remove then said "no such machine"): the remove takes it out of the
  last look and of the recovery watch's notes, which kept it until the next look.

## 2.217.0 — groups of the home network

- **A part of the home network can be a group in *who may reach what*** (the owner: VLANs "kinda
  like the subnet-group filter"; docs/design/lans.md): a guest Wi-Fi, a camera network, a VLAN,
  any subnet at home, given things in the same words as a VPN group.
  `kiwi-server access FLEET iot --new --lan 192.168.20.0/24 cloud.papaya`; on the page and in
  the app a new group with a subnet in *At home*. It lives in the fleet file as `lan.groups`. A
  fleet without it changes nothing: the home network reaches everything, as before.
- **Each node keeps it itself**, since the home network reaches a node across the switch and not
  through the master: its web server refuses the names the group was not given (as for a VPN
  group), and its nftables table `kiwi_lan` the ports (kiwi-stack 0.9.17, kiwi-modules 0.96.0:
  `lan_rules`), before a published container port's DNAT. DNS, DHCP and ping stay open.
- `tests/drills/lan-netns.py` proves the node side on this computer's kernel (rootless
  namespaces): a client in a group opens only what it was given, a published port included; one
  in no group everything; 8 checks.

## 2.216.0 — two networks of one mesh and one domain linked

- **`federate` links two networks with the same mesh or the same domain** (the owner:
  "translate is the best idea"). Each side sees the other under an alias it picks and keeps in
  `federation.yaml` (`seen_as`: a free /16 at random, `<its name>.<its domain>`). The master
  maps the alias onto the partner's real addresses at the link, its small DNS (kiwi-modules
  0.95.0, `<prefix>-fed-dns`) gives the partner's names under the alias, and a shared machine
  answers the name the partner gives it and carries it in its certificate
  (`papaya.kiwi.home` beside `papaya.home`). Devices' split configs, `connect`, the nodes'
  Pi-hole, the picture and who may reach what all use the alias. Before, `federate` refused it.
- `tests/drills/translate-netns.py`: two masters of one mesh and one domain in throwaway network
  namespaces of this computer (no root) over real WireGuard, running the link part of the
  start.sh each would get: 10 checks, among them a client that reaches its own 10.9.0.5 and the
  partner's at 10.209.0.5.
- Two partners that have one mesh between them are refused: the link cannot tell them apart.

## 2.215.0 — ready to link: a mesh of its own, a domain of its own

- **`kiwi-server secrets forget FLEET HOST module.key`** takes a password out of the secret store
  (what `secrets keep`, the page or the app put there): the machine gets a generated one with
  its next apply. The app's kept password field has *Forget* for it, as the page has its
  *clear* box.
- **`federate` refuses two networks with one domain** (the owner: "can we federate with the same
  subnet and tld?"). Each side's DNS sends the other's domain to the other: with `home` on both,
  `papaya.home` would be both networks' and the partner's names would not resolve. The same
  mesh was refused already. docs/network.md says to pick a free /16 and domain from the start.
- **A new network takes its VPN's /16 at random** (the owner: "give a random 10.X.0.0 subnet, so
  the chances that X is the same are lower"): `init`, the app's and the page's first run write
  10.X.0.0/16 with X from 10 to 250, not the home's LAN and not a mesh of the fleets in
  `~/kiwi*`, with everything that goes with it (the VPN server and its groups, Tor's allow list,
  every node's mesh and master). Before, every network took 10.8. *The VPN's addresses* is an
  advanced question to change it.
- **A machine at another site is reached at its mesh address before the home's DNS knows its
  name**: `status`, the control node and every other command take the Address of the WireGuard
  config the master gave it (the encrypted offsite drill: "Could not resolve hostname
  nashi.home" right after its first apply). That drill passed otherwise: its disk, bound to the
  ring `away` on the master, opened by itself after a restart.

## 2.214.1 — secrets keep with a standby

- `secrets keep` stopped at a master's standby ("no such machine: crumble-b"): a standby is not
  written in the fleet file of its own, it renders with its master's settings under the same
  host name. It is left out now; its master's passwords in the store are its own as well.

## 2.214.0 — no password in the fleet file

- **`kiwi-server secrets keep FLEET`**: every password written in the fleet file for a machine's
  module (Pi-hole's, Portainer's, Nextcloud's, the VPN server's, a VPN provider's key, a
  router's password) moves into the secret store (`secrets/pins.json`, `hostname/module/key`)
  and out of the file. The machines keep the same passwords: an apply afterwards changes
  nothing. Before, taking one out of the file would have changed it, so they stayed there in
  the clear on the disk of every computer that holds the fleet.
- **A password typed on the control page or in the app goes into the secret store by itself**
  when the settings are saved; the page counts it as set (and its *clear* box takes it out of the
  store), the app's field says it is kept there.
- kiwi-modules 0.94.0: a password nothing can generate (a provider's key, a router's
  password) comes from the secret store when the fleet file leaves it out.
- `validate` names every module password still written in the file, generated or not, and
  points to `secrets keep`.
- The messages' mail password and ntfy token (`notify.mail.password`, `notify.ntfy.token`) go
  the same way: `secrets keep` moves them, the messages still go out with them.
- ROADMAP: restore on first boot (2.43.0) and port ranges were done; the RAM master's
  one-image-at-a-time update (2.209.0) noted.

## 2.213.0 — the control node's buttons for the apps

- **What changes something, as JSON for a computer or phone that manages the network** (its key's
  token; ROADMAP: the control node as the one admin, the Android app as its controller). Each route
  runs the same function as the control page's button, so the page and the apps cannot drift apart:
  - `GET/POST /api/devices`, `DELETE /api/devices/{name}`: list, add (its config, QR code and, for
    a split config, the NetworkManager line's domains), remove. The phone can put itself on the VPN.
  - `GET/POST /api/accounts`: the people, invite codes and requests; every button of the Accounts
    page (invite, accept, deny, create, link, delete, admins …).
  - `GET/POST /api/settings`: the settings form of a machine or the defaults, with the page's checks;
    secrets say only whether they are set. Save, or Save & apply as a job.
  - `GET/POST /api/access`, `GET/POST /api/federation`: who may reach what, and what a linked
    network may reach here.
  - `GET/POST /api/modules`: the module sources, their newest tags; add, update, remove.
- The Accounts page, the settings form and the module sources were split into one part that reads or does and one
  that shows it; nothing changes on the page.
- **The app shows a person's key for a hidden service** (*Show key* under Who may reach what), as the
  control page does; before, it sent you to `onion … --show NAME` in a terminal.
- Docs: the split choice for devices and its DNS line (network, control page, app).
- The offsite drill takes `ENCRYPT=RING`: an encrypted machine at another site, its first boot opened
  through the initramfs's SSH, every later one by a ring on the master.

## 2.212.0 — a laptop that keeps its own Internet and names

- **Only the network through the VPN, from the app and the control page** (the owner: "does the
  GUI show the same?"): *Add a phone or laptop* has the choice, as `enroll --split` has; the
  linked networks' meshes go through it too. Such a config imported as it is would send every name
  of the computer to the network's DNS (the owner: "I can't use my local DNS stuff anymore"):
  the app, the page and `enroll --split` say the one NetworkManager line that keeps the
  computer's own names (`ipv4.dns-search ~home,~internal`).
- **`kiwi-server connect` asks the master for the linked networks' names too** (`~internal`).
- **`www.` of the website leads to its bare name** (one address for the site).
- The offsite drill leaves `run.sh` beside its VM: after this computer restarts, it starts the
  machine at the other site again.

## 2.211.0 — the public pages apart from the network

- **The website, the status page for the Internet and the public registry run in a container of
  their own** (kiwi-modules 0.93.0; the owner: "this whole thing is about security"). They ran in the
  control part, which holds the fleet, the secret store and the keys to every machine; one fault in
  a page strangers reach would have opened all of it. `kiwi-server serve-public` has no fleet and
  no key, its root is read-only, and what it shows comes from a folder the control part writes
  after each look (`health.for_strangers`: the services open to the Internet, their days and
  incidents, the domain, the CA's certificate), mounted read-only. Every other route answers 404
  there.

## 2.210.0 — the public names in the check

- **`kiwi-server check` asks every public name too**, from this computer as a stranger reaches it,
  its certificate checked as a browser does: a public name was left out since 2.202.0 (it is not
  in the network's own DNS). `--no-public` leaves them out. On the test network: 8 public names, all
  answering with valid certificates.
- The page-parity notes say what the app has had since 2.92.0 (Preview, Update everything, the
  devices' codes and removal).

## 2.209.0 — an update with little room

- **An image update on a nearly full disk takes one image at a time** (kiwi-stack 0.9.16): it pulls
  one, restarts what runs it and drops the old copy before the next. A master in RAM has its
  images in half its memory, and `podman auto-update` pulled all new images first (crumble had an
  nginx update waiting with 51 MB free).
- **A device's config downloads on the control page** (the owner: "Download lime-pc.conf doesn't
  work"): the button asks the control node for the file. It was made in the page by a script that
  some browsers ignore, and the private key stood hidden in the page.
- The access drill finds the linked network by its mesh, not by its folder's name.
- The roadmap's top says where things stand at 2.208.0.

## 2.208.0 — the console password out of the fleet file

- **The machines' console password is no longer kept in the clear** (the owner's rule: no secret
  unencrypted on a disk). `kiwi-server secrets admin-password FLEET` moves a `password:` of the
  fleet file into the secret store and leaves its hash (`password_hash`); a password typed on the
  control page or in the app is kept as a hash from the start. `validate` points to the command.
- **`kiwi-server logins` lists every machine's screen login**: the admin user and its password,
  from the secret store (or the fleet file while it is still written there). A bare machine has it
  too.

## 2.207.0 — the website's images, through a CDN if wanted

- **Images in the website's pages**: `![Our logo](assets/logo.png)`, the site's own files only;
  an image from elsewhere stays a link that loads nothing by itself.
- **The website's files through a content delivery network** (the owner: "use cdn.dlhost.eu
  properly"): the control module's `website_cdn_hostname` (kiwi-modules 0.92.0). The pages load
  their images from that name, which answers `/assets/` and nothing else; each address carries the
  pinned commit, so the CDN keeps a file for good. The site says that the CDN sees the address of
  whoever loads those files.

## 2.206.0 — a group and a linked network, on the page and in the app

- *Who may reach what* on the control page and in the app says that a group can be given a
  linked network by its name (`kiwi2`) next to single ports, as `kiwi-server access` can since
  2.205.0; the network docs list it.
- The federation drill runs both ways: B's DNS is found from B's mesh, and it says which group
  its client stands in. Run from kiwi's side (a client of kiwi opening kiwi2's damson), a group
  without `kiwi2` was refused and one with it got through.

## 2.205.0 — the website, and what federation is for

- **Every page the network shows wears the control page's frame** (the owner: "why don't you use
  the control node's design for every web UI"): the website, both status pages and the game page
  have its sidebar, its top bar and its bottom bar on a phone, with their own menu and the light
  or dark switch, no machine counts and no logout. The website's pages are its cards and buttons;
  its *Status* is the status page for the Internet (status.dlhost.eu), and `/status` leads there.
- **The public services are listed again** on the status page for the Internet and the website:
  since 2.202.0 a machine's sites were read from the network's own DNS, which no longer has the
  public names.
- **A device on a split tunnel routes the linked networks' meshes too** (the owner: "that's the
  sense of federation"): `enroll --split` writes them into the config next to the own mesh. A
  device made before gets them with `kiwi-server enroll FLEET NAME --existing --split`.
- **Measure the MTU on a machine's page**, in the app (beside *VPN client*) and on the control
  page (the owner: "is the MTU measuring in the GUI?"). `kiwi-server mtu` now measures the path
  outside the tunnel too and says how large the tunnel could be: at home today 1376 fits, the path
  to the master takes 1452 bytes, so 1392 would (1.2 % more a packet).
- **Who may reach what in a linked network** (kiwi-modules 0.91.0): a group that reaches
  everything reaches what the linked networks open; a narrower one only when it names the network,
  `kiwi-server access FLEET guests kiwi2`. Before, guests and pentest reached what a partner
  opened.

## 2.204.0 — the control part of this release, and the picture at its smallest

- **An apply brings the control part of the kiwi-server that applies it**: the control module's
  image is pinned to this release (`kiwi-server-control:2.204.0`) instead of `:2`. Since 2.198.0 an
  apply pulls only images a machine does not have, and `:2` was always there: papaya still ran
  2.200.0's page after 2.203.0 was applied, and dlhost.eu answered with the control page. An
  image set by hand in the fleet file stays as it is.
- **The narrow picture** (a phone, the app at its smallest) puts what a linked network opens right
  under it, with its label beside its line, not after the last site.

## 2.203.0 — a linked network's names at home

- **A node's Pi-hole knows the linked networks' domains** (kiwi-modules 0.90.1): it asks the
  master for them, and the master asks the partner. `dig damson.internal @papaya` said NXDOMAIN
  while the master's Pi-hole answered: a computer at home could not reach a partner's machine by
  its name.
- dlhost.eu's website is on, from the template [dlhost-eu](https://github.com/derlocke-ng/dlhost-eu).

## 2.202.0 — a public name is public everywhere

- **A public name resolves to the master's public address at home and on the VPN too** (the
  owner: "for dlhost.eu, an only external domain, we want no internal one"): the network's
  Pi-holes and hosts files keep only its own names. `status.dlhost.eu` shows the status page for
  the Internet from everywhere; what is public is reached the way strangers reach it, through
  the master. A service wanted both ways is better two: a public one and an internal one.

## 2.201.0 — a website for strangers, and a partner's names under .internal

- **The network's website** (docs/design/website.md): the control node serves it at its name and
  `www.` (kiwi-modules 0.90.0: the control module's `website_hostname`, `website_repo`,
  `website_commit`). The pages come from a repository pinned to a commit, in a small, safe part
  of Markdown; the services open to the Internet, whether they work, the sign-up and the status
  come from the network itself. Nothing else answers at those names. The template is
  [dlhost-eu](https://github.com/derlocke-ng/dlhost-eu); `KIWI_SITE_DIR` shows a folder while
  writing it.
- **A partner's names under `.internal` resolve** (kiwi-modules 0.89.2): Pi-hole 6 kept
  `.internal` to itself, so kiwi did not reach kiwi2's names, nor learn what kiwi2 opens to it.
  The federation drill now asks that way too.

## 2.200.0 — one admin: the control node applies

- **A computer that joined lets the control node apply the machines** (the owner: "two apps
  working against each other"): the app's Apply buttons push this copy, start the job on the
  control node and show its log as it comes; what the job changed comes back. In a terminal:
  `kiwi-server control run FLEET apply MACHINE…`. Without a control node, for a first apply
  over SSH, or with *Preferences*, *Applying* off, the computer applies itself as before.
- **`POST /api/jobs`** for a computer's token: the page's actions as jobs; `/api/jobs/{job}/log`
  takes the token too. kiwi-app can start applies the same way.

## 2.199.0 — the network as a family tree, with what the linked networks open

- **How they are linked is a family tree** on the control page and in the app (the owner: "masters
  on top, show proper federation"): the master on top with its standby and the linked networks
  beside it, the machines under it in a zone per site, one line down from the master to each
  site, a bar and a drop for each column. Many machines make more rows instead of a list; only a
  phone gets the list. Dashed: another site, over the Internet.
- **A linked network's line goes the way its traffic does**: over the link into the master, then
  beside the master's own line down to each machine it may reach here, in violet, with its name
  on the machine.
- **What a linked network opens to this one stands under it**, in a violet zone: its machines and
  their services. Its master's DNS says so over the link (kiwi-modules 0.89.0, 0.89.1: a TXT
  record per partner, and the master's name), and the master's status asks it. The linked
  network's box names its master.
- Pointing at a machine or a linked network shows its lines and dims the rest; on the page too.

## 2.198.0 — quicker applies, the kiwi on every page

- **An apply pulls only the images a machine does not have yet** (kiwi-stack 0.9.15; the owner:
  "why do reapply/download everything?"): newer ones come with the update (the weekly timer,
  `apply --update`). papaya asked 31 registries at every apply, medlar tried codeberg.org three
  times. Packages already installed are not looked up again either. With `auto_update: off` the
  apply still brings every image, as there it is the only way they change.
- **A new release alone is no change of a machine**: `apply --changed` and the app's "changes
  not applied" compared the whole role script, kiwi-server's version in it, so every release
  named every machine. Now only what a machine gets counts.
- **Every page kiwi-server serves has the kiwi as its icon**: the control page, the status page
  and each machine's page, the game page, the starter's page. It is inside the page, so it shows
  under every name a page answers at.
- **A machine's logins list its accounts page** (kiwi-modules 0.88.2), and say why Kanidm's admin
  has no password there: kiwi-server takes it over for each change and keeps none. Accounts and
  admins are made under Accounts.
- **Images nothing uses go when the disk runs full** (kiwi-stack 0.9.14): after an apply, at 75%
  or more, every image that no container and no module of the stack uses. crumble, a master in
  RAM, had its 913 MB /var 95% full.

## 2.197.0 — a module ticked shows its settings at once

- **The control page shows a module's settings as soon as it is ticked**, as the app does (the
  owner: "need to save first it seems and refresh"). Every module the machine may run has its
  settings on the page, hidden until it is ticked; tick it, fill them in, save once. The settings
  of a module that is not ticked are not written.

## 2.196.0 — the app asks one kiwi-server that stays

- **`kiwi-server agent`**: a kiwi-server that the app starts and that ends with it, on a socket
  only its user opens. It answers what only reads (`list`, `logins`, `secrets status`,
  `sources list`, `names`, `validate`) without a new process each time, and keeps `list`'s
  answer until the fleet's folder changes: four seconds became none after the first time, a
  machine's logins half of what they took. Everything that changes something still runs as a
  command of its own.
- **The registry** names the network's mark (`ca_mark`: the CA's first eight SHA-256 digits, as
  an invite ends with) and each service's Android app where there is one (`android`); the status
  page on the VPN shows the mark, to compare with what kiwi-app shows for the network.

## 2.195.0 — several machines at once

- **`apply` of several machines runs them together** (the owner: "the last apply took server
  after server"): the masters first, then the machines that answer for names or open disks,
  then all the others at once, up to six; the control node last when it applies itself. Every
  role script is made first, the fleet's own files are written one machine at a time, and each
  line of a machine's output starts with its name.
- **A machine with no site of its own answers at the mesh** (kiwi-modules 0.88.1): its own page
  and its registry address open 80 and 443 there too. The offsite drill's minimal node answered
  SSH over the mesh but not its page.
- **The offsite drill** (tests/drills/offsite-vm.sh) gives the new machine its first apply the
  way a computer at its site would: home cannot reach a machine behind another router before it
  is on the VPN, and the install media bring a bare system. It asks SSH over the mesh, not ping,
  which nodes do not answer.

## 2.194.0 — a machine's logins on the phone

- **`GET /api/hosts/{name}/logins`** answers a controller's token too (kiwi-app shows a machine's
  logins and passwords): the same body, never cached, and the activity log names the computer
  that was shown them.
- **"Changes not applied yet"** names a new kiwi-server release as a cause too: after an update
  every machine whose files it changes is listed until it is applied.

## 2.193.0 — the network's registry, for the apps

- **`/api/registry`**: the status page as data, without a login: every service with what it
  is for, where it is and whether it works, the game servers, the sign-up and game pages, the
  network's CA. `?machine=NAME` for one machine; the public name gives the strangers' part.
- **`/.well-known/kiwi`** at every machine's own name and at its address on the LAN leads
  there: kiwi-app's user side finds the network from a name typed in or by asking the LAN
  (kiwi-modules 0.88.0). [control.md](docs/control.md#more-computers) has the fields.
- `tests/drills/offsite-vm.sh` waits longer, keeps a machine that failed a check, and reads
  what the machine itself says when it does not join; `status` that cannot reach it is a fail.

## 2.192.0 — every machine's own name has a page, and the roadmap is current

- **`https://papaya.home`**, a machine's own name, leads to its part of the status page: its
  services, whether they work, whether it answers, and a link to the whole network. It
  answered nothing before. `status.<host>` stays the network's page, and the page for the
  Internet stays at its public name (kiwi-modules 0.87.0).
- **The roadmap** ([docs/ROADMAP.md](docs/ROADMAP.md)) opens with where kiwi-server stands and
  what is open, in order; the history stays below. Five design pages are listed for the site.
- `tests/drills/offsite-vm.sh` is in the repository now (2.191.0 named it without it).

## 2.191.0 — Preferences and a short menu, sites, and the whole network in the picture

- **Preferences** in the app (the gear, Ctrl+,): its icon from kiwi-icons in any colourway and
  style, as in Kiwi Apps; light or dark; the kept messages; sharing; locking; the tools. The menu
  is short now: *This network* and *Manage* open further, the app's own things are in Preferences.
- **The picture of the network** (app and control page, one layout for both): the master's
  standby beside it, dashed; every linked network, dotted; and the machines in a zone per site.
- **A machine at another site** (`network.site`, "Site" in its settings): machines of one site
  with fixed addresses reach each other on their LAN, one at another site over the VPN, also
  when that LAN has the same numbers as home's; its unlock ring is not looked for on this LAN.
  `list` names each machine's site and the linked networks.
- `tests/drills/offsite-vm.sh`: a node in a local VM behind QEMU's NAT joins the network as a
  machine elsewhere.

## 2.190.0 — in step without a riddle, the differences, the network's admins, the API for the phone

- **"Not in step: this copy changed since the last sync … push first"** came again and again
  in the app (kiwi-2, 18 Nov 2026): a sync fetched first, the fetch refused because only this
  computer had changed something, and the push that would have sent it never ran. Now the fetch
  says there is nothing new there and the push follows; the CLI says so too.
- **What differs** between this copy and the control node's or the synced folder's:
  `kiwi-server control diff FLEET`, and in the app *Compare with the other copy…* in the menu
  and *Show the differences* when both sides changed the same thing. Per file who changed it
  since the last sync; of the settings the lines, every secret value shown as •••; of the
  secrets only their names. Then *This computer's* or *The other one's*.
- **Leave it out** takes the warning away at once, in the app and on the page, instead of
  after the next look at every machine.
- **The Android app reads the network** (kiwi-app): `GET /api/status` answers a controller's token
  too, and the new `GET /api/machines` gives what `list --porcelain` says of the machines, without
  the control node's own paths. Both only read and leave no line in the activity log.
- **The network's admins** (kiwi-modules 0.86.1): *Make admin* at an account on the control
  page, the *Admin* switch in the app's Accounts: an admin of Forgejo, Nextcloud and Immich
  from the next sign-in there. Tried in a real browser on 18 Nov 2026: a test account in
  `admins` became an admin of Forgejo (medlar), Nextcloud and Immich (papaya) at its first
  sign-in; one outside it stayed a user.

## 2.189.0 — what to do, warnings on purpose, the app's messages, module sources on the page

- **What to do**, with every problem a machine reports: on Home and a machine's page, in the
  app's *Needs attention*, and in the messages that are sent ("What to do: …").
- **Leave it out**: a warning that is so on purpose (papaya's unencrypted data disk) becomes a
  note with one click, on the page and in the app: the machine's setting `quiet` (*Warnings
  left out*). Something broken never goes quiet.
- **The app's messages**: a bell at the top right keeps everything the app said, with its time,
  after its short messages vanish; new and gone warnings of the machines too, and a count of
  what is new.
- **Kanidm's pages kept "Kanidm id.quince.home"** after 2.188.0: their name and logo are Kanidm's
  `admin`'s to set, not `idm_admin`'s (kiwi-modules 0.85.1).
- **A look at the machines said it was done** a moment before what it found was written: the
  page could show the look before as the new one.
- **The status page linked the game page wrong** (`play.<host>/play/`, which does not answer):
  it is `play.<host>/` now.
- **Module sources** without a terminal: *Module sources* on the control page (More) and
  *Module sources…* in the app's menu: each source with its version and modules, add one by its
  link, look for new versions, update, remove. The same checks as `kiwi-server sources`, which
  runs underneath; the control node saves its copy and the app sends it to the other computers.
- `sources FLEET list --check` asks each repository for its newest version; `--porcelain` names
  each source's modules. An `update` that finds the pinned commit is a version tag records it.
- **The docs** for a first network: the logins are on the *Services* page and a machine's
  *Services* tab, the app's entry is *Role script*, the disk's key is among a machine's logins,
  and the example of a control node is under `hosts:` (a check of the getting-started path
  against the code, 18 Nov 2026).

## 2.188.0 — one status page, the game servers on it, one look for the account pages

- **status.<host> is one page**: the status page (its look stays), with what each service is
  for, "Get an account" and the hidden services. There was a start page at `/` and the status
  at `/status`, which looked like two halves of one thing (18 Nov 2026). `/status` and `/start`
  still answer, with the same page; so does the page for strangers.
- **Game servers on it**: up, asleep (wakes when you join) or stopped, where to connect, and a
  button to the game page, which nothing linked to before.
- **The page on the VPN says Kiwi**: papaya's status page said dlhost, the name of the page for
  the Internet. `brand_name` names only that one now.
- **The account pages** (kiwi-modules 0.85.0): Kanidm's own pages say the network's name (the
  accounts `brand`, else Kiwi) and show the kiwi, not "Kanidm id.quince.home"; the sign-up
  page says Kiwi, not "home".

## 2.187.0 — a hub on a node calls through the master's turn

- A hub on a node with no `ice` of its own is told the master's `turn` server and its login,
  as a hub on the master always was: calls between two strict networks get through without
  copying the address and password by hand ([roles.md](docs/roles.md)). kiwi-modules 0.84.1.

## 2.186.0 — the network's public names on the VPN; versioned module sources

- **This computer on the VPN asks the network's DNS for its public names too**
  (`kiwi-server connect`, the app's Connect this computer…): `hub.kiwi-network.eu` was answered
  by the home router with the master's public address, so the relay took this computer for the
  Internet and refused the hub's posts (18 Nov 2026). Run Connect again once to get it.
- **Module sources by version**: `sources add` and `update` pin a repository's newest version tag
  (`v0.2.0`), `--ref` follows a branch. A source from `sources new` has a `VERSION`, tags it when
  merged, and checks that a changed module bumps its version (kiwi-modules-community and
  -personal have it, both at v0.1.0).
- **`control push --ssh` refused** whenever the control node had changed anything since the
  last sync ("the control node's copy changed"), even only a host key it learned; after every
  release here (17-18 Nov 2026). Over SSH push and pull now make one copy of the two like the
  control node's API always did; only a file both sides changed stops them.
- **`turn`** (kiwi-modules 0.84.0, from the pull request of 10 November): coturn on a master for
  the calls in a hub's chat when two networks allow no direct path; it relays and keeps nothing.
  A hub on the same master finds it by itself, a hub on a node names it in `ice`.

## 2.185.0 — save & apply, and every machine a change reaches

- **Changes not applied yet**: kiwi-server remembers a fingerprint of what each apply sent and
  names every machine whose role script is another now, also where the change came from
  elsewhere (a new site on medlar is a new name the master's and the home gateway's DNS must
  learn: `grow.medlar.home` did not open from a phone, 18 Nov 2026).
- `kiwi-server apply FLEET --changed` applies them; an apply says which other machines it left
  changed; `next` offers them once every machine is set up.
- **The page**: **Save** and **Save & apply** (this machine and every other one the change
  reaches) in the settings; on Home "Changes not applied yet" with **Apply all**.
- **The app**: Apply offers the other changed machines along ("Apply all 3" or "Only medlar");
  the overview has "Changes not applied yet" with **Apply all**.
- A machine this computer never applied with this release is not judged yet.

## 2.184.1 — a sleeping game server is not a failed one

- Zandronum ends with an error code when it is stopped, so a sleeping Doom server left a failed
  unit and medlar showed a failed service (18 Nov 2026). The sleeper clears it now
  (kiwi-modules 0.83.1), and a failed unit of a server that is asleep is not counted.

## 2.184.0 — open to: the VPN, Tor, the Internet, per site

- **Open to**, at each module of a node in the app and on the page: per web site a switch for
  Tor (a hidden service; the machine's onion part comes along) and a public name (the module's
  own name setting; the node's proxy opens through the master). On the VPN always; admin pages
  are offered neither. [roles.md](docs/roles.md#a-nodes-service-open-to-the-internet).
- kiwi-modules 0.83.0: each module says its sites, the setting for a public name, and whether
  they work over Tor.

## 2.183.0 — modules from a source, first-class; the app keeps hidden services

- **The app dropped a machine's hidden services** when any of its settings was saved (the page's
  bug of 2.179.1, in the app's own copy): saving smartfritz's settings on medlar took
  `onion: services` away again. Now only what a change itself empties goes.
- **A game server asleep failed the apply** although every container ran (kiwi-stack 0.9.13).
- **Its parts in the app** are switches with what each one is for, what it needs and the module
  source it comes from, as on the page; no more text box.
- **A module names its own pages** (`pages:` in its module.yaml, kiwi-modules 0.82.0): the start
  and status pages, the app and `list` show that name and what it is for.
- **A module that says how it signs in** (`accounts:` in its module.yaml) is connected to the
  network's accounts like Forgejo or Nextcloud: `kiwi-server accounts FLEET connect HOST MODULE`.

## 2.182.0 — the master routes, your data lives on the nodes

- Said where people read first (the README, [roles.md](docs/roles.md)): the master is a server you
  need not trust; by default it only moves traffic, and every service that keeps data runs on a
  node, reached over the VPN or through the master without it reading along.
- A master that runs a service with data of its own gets a warning naming it, and the way to run
  it on a node (allowed, not refused).
- A hub and its nostr relay run on a node now (kiwi-modules 0.81.0); roles.md shows a public hub
  on a node. The kiwi-modules table no longer lists Jabber, tang, the relay and the hub as master
  only, and its links lead to pages that exist.

## 2.181.0 — modules from elsewhere

- **Module sources**: modules beyond the built-in ones, from a git repository or a folder,
  listed by hand in the fleet file (`module_sources:`) and pinned to a commit.
  `kiwi-server sources FLEET [list|add LINK|update NAME|remove NAME]`; `sources new FOLDER`
  starts a source of your own with an example module and its render check.
  [modules.md](docs/modules.md#modules-from-elsewhere).
- Two to start from: [kiwi-modules-community](https://github.com/derlocke-ng/kiwi-modules-community)
  (pull requests, each module rendered by its CI) and
  [kiwi-modules-personal](https://github.com/derlocke-ng/kiwi-modules-personal) (smartfritz).
- Their modules are in the settings of the app and the page, and `list --porcelain` names the
  sources and their modules. The control image has git for them.
- kiwi-modules 0.80.0: `--source NAME=FOLDER`; a source never takes a built-in module's name.

## 2.180.0 — the first steps, tried as a newcomer

A dry run of `init` and `next` from nothing, as the getting-started guide says, found:

- A yes-or-no question took anything that was not a yes for a no (an address typed one line too
  early declined the sign-in at netcup). It now asks again: "y or n, please".
- A connection netcup dropped ended `next` in a Python traceback; netcup's login being down
  said "answered 503 with something that is not JSON". Both are a sentence now, and when the
  sign-in fails `next` says the way on: try again later, or upload the install media at the
  hoster yourself.
- `next` for a machine at home before the master was set up ended in an SSH error about the
  master's name. It now says that the place on the VPN comes from the master, and to set that
  up first.
- The app shows at each hidden service of a machine whether it answered through Tor when last
  asked (the status page has had it since 2.179.0).

## 2.179.2 — the hidden services' self-check keeps going

- kiwi-modules 0.79.1: the onion part's self-check ended quietly at the first address that did
  not answer through Tor (since 0.76.0, the case it is for): it neither started Tor anew nor
  told the status page. Now it writes the miss down and goes on.
- Tried on the test network: the status page as papaya's hidden service, opened over Tor from
  the master; the game page in a browser (`tests/drills/play-browser.py`: a member signs in and
  reads a log, someone outside the group is turned away); sleeping game servers on medlar
  (`tests/drills/games-sleep.py`: both slept, `status` said asleep, they woke in 21 s and 13 s).

## 2.179.1 — a saved setting no longer drops a machine's hidden services

- Saving any setting of a machine (the Settings page, the app's settings) also removed every
  empty `{}` of that machine's block. A hidden service with its defaults is one:
  `onion: services: {git: {}}` went, and the next apply would have taken the hidden services
  off the machine (seen on medlar's test entry, 17 Nov 2026; stopped by the nginx check before
  an apply). Now only the blocks a change itself leaves empty go. A machine whose hidden
  services are missing from its settings: compare with a copy (`kiwi-server backups`) and put
  them back.

## 2.179.0 — the hidden services on the status page; Jabber's sign-up page names its own service

- **Over Tor** on the status page: each hidden service, its Tor address, and whether it answered
  when its machine last asked it through Tor (kiwi-modules 0.79.0: the onion part's self-check
  writes that down; `kiwi-server status` notes one that did not). The status page as a hidden
  service itself: [control.md](docs/control.md#the-status-page-and-messages).
- **Jabber with `separate: true`**: the internal sign-up page was headed with the public name
  (xmpp.example.org); each page now says its own service's name, the one its addresses end in.

## 2.178.0 — the game page

- **The game page** at `play.<control node>`: people with an account of the network, in one
  group (`games`), start, stop, restart and read the logs of the game servers without being
  admins. [control.md](docs/control.md#the-game-page). VPN only unless it gets a public name;
  the sign-in is the accounts' (OpenID Connect with PKCE), only for the group's members; only
  `/play/` answers at its name. Each click is a task, in the machine's History with the name.
- `kiwi-server accounts FLEET connect HOST control` and `accounts FLEET member GROUP NAME
  [on|off]` / `members GROUP`; the Accounts page has **Let in** at each account, the app a switch.
- `play_extra` (`machine/container`) puts other containers on it.
- `list --porcelain` names each machine's game servers and the game page.
- kiwi-modules 0.78.0: the control module's `play_page`, `play_hostname`, `play_group`,
  `play_extra`; the accounts' group-only sign-in.

## 2.177.0 — game servers that sleep while nobody plays

- **`sleep: N` for a game server** (kiwi-modules 0.77.0): stopped after N minutes without
  players, woken by the first who tries to join. [roles.md](docs/roles.md#game-servers).
- A sleeping server is **asleep, not down**: kiwi-stack 0.9.12, `kiwi-server status`, the
  machine's page (Its containers) and the app say so; nothing warns.
- Drill `tests/drills/games-sleep.py`: waits until the servers sleep, then knocks the way
  their games do and times the waking.

## 2.176.0 — one container at a time

- **`kiwi-server service FLEET HOST NAME restart|stop|start|logs`**: one container
  of a machine's stack, without an apply. `logs` shows its last lines (`--lines`,
  200 by default). Only the stack's own containers (`kn-…`, `km-…`); podman
  machines through their systemd units, docker ones through docker.
- **The control page**, machine page, Services tab: **Its containers**, with
  Restart, Stop or Start, and Log. Each is a job, and the machine's History says
  which container and what was done.
- **The app**: the same in a menu at each row of the machine's Services.
- kiwi-modules 0.76.1: the onion module's help says what `round` does (a new address).

## 2.175.0 — a new recovery key from the app

- **The app's Locking… has "A new recovery key…"**: kiwi-server makes the key (nothing changes
  yet), the app shows it, and only once its last 8 characters are typed back does it replace the
  old one (`secrets recovery --make` / `--key-stdin`, as the terminal's own way). The dialog is
  taller, so its three parts show without scrolling.
- A wrong passphrase on an older backup that slips past openssl (about once in 256 tries) is
  refused in plain words, not with a gzip traceback (it had failed CI now and then).

## 2.174.0 — the control page logs out when left; a hidden service gets a new address

- **A login on the control page ends 30 minutes after its last use** (and 12 hours after the
  login whatever happens), as the app's secrets lock: a browser left open at an unwatched
  computer does not stay in. The page's own refreshes do not count; a form already on the page
  stays good while the login lasts.
- **A hidden service gets a new address**: `kiwi-server onion FLEET git.medlar --new-address`, and
  **New address** on the page (Who may reach what) and in the app. The old address stops
  answering with the machine's next apply; its people and their keys stay. The first address of
  every service is the one it had (rounds: `onion.services.<name>.round`).

## 2.173.0 — the app sets how it locks

- **The app's Locking…** (in its menu): after how long unused the secrets lock (15 minutes,
  30, an hour, 8 hours or never), whether with the screen, and a password for this computer's
  key (set, change, remove). Clicked through in the app: 15 minutes taken, a password set, the
  store locked and opened again with it. `secrets status --porcelain` says both.

## 2.172.0 — the store locks by itself

- **After a while unused**: 30 minutes after the last command or action in the app (its own
  looks do not count), instead of 8 hours after it was opened. `secrets autolock --idle`.
- **With the screen**: a user service locks every open store when the screen locks (GNOME and
  others that say so on D-Bus), what changed in it saved first. `secrets autolock --screen`.
- **An extra password, if wanted** (not by default): `secrets password` keeps this computer's key
  in the keyring only together with a password, asked once per unlock; the app asks for it.
- **Hidden services that stop being found start anew** (kiwi-modules 0.76.0): each checks its
  own addresses through Tor every ten minutes. Two of medlar's three had answered nothing for
  hours while Tor ran on.

## 2.171.0 — a new recovery key when the old one is lost

- **`kiwi-server secrets recovery FLEET`**: a new recovery key in place of the old one, while a
  computer still opens the store. It replaces the old one only once it is kept: shown once and
  typed back (its last 8 characters), or written to a new file with `--out` and never shown. The
  copies are signed with it from then on; the old key opens only the copies made before. A recovery key is shown once, when the store is
  made, and nothing shows it again (docs/backups.md).

## 2.170.0 — a network's copies are signed

- **A copy carries a signature from a key the recovery key gives** (`secrets/copy-auth.key`,
  made with a new store). `restore` (and the control page's) checks it with the recovery key
  and refuses a copy that is not the network's own: someone who can replace a copy on a machine
  could otherwise hand back another network, encrypted for the same public keys. A copy without
  a signature is refused too, unless `--unsigned` (an older copy of one's own).
- A network made before: `kiwi-server secrets copy-key FLEET --identity RECOVERY-KEY-FILE`
  once, and its copies are signed from then on.
- tests/drills/ram-master-power.py finds a master that booted from its image on port 22
  (17 November 2026: role back 219 s after power on, the ring `away` with the same keys).

## 2.169.0 — the first machine at home opens by itself after a power cut

- **The ring `away` on the master** (docs/design/encryption.md): the first run gives the master
  a Tang ring (port 7501) that answers only the addresses enrolled machines last connected from,
  and the first machine at home opens with it. Tried on kiwi2: damson (a Debian VM at home)
  restarted and opened its disk with crumble's ring, nobody typing a key; asked from another
  address, the ring did not answer. A RAM master keeps the ring's keys with the fleet's copy; a
  standby serves no ring.
- **A smaller Tang image** (kiwi-modules 0.75.1, 15-kiwi6, 14 MB instead of about 150): it fits on
  a master in memory, whose first try ran out of room.
- **An apply that ended early no longer forgets a restart** (kiwi-stack 0.9.10, 0.9.11): what it
  wrote and did not restart yet is restarted by the next one. Seen when crumble's VPN server kept
  its old settings after an apply that ended at an image pull.

## 2.168.0 — a ring's keys survive a power cut

- **kiwi-modules 0.74.0**: the Tang image (15-kiwi4) makes a ring's keys beside, on the disk,
  then puts them in place, and never serves a key file a power cut left empty (the last gap of
  the power-cut round: files the machines write themselves; kiwi-stack's own were already
  flushed before they take their place).

## 2.167.0 — data disks are encrypted too

- **A new data disk is put in LUKS before its filesystem** (`mounts:` with a format word), as the
  system disk is. Its key comes from the seed, so a machine installed anew opens it again; on
  the machine the key lives on the encrypted system disk, and `/etc/crypttab` opens the disk at
  every start. `plain` (a fourth word) or `encryption: false` leaves it as before. A disk that
  holds data is not touched. Tried on a VM: encrypted, mounted, still there after a restart.
- `kiwi-server status` warns about a data disk that is not encrypted.
- Making the filesystem on a small Debian installs its tool first (xfsprogs was missing).

## 2.166.0 — phones follow the master to its standby

- **Phones and laptops are sent to the standby** (docs/design/standby.md, step 4): when the
  master's address is a name at a dynamic DNS provider and `secrets/ddns.url` holds the
  provider's update address, the control node points the name at the standby once the master
  is gone for its `recovery.master.after` minutes, and back when it answers again. The
  provider's token never appears in a message.
- **A standby shows as one** (step 3): `list` says "standby" and whose, `list --porcelain`
  has `standby_of`, the page says "Standby of crumble", the app lists it under the master.
- Getting started: `init` asks the way to the Internet too (Riseup suggested), and says every
  disk is encrypted.

## 2.165.0 — the master's VPN server always starts again

- **A container others live in starts again also when something was left in its network**
  (kiwi-modules 0.73.0): a container started there by hand once kept kiwi2's VPN server from
  being replaced after a stop, and its unit failed over and over until the whole stack was
  started anew.
- Proven on the test networks, 17 November 2026: a chained exit, a device of the first
  network leaving the internet through the second network's master and its Riseup exit
  (`tests/drills/multihop.py --expect`); a standby master taking over a node and handing it
  back (`tests/drills/standby.py`, in the drills README now).

## 2.164.0 — a standby master; federation by group and by card

- **A standby master** (docs/design/standby.md, step 1): a `standby:` block under the master
  names a second machine that runs the same master (same name on the mesh, stack, secrets and
  WireGuard identity) with its own disk, address and unlock. An apply of the master applies it
  too and gives it the master's peers. The master's nodes move their tunnel to it when the master
  is gone for three minutes, and back once the master's machine answers again (kiwi-modules
  0.72.0). A standby at home runs no Tor; no standby runs the federation link.
- **Federation: `from` takes the partner's groups by name** (`from: [devices]`) as well as
  addresses; `federate` writes each side's groups into the other's file. An entry that is neither
  is refused with what is known, not passed on to the master's firewall.
- **Federation between two operators**: `federate FLEET --invite CARD` writes this network's card
  (nothing secret), `--accept CARD` takes the other's and writes this side's card beside it.
- `federate` keeps a partner's entry when its folder was renamed, instead of linking it twice.
- `control pull` with nothing new on the control node sets the node's stamp right, so the next
  `push` is not refused for a change that is not there.
- A RAM master is no longer reported as "the disk is not encrypted": it has no disk.
- The app shows a computer asking to join also when the sync with the control node failed.
- tests/drills/federation-styles.py (each way of sharing) and tests/drills/standby.py (a node
  follows its master to the standby and back).

## 2.163.0 — Riseup, a free exit

- **`vpn-client: { vpn_provider: riseup }`**: the VPN's way out through Riseup, free and with
  no account. The computer that applies fetches Riseup's anonymous certificate into the
  fleet's secrets, renews it when fewer than ten days are left (the control node applies
  that machine by itself), and picks a Riseup server that
  answers (`server_cities` for a city). docs/network.md.
- **The first run suggests it** as the way to the Internet for people without a VPN provider
  (the master's own connection and the paid providers stay choices).
- **Tor wakes at every start** (kiwi-modules 0.70.0): it slept after a day without a client and
  stayed asleep across restarts, so the first request after that waited.

## 2.162.0 — the master is encrypted too

The third step of encryption by default (docs/design/encryption.md).

- **The first run and "Add a master" write the master encrypted** (Debian, with SSH in its
  initramfs). Its first start waits for the one-time key of its media; `kiwi-server next` sees
  that and gives it; the first apply puts its own key in place.
- **After a restart the control node gives a waiting machine its key** by itself when no ring is
  forwarded to it (`kiwi-server unlock` does the same, without `--manual`).
- **Rings that do not answer yet** at an apply (the machine serving them comes later) no longer
  stop it: the disk opens with its key until an apply after they answer, and the machine does
  not restart by itself for updates meanwhile.
- docs/ROADMAP.md: "a master without LUKS" (10 November) is replaced.

## 2.161.0 — every machine kiwi-server installs is encrypted

The second step of encryption by default (docs/design/encryption.md).

- **kiwi-server's own media for CoreOS and uCore encrypt the disk.** With the machine's rings when
  they answer at build time; else its TPM 2.0 stands in until the first apply binds the rings or
  leaves the key for its screen (as the starter stick since 2.159.0). The install checks for a
  TPM and 4 GB of memory first and, without, stops and says what to do.
- **`encryption: false`** on a machine installs it plain: a choice, and every page says so.
- **The first run picks the rings:** the first machine at home with a fixed address serves the
  ring `home`; the others open with it at boot by themselves. That first machine opens with the
  key for its screen (its Logins tab shows it): after a power cut one key is typed, the rest
  follow. A machine added later with a fixed address binds `home` too.
- Tried in a local VM with an emulated TPM (`tests/drills/iso-encrypt-vm.sh`): installed
  encrypted by itself; without a TPM no install, and the screen says why.

## 2.160.0 — the rest of the review of 15–16 November

With kiwi-modules 0.69.0 (0.68.0's fixes inside).

- **Taking in a starter stick** says what would be refused (its name, its address) before the
  stick hears anything: a stick taken in and then refused waited until it was restarted.
- **The sign-up page trusts only Kanidm's own certificate** when it talks to Kanidm; it checked
  none before.
- **One damaged line in the activity log** (a power cut while it was written) no longer breaks
  every machine's page; it is left out.
- **"Update every machine"** is in each machine's History, with how it went for that machine.
- **Several at once** does the control node last: its own apply restarts the page.
- **A wrong setup code** no longer freezes the whole page for a second.
- **The Accounts page** asks an accounts machine that is off once a look, not once a list, and
  keeps the lists 30 s.
- **Adding a master** to a network without one keeps a machine's commercial VPN: it becomes the
  new master's exit, for the whole network.
- **The role script** is shown after a button (a POST with the page's token), not a link: making
  it may make the machine's missing secrets.
- **Two hoster sign-ins** after another no longer share the file a login lands in.
- Site names and the recovery's view of a machine take `vpn: false` into account.
- From kiwi-modules 0.68.0: `vpn: false` keeps a module's exit or says so; no mesh ways in on a
  commercial exit off the VPN; Paperless documents without an owner belong to the admin;
  Immich's and Nextcloud's client secrets off command lines; a second Bedrock server's port; a
  provider's OpenVPN file as an exit (Riseup), also without a master.
- The starter's SSH key on the disk (a low finding) is on an encrypted disk since 2.159.0.
- docs/roles.md: `ldap` makes Kanidm answer LDAP; nothing uses it yet.
- `tests/drills/riseup-ovpn.py`: a RiseupVPN config (Riseup's API hands out an anonymous
  certificate), for a master's exit in tests.

## 2.159.0 — the starter stick encrypts the disk; admin areas for the admins only

With kiwi-modules 0.67.0. The first step of encryption by default (docs/design/encryption.md).

- **Admin areas inside a site are for the admins only.** The access drill, with one real VPN client
  per group, found that a device in a group without the admin pages (`family`) reached
  Vaultwarden's `/admin` and ejabberd's `/admin` on the test network. Such a location is now
  refused to every group without the admin switch, on nodes and masters; Vaultwarden's `/admin`
  answers the VPN only. The drill tries these areas too (`tests/drills/access.py`).
- **A new device lands in a group without the admin pages** (`family`, or the first such group),
  on the control page, in the app and with `kiwi-server enroll`, unless another is chosen. Before,
  the page preselected `devices`, which has them, and `enroll` without `--group` put a device
  outside every group, where it reaches everything.

- **The starter stick installs encrypted.** There is no ring before there is a network, so the
  machine's TPM 2.0 opens the disk until a network takes the machine in. Then `apply` adds the
  key for the console, binds the network's rings, or leaves the console key alone when there is
  no ring yet, and removes the TPM: from then on the TPM opens nothing. This also happens when
  the control node sets itself up ("Set it up" on the starter's page). Tried in a local VM with
  an emulated TPM (`tests/drills/standin-vm.sh`): installed by itself, then opened by the ring,
  and by the console key with the ring away.
- **Without a TPM 2.0, or with less than 4 GB of memory, only by choice:** the stick says why it
  cannot encrypt and installs only when PLAIN is typed at its screen or the page's button is
  pressed. Its screen says afterwards whether the disk is encrypted.
- **An unencrypted disk is a warning**, on the control page's overview and the machine's page
  and in `status`, not a note.
- **The disk's key is among a machine's logins** (the Logins tab, `kiwi-server logins`): a
  machine alone at home waits for it at its screen after a power cut. Such a machine no longer
  restarts by itself for updates (it would wait at the prompt): they are staged and start with
  the next restart. The key is sealed into the store before the control node restarts itself.
- Taking in a starter machine keeps a disk the stick encrypted, and sets the kernel arguments
  its rings need.

## 2.158.0 — Jabber: one page for new accounts; its admin panel from the VPN only

With kiwi-modules 0.66.0.

- **One page, `/join`, for every way to a Jabber address**: an invite code, a request, and (when
  switched on) the open way, now with a picture test on that page. It works without scripts, so
  also in Tor Browser at its safest. ejabberd's own `/register` page sends people to `/join`.
- **Invite codes and requests by default**, open sign-up off, as for the network's accounts.
- **ejabberd's admin panel answers the VPN only.** It showed its password prompt to the internet
  and over Tor.
- New Jabber settings: files deleted after `file_days` (30), `room_creators` (everyone or
  admins), `admins` (more admins). docs/roles.md says what kiwi-server sets and what the admin
  does in an app or on the panel.
- The control image carries Pillow, for the picture test.
- Dates: what happened on 16 November no longer says the 17th.

## 2.157.0 — fixes from the review of 15–16 November; Tor without a VPN only by choice

With kiwi-modules 0.65.1.

- **No account takeover through the network's accounts.** Forgejo no longer hands an existing
  account to a network account of the same name (it asks for that account's password once), and
  Nextcloud no longer joins a network account to a local user of the same name.
- **Tor never leaves on the home line unasked.** A master at home now counts as at home (the first
  run's "at home" marks it): its hidden services need a VPN exit like any machine at home.
  Without one, `kiwi-server onion` refuses, unless the owner chooses `onion: { without_vpn: true }`;
  then it warns that the internet provider can see Tor.
- **Taking in a starter stick checks who answers.** The stick and kiwi-server prove to each other
  that they know the setup code (it never crosses the network), and only well-formed host keys
  are written down. A device that pretends to be the stick gets nothing and gives nothing.
- **The starter's first run is HTTPS.** The stick makes its own certificate at every start, shows
  the start of its fingerprint on its screen, and sends plain HTTP on to HTTPS. The setup code,
  the page password and the hoster sign-in no longer cross the home network in the clear.
- **The setup code's limit holds.** The control page and the sign-up page believe a forwarded
  address only from the machine's own proxy (`KIWI_PROXY`); a wrong code waits a second without
  stopping the page, and after 50 wrong codes from anywhere the stick stops taking them.
- **fail2ban corrected** (kiwi-modules 0.65.0): wrong logins at Immich and Paperless count again;
  "never ban home addresses" only on machines at home; each jail has its own ban list, so one ban
  ending does not end the others; bans keep reaching nginx after many in a row; nginx closes the
  connection of a banned address; images and other bulk files have a larger allowance instead of
  none.
- A master whose public names are all other machines' keeps its admin pages to the VPN.
- docs/getting-started.md: "What the machines need", measured on the test network.

## 2.156.0 — several machines at once; a machine's History with its tasks

- Machines, "Several at once" (after Foreman): tick machines, then Check them, Preview or Apply,
  one after the other. A machine that fails does not stop the others, and each machine's History
  says how it went for that machine.
- Fixed: a machine's History tab (2.153.0) answered with an error once the machine had a task in
  this session; its tasks now say how long ago they started.

## 2.155.0 — Tor behind a commercial VPN without a master; settings say where they come from

- **Hidden services without a master** (kiwi-modules 0.63.0): Tor must never leave on the home line,
  so a machine in a network without a master can run them when a commercial VPN is its exit (a
  `vpn-client` with `vpn_provider: mullvad`, `protonvpn`, …, in its modules). Tor and downloads
  leave there; the home network is never routed into it. `kiwi-server onion` says so when the exit
  is missing. docs/design/onion.md, no-master.md.
- **A machine's settings say where each value comes from** (after Foreman): "set here" on what the
  machine sets itself, one tick to bring it back to what every machine has, and "Only what NAME
  sets itself" to see just those.

## 2.154.0 — the starter stick, ready to download, and it lets you choose the disk

- Every release carries the starter stick: `kiwi-starter.iso`, its `SHA256SUMS` and a Sigstore
  signature made by the project's build (`.github/workflows/starter.yml`, with the release's own
  control image). The newest is always at
  https://github.com/derlocke-ng/kiwi-server/releases/latest/download/kiwi-starter.iso, so a phone
  or Windows user no longer needs kiwi-server to make one. docs/getting-started.md says how to
  check it.
- A machine with several disks: the stick's page shows them, one button each, and installs on the
  one pressed; the others stay as they are. Before, it said "unplug the others".

## 2.153.0 — nobody banned for using a service; a machine's page in tabs; safer install disks

- **fail2ban as others ship it** (kiwi-modules 0.62.0, compared with fail2ban, Debian, YunoHost,
  mailcow, SWAG, Nextcloud's, Vaultwarden's and Forgejo's guides; on the test network every wrong
  ban came from our own rules): no ban for a bad request (400), SSH counts failed logins only,
  private home addresses are never banned, bans start at 10 minutes and grow for repeat visitors
  (up to a week), a week on every port only after five bans a day, the apps' own paths and bulk
  downloads (WebDAV, previews, thumbnails) are left out of the "refused" jail and the rate limit,
  Forgejo takes ten tries. docs/security.md says it all.
- **Nextcloud's push channel** passes the proxy (kiwi-modules 0.61.3): it answered 400 every two
  minutes, and its apps got their computers banned.
- **A machine's page in tabs** (after The Foreman): Overview, Services (with the logins), History
  (what was done to it, kept across restarts, with how each task ended), Backups, and Settings. On
  top, its parts' state at a glance: set up or not, its last apply, its backup, its fullest disk.
- **Install media never erase a USB disk by themselves** (an idea from KIWI NG): with `disk: auto`
  a disk on USB is not a candidate, on CoreOS and Debian alike, so a backup disk left plugged in is
  safe when the machine's own disk is not seen.
- **The starter stick's page shows why an install stopped**: the installer's last lines, for a
  phone that has no console to read them on.
- **The secret store saves one at a time**: two saves at once could leave the older snapshot on
  disk while it counted as the newer, so a secret written in between (a new device's key) never
  reached `secrets.age`. Found by the test 2.152.1 added, failing once in CI.

## 2.152.4 — looking for a waiting disk no longer gets you banned

- Is a machine waiting at boot for its disk key? kiwi-server asked its SSH port for every kind of
  key (six connections, some of a kind the server lacks). fail2ban there (aggressive) counts those,
  and after a few looks banned the computer that asked: in the power-cut drill of 16 November it
  banned the operator's computer from papaya. Only the kinds of key on record are asked for now.
- The power-cut drill logs in first and scans keys only when that fails. Result of its run on
  16 November: every machine back by itself, papaya, lychee, quince, medlar and lime; the slowest
  (quince) in 2.2 minutes; the control node unlocked by itself; all 37 names answer afterwards.

## 2.152.3 — kiwi-modules 0.61.2; the docs know a network without a master

- kiwi-modules 0.61.2: its file writers have a temporary file per writer too.
- docs/getting-started.md: "No master yet" in the questions, and `add … master --address`.

## 2.152.2 — certificates made one at a time

- The fleet's CA and the machines' certificates are made by one command at a time per CA folder
  (a lock there). Two renders at once (the control node's look, an apply, the page) could make two
  CAs, keep one certificate's key with another's certificate, or stop with "No such file".
- Every file kiwi-server writes whole (the fleet file, a machine's known host keys, a sync's
  files) goes through a temporary file of its own per writer, as the store's do since 2.152.1.

## 2.152.1 — two writers never break the store

- The encrypted store (`secrets.age`), its list of keys and the control node's `fleet.yaml.age`
  were written through one shared temporary file. Two commands saving at the same moment (a backup
  and the control node's own look, say) could leave a store that half of each made, which the
  recovery key no longer opened, or stop with "No such file". Each writer has its own temporary
  file now. Found through a test that failed once in CI.

## 2.152.0 — add a master at netcup from the page

- "Add a master" on Machines (a network without a master) offers "Sign in at netcup and pick it",
  as the first run does: the server picked fills in the address, the fleet knows which server it
  is, and the login is sealed with the network's secrets, so "Install it at netcup" needs no
  second sign-in.

## 2.151.0 — check and access without a master

- `kiwi-server check` in a network without a master asks from the machine that answers for the
  names (its Pi-hole) instead of stopping; `access` says that who may reach what comes with a master,
  and the control page's Home leaves its tile out.

## 2.150.0 — add a master, from the page and the app

- A network without a master: the control page's Machines has "Add a master" (its address on the
  Internet); the machines at home go onto its VPN, as `kiwi-server add FLEET master --address A`
  does. The app's "Add a host" does the same when the new host is a master, and a node added
  there without a master is off the VPN and asks for its fixed address.
- A machine at home added on the page in such a network needs its fixed address; a gateway is refused.

## 2.149.0 — a network without a master (yet)

- The first-run questions (control page, app, `kiwi-server init`) have "No master yet: my services
  open at home only". The fleet then has no master: each machine at home gets `vpn: false` and its
  fixed address, the first one runs Pi-hole and answers for every machine's names with home
  addresses. A gateway is refused (it leads into the VPN). kiwi-modules 0.61.1 brings `vpn: false`
  (the home address and the home subnet stand in for the VPN's, so the control page opens at home).
- `kiwi-server add FLEET node` in such a network writes `vpn: false`; `kiwi-server add FLEET master
  --address A` puts every machine at home back onto the VPN, the fleet file's comments kept.
- Drill: `tests/drills/starter-vm.sh alone` (the starter in a local VM, without a master).
- A list written as `- item` lines at its key's indent is replaced in the text now (fleettext).
- Without a master, the page and the app no longer say "the master first", and instead of putting
  a phone on the VPN they say how a phone finds the services at home. Design and what is still to
  build (adding a master later): docs/design/no-master.md.

## 2.148.0 — the status page from the app

- Under the message settings (Defaults), next to "Send a test message": "Open the status page", the
  control node's page of what everyone on the network sees without a login, as the control page
  links it there.

## 2.147.0 — the control node's Activity in the app

- The app's menu has "Activity…": the control node's newest tasks and who did what, as on its
  page. In a terminal: `kiwi-server control activity FLEET`. A computer that joined asks with its
  own key (`/api/activity`); the answer holds names and times, no output and no secret.

## 2.146.0 — sign in at the hoster from the control page's first run

- A machine with nothing on it: its questions now offer "Sign in and pick it" for a master at netcup,
  as the app's wizard does. The setup code guards it; the sign-in at netcup runs on the machine (you
  confirm it in a new tab), the account's servers are listed, and the one picked fills in the
  address. Its login goes into the new network's sealed secrets (`secrets/netcup.token`), so the
  master can be installed from the page without signing in again; nothing of it stays beside them.

## 2.145.0 — the role script on the control page

- A machine's page links to "The role script": what Apply sends to it and runs there, made on
  request in the open store's memory and shown as text, as the app's "Show role script" does. It
  holds the machine's secrets: it is written nowhere, and the activity list says it was shown.

## 2.144.0 — prune, start new and the tools check on the control page

- Backups: "Prune now" on each machine that keeps backups (with its free space), and "Start new"
  on a rebuilt machine whose backups wait, next to "Bring something back". The app had both.
- Help: "Check them" runs `kiwi-server doctor` on the control node itself, as the app's Doctor does.

## 2.143.0 — let a key in from the control page

- Computers, "Let a key in for a while": the control page makes the network's SSH certificate
  authority and signs a pasted public key for 1 to 365 days; the certificate is shown once, to save
  next to the key (`id_ed25519-cert.pub`). The key passes through RAM only; each one let in is in
  the activity list. The app (SSH certificates…) and `kiwi-server sshca` did this already.

## 2.142.0 — leave a machine out of a replacement, in the app

- The app's "A computer was lost or stolen…" says which machines still wait on the old keys or secrets
  and offers "Leave NAME out" for each (`rotate --waive`), with the control page's warning first.

## 2.141.0 — take in a waiting starter from the app or a terminal

- `kiwi-server take-in FLEET ADDRESS --code CODE --preset P [--name N]`, and in the app's menu
  "Take in a waiting machine…": a machine started from the starter stick, waiting at home with an
  address and a setup code on its screen, joins the network and is set up, as the control page's
  "A machine with the starter stick is waiting" does. The handshake (the code, the network's key,
  the machine's host keys) and the fleet file's new machine are one code path for all three.

## 2.140.0 — a test message from the app

- The app's settings, Defaults, "Messages when something is wrong": **Send a test message** sends one
  on every channel that is set, as the control page's button does. Unsaved settings are saved first.

## 2.139.4 — the docs' commands are checked

- A test reads every `kiwi-server …` line in the README and docs/ and checks that the command
  exists and takes the flags shown (57 lines today, all right). The docs are what a newcomer types.
- kiwi-modules 0.60.4: the games catalog says all ten games were started from it and listened.

## 2.139.3 — Minecraft Bedrock as it runs now

- kiwi-modules 0.60.3: Bedrock's catalog entry follows its current networking (TCP 19132 and a UDP
  game port) and can write its folder.
- Drill `tests/drills/game-images.py`: starts games of the catalog on this computer and checks that
  they listen. Terraria, Factorio, Bedrock and Valheim pass.

## 2.139.2 — whole files after a power cut during an apply

- kiwi-stack 0.9.9: each file an apply replaces on a machine is on the disk before it takes the
  old one's place. A power cut in the middle of an apply leaves the old file or the new one, never
  an empty one.

## 2.139.1 — connecting a service works; a silent stop is called one

- `accounts connect` (kiwi-modules 0.60.2): the accounts script lost Kanidm's address after the first
  callback and stopped; Immich's three callbacks found it.
- A command on a machine that stops without a message is no longer reported as "ssh failed": it says
  that it stopped, with its exit status.

## 2.139.0 — Connect in the app; connecting works again

- The app's Accounts… has "Services that sign people in with these accounts", with Connect, as the
  control page has. A test keeps the app, the page and `accounts connect` offering the same services.
- `accounts connect` failed for every service since 2.138.0 (kiwi-modules 0.60.1: the accounts
  script read its words one place off); a client Kanidm refuses is now said, not swallowed.
- README: which services the accounts sign people in to.

## 2.138.0 — Immich signs people in with the network's accounts

- `kiwi-server accounts FLEET connect HOST immich`, and Connect on the Accounts page: Immich
  (kiwi-modules 0.60.0) gets "Sign in with accounts" in its own settings; its admin page stays
  editable, and the phone app signs in the same way. A service can now have several callbacks, and
  Kanidm keeps checking PKCE for a service that sends it (Immich).
- Drills: `sso-browser.py --immich`; `--vault` types the email Vaultwarden asks first (a hint only).

## 2.137.0 — Vaultwarden signs people in with the network's accounts

- `kiwi-server accounts FLEET connect HOST vault`, and Connect on the Accounts page: Vaultwarden
  (kiwi-modules 0.59.0) offers "Use single sign-on". The person still sets a master password; a vault
  user made earlier with an email and password is never taken over by a sign-on.
- Paperless: a person from the network's accounts lands in the group `accounts` and can use it;
  before, the new user could do nothing (kiwi-modules 0.59.1).
- Drills: `sso-browser.py --vault`; `--paperless` clicks Paperless' "accounts" button.

## 2.136.0 — Paperless signs people in with the network's accounts; passwords over Tor work

- `kiwi-server accounts FLEET connect HOST paperless`, and Connect on the Accounts page: Paperless
  (kiwi-modules 0.58.0) offers "Sign in with accounts" after the next apply of that machine.
- Over Tor, setting the password at the accounts' page failed with 403: the browser dropped Kanidm's
  cookie, made for the usual name. Hidden services now hand cookies the hidden address (kiwi-modules
  0.58.1). The sign-up page over Tor offers a password only: a passkey belongs to the usual name.
- Drills: `sso-browser.py --paperless`; `join-tor.py` waits as long as Tor needs and shows where it
  stopped.
















## 2.135.0 — signing up over Tor

- The accounts as a hidden service (`kiwi-server onion <fleet> id.<machine> --anyway`): someone who
  comes to the sign-up page over Tor gets a password link and a way to sign in that stay on Tor
  (kiwi-modules 0.57.0, `kiwi-server signup --id-onion`).

## 2.134.0 — Accounts in the app

- **The app has Accounts…** in its menu, as the control page has: the requests to accept or deny, invite
  codes to make and withdraw, the accounts with a new password link or deletion; for the network's
  accounts and for Jabber's sign-up page.
- Jabber's sign-up page passed its drill on the test network: an invite code on the public name with a
  chosen password, a request on the VPN's name accepted with a password shown once, both taken by ejabberd.

## 2.133.0 — Nextcloud signs people in with the network's accounts

- **`kiwi-server accounts <fleet> connect <host> cloud`** (and Connect under Accounts on the control
  page): after the next apply, Nextcloud shows "Log in with accounts" and a person's first sign-in
  makes their Nextcloud user (kiwi-modules 0.56.x, Nextcloud's own OpenID Connect app). The admin and
  people made in Nextcloud keep their own passwords.
- Drill `sso-browser.py … --nextcloud`: a real browser signs in to Nextcloud with an account (on the
  test network: Kanidm on one node, Nextcloud on another).

## 2.132.0 — where to change what

- **Services** on the control page: under each service, **Settings** opens its module's part of
  the machine's settings (what kiwi-server sets: names, who may sign up, sizes, switches), and
  **Admin page** opens the app's own admin page (what is inside it: users, rooms, repositories).
- A Jabber password made for an accepted request is easy to read out: four groups of five, no 0/O
  or 1/l. Jabber's sign-up page has the server's name (kiwi-modules 0.55.2).

## 2.131.1 — a command word with spaces

- kiwi-modules 0.55.1: each word of a container's command is quoted in its systemd unit; Jabber's
  sign-up container did not start without it. Drill `jabber-join-browser.py`.

## 2.131.0 — Jabber's sign-up page

- **Jabber addresses with invite codes or by asking** (kiwi-modules 0.55.0, `xmpp: { invites: true,
  requests: true }`), beside the captcha or instead of it. The page is `kiwi-server signup` again,
  making Jabber addresses over ejabberd's API: with an invite code the person chooses name and
  password; an accepted request gets a password, shown once.
- The control page's **Accounts** has a Jabber section: its requests to accept or deny, its invite
  codes. `kiwi-server accounts <fleet> --jabber invite|requests|accept|deny …` from a terminal.

## 2.130.2 — start pages without doubles

- A VPN name that sends on to a public name is no service of its own: it is no longer checked,
  listed or shown twice on the start pages.
- The status page is the button at the top of the start pages, not a tile.

## 2.130.1 — a sentence with a colon

- kiwi-modules 0.54.1: the network's words on the start pages may hold ": ".

## 2.130.0 — start pages, inside and out

- **The network's start page** at `status.<control node>` (kiwi-modules 0.54.0): every service people
  use, as a tile with what it is for, its address, its Tor address, whether it works and its last
  30 days. "Get an account" when the network has the accounts module. The status page moved to
  `/status` on the same name.
- **A front page for strangers** at the public status name: the services open to the Internet,
  their web and Tor addresses, whether they work, how to get an account, and the network's own
  name, colour, words and contact (`control: { brand_name, brand_accent, about, contact }`).
- Both pages read only what the control node writes down at every check, so they work while the
  fleet is locked.

## 2.129.0 — Forgejo signs people in with the network's accounts

- **`kiwi-server accounts <fleet> connect <host> git`** (and Connect on the control page's
  Accounts): Forgejo on that machine shows "Sign in with accounts" after its next apply, and a
  person's first sign-in makes their user there (kiwi-modules 0.53.0). The service's secret is kept
  in the fleet's store.
- Drill `sso-browser.py`: in a real browser, a person sets a password with their link, chooses
  "Sign in with accounts" in Forgejo, signs in at the accounts' page and is back in Forgejo as
  themself (on the test network: Kanidm on one node, Forgejo on another).

## 2.128.0 — the network's accounts on the control page

- **Accounts** on the control page: the requests to accept or deny, invite codes to make and
  withdraw, the accounts with a new password link or deletion. `kiwi-server accounts <fleet>`
  does the same from a terminal (`invite`, `requests`, `accept`, `deny`, `create`, `link`,
  `list`, `delete`, `jabber`).
- The sign-up page remembers where Kanidm is, so the control node's calls need only its folder.
- Drill `join-browser.py`: in a real browser, a stranger signs up with an invite code and by
  asking, sets a password with the link and signs in (8 of 8 on a test node).

## 2.127.0 — accounts with a sign-up page, the look of monitoring tools, one name per service

- **Accounts for the public** (kiwi-modules 0.52.0, module `accounts`, [Accounts](docs/roles.md#accounts)):
  Kanidm keeps the network's people; `kiwi-server signup` is its sign-up page at `/join`, with
  invite codes and requests the people who run the network accept, and a link to set the password
  instead of an email. Chosen over Authentik after a test on a node: 59 MB against 800
  (docs/design/public-services.md).
- **The pages look like monitoring tools now** (as Checkmate does): a darker, quieter palette,
  small capitals over the numbers, the services as a table with each one's last 90 days, how much of
  the time it worked, how fast it answers and where it is open (VPN, Internet, Tor).
- **A service with a public name keeps its VPN name**, which sends the browser on to the public one.
- Drills: `accounts-spike.sh`, `accounts-browser.py` (a real browser sets a password and signs in).

## 2.126.0 — a status page like the others, every VPN provider

- **The status page shows each service's last 90 days**: a bar a day, green when it worked all
  day, yellow with gaps, red when it was down most of it, and how much of the time it worked,
  as status pages show it. It says how fast each service answers and how long it has worked;
  what was wrong is a list of cards. On a phone it shows 30 days.
- **Every VPN provider gluetun runs with WireGuard as the master's exit** (kiwi-modules 0.51.0):
  Mullvad, Proton VPN, NordVPN, Surfshark, AirVPN, Windscribe, IVPN, FastestVPN, and any other
  provider's WireGuard config. The first run on the page and in the app offers them all and asks
  each for the lines it needs. Proton VPN and NordVPN were refused before: they give no address.

## 2.125.0 — fail2ban done the way the apps document it

- **Wrong logins, per app** (kiwi-modules 0.50.0): fail2ban reads Forgejo's, Vaultwarden's and
  Nextcloud's own logs for wrong logins and bans who keeps on (Jabber counts its own). A module
  says what a wrong login looks like: module.yaml `fail2ban:` ([Modules](docs/modules.md#fail2ban)).
- **Never for loading a page**: a page's own files no longer count against the rate limit, and
  only a flood is banned. One load of a busy page had locked a home out for an hour.
- **Bans that work on a node behind the master**: there a ban is nginx's (403); a firewall rule
  never saw the visitor. A node whose sites strangers reach gets fail2ban by itself, and the
  master tells it the network's own addresses, which it never bans.
- A master's port 80 answers the mesh again (they left by its internet interface).
- `tests/drills/fail2ban.py`: from a stranger's address (a Tor exit, through a Tor on the master
  for the drill only), a page with 150 files is neither refused nor banned, a scanner is banned
  and let out again, and an app's wrong logins reach its jail.

## 2.124.1 — a master never bans its own network

- **fail2ban on a master leaves the network's own addresses alone** (kiwi-modules 0.49.1): every
  minute it reads where the machines' VPN tunnels come from — a home's public address — and no
  jail bans those. Before, loading a busy public page a few times from home locked the whole home
  out of the public names for an hour.

## 2.124.0 — the app: plain names, less in view, a picture of the network as it is

After the owner's look at the app ("slow, bloated, the settings, the module names, how
they are linked"):

- **Plain names** (kiwi-modules 0.49.0): the modules are called what they do (*Files
  (Nextcloud)*, *Ad blocker (Pi-hole)*, *VPN connection*, *Web addresses (nginx)*…), the
  roles *Master*, *Home server*, *Bare machine* instead of kiwi-master, kiwi-node.
- **Settings, less in view**:
  - what kiwi-server fills in itself (27 settings) is no longer shown as something to type;
  - what is seldom changed (images, ports, certificate files) is folded under *More*;
  - a machine's install-only settings (target, disks, keyboard, language) are folded under
    *For its next install*;
  - Admin, uCore, CoreOS, Debian and the post script are one page, *Advanced*: the list
    beside the settings has 3 system entries instead of 7.
- **"How they are linked"**, in the app and on the control page: the master out on the
  Internet, the machines *At home*, a tunnel from the master to each (green while it
  answers, dashed when not); with many machines and little room, a list.
- **Quicker**: "Who may reach what" opens at once and reads its rules beside the window
  (it waited about a second before); the app asks for its roles and targets at the same
  time when it starts.

## 2.123.1 — Jabber's accounts stay with their name

- kiwi-modules 0.48.1: switching Jabber between one service and two keeps every account
  (before, the public service started without the accounts of its name). Found by the
  owner: his account on the public name could not log in, and his client's tries got a
  Tor exit banned for an hour ("Policy Violation").
- New drill: `tests/drills/onion-browser.py` opens hidden services in a real Firefox, told
  what Tor Browser is told about `.onion` addresses, through Tor on the master; with
  `--login`, logs in with the network's logins.

## 2.123.0 — a second starter machine, taken in from the control page

- **More machines from the starter stick**: start a machine from the stick on the home
  network; its screen shows its address and setup code. On the control page, Machines,
  "A machine with the starter stick is waiting": type both and say what it does. The
  control node hands it the network's key (the code is the proof that you are at that
  machine), keeps its SSH host keys, adds it to the network with the address it has, and
  sets it up; the starter's page and key go away. [control.md](docs/control.md).
- The starter's page answers `POST /api/join-me` (once, with its setup code), and shows
  "This machine joins …" from then on instead of the questions for a new network.
- `kiwi-starter-done joined`: on a machine without a control part of the network's own,
  the starter's control container goes too.
- Tried: unit tests on both sides; a home power cut of the test network (every machine
  back by itself in 2.3 minutes, the control node unlocked by itself).

## 2.122.0 — a game server open to the Internet, through the master

- **`public: true` at a game server** (kiwi-modules 0.48.0): players outside your VPN join
  at the master's address; the master hands the game's ports on to the machine, TCP and
  UDP. Nothing is opened at home. [roles.md](docs/roles.md#game-servers).
- kiwi-server tells the master about a node's ports whether or not the node has a public
  web site, and with their protocol.
- New drill: `tests/drills/game-ports.py` asks a game server the way its client does.
  Tried on the test network: Minecraft (TCP) and Zandronum (UDP) answer at the master's
  public address.

## 2.121.2 — the logins drill and BTCPay

- `tests/drills/logins.py` tries BTCPay through its login page. Its old check (Basic
  authentication) only passed in the first five minutes of an account: since 2.4.2 BTCPay
  switches that off afterwards, and a login that works was called wrong. An account that
  was changed inside BTCPay (its password, two-factor login) is reported as not decided.
- After 2.116.0 to 2.121.1 changed the master's port 443, the access drill was run again:
  203 tries, none other than the rules say; and the logins drill: every login the network
  keeps works.

## 2.121.1 — logins through Tor, tried

- `tests/drills/onion-sites.py --logins` logs in through Tor by a service's own API where
  the network has a login for it. Tried: Nextcloud (its files open, WebDAV), Forgejo and
  Paperless (their APIs take the login), Immich (its login answers; nobody has an
  account there to log in with). [network.md](docs/network.md#which-services) says for
  each marked site what was tried.

## 2.121.0 — a computer at home finds the control node; the master's door counts connections

- **A computer at home that is not on the VPN** stays in step with the control node: when
  the control page's name does not open, kiwi-server reaches the machine at its fixed
  address at home (from the fleet file), its certificate still checked against the
  fleet's CA. Before, such a computer could not sync, and the control node went on with
  an older copy of the network.
- **The master's door counts connections** (kiwi-modules 0.47.1): one address may hold
  four times `rate_limit` connections at once at 443 and the handed-on ports. What the
  master hands on to a node passes the node's limits, which cannot keep an address out.
- Jabber's admin password may start with a dash.
- Tried on the test network: the public status page (`status.dlhost.eu`: the five services
  open to the Internet, asked at the master's address) and the pastebin on a node under
  a public name (`paste.dlhost.eu`), both from the Internet.

## 2.120.0 — hidden services reworked, an authenticator, a public status page

- **Hidden services** (kiwi-modules 0.47.0), after seventeen of them were opened
  through Tor on the test network:
  - **Only sites that work there are offered.** A module marks the sites that were tried
    through Tor; `kiwi-server onion`, the control page and the app offer those. Marked:
    Git, BTCPay, Jabber, the pastebin, the hub, the relay, Nextcloud, Immich, Paperless,
    the status page. `--anyway` makes one of another site; one that existed stays, with
    a warning. [network.md](docs/network.md#which-services) has the table.
  - Fixed: a redirect at a hidden service named a port Tor does not carry (BTCPay's
    login); Nextcloud as a hidden service made nginx refuse the machine's whole config;
    Paperless refused a login sent from its `.onion` address.
  - **https at the address**: `onion: { tls: true }`. The certificate is the fleet CA's,
    or the machine's own when the CA is limited to the fleet's names.
- **Authenticator** (new module `authenticator`): 2FAuth at `auth.<machine>`, the codes
  of your two-factor logins in a page of your own, for the VPN only.
  [roles.md](docs/roles.md#authenticator).
- **A public status page**: `control: { public_status_hostname: status.example.org }`
  shows strangers the services that are open to the Internet, and only those. A public
  name is asked at the master's address, the way a stranger reaches it.
- New drills: `nginx-check.py` (does a machine's proxy config load? Before every apply
  that changes a proxy) and `onion-sites.py` (every hidden service through Tor).
- Not tried: an account and a code in the authenticator; logging in to Nextcloud and
  Immich through Tor (their pages and what their apps are told were).

## 2.119.0 — two Jabber services on a node, tried; two gaps closed

- **A hidden service for a public site**: `onion: { services: { xmpp: {} } }` was refused
  ("it has no site called xmpp") because only names under the machine's own name counted
  as its sites. A public name counts now, by what its module calls the site; `kiwi-server
  onion --porcelain` gives its full name.
- **Jabber at home** (kiwi-modules 0.46.2): the network's DNS at home answered Jabber's
  names with the machine's home address, where Jabber's ports are closed (they are at its
  VPN address). Such names keep their VPN address now.
- Tried on the test node, two services apart (`separate: true`): the public one from the
  Internet (5222, 5223, the page, its captcha) and through Tor; the internal one through
  Tor and its page. [roles.md](docs/roles.md#jabber).
- New drill: `tests/drills/onion-sites.py` opens every hidden service through Tor and says
  whether the page and its files stay at the address.

## 2.118.0 — hidden-service addresses in the app

- **The app shows a service's hidden-service address** where the service is: on a
  machine's page, under *Open a service*, with who may open it and a button that copies
  it. Before, it was only in the dialog *Who may reach what*.
- Services are named in words there too (*Files (Nextcloud)*), also when they have no
  login: `kiwi-server list --porcelain` carries the names (`site_labels`), the same ones
  the control page uses.
- The app's preview drill runs on a session bus of its own: started beside a running Kiwi
  Server, it handed its made-up network to that window instead of starting.

## 2.117.0 — the control page, grouped; hidden services where the services are

- **The page's menu is grouped**, after a look at how The Foreman lays out its pages:
  Home, Machines and Services first; then *Access* (Devices, Who may reach what, Linked
  networks), *Safety* (Backups, If something breaks, Computers) and *More*.
- **Home** shows what needs a look, the numbers and the picture of the network. The
  machines moved to **Machines** (their cards, Add a machine, Update everything) and
  the services to **Services**.
- **Services**: every service under its machine, with a dot that says whether it
  answered, its login, a mark when it is open to the Internet, and its **hidden-service
  address** with a button that copies it. Before, a `.onion` address was only to be found
  under Who may reach what.

## 2.116.0 — a node's service open to the Internet, through the master

- **A service on a node can have a public name** (kiwi-modules 0.46.1): give it one,
  point the name at the master, and set `reverse-proxy: { public: true }` on the node.
  The master hands the name on through the VPN, still encrypted; the node holds the
  Let's Encrypt certificate and sees each visitor's own address. Nothing is opened at
  home. kiwi-server tells the master which names and ports of which node to hand on.
  [roles.md](docs/roles.md#a-nodes-service-open-to-the-internet).
- **Jabber runs on a node**: the service, its accounts and its certificate stay there;
  the master hands the public name and Jabber's ports on. Tried with a new drill,
  `tests/drills/jabber-doors.py`: logins from the Internet, the VPN and Tor with one
  service, the page for new accounts with its captcha through all three.
- **Connect this computer** when a machine of the network has the computer's name: the
  computer gets a name of its own on the VPN (`lime-pc`) instead of an error the app had
  no answer to.
- A service removed from a machine leaves no failed unit behind (kiwi-stack 0.9.8).

## 2.115.0 — add a computer from the app

- **Add a computer** in the app: menu, **Use another computer…**, **Add a computer**
  makes the invite link itself (`kiwi-server control invite <fleet>`), with a button
  that copies it and a QR code. Before, only the control page could make it.
- The control page shows the link as a QR code too.
- **Join** in the app fills in an invite link that was copied a moment ago.

## 2.114.0 — Firefox Sync

- **Firefox Sync** (kiwi-modules 0.45.0): add `firefox-sync` to a machine and your
  Firefox browsers keep what they sync on it: Mozilla's own sync server with a database,
  at `https://ffsync.<machine>`. The login still goes through a Mozilla account.
  [roles.md](docs/roles.md#firefox-sync) says what to set in Firefox.
- The pastebin was tried through a fleet: a paste stored and read back over the VPN.

## 2.113.0 — a pastebin, rate limits, and fail2ban that acts on them

- **Pastebin** (kiwi-modules 0.44.0): add `paste` to a machine: PrivateBin at
  `https://paste.<machine>`; public with `paste_domain` on a master, a hidden service
  with `onion: { services: { paste: {} } }`. [roles.md](docs/roles.md#pastebin).
- **Rate limits** on sites that strangers reach (public names, hidden services):
  20 requests a second from one address, 80 at once, then "429"; `rate_limit` in the
  reverse proxy's settings. Sites of the VPN are not limited.
- **fail2ban** on a master with public sites also bans who keeps hitting the rate
  limit and who is refused twenty times; who is banned three times in a day is out for
  a week (every machine). A machine's page and `kiwi-server status` say how many
  addresses are kept out right now. [security.md](docs/security.md).
- Tried on the test master: a flood from another server got answers, then 429, then a
  ban, which was lifted afterwards.

## 2.112.0 — Jabber: a registration page, a captcha, three doors

- **Jabber moved to ejabberd** (kiwi-modules 0.43.0), for what was asked: people make
  their own account in the app or at `https://<name>/register`, each after a captcha.
  A server set up with 2.110 or 2.111 starts empty (its accounts were Prosody's), and
  its name on the VPN is `jabber.<master>` now, not `chat.`.
- **One service, three doors**: the VPN (`jabber.<master>`), the Internet
  (`xmpp_domain`) and Tor (`onion: { services: { jabber: {} } }`: the page and port 5222
  at the hidden address, and accounts `@<address>.onion`). Or two apart with
  `separate: true`: an internal and a public service that exchange no messages.
  [roles.md](docs/roles.md#jabber).
- Hidden services: what a page links to under its usual name leads back to the hidden
  address. `tests/drills/onion.py` also asks a Jabber hidden service on its own port.
- Tried on the test master: login over the VPN, the captcha on the page and in the
  app's form, the page and port 5222 through Tor. Not tried: a public name (the test
  name still points at a web proxy), two apart, an app.

## 2.111.0 — payments with BTCPay Server

- **BTCPay Server** (kiwi-modules 0.42.0): add `btcpay` to a node and take Bitcoin
  payments yourself, with its own Bitcoin node, invoices and a point-of-sale page as a
  shop. `https://pay.<machine>` on the VPN; with `onion: { services: { pay: {} } }` a
  hidden service for strangers. The node reaches the Bitcoin network through the VPN.
  The admin's login is in `kiwi-server logins`. `network: regtest` tries it on a chain
  of one's own. [roles.md](docs/roles.md#payments-btcpay-server).
- Tried on the test network in regtest: the admin logs in over the VPN (the logins
  drill tries BTCPay's login now), an invoice was paid and settled, and the hidden
  service opened through Tor from the master (`tests/drills/onion.py`). Not tried:
  mainnet's first sync.
- The status page and Home call the site by its name.

## 2.110.0 — Jabber, and game servers tried through a fleet

- **Jabber** (kiwi-modules 0.41.1): add `xmpp` to a master and your people have a
  Jabber (XMPP) server: chats, rooms, files, history across devices. For the VPN by
  default (`<name>@chat.<master>`); with a public name it is open to the Internet and
  reaches other servers. The admin's account is in `kiwi-server logins`; everyone else
  comes by invitation from the admin's app. [roles.md](docs/roles.md#jabber).
  Tried on the test master: the admin logs in from a device on the VPN (the logins
  drill now tries a Jabber login). Not tried: a public name, an app, rooms, files.
- **Game servers were run through a fleet**: a Minecraft and a Zandronum server on a
  test machine. Minecraft answered at the machine's address at home and at its VPN
  address.
- The status page and Home call the Jabber site by its name.

## 2.109.0 — game servers, and the messages tried

- **Game servers** (kiwi-modules 0.40.0): add `games` to a node and list the servers:
  Minecraft (Java and Bedrock), Enshrouded, Abiotic Factor, Zandronum, Valheim,
  Factorio, Satisfactory, Palworld, Terraria, or any other from an image. One
  container and one folder per server; players connect at home or from the VPN, where
  a group can be given one server's port and nothing else. Worlds are in the backup.
  [roles.md](docs/roles.md#game-servers) has the settings.
- **The messages were tried for real** (`tests/drills/notify.py`): a service stopped on
  a machine that stayed fine. The status page said so, a push message and a webhook
  post came after the second look, and "answers again" came when it was started.
- A service that does not answer is asked once more before it counts, and while one is
  down the control node looks again after three minutes, not ten: the drill showed one
  lost question keeping a service "down" for a look longer than it was.

## 2.108.0 — a status page for everyone, and messages

- **Every service is asked for a page.** After each look at the machines the control
  node asks every service over HTTPS, as a device on the network does. A machine that
  says all its containers run while a service answers nobody is found that way. Home
  lists such a service under what needs a look.
- **The status page**: `https://status.<host>`, without a login, for everyone who may
  reach the node's web pages. Which services work, since when one does not, how many
  machines answer, the update window, the last seven days. No admin page, no address,
  no setting. It also answers while the node's keys are locked.
- **Messages**: by mail, as a push message (ntfy) and to a webhook (an SMS gateway, a
  chat room), when something has been wrong for ten minutes and when it is fine again:
  machines, services, problems a machine reports, and what the control node did by
  itself. `notify:` in the settings for every machine; "Send a test message" on the
  settings page, `kiwi-server notify <fleet> --test` in a terminal. `heartbeat` asks an
  address after every look, so a service outside can say when the network falls silent.
- **Nextcloud survives a power cut** (kiwi-modules 0.39.0): its database lost its
  settings file in a power-cut drill, listened on 127.0.0.1 only, and Nextcloud waited
  for ever with every container "healthy". The settings are put back at the next start.
  Apply the machine to get the fix.

## 2.107.1 — the starter finds itself, and the whole run is tried

- **The starter did not list "This machine".** The control image has no `ip` command,
  so the first-run page on a starter could not tell its own address and offered an
  ordinary machine to install. It reads the kernel's own table now. Found by the first
  whole run.
- **Tried for real** (`tests/drills/starter-vm.sh first-node`): the starter in a virtual
  machine, a new network from its page, the master's install media made on the machine
  and installed on a rented server, the master set up from the page, then "Set it up"
  for this machine. It became the node in under a minute, the starter's key, port 80
  and screen message were gone, it reached the master over the VPN, and after a hard
  reset it came back as the node at its fixed address. A quarter of an hour in all.
- `starter-vm.sh code` puts a checkout's code into the VM's control part, for trying
  what is not released.

## 2.107.0 — the starter's machine becomes the first node

- **"This machine" in the questions.** On a machine the starter stick installed, the
  first-run page lists that machine as the first one at home: it keeps the address the
  router gave it, nothing is installed again, and it carries the control part.
- **"Set it up" without install media.** What an install would have written is put in
  place with the starter key (`kiwi-server adopt` now also takes a machine that runs
  Fedora CoreOS: `--as core@address --key FILE`), then the role runs as a unit of the
  machine itself. When it is in, the starter takes its own things away (its key, port
  80, the message on the screen) and the control page moves to
  `https://control.<name>.<domain>`, on the VPN.
- The starter keeps the fleet where a node's control part keeps it, so the node it
  becomes finds it there. A starter image made before this version keeps it elsewhere:
  make the image again.
- Tested in parts (the takeover script, the questions, the page). **Not done yet**: the
  whole run on one machine with a master. Until then, treat it as untried.
- Tried on the test network: the master's server and the whole home off together. The
  home was back by itself after 7 minutes, the master, restarted and unlocked by the
  control node, after 12.

## 2.106.0 — the app shows the network as a picture

- **The app's overview has the picture too**: "How they are linked" draws the master
  and a line to every other machine, green when the machine answers, grey and broken
  when it does not. A click on a machine opens it. Narrow windows get it as a list.
- Every machine's card says what it is for the others (control node, gateway, keeps
  backups) and shows its processor's load beside memory and disks. A new number on
  top: how old the oldest last backup is, and whose it is.
- `tests/drills/app-preview.sh` shows the app in a browser with a made-up network,
  the same one `page-preview.py` uses (`madeup.py`).

## 2.105.0 — the control page's new look

- **A menu at the side, numbers on top.** On a computer the control page has its menu
  at the side, with how the machines are below it; on a phone a bar at the bottom, the
  rest under "More". Home starts with four numbers: machines on, services running, the
  oldest last backup, devices on the VPN.
- **Every machine as a card**: what it is (control node, gateway, keeps backups), how
  busy its processor is, how full its memory and its fullest disk are, and its first
  services. A machine's own page shows the same as numbers.
- **A picture of the network**: the master, and a line to every other machine, green
  when the machine answers and grey and broken when it does not. Drawn by the node
  itself, as a list on a phone.
- **Light and dark**: the page follows the device, and the moon button switches it.
  Nothing is fetched from elsewhere (no fonts, no scripts), so the page looks the same
  with the Internet down.
- `tests/drills/page-preview.py` shows the page on this computer with made-up
  machines: for looking at a change without a real network.
- The starter stick's test checks its scripts with bash, which they are written in.

## 2.104.0 — the starter stick

- **`kiwi-server build --starter`** makes one install image with no secret and no fleet
  in it: for a USB stick, for people with a phone or a Windows PC and no Linux computer.
  It installs to the one blank disk it finds, takes its address from the router,
  answers to `kiwi.local`, and shows on its screen where to open its first-run page:
  the address, a setup code and a QR code.
- **It asks before it erases.** A disk that holds something (the stick says what:
  Windows, Linux, macOS, data) is erased only after a yes: `ERASE` typed at the machine,
  or one button on a small page at `kiwi.local`. Several disks, or a disk under 16 GB:
  nothing is installed. A machine that is installed already is started, not installed
  again, also with the stick left in.
- Tried in virtual machines (`tests/drills/starter-vm.sh`): a blank disk, the same
  machine with the stick left in, two disks, a small disk, a disk with a Windows layout
  answered by the button and by typing. Not tried: real hardware, Secure Boot, and
  `kiwi.local` from another device on a real network.
- A control node whose copy is damaged, or that was installed anew, no longer knows the
  computers that sync with it: `control push` says how to bring this computer's copy
  there over SSH, and so does the node's page.
- kiwi-stack 0.9.7: a machine forces what an apply wrote to its disk.
- Not yet: the stick's machine as the network's first node by one button, and the
  sign-in at the hoster on the page ([design](docs/design/starter.md)).

## 2.103.0 — a power cut right after a change

- **The network's files survive a power cut in the moment they are written.** Found by
  the power-cut drill: a minute after a push to the control node the power went out, and
  when the node was back its whole copy of the network was there as empty files (the
  encrypted store, the fleet file, the federation file, the recipients, the salt). The
  node could not be opened, by its password or by its ring. Nothing forced the new files
  to the disk before they took their names. Every file kiwi-server writes is now forced
  to the disk first, and its folder after: the store, the fleet file, what a push or a
  sync brings, the state files. Update the control node (`apply <its machine> --update`).
- A control node whose copy is damaged says so on its login page, with what to do,
  instead of a lock that no password opens. `/healthz` carries `damaged`.
- The power-cut drill waits for the control node to open by itself (it asks its ring
  every half minute).
- Tried on the test home with a second machine that starts without a ring (a ring on
  medlar besides lime) and a second ring for every encrypted machine: with lime's ring
  away, all four machines were back by themselves in under three minutes.

## 2.102.0 — who starts whom after a power cut

- **"If something breaks" says what a power cut would do.** After a power cut every
  machine starts at once, and a machine whose disk opens through a ring waits for the
  machine with the ring. The report now follows that chain: it names machines that wait
  for each other and would stay locked, and the one machine everything hangs on ("every
  machine comes back by itself, as long as lime starts too. If it is off or broken then,
  lychee, papaya and quince stay locked").
- Tried on the test home, both ways: with every machine, all five were back by
  themselves in three minutes; with the one machine's ring away that needs no ring
  itself, the three encrypted machines stayed locked, and were up 11, 32 and 52 seconds
  after it was back.
- **Save its data first.** A machine that still answers and is installed anew on purpose
  gets back what its last nightly backup holds. "This machine is lost or was replaced"
  now starts with "Save its data now" (page and app) when the machine has backups and
  still answers.

## 2.101.0 — a power cut

- **`tests/drills/power-cut.py`** cuts the power of a whole home: every machine off in
  the same moment, without a clean shutdown, and on again together. On the test home
  (five machines, three with encrypted disks whose rings are on each other) four were
  back by themselves within two minutes, in the right order: the board without disk
  encryption first, then the machine its ring opens, then the one that machine's ring
  opens. The control node opened by itself.
- **The unlock at boot gives a silent ring seconds, not minutes.** Its request to a ring
  had no time limit: a ring machine that was still starting and swallowed the request
  held the unlock up for 133 seconds a round. Measured on a real machine: now it asks
  again every six seconds and was up seven seconds after its rings answered. Debian and
  uCore; from the next boot after an apply.
- The hardware watchdog is active before the disk is open too (uCore): a machine that
  hangs while it waits at boot is reset.
- [docs/design/starter.md](docs/design/starter.md): a network from a phone. One image
  for a USB stick with no secret in it, the first-run page in the phone's browser.

## 2.100.1 — an apply waits for the system's own update check

- On uCore an apply failed with "You can cancel the current transaction with
  `rpm-ostree cancel`" when the system's automatic update check was at work, as it is
  on a machine that was just installed. The step that prepares SSH for the unlock at
  boot now waits for it, ten minutes at most. Nothing on the machine was wrong; the
  apply only said so.

## 2.100.0 — a restart does not take a machine away

Found in one night, with a machine being installed anew while the others ran:

- **No restart for updates while the unlock rings do not answer.** A machine whose
  disk opens through rings asks them before it restarts in the update window. When too
  few answer, it stays up and tries again in the next window. Before, a machine
  restarted for a kernel update while the machine with its only ring was off, and
  waited locked until someone typed its key. Comes with the next apply
  (`/usr/local/sbin/kiwi-rings-answer`, asked by both update reboot units).
- **A hardware watchdog**, where the machine has one: a machine that hangs is reset
  after a minute, one that hangs in a restart after ten. Before, a machine hung in its
  update restart for 39 minutes, with SSH and its services already stopped, until
  someone reset it. `watchdog: false` switches it off; "Hardware watchdog" in the app
  and on the page.
- Proven the same night: a machine installed anew from its media (papaya: encrypted
  system disk opened by two rings, its data disk mounted from the fleet file) came back
  with its services, its logins, its unlock ring's keys and its control node, from the
  backups on another machine. What it lost is what changed since its last nightly
  backup.

## 2.99.0 — a second disk for the data

- **`mounts:`** in a machine's settings ("Data disks" in the app and on the page): a
  second disk and where it goes, like `LABEL=data /mnt/data`. It is mounted at every
  apply, before the services start, and at every boot, and never erased. Until now such
  a disk was mounted by hand after an install, which a machine installed anew did not
  survive without a person.
- `kiwi-server disks <fleet> <machine>` lists a machine's disks and gives the line for
  one that holds data and is not mounted.
- An apply stops when a data disk is not there, instead of letting the services write
  into an empty folder on the system disk.
- A new, empty disk gets a filesystem when its entry asks for one (`xfs`, `ext4`), and
  only when the disk has nothing on it.
- Proven on the test network: papaya installed anew from its media with an encrypted
  system disk (opened by the rings on two other machines, twice, with nobody there),
  its data disk mounted by this setting.

## 2.98.0 — admin pages, and every app

- **Admin pages are a class of their own.** The control page, Portainer, Pi-hole, the
  VPN's device page, the backup server and the cache are the network's controls, not
  apps (kiwi-modules 0.38.0 marks them). A group of the VPN opens them only with its
  admin switch on, or when one is named for it; a linked network only when one is named.
  Until now "everything" and "every node" included them without saying so, and a linked
  network that was given a whole machine got its control page with it.
  `kiwi-server access` lists the admin pages of your network. Apply the machines.
- **Every app**: a new level, `apps`. The web pages of every node and nothing else of
  them: no SSH, no other port, no admin pages. A new network has a group `family` with
  it, for the people of the house.
- "Who may reach what" on the control page and in the app: "Every app" beside
  "Everything", the admin pages of a machine set apart from its apps, and a new group
  starts with nothing, every app, or everything.
- The access drill knows the admin pages and the apps level.
- Fixed: a value changed in the fleet file kept the comments only for some layouts; a
  block of plain words (a machine's `unlock:`) made it write the whole file anew.

## 2.97.1 — a control node that is older says so

- `kiwi-server control push` says when the control node runs an older version than this
  computer, and how to update it. An older control node does not know a part that came
  since, and its page then calls a machine's settings wrong ("unknown module 'onion'")
  though nothing is wrong with them. Seen on the test network.
- Taking away a machine's last hidden service takes the `onion` part off the machine
  too: it does not run Tor for nothing.
- The hidden-service drill asks through a Tor client of its own on the master, with a
  key and with none. Run for real with Forgejo (`git.medlar`): the page opens through
  Tor, another site's name on the address gets nothing.

## 2.97.0 — git hosting, and pictures of what a network can be

- kiwi-modules 0.37.0: a new part `git` for a node: Forgejo at `git.<machine>`, its
  admin made at the first start (the login is in `kiwi-server logins`), sign-up off, git
  over SSH on port 2223. Add `git` to a node's parts. As a hidden service
  (`kiwi-server onion <fleet> git.papaya`) strangers can read and clone what you make
  public, without your VPN.
- **[Example setups](docs/examples.md)**: seven diagrams with their tables: the smallest
  network, a home with a gateway, two homes, who may reach what, two linked networks, a
  service for strangers, and what happens when something breaks. Then the parts, what
  runs by itself, and who can reach what, each with the technical name behind it. The
  diagrams are SVG in `docs/images/`, for the docs, the website and print.
- The logins drill tries Forgejo's login; the hidden-service drill waits for a service
  that was just made or changed to be found.

## 2.96.2 — a hidden service on a real machine

- kiwi-modules 0.36.1: with a hidden service the machine's web server did not start. A
  `.onion` name is longer than nginx's table of names takes by default. Found on the
  first real machine; apply a machine that has a hidden service.
- `tests/drills/onion.py` tries a hidden service for real, and did on the test network:
  the machine's Tor shares the VPN client's network and leaves at the master's exit, not
  through the machine's own connection; the address opens through Tor and another site's
  name on it gets nothing; limited to a named person it opens with that person's key and
  cannot be found without one.

## 2.96.1 — connect does not hang, the app can stop a task

- `kiwi-server connect` (and "Connect this computer…" in the app) waited for minutes
  when the master was off, and could not be ended: the system's resolver waits for a DNS
  server that is not there. It now gives the master 15 seconds, then says that the
  computer is connected as soon as the master answers.
- The app has a Stop button beside the output button while a task runs. One press ends
  the command and what it started; a second press ends it hard.

## 2.96.0 — hidden services

- **`kiwi-server onion <fleet> git.papaya`** makes one service of a machine a Tor hidden
  service: an address that anyone opens with Tor Browser, for people who are not on your
  VPN. `--client anna` limits it to named people and prints the key anna types;
  `--show anna` prints it again; `--remove` takes a name or the service away;
  `kiwi-server onion <fleet>` lists them with their addresses.
- **Tor never leaves through a home connection.** A node's Tor shares its VPN client's
  network: it goes to the master and leaves there, through the master's VPN exit or,
  when it has none, from the master's own address (kiwi-modules 0.36.0, module `onion`).
- The addresses and the keys derive from the fleet's seed: known before the machine is
  set up, kept by a machine installed anew. A person's private key never goes to a
  machine.
- On the control page under "Who may reach what" and in the app's dialog of that name:
  a tick per service, names for the people, a person's key shown on asking (noted in
  Activity). `kiwi-server access` lists the hidden services beside the groups.
- A change of one value in the fleet file (`access`, `onion`) keeps the file's comments.
- The control node looks again after two minutes while a machine is gone, instead of
  after ten: a master that hangs is restarted sooner.
- [docs/design/onion.md](docs/design/onion.md): the design, and what comes next (a
  switch that sends any service's traffic through the VPN, as the download station's
  already goes).

## 2.95.0 — if something breaks

- **`kiwi-server recovery <fleet>`** says, before anything breaks, whether the network
  could be brought back: is there a copy of the network and how old is it, does every
  machine with data save it to another machine, does a disk still open when one machine
  is lost, does the fleet keep the master's VPN state, who can open the network's keys,
  and what happens when the master stops answering. Gaps come first, each with what to
  do. Then the cases, with this network's names: a service's data is wrong, a machine is
  dead, the master is dead, a phone or a computer is lost, the control node is gone, a
  whole home is gone.
- **"If something breaks"** on the control page (from Home and Help) and in the app's
  menu shows the same report, with buttons to the place for each case.
- **"This machine is lost or was replaced"** on a set-up machine's page and in its menu
  in the app: its install media and its set-up again, under the same name. A machine
  with backups brings its data back by itself afterwards.
- `backup` notes where and when the network's copy was stored, so the report can say.
- **A master with an encrypted disk comes back by itself.** After a restart it waits at
  boot for its unlock ring, which lives at home. The control node now forwards the ring
  when it sees the machine waiting, at most six times a day (`recovery: { unlock: false }`
  switches it off; Settings, "Open a disk that waits at boot"). Together with 2.93.0's
  restart, a master that hangs is switched off and on, opened and running again with
  nobody there.
- `kiwi-server gone` is the new name of 2.93.0's `recover` (a look at machines that do
  not answer); `recover` still works. It was one letter from `recovery`.
- Fixed: the control page could read a file of the last look while it was being written,
  and answer with an error once in a while.
- New page: [docs/recovery.md](docs/recovery.md), "When something breaks".

## 2.94.0 — who may reach what

- **`kiwi-server access <fleet>`** lists every group of the VPN and every linked network
  with what it may reach, and changes it: `access <fleet> guests cloud.papaya
  paperless.papaya`. A group or a linked network is given `all`, `nodes`, a machine
  (`papaya`), one service (`cloud.papaya`: its web address only), one port (`papaya:22`)
  or `nothing`. `--add` and `--remove` change single entries, `--new` makes a group,
  `--internet`, `--peers` and `--admin` set the rest. The same words for both, so a
  partner network's `share` is set here too.
- **"Who may reach what"** on the control page (from Home) and in the app's menu: a
  tick per machine and service for every group and linked network, and a new group by
  its name.
- kiwi-modules 0.35.1 enforces it in two places. The master's firewall lets through what
  is given and drops the rest. A node that a group reaches for single services refuses
  that group its other names in its own web server: all services of a node share one
  address and port.
- The change goes into the fleet file's `vpn-server.groups`; the other lines and their
  comments stay. It reaches the machines with the next apply, and the command says
  which machines.
- `tests/drills/access.py` tries the rules with a real VPN client per group, one outside
  every group and one in a linked network: every machine's SSH port, every service's
  page, the admin page, another device and the Internet.
- **Found by that drill and closed:** a group or a linked network that was given one
  service of a node could also open the login of the control page on that node. The
  control page has an allow list of its own, and nginx then ignored the lines that shut
  others out. Apply the node that runs your control page.

## 2.93.0 — the control node restarts a master that is gone

- **A machine that does not answer** is named on the control page's Home, with how long
  it has been gone. `recovery:` in the fleet's defaults (page and app: Settings, "When
  a machine is gone") says what may be done without a person:
  `master: { after: 15, do: restart }` lets the control node switch a master that has
  been gone for 15 minutes off and on at its provider. Nothing on the server is
  touched. The default stays `tell`.
- It holds back when it restarted the same machine within the hour, three times within
  a day, or when the provider does not answer (then the control node is the one that
  is cut off). Every restart is in Activity.
- `kiwi-server recover <fleet> [--dry-run]` does one such look by hand;
  `kiwi-server vps restart <fleet> <host>` is the power cycle alone.
- [docs/design/recovery.md](docs/design/recovery.md): what runs by itself today and
  what is planned, from a dead service to a home that is gone, and the emergency
  channel over Tor.

## 2.92.0 — a clean boot for a machine with its own ad blocker; the app catches up

- kiwi-stack 0.9.6: a machine that runs Pi-hole asked it for every name, also at boot,
  when it was not up yet. The time service could not find its servers, the stack waited
  90 seconds for the clock, and `status` showed "failed units: chrony-wait.service" on a
  machine with no problem (seen on papaya). The machine now asks its own Pi-hole only
  while the stack runs, and the wait for the clock ends without counting as a failure.
  Apply the machines once.
- The app, as the control page has it:
  - "Preview what Apply would change" in a machine's menu (`plan`).
  - "Update everything…" in the main menu (`apply --update`, the control node last).
  - "Add a phone or laptop…" lists the devices on the VPN, each with "Show its code"
    and "Remove".
  - "Save a copy of the network…" asks for no passphrase where the keys are protected:
    the recovery key opens the copy. "Restore a network from a copy…" takes the
    recovery key or, for an older copy, its passphrase.

## 2.91.3 — connect tells the browsers, and only them

- `kiwi-server connect` put the network's certificate authority into every Flatpak app
  that keeps a certificate store, Steam included. It now tells the browsers only:
  Firefox and LibreWolf profiles, Chromium's own store, and the Flatpak browsers of the
  Chromium kind by name (Brave, Chromium, Chrome, Edge, Vivaldi, ungoogled Chromium,
  Opera).

## 2.91.2 — linked networks on the page; every login tried for real

- **"Linked networks"** on the control page (from Home): per partner what is shared
  with it, who of it may come, and the two switches for leaving to the Internet
  through it or letting it out through this network. A change that makes no sense
  (two exits, out through each other) is refused and the file stays as it was.
- `tests/drills/logins.py`: every login `kiwi-server logins` names, tried through the
  service's own API from a device on the network. On the test network, from the dev
  VM: 15 of 15 in (wg-easy, Pi-hole, Transmission, JDownloader, Portainer, SFTP,
  Nextcloud, OpenCloud, Paperless).

## 2.91.1 — "Back up the network now" has somewhere to go

- `kiwi-server backup <fleet>` without a place named (no host given, `backup.hosts`
  empty, which is how a new network starts) sends the copy to every machine that is set
  up. Before, it stopped with "nowhere to back up to", and so did the app's "Back up
  the network now".

## 2.91.0 — a network leaves through its partner over the link they have

- **`kiwi-server federate A B --exit`**: network A leaves to the internet through
  network B (multi-hop), over the federation link that is there. `exit: true` for the
  partner in A's `federation.yaml`, `internet: true` in B's (kiwi-modules 0.34.0). The
  earlier plan made A's master a client of B's VPN server: a VPN client container, a
  second tunnel and a login there. None of that is needed now. B sees A's master only,
  A gets out to the internet only, and with the link down A's devices have no internet
  instead of falling back. `--no-exit` puts it back; `validate` says which way a
  network leaves. docs/network.md, Leave to the Internet through a partner.
- The app's Federation dialog has the two switches per partner: "Leave to the Internet
  through it" and "It may leave to the Internet through this network".
- `kiwi-server backup <fleet> <machine>` reaches the machine where it answers from this
  computer. It used its name, which resolves on the VPN only, so a computer that is not
  on the VPN could not send the network's copy anywhere.
- `connect` stops when a device of this computer's name exists and the computer has no
  connection yet: two computers sharing one device cut each other off. `--existing`
  when it is this computer, `--name` for another one.
- The multi-hop drill knows both ways.

## 2.90.1 — a gateway says how the home uses it

- The logins of a machine with the gateway (or only the ad blocker) and a fixed address
  end with how devices at home use it: which address to set as DNS server and gateway.
  In `kiwi-server logins`, the app's "Logins…" and the page's "Logins" card.

## 2.90.0 — a machine leaves for good; using your network

- **`kiwi-server remove <fleet> <machine>`**: off the VPN (its key stops working), its
  keys and files on this computer gone, its block out of the fleet file with the
  comments of the others kept. It names who still counts on the machine (a backup
  server that lists it, a disk it helps unlock) and refuses the last master of other
  machines. The app's "Remove" does this now, where it only edited the file and left
  the machine its place on the VPN; the page has "Remove this machine".
- **docs/using.md, "Using your network"**: your devices onto the network, opening a
  service and its login, keeping it well.
- kiwi-modules 0.33.2: an empty blocklist database left by a first start is made anew.

## 2.89.1 — the ad blocker blocks

- kiwi-modules 0.33.1: no Pi-hole of the test network had ever loaded its blocklist.
  Pi-hole fetches it when its container starts, before it can resolve a name itself,
  and on a machine that resolves through that Pi-hole the fetch failed for good. A
  timer now looks at the list after every start and once an hour and fetches an empty
  one again. Apply the machines once.

## 2.89.0 — the app leads with what you came for

- **A machine's page starts with "Open a service"**: each service by what it is
  (Files (Nextcloud), Ad blocker (Pi-hole)…), opened with a click, and "Logins…" right
  there. The machine's inner life (backups, kernel, network) follows.
- **"This computer is not on your network yet"** on the overview, with a Connect
  button, when the network's services do not open from the computer the app runs on.
- The status dots use the solid colours: the lighter ones are mixed at run time, and
  one renderer showed the green of "All good" as red.
- `connect` over SSH says why NetworkManager refuses, and where to run it.

## 2.88.0 — the logins of your services, where you look for them

Opening a service led to a login page and nothing said what to type: the passwords are
made from the network's seed, and only a YAML dump showed them.

- **`kiwi-server logins <fleet> [machine…]`**: every service of a machine with its
  address, user name and password, and a note where there is something to know (Immich:
  the first person to open it makes the admin account).
- **On the page**: a machine's page has a "Logins" card. A password is fetched when
  "Show password" is pressed, never part of the page itself, and Activity notes that
  logins were shown, not which.
- **In the app**: "Logins…" on a machine, with reveal and copy.
- Modules say how to log in to them (kiwi-modules 0.33.0, `logins:` in `module.yaml`).

## 2.87.0 — this computer on the network; a board that does real work

An Odroid N2+ became the home's ad blocker and VPN gateway, and the computer the
network is run from got a way in. What that took:

- **`kiwi-server connect <fleet>`** (the app: "Connect this computer…"): this computer
  as a device of the network, with a NetworkManager connection that carries only the
  network and asks its DNS only for the network's names, and the network's
  certificates trusted in the user's browsers. docs/network.md, This computer on the
  network.
- **A machine's address reaches a board.** After an apply the machine is asked what
  address it has; a board on the router's address takes the fixed one from the fleet
  file by itself and falls back when it does not work (kiwi-stack 0.9.x,
  `kiwi-stack address`).
- **The gateway cannot leak any more** (kiwi-modules 0.32.3): a kill switch drops what
  a LAN client sends unless it leaves through the VPN client, and the gateway's unit
  now watches its rule and routes and puts them back. On the Odroid, systemd-networkd
  had dropped the rule and every client went out around the VPN; a v1 gateway needed
  restarts by hand for the same thing. kiwi-stack 0.9.4 also tells systemd-networkd to
  leave the stack's routes and rules alone.
- kiwi-stack 0.9.0 to 0.9.4, found on the same board: the dns module added to a running
  stack starts (podman's resolver let go of port 53 only after a full restart);
  `iptables` is installed where Armbian has nft only; Armbian's own package source no
  longer goes through the LAN's cache, which refused it; `verify` names a container
  whose address the router's DHCP routes elsewhere.
- The toolchain image carries `certutil` (for the browsers' certificate stores):
  `kiwi-server toolchain build` once.

## 2.86.0 — the recovery key brings the network back

Until now nothing a wizard-made network could do saved the network itself: the copy was
encrypted with a passphrase no wizard asked for, "Save it now" failed, and the recovery
key shown at the first run opened nothing once the one computer was lost.

- **The network's copy is the encrypted store plus the fleet file** (design/control-plane.md,
  The secret store): `kiwi-server backup` makes `fleet-<name>-<time>.tar` with
  `secrets.age`, the fleet file encrypted for the same keys, and a manifest. No
  passphrase. A fleet without a store keeps the old `.tar.enc`.
- **`kiwi-server restore <copy>`** asks for the recovery key (`--recovery-file`,
  `KIWI_RECOVERY_KEY`), recreates the folder and gives this computer its own way to
  open the store.
- **On the page**: "Save it now" works for a network made on the page; "Fetch a copy"
  on Backups downloads one; a machine with nothing on it offers "I already have a
  network: bring it back from a copy" (the copy, the recovery key, the setup code, a
  new password).
- The setup code of a fresh page now reaches `podman logs` at once (it sat in a
  buffer), and unpacking a store refuses entries that would write through a link.
- A copy is not signed: someone who can replace the file on a machine could hand back
  another network. Compare the machines' names after a restore (ROADMAP, problem 21).

## 2.85.0 — the page brings data back and unlocks a waiting disk

- **"Bring something back"** on the Backups page, per machine: every part's snapshots
  (when, and which backup machine keeps them), one button to put a part back as it was
  then, one for everything to the newest. It says that what is there now is replaced.
- **"Unlock it"** on the page of a machine whose encrypted disk waits after a restart.
- The page's help on getting things back no longer sends people to the app.

## 2.84.0 — phones and laptops get a tunnel that fits every network

- **A device's config says `MTU = 1280`.** It used to carry the master's `wg_mtu`,
  measured for the home line (1376 here). On a path that carries less (mobile data,
  a tunnel inside a tunnel) pages opened and every upload hung, with no error anywhere.
  `kiwi-server enroll` and the page's "Add a phone or laptop" now write 1280 for a
  device; `enroll --mtu N` sets another size; a machine keeps `wg_mtu`.
  A device made before: `kiwi-server enroll <fleet> <name> --existing --qr` and scan
  it again. docs/network.md, Phones and laptops.
- Found by the users drill: two of three synthetic users, behind a path that carries
  1344, could not upload 30 kB to any machine while 1 kB went through. The drill now
  says which answer failed and for whom (`000/curl28` is a timeout).

## 2.83.0 — settings are changed on the page

- **"Settings" on a machine's page and "Settings for every machine" on Home.** The
  fields the app shows: the machine, its network, updates, what it does (the preset and
  its parts), and every setting of each part. Empty means "as for every machine", and
  the page says what that is. Only what was changed is written.
- A change is checked (`validate`) before it is kept: a mistake it would bring is said
  in plain words and the fleet file stays as it was. Then "Apply" brings it to the
  machine.
- A secret is never shown: its field is empty, typing replaces it, a tick removes it.
- One list of fields for the app and the page (`lib/kiwiserver/form.py`, published as
  `base` in `kiwi-server roles --porcelain`); a test keeps the app's copy the same
  until the app reads it from there.
- Not on the page yet: the VPN groups as a list editor (a text box for now), choosing a
  file, the control node's own unlock ring, removing a machine.

## 2.82.0 — machines are added, installed and set up from the page

- **"Still to set up" on the control page**: every machine that is not running yet, the
  master first, with its next step as one button: make its install media, fetch them,
  install it at the provider, give it its place on the VPN, set it up. The page's
  counterpart of `kiwi-server next` and of the app's buttons.
- **Install media from the page.** Made in memory (they hold the machine's keys, and
  nothing of the network rests on a control node's disk in the clear), fetched by
  someone who is logged in, gone after a restart and once the machine is set up.
  kiwi-modules 0.32.2 gives the control module 2048 MiB for that (`ram_mb`, a limit).
- **"Add a machine at home"** on Home: what it does, its address, name and system.
- **A forgotten password**: the login page takes the recovery key and a new password.
- A task that waits for a sign-in at a hoster shows the page to open as a link.
- Fixed: a command that wrote the fleet file on a control node (it is a link into
  memory there) left a plain copy on the disk beside the link. Files behind a link are
  written where the link points (kiwi-modules 0.32.2).
- `next <fleet> <host> --yes` without a terminal ends with an error when the machine
  does not answer, instead of printing the steps for a person and saying it went well.
- Home is quiet until the first machine runs: no checking loop, no service tiles.

## 2.81.0 — a network can be started from a machine's page

- **The first run on the control page.** A machine with nothing on it (`serve --wait`,
  no store, no fleet file) shows the questionnaire of `kiwi-server init`: the same
  questions as the terminal and the app, checked where they are asked, machines at home
  added with a button. It asks for a setup code that `serve` prints (the container's
  log), so the first person to open the page is not the owner by accident, and for a
  password. The network is made in memory and reaches the disk encrypted only; the
  recovery key is shown once. docs/control.md, Start a network from the page.
- The page takes time zone and language from the browser and the home network from the
  address it was opened at, when the machine (a container) cannot tell.
- `notes/REVIEW-page-parity.md`: what the app can do and the page cannot yet, and the
  other way round. Next: adding and installing machines from the page, then settings.

## 2.80.2 — system updates work behind the LAN's image cache

- kiwi-stack 0.8.3: a uCore machine that fetches its images through the LAN's cache (the
  cache module on a node at home) never got a system update. The update checks the system
  image's signature where it fetches it, and the cache counted as a place without
  signatures; `status` showed `failed units: rpm-ostreed-automatic.service`. Apply the
  machines once: the cache is then named as a place that carries signatures.
  Found by a two-hour soak after the drills (quince, papaya).

## 2.80.1 — a master whose SSH port moved is found from a computer off the VPN

- After `admin.ssh_port` moved a machine's sshd, a computer that does not resolve the
  VPN's names lost it: `status` and `watch` said "unreachable" although its address was
  on record. ssh keeps no separate line for the new port, and the lookup wanted one.
  Found when a RAM master was put back on a spare server.
- `watch` puts a RAM master's own key on record for its addresses before each look,
  so a computer that never built the image finds the booted, blank machine.

## 2.80.0 — the drills of the first run, backups and the gateway; what they found

Four drills on real machines, kept in `tests/drills/`, and the fixes for what they met.

- **`kiwi-server next` installs a master at a provider by itself**: it signs in when
  needed, asks once ("everything on that server is erased"), uploads, installs and
  switches the server on. Without a terminal it wants `--yes`.
- **A backup server with a new client is named.** A machine added to
  `backup-server.clients` was refused (401) until the server had its role again, and
  nothing said so. `apply` now warns with the command, and `next` does it.
- **kiwi-stack 0.8.2**: a first apply on a fresh Debian waits for apt's lists lock
  (the system's own first update held it and the apply failed).
- Drills (each prints PASS or FAIL per check; results of 13 November 2026):
  `master-vps.sh` (a master from wizard answers on a spare server at the provider:
  build, install, set up, a phone enrolled), `first-run-vm.sh` (a machine from an empty
  disk, CoreOS with unlock rings and Debian; restarts with the rings up, one off, all
  off), `regenerate-vm.sh` (a file restored from a snapshot; the machine wiped and
  installed anew brings its data back by itself), `uplink-change.sh` (a client behind
  the gateway: nothing leaks while the home's connection is gone, back 0 s after 90 s
  without it; with the old path dead for good the gateway came back from a new port
  by itself after 41 s, nothing restarted).

## 2.79.0 — the rest of the first run: the server from a list, a reinstalled machine

The three items the first-run review had left open.

- **Hosters are providers.** `providers.py` is a small registry; netcup is its first entry,
  and another hoster is one class there (how to sign in, how to list the account's
  servers, where its token rests). The wizard, the commands and the fleet file
  (`vps: { provider, server }`) speak of the provider.
- **The master's address is picked, not typed**, when it runs at a provider: sign in from
  the wizard (the terminal prints the page, the app opens it), pick the server from the
  list. The login becomes the network's own. Typing the address still works.
- **A machine that was installed anew** shows another SSH key than the one remembered.
  A first setup (the app's, and `next`) forgets the older key by itself;
  `kiwi-server next <fleet> <host>` takes a named machine although it was set up before;
  the app asks "I installed it anew" or stops; `apply --ssh … --new-host-key` by hand.
- **No card name needed** for a fixed address together with disk unlock: the boot-time
  setting applies to the machine's wired card. `network.interface` is for a machine with
  several.

## 2.78.1 — a locked machine is told what really opens it

Reboot tests on an encrypted uCore node with two rings (either one is enough): with both
up and with one off it came back by itself; with both off it waited, and opened by itself
ten seconds after one ring was back.

- `status` said "kiwi-server unlock" for such a machine, and that command could only
  answer that there is nothing to forward. Both now say which machines serve its rings,
  that it opens by itself when one of them is back, and where its key for the console is.

## 2.78.0 — the first run, for someone who has never done this

Both wizards were run from an empty home as a beginner would (notes/REVIEW-first-run.md
lists what that showed). The questionnaire is still one definition for the terminal and
the app; it now asks three things instead of fourteen.

- It opens with what you need (a master, a machine at home, a USB stick, an hour) and
  what it found on this computer. Then: where the master runs, its address on the
  Internet, what the machine at home does. Names, the system, the disk, the name ending
  and the key have answers that work and sit under **More settings** (`init --all`).
- It looks before it suggests: a home address that does not answer a ping and is not in
  the neighbour table, the language from this computer.
- `disk: auto`, the new default: the install media take the machine's one disk and stop,
  with the disks listed, when there are several. Nobody has to know `/dev/nvme0n1`
  before the install.
- Every answer is checked where it is asked, by one check for both (`init --check`): an
  address inside a home network is not a master's address, a machine cannot have the
  router's address. The terminal asks again; the app does not go on.
- A machine at home starts as the one that keeps files and passwords, not as the gateway
  that takes over the home's DNS and DHCP.
- The keys are always encrypted (no question), and the terminal waits until the recovery
  key is written down.
- `kiwi-server init` writes into `~/kiwi`, says so without a terminal instead of writing
  the example, and ends with one command instead of ten.
- `kiwi-server next <fleet>`: the terminal's guide. It does the next step for the next
  machine, the master first, and says what a person has to do where one must (it names
  the USB stick that is plugged in).
- `validate` calls a machine that is not on the VPN yet a step to do, not an error.
- The install that follows, found by installing a VM from such a network:
  - An apply waits until a fresh uCore machine has finished its switch from Fedora CoreOS
    (two steps, a restart each); before, it was cut off by the restart.
  - Both switch steps retry: one failure used to leave the machine on the unverified image
    for good.
  - An encrypted machine with a fixed address resolved no name after booting: the
    connection made for unlocking at boot stays, and it had no DNS servers. It carries them
    now.
- The master is called the master everywhere; "hub" stays the word for hub software.

## 2.77.2 — the app has "When the install is done"

- A machine's settings in the app have **When the install is done**: power off (the
  default: pull the stick, switch it on) or reboot (a virtual machine, or a disk that comes
  first in the boot order). The setting `install.on_done` existed, the app did not show it.
- The guide's install step says what to wait for by that setting.

## 2.77.1 — the list says whose turn it is

- `kiwi-server rotate <fleet>` names the admin logins of the services apart from the other
  secrets that keep their value: an admin password is the owner's to change in the service
  itself (it was only the first password, and may have been changed since).
- kiwi-modules 0.32.1 and kiwi-stack 0.8.1: no change to what they do, both have a
  `docs/` folder now.

## 2.77.0 — Nextcloud's database password changes with the seed

kiwi-modules 0.32.0.

- A secret can have several steps that change it where it lives; `rotate seed` runs them
  in order, each in its container, and the secret counts as changed when all worked.
- Nextcloud's database password: `config.php` through occ, then the two roles in its
  Postgres. Its containers then restart with the new value.

## 2.76.1 — the app protects the keys itself

Save a copy and Restore were run the way the app runs them: both work, with and without a
store. What they showed:

- A restored network had its keys as plain files, and the app only said so in a tooltip
  with a command. Now the overview has a step **Protect your network's keys** with a
  button, the app does it by itself right after a restore, and the recovery key is shown
  once with a button to copy it. The foot's button says **Protect** as well.
- A wrong passphrase said so with a line of OpenSSL's own words after it.

## 2.76.0 — two sides that both changed become one copy

Found with the key rotation: the control node learned a host key while this computer made
new keys, and the only way on was `--force`.

- `control push` and `control pull`, over the control node's API and through a synced
  folder: when both sides changed since they last agreed, each file is taken from the side
  that changed it, the host keys of both are kept, and the result goes to the other side.
  Only a file that changed on both sides stops it, by name.
- What both sides held at the last sync is kept as `control/base.age`, encrypted for the
  store's keys.
- The app: a push that merged shows what came; when both changed the same thing it asks
  which computer's change counts, instead of a line about `--force`.

## 2.75.1 — the backup server password follows the seed

kiwi-modules 0.31.1.

- backup: the password a machine signs in with at its backup servers follows a new seed
  (the machine reads it at every backup, the servers keep a hash of the same value).
  Between the two applies of one `rotate seed` that machine's backups to that server are
  refused; when the run is through, both sides agree again.
- A secret that an older release pinned because it could not change it yet loses its pin
  at the next `rotate seed`, once its module says it can follow.

## 2.75.0 — database passwords change with the seed: Immich and Paperless

kiwi-modules 0.31.0.

- A module can say how a generated secret is changed where it lives (`rotate: { in, run }`).
  `kiwi-server rotate <fleet> seed` runs that on the machine before its apply: the new
  value goes in on stdin, and only when the command worked does the pin go and the files
  follow. One that does not work keeps its value; the next rotation tries again.
- First: the database passwords of Immich and Paperless. A password written in the fleet
  file by hand is left alone.

## 2.74.2 — the app's home-server setup, run for real

The dialog **A home server that looks after it…** was run on a real machine for the first
time (a copy of the test fleet, lychee as the server): all four steps work, the server
holds only encrypted files and waits for the first login.

- It crashed when it had finished: the closing text had two blanks and one value.
- The choices for "After a power cut" were cut off before the machine's name, and a machine
  was offered its own ring (which was then silently not used). Now "papaya opens it", and
  a machine's own rings are not offered for it.

## 2.74.1 — rotation, after clicking through the page

- The app's **A computer was lost or stolen…** offers both steps: the keys and the secrets.
- `kiwi-server apply --update`: after the apply, the containers a newer image came for
  restart (`kiwi-stack update`). The page's **Update everything** does that now: an apply
  alone fetched newer images and left the old ones running (found: the control node
  stayed on its old version).
- The page: a rotation that leaves machines waiting no longer reads as "did not work", and
  its task leads back to Computers; the password change is confirmed at the top of the
  page, where it is seen; the hint for a machine that is gone names the **Leave out** button.

## 2.74.0 — a new seed: the secrets inside the services

design/controllers.md, step 3, second part. kiwi-modules 0.30.1, kiwi-stack 0.8.0.

- `kiwi-server rotate <fleet> seed`: a new seed, then every machine is applied. The secrets
  that containers read at every start follow it (ten kinds: Nextcloud's Redis, OnlyOffice,
  Imaginary, whiteboard and Talk-internal secrets, Pi-hole's and wg-easy's admin password,
  Paperless's session key, the sftp and downloader passwords) and those containers
  restart.
- Every other generated secret (database and admin passwords, the backup passwords, Talk's
  TURN and signaling secrets, the federation secret) is pinned to the value it has, in
  `secrets/pins.json`, before the seed changes: nothing a database knows changes under it.
- A machine takes a changed seed only from a bundle that names the seed it runs
  (`KS_SEED_FROM`); any other changed seed is refused as before. The old seed stays as
  `secrets/seed.old` until every machine has changed.
- The control page, **Computers**: **Replace the secrets**, with **Leave NAME out**.
  `kiwi-server rotate <fleet>` counts what follows the seed and names what stays.

## 2.73.1 — rotate: machines outside the fleet, leaving one out

Found on the test network right after 2.73.0.

- A machine outside the fleet that had the fleet's key by hand (a desktop VM here) was locked
  out by `rotate ssh`: the list, the command's last line, the page and the docs now say that
  such machines need the new public key.
- The control page offers **Leave NAME out** for each machine that still waits (a machine
  that is gone, or will be installed anew); the command's message names `--waive`.

## 2.73.0 — a computer was lost: new SSH keys, a new control password

design/controllers.md, step 3, first part: what a removed computer knew can be replaced.

- `kiwi-server rotate <fleet>` lists what a computer that managed the network could read,
  when it was last replaced and how.
- `kiwi-server rotate <fleet> ssh [hosts…]`: a new admin SSH key (the fleet's own, under
  `secrets/`) and a new SSH CA. Each machine takes the new key, a login with it alone is
  tried, and only then the old key is taken out. The old keys stay beside the new ones
  (`.old`) and ssh tries both until every machine has changed; a machine that was off is
  done by the next run, `--waive HOST` leaves one out for good. Nothing restarts.
- The control page, **Computers**: **Replace the keys** runs it as a task and says which
  machines still wait; **Change the password** replaces the page's password (the old one's
  key leaves the store, the unlock ring is armed again).
- The app: **A computer was lost or stolen…** in the menu.
- Not yet: the seed (service passwords), WireGuard keys, the boot-unlock key, the CA.

## 2.72.0 — several computers through a synced folder

design/controllers.md, step 2: a network without a control node is shared through a folder
that Nextcloud, Syncthing or the like syncs.

- `kiwi-server control push|pull <fleet> --dir FOLDER`: the encrypted store and the fleet file
  (encrypted for the store's keys) in that folder, with a manifest written last; a folder
  caught in the middle of syncing is refused. The folder is remembered: later syncs need no
  `--dir`.
- A computer asks to join through the folder (`control join <fleet> --dir FOLDER`: its own
  key, the public half left in `requests/`); a computer that has the network accepts or denies
  (`control requests|accept|deny`). Whoever runs the syncing never sees a secret.
- The app: **Keep it in a synced folder…**, a folder field in **Join**, and a question
  "… wants to join: Accept / Not now / Deny" when it syncs. A request that was made continues
  where it stopped; it is not asked twice.

## 2.71.1 — the docs reach kiwi-network.eu

- `docs/kiwi-docs.yml`: the pages in menu order. kiwi-network's site lists kiwi-server as a
  source already and skipped it for want of this file; its build script now takes the folder
  (21 pages under the kiwi-server reference), at the next release tag.
- The kiwi-docs guide is in `.claude/skills/kiwi-docs/` (a copy from kiwi-network, with its
  checker: 0 errors), and `.gitignore` lets it be committed.
- The handoff notes and the 2.1.0 review are internal: they moved from `docs/` to `notes/`,
  so they are not shown on the site.

## 2.71.0 — the QR code needs nothing installed

- kiwi-server draws QR codes itself (`lib/kiwiserver/qr.py`: byte mode, level L, up to 858
  bytes), checked module for module against qrencode in the tests. So:
- the app's **Add a phone or laptop** shows the code on any computer (it needed qrencode);
- `kiwi-server enroll … --qr` prints it in any terminal, light or dark;
- `kiwi-server qr <file> [--png|--svg]` makes one of a device's config;
- the control page no longer depends on the qrencode program.

## 2.70.2 — the first setup finds the machine

- The first **Set it up** of a machine failed for a newcomer: before its first setup a machine
  has no name on the VPN, and the app tried that name. It now asks where the machine answers,
  with the hub's address or a home machine's fixed address filled in.
- A machine's own page shows its next step as the same sentence and button as Getting started,
  where it had a command line.
- Apply's question says what it does in plain words.

## 2.70.1 — the app's device dialog asks who it is for

- Add a phone or laptop in the app had no "Who is it for?" choice: the app dropped the groups
  it had been told. Found by clicking the dialog through on a real fleet.

## 2.70.0 — a first run without hurdles

Walked through as a newcomer, start to finish, and fixed what stood in the way:

- **Start a new network** goes straight to the questions; it no longer asks where to save a
  file first (the network goes into ~/kiwi).
- The questions are in plain words, in the app and in `kiwi-server init`: Your network, The
  hub, At home, Keys and passwords, with what each is for and suggestions that work.
- A question with only one possible answer is not asked; the choices show what is selected.
- The last page shows the recovery key with a Copy button and one sentence on what comes
  next; the command output is folded away under Details.

## 2.69.0 — the control node unlocks by itself

- `unlock: { control: <ring> }` on the control node: after a restart or a power cut it asks a
  Tang ring on another machine and opens its keys with nobody there. After a login it keeps
  the key its password made encrypted for that ring (clevis, `control/auto.jwe`). The ring's
  machine off: it stays locked, and the login page says which machine it waits for. A stolen
  disk alone opens nothing.
- The app's control dialog offers it: "After a power cut: by itself, with the help of …".
- The control image has clevis and jose.

## 2.68.0 — a lost phone

- Control page, Devices: **Remove** takes a device off the VPN at once (the master forgets its
  key), with what to do when a phone is lost or stolen.
- `kiwi-server enroll <fleet> <device> --remove` does it from a terminal.

## 2.67.0 — update everything

- Control page, Home: **Update everything** brings every machine to the current settings and
  the newest versions of its services, one after the other, the control node itself last.

## 2.66.0 — copies, restoring, another computer

- App: **Save a copy of the network…** (one encrypted file, a passphrase typed twice),
  **Restore a network from a copy…** (also on the welcome page: choose the file, type the
  passphrase, it opens), and **Use another computer…**, which says which of the two ways
  fits: a link from the control page, or a copy.
- Control page, Backups: **Save now** on every machine that is backed up.

## 2.65.0 — a control node from one dialog

- **A home server that looks after it…** (menu, and What next): pick the home server, type a
  password twice, and the app does the rest by itself: protects the keys if they were plain
  files (and shows the recovery key once), adds the control part, sets the password, applies
  the machine and sends it the network. Each step is ticked off; a step that fails says why.
  It replaces four terminal commands.

## 2.64.0 — the app guides

- A **welcome** page says what Kiwi Server is and offers three ways in: start a new network,
  join one with an invite link, open one from a file.
- **Getting started** replaces the list of command lines: one machine after the other, the hub
  first, one sentence and one button each (Make install media, Set it up, Connect it, Open its
  settings). A machine that was never installed is a step there, no longer a wall of red
  "unreachable" and "invalid" lines.
- **What next**, once the machines run: put your phone on the VPN, set up backups, let a home
  server look after the network. Steps that are done are not listed.
- **Add a phone or laptop** (menu, and What next): a name, then the QR code (with qrencode) or
  the file to save, and the way to the control page's QR code.
- **Back up the network now** in the menu.

## 2.63.0 — a control page that explains itself

The owner's verdict on the control page: it could do nothing and explained nothing. Rebuilt:

- **Home** says in one line how the network is ("All 6 machines are fine"), lists what wants
  a look with what it means and what to do, shows every machine, and has a tile for every
  service (Files, Photos, Passwords, …) that opens it.
- **A machine's page** says what it is, what runs on it, when it was last saved, and what each
  button does ("Changes nothing." / "Services that changed restart for a moment.").
- **Devices**: add a phone or laptop to the VPN; the page shows the QR code for the WireGuard
  app and a file to download, with the three steps to take.
- **Backups**: when each machine was last saved and where, and the network's own backup.
- **Help**: how do I…, and what the words mean. **Activity**: tasks and the log.
- Tasks say what happens in words and keep the command output folded away; the first check
  after unlocking starts by itself; the login page says why it is locked.
- `list --porcelain` names the master's client groups.

## 2.62.0 — join a fleet: paste one link

- Joining was a code, a command, an accept step and a pull. Now: the control page's
  **Add a computer** gives an invite link; Kiwi Server's new **Join a fleet** takes it, and
  the fleet is there. The invite is the approval (made by someone logged in, once, fifteen
  minutes); the Accept and Deny steps are gone, Remove stays.
- The app keeps a joined fleet in step by itself: it fetches when the fleet is opened and
  sends after a save, and says when two computers changed it.
- `kiwi-server control join <fleet> <link>` does the same in a terminal, fetch included.
- The first computer syncs through the control page too after its next push, no SSH needed.
- A computer that already has a key for the fleet is refused before anything is sent: its
  keyring's key is not overwritten.

## 2.61.0 — controllers join through the control node

- The control page has **controllers**: the computers that can open the fleet, a join code
  for a new one (fifteen minutes, once, void after three wrong guesses), and Accept, Deny,
  Remove. Accepting or removing re-encrypts the store and the fleet file for the keys there
  are.
- `kiwi-server control join <fleet> --url https://control.<host>`: the computer makes its own
  key (keyring, or `--identity FILE`), checks that the code is from that fleet (the code
  names its CA), and sends the public half.
- A joined computer syncs through the control node's API, with no shell on it: it proves its
  key by reading a token encrypted to it, then `control pull` and `push` work by revision
  and refuse to overwrite another controller's change.
- design/controllers.md: step 1 of 4. Every controller is fully trusted for now; rotation
  and scopes follow.

## 2.60.1 — the control page takes a browser's login

- No browser could log in to the control page: under the reverse proxy's no-referrer policy
  a form carries `Origin: null`, which the page took for another site. Forms of a session now
  carry that session's token instead (and the cookie stays SameSite=Strict); the login needs
  no such check, it needs the password.
- A host's page answered with an error.
- The password is taken without spaces around it.
- A test asks the pages over a socket the way a browser does; CI installs FastAPI for it.

## 2.60.0 — the control node

- `kiwi-server serve <fleet>`: web pages and an API over the CLI (FastAPI, Jinja, no build
  step): every machine's state, a host's page, jobs with a live log (status, plan, apply,
  check), an audit log. It looks at the fleet every ten minutes and sends the role to RAM
  masters by itself.
- Its secrets: the control node holds the fleet only encrypted (`secrets.age`,
  `fleet.yaml.age`). `serve --set-password` makes the password one more key of the store
  (an age key from scrypt and a salt; no hash, no key file). Logging in opens store and
  fleet file into RAM; a restart or **Lock** closes them. Five wrong tries from an address
  wait ten minutes; sessions are signed per process; posts from other sites are refused.
- The `control` module (kiwi-modules 0.29.1) runs it on a node at `control.<host>`, for the
  mesh only; image `ghcr.io/derlocke-ng/kiwi-server-control` for amd64 and arm64.
- `kiwi-server control push|pull <fleet>`: the encrypted store and the fleet file (encrypted
  on the way) to the control node and back; a side that changed since the last sync is not
  overwritten.
- The control node applies itself detached (`kiwi-self-apply` on the machine), since the role
  may restart the container the job runs in.
- Nodes take SSH on their mesh address (vpn-client `mesh_ssh`): the control node and admin
  devices reach them from anywhere in the VPN.

## 2.59.1 — the N2+ image checks clean

- An Armbian image gets only the directories it lacks: debugfs's mkdir of an existing one
  left a wrong link count, and the Odroid N2+ image failed its check.

## 2.59.0 — Armbian images

- `target: armbian` with `armbian: { board: odroidn2 }`: `build` takes the board's Armbian
  image from dl.armbian.com (checksum checked) and writes the first boot into it with debugfs,
  no root needed. Flash, boot: the admin user with the fleet's keys, root locked, no console
  autologin, then the role. Proven with the uefi-arm64 image in QEMU, then `apply` made it a
  mesh node.
- The toolchain image has xz and e2fsprogs.

## 2.58.0 — boards

- `kiwi-server adopt <fleet> <host> --as user@address`: a machine that already runs Debian or
  Armbian (an Odroid, a Banana Pi from its SD card or eMMC) becomes the host without a
  reinstall: the admin user, the fleet's keys, sshd and update settings, then its role. The
  board's login may use a password; the fleet is checked before the board is touched.
- `debian.arch: arm64`: the arm64 netinst for UEFI machines, and the right kernel.
- An apply over a forwarded SSH port (a VM, a router's port forward) no longer takes it for a
  moved sshd and switches to port 22.
- kiwi-stack 0.7.1 (nftables for podman on minimal Debian, Docker on Ubuntu-based Armbian),
  kiwi-modules 0.28.1 (tang and strfry for arm64).
- Proven on an arm64 VM: adopted, a node of the mesh through the master's exit.

## 2.57.0 — a cache for the LAN

- kiwi-modules 0.28.0 and kiwi-stack 0.7.0: the `cache` module on a node keeps container
  images (a mirror per registry at `cache.<hostname>`) and Debian packages (a proxy on 3142)
  for its LAN. Every node of the fleet on the same LAN uses it without a setting; the master
  and nodes elsewhere do not (over the mesh every byte would cross the master). When the
  cache is off, podman and apt go to the source by themselves.
- docs/design/local-first.md: the plan for the cache, netboot and a control node that starts
  from a Raspberry Pi; what ublue-os/forge has that is worth taking.
- Tried: papaya as the cache, lychee pulling an image and updating through it, and both again
  with the cache stopped.

## 2.56.3 — watch on a copy of its own

- `watch` with a locked store decrypts it into a copy for itself alone and writes back what it
  changed (unless `secrets.age` changed meanwhile): the store's link is never touched, so the
  owner can open and lock the store while a look runs. Before, a look locked a store the owner
  had just opened, and a lock during a look made it write a new seed (2.56.2 stops that too).
- `admin.ssh_port` on a host with SELinux and no semanage (Fedora CoreOS): the port's label as a
  small policy module (`semodule`).

## 2.56.2 — never a new seed beside a locked store

- A command that needed the fleet's seed while its secret store was locked made a new seed in
  a plain `secrets/` directory — different from the store's, so every generated secret would
  have differed, and the next `secrets unlock` refused the directory. It says the store is
  locked now. Seen once on the kiwi2 test fleet with the watch timer; nothing was applied with
  the stray seed. `watch` also checks that the store really opened.

## 2.56.1 — fail2ban for public sites

- kiwi-modules 0.27.1: on a master with public sites fail2ban also bans scanners (requests for
  wp-login.php, .env, cgi-bin and the like), bad requests and failed basic logins, from the
  host's port forwarding. Tried on sorbet: four probes from crumble, then a ban.

## 2.56.0 — SSH off port 22, fail2ban

- `admin.ssh_port` (22 unless set), also in the GUI: an apply moves sshd there without a
  lockout — 22 and the new port first (firewall and SELinux told, `sshd -t`), 22 dropped only
  after kiwi-server logged in over the new one. kiwi-server reaches every host on its port
  (`ssh://user@address:port`), and on 22 while a move is under way.
- kiwi-modules 0.27.0: the `fail2ban` module, in the master preset: an sshd jail and a rate
  limit of new SSH connections per address, on whatever port sshd listens.
- Tried on sorbet: port 41022, 22 closed; crumble banned after six failed logins.

## 2.55.3 — watch opens a locked store

- `kiwi-server watch` opens a locked secret store from this computer's keyring for each look
  and locks it again after; a store the owner opened stays as it is. The timer went on
  working only while the store happened to be open (it locks itself after 8 hours).

## 2.55.2 — synthetic users

- `tests/drills/users.py`: WireGuard clients of the master run here, doing every minute what a
  person does (names, every service, a file to Nextcloud and back), with failures and response
  times per name; across a backup, a restart or an update.
- kiwi-modules 0.26.3: nodes keep their tunnel alive every 10 s (was 25): after the master's
  WireGuard server restarted, lychee's services were gone until its next keepalive.

## 2.55.1 — federation and SSH certificates in the app

- GUI menu: *Federation…* lists the partner networks with what each may reach here (Shared
  here, From) and links another network's fleet file; *SSH certificates…* makes the CA, trusts
  it on this computer and signs a key for a number of days.

## 2.55.0 — a check after an update

- `kiwi-server check <fleet>`: from each master's Pi-hole container, every name of the fleet
  over HTTPS (a redirect, a login or a refusal counts as an answer), a fleet name and an
  outside one in DNS, and the exit's address. Read-only; exits 1 when something fails.
- kiwi-modules 0.26.2: a master's containers found its own names at an address where nothing
  listens (the proxy lives in the WireGuard server's namespace); `check` found it on sorbet
  and crumble.

## 2.54.1 — an apply waits for the containers that follow

- kiwi-stack 0.6.2: the check after an apply asks again for a minute; an apply that restarted
  the WireGuard server failed it while the containers in its network were still following.

## 2.54.0 — federation

- `kiwi-server federate <fleet> <other fleet>` links two networks: each master gets the
  other's link key, address, mesh and DNS in `federation.yaml` next to its fleet file. What a
  network shares is its own `share` (a node, `node:port`, or `service.node`) and `from` (the
  partner's groups or addresses) there; nothing is shared until written.
- kiwi-modules 0.26.1: the masters' WireGuard link (`wg-fed`, UDP 51822), its firewall, the
  partner's domain in the Pi-hole, and per-name partner access in the nodes' nginx. A partner's
  connections reach the nodes from one address of this mesh (`as`, 10.x.255.N).
- `tests/drills/federation.py`: a client of the partner reaches the shared service, gets 403
  on another name of the same node, and nothing from a node that is not shared.

## 2.53.2 — multi-hop, tried

- kiwi-modules 0.25.2: a master whose VPN client leaves through another network's master
  keeps its own mesh address; its Pi-hole answered its names with the other network's.
- `enroll` and `mtu` reach the master the way `apply` does, not only by its name, which does
  not resolve off the mesh.
- `tests/drills/multihop.py`: a client of one master leaves at another network's master,
  resolves the first master's names, and the second network's mesh stays closed. Passed with
  sorbet exiting through crumble.

## 2.53.1 — VPN groups in the app

- GUI: the WireGuard server's client groups are a list to edit, not YAML: each group with
  its addresses, what it may reach, other VPN clients, the internet exit and the admin pages;
  a new group gets the next free /24.

## 2.53.0 — a master in RAM

- `target: coreos-ram`, a prototype for masters that live in RAM only: the Fedora CoreOS
  live ISO with the master's live Ignition, booted every time, touching no disk. The image
  carries no role, only the machine's SSH host key (`secrets/ram/<host>/`, signed by the SSH
  CA when the fleet has one, pinned in `known_hosts`).
- `kiwi-server watch <fleet>`: sends the role to a RAM master that booted blank, putting
  back its WireGuard identity and Let's Encrypt state from the fleet's copies, and refreshes
  those copies hourly while it runs. `--timer on`: a user timer that looks every minute.
- `ram.copy_to_ram` (off: copying the image into RAM needs 3 GiB, a 2 GiB machine stops in
  emergency mode), in the GUI under RAM. Design and the answer to the roadmap's objection:
  docs/design/ram-master.md. Tried in a local VM without a disk.

## 2.52.0 — a plan before an update

- `kiwi-server plan <fleet> [host...]`: what an apply would change on each running machine
  (the stack's files, quadlets and units, by hash) and which containers it would restart, by
  kiwi-stack's own rule. Read-only; run it before an update. Right after an apply it says
  "nothing changes".

## 2.51.1 — the night's backup runs

- kiwi-modules 0.25.1: a backup by hand in the evening no longer skips the nightly run (seen
  on all three test machines the night of 11 November).
- `tests/drills/soak.py`: a read-only watch over hours, noting problems, restarts, reboots
  and backups.

## 2.51.0 — an SSH CA

- `kiwi-server sshca <fleet> init` makes the fleet's SSH CA (`secrets/ssh_ca`). Every apply
  then signs the machine's host key for its names and addresses and lets its sshd take user
  certificates; `sshca sign KEY [--days N]` lets a key in on every machine for N days,
  `sshca trust` adds the CA to `~/.ssh/known_hosts`.
- A machine at an address the fleet never met is reached when it shows the CA's certificate,
  still never by trusting the first key it shows.
- GUI: text too long for its row is cut, the rest behind an (i); "?" stays for what a field
  means. Role and module descriptions are behind an (i).

## 2.50.0 — backups in the app

- `kiwi-server backups <fleet> <host>`: what each backup server keeps of a machine;
  `--now` backs up, `--restore [MODULE...] [--at TIME]` brings modules back (all without
  names), `--new` starts a new history on a rebuilt machine.
- GUI, on a host's page: Backups (last backup, copies, Back up now, Snapshots with Restore,
  a rebuilt machine's Restore all / Start new, Prune now on a backup server) and Unlock for a
  disk that waits at boot.
- kiwi-modules 0.25.0: what prune keeps is the backup server's `keep_daily`, `keep_weekly`,
  `keep_monthly`; the flags still override.

## 2.49.0 — settings in pages

- The GUI's settings have a sidebar: machine, role, one page per module, then the system
  sections; a host shows only its target's. Help sits behind a plain "?" (as in the other kiwi
  apps), no longer in the rows.

## 2.48.0 — every setting explained

- kiwi-modules 0.24.2: help for the 94 module settings that had none.
- Every field in the GUI's settings has a (?) with what it is for and a link to its
  documentation: a module's `module.yaml`, a role's `role.yaml` or the fleet file's section of
  the README. The 16 base fields without help have it now, and tests keep a new setting from
  coming without one.

## 2.47.1 — machines that already run are not new

- `status` remembers every machine it finds with its role applied, so `list` no longer tells
  a machine an older release (or another computer) applied to install itself from its ISO.
- `list` shows an ISO outside the working directory from `~`, not as `../../..`.

## 2.47.0 — a guided start

- `kiwi-server init fleet.yaml` asks a few questions in the terminal (domain, the admin
  key, the master and where it runs, the exit, the machines at home, a secret store) with
  what it found on this computer as the suggestions, and writes a fleet that validates and
  waits only for its master. `init --questions` and `init --answers FILE` are the same for
  the app, `init --example` keeps the commented example.
- The GUI's *New fleet* is a wizard on those questions, each with a (?) that explains it and
  links the documentation.
- Machines are named after fruits and masters after fruit dishes: `kiwi-server add fleet.yaml
  node|master|bare [name]` appends a host with the next free one and what its role needs,
  `kiwi-server names` prints the suggestions; the GUI's *+* asks for the role and suggests
  the name.
- `list` says where each host stands (enroll, build, install, applied) and what comes next;
  `apply` records which host it applied. A node whose WireGuard config is missing is "not
  enrolled yet", with the command that enrolls it, no longer "file not found". The GUI shows
  the next step on every machine not installed yet.

## 2.46.1 — an apply leaves a data directory's owner alone

- kiwi-stack 0.6.1: an apply no longer gives a container's data directory to the stack user
  when the container owns it as another (Tang could not read its keys after an apply of
  2.46.0), and an image that cannot be pulled no longer stops an apply when the machine has a
  copy.

## 2.46.0 — an apply restarts only what changed

- kiwi-stack 0.6.0: on a running podman stack an apply restarts only the containers whose
  quadlet or files changed and starts what is new; the rest goes on serving. Every container
  restarted before (problem 55), on a master every VPN client's tunnel with it.

## 2.45.0 — one fleet, several computers

- `kiwi-server secrets join <fleet> <name>` on another computer makes it a key in its own
  keyring and prints what to run on a computer that can open the store:
  `secrets add <fleet> <name> <key>` encrypts the store for it too, `secrets remove` takes a
  computer out again. The store keeps its id in `secrets.recipients` once the first key goes,
  so the other computers' keyrings still find theirs.
- A computer no longer saves over a `secrets.age` that another one saved since it opened the
  store: it refuses, says so and keeps its changes in memory.

## 2.44.0 — rendered secrets stay in memory

- While the secret store is open, `render`, `apply` and `build` write the rendered files into
  its tmpfs beside it, as design/control-plane.md always said: the role scripts, the stacks with
  their env files and the installer configs hold the fleet's secrets and are made again by
  every command. Only the ISOs stay on disk in `output/<host>/`; a render removes the plain
  copies an older release left there. Without a store nothing changes.
- A bare ISO says so beside itself (`<host>.iso.bare`), so `vps install` no longer needs the
  rendered role to know it may upload one. An ISO built by an older release has no such mark:
  build it again before `vps install`.

## 2.43.1 — the automatic restore finishes

- kiwi-modules 0.24.1: a restore no longer restarts kn-backup.service, which killed the
  automatic restore running in it, and that unit has no start limit any more, which kept the
  night's run from starting after a few applies; its retries back off from an hour to eight.

## 2.43.0 — two copies, and a rebuilt machine brings itself back

- kiwi-modules 0.24.0: a machine backs up to every server in `backup.servers`, each with a
  full copy of its own; a rebuilt machine restores every module by itself at its first run;
  a commercial VPN exit stuck on a bad server is started anew with another.
- `kiwi-server status` lists each backup copy's age and warns when one falls behind while
  the others are fine.
- `kiwi-server prune <fleet> --every week|day|off`: a systemd user timer on this computer that
  prunes the fleet's backup nodes; a missed run comes at the next login. `status` says on each
  backup node when it was last pruned from here, and warns after three weeks.
- `tests/drills/`: drills that break the test machines on purpose, refusing every host not in
  `drills.yaml` next to the fleet file. The first: a commercial VPN exit stuck on a bad server.
- The roadmap has netboot (PXE and Wake-on-LAN), more hosters, masters without LUKS by
  default, the RAM-only master (parked) and testing updates before they reach live machines.

## 2.42.0 — every disk in sight

- `kiwi-server status` shows every machine's disks (how full, how big), the stack's own
  among them.
- A backup node says what it keeps for each machine (size, snapshots), how much is free and
  how long that lasts at last week's rate; less than 30 days is a problem, less than a week a
  bad one, and both say to prune. The figures are in `--porcelain` for the app.

## 2.41.0 — a restore from before

- kiwi-modules 0.23.0: `kiwi-stack restore --at TIME MODULE...` puts back the newest snapshot
  of each module made at or before TIME, and `kiwi-stack snapshots MODULE...` lists only those.

## 2.40.0 — what shares a network comes back with it

- kiwi-modules 0.22.0: a container in another's network is bound to it and wanted by it, so
  Pi-hole, Tor and nginx come back when the VPN container does, after a stop or a crash. The
  master's public sites stayed down after its WireGuard server had been stopped and started.

## 2.39.0 — a rebuilt machine does not back up its empty self

- kiwi-modules 0.21.0: a machine that has made no backup but finds snapshots of itself on the
  server holds its backups until `kiwi-stack restore` or `kiwi-stack backup --new`. A rebuilt
  master had backed up its empty self during its first apply, and `restore` put that back.
- `kiwi-server status` says when a machine's backups are held, and why.

## 2.38.0 — a reinstalled machine can open its disk

- `unlock --manual` opens a reinstalled encrypted Debian machine. Its initramfs shows a new
  host key, which ssh refused against the old one on record, and the key on record was the
  old disk's: `unlock` retried for ten minutes and said no initramfs had answered. A host key
  that is not on record now gets the one-time key of the new ISO and nothing else.
- No ring is forwarded to an initramfs whose host key is not on record, and `unlock` says so
  at once instead of waiting out its timeout.
- A timeout says what ssh said last.

## 2.37.0 — what a module leaves behind goes with it

- kiwi-stack 0.5.0: an apply removes the files a module rendered once it has left the stack,
  as it already did with its host units. A backup script left behind kept `status` reporting a
  backup that would have turned late after a day and a half.

## 2.36.0 — a full disk costs one module

- kiwi-modules 0.20.0: a backup repository that cannot take a frozen database ends the run
  instead of pausing each module in turn, a database is paused for at most ten minutes, a file
  that goes away during a backup no longer fails the run, and a stale exclusive lock no longer
  stops a run before it can be removed.

## 2.35.0 — what a night of pulling plugs found

- `kiwi-server status` says when a machine is waiting at its locked disk, and with which
  command to open it, instead of calling it unreachable. On CoreOS the initramfs answers on
  the system's own SSH port, where a command used to suggest removing the host key.
- `status` shows how long the public certificates are good for, and calls it a problem when a
  certificate should have been renewed by now: the job that renews them does not fail.
- `prune` removes the locks of dead restic processes first. One left by a machine that lost
  power stopped it for good.
- kiwi-stack 0.4.0: a podman machine switches off a Docker daemon that runs nothing. It kept
  the published ports from the network after a reboot while every container looked healthy.
- kiwi-modules 0.19.0: a backup that dies or hangs lets its databases go, tries again an hour
  later and clears dead locks; a machine restored with two ACME accounts can renew again.

## 2.34.0 — the certificates come back too

- kiwi-modules 0.18.0: the backup holds a public site's Let's Encrypt certificates and
  ACME account, so a rebuilt machine does not have to ask again.

## 2.33.0 — status knows about backups and disks

- `kiwi-server status` says when a machine with the `backup` module last backed up, warns
  after a missed night and calls a week without one bad, and says whether the disk is
  unencrypted or opens only with a typed key. The app shows the same.
- A machine keeps its reboot window when rings it reaches by itself are enough to open
  its disk, even if another ring has to be forwarded in.

## 2.32.0 — a ring on this machine

- The ring named **`operator`** is served by kiwi-server itself, from the operator's machine,
  only while it is needed and only on that machine's loopback, with its keys in the secret
  store. It is for what no other machine can open: a fleet with one master, the machine
  that serves the other rings. `unlock.via` forwards it in.
- `kiwi-server vps install` gives every upload a name of its own: netcup booted the previous
  ISO of the same name, so a machine came up without the encryption it was built with.
- It also refuses to encrypt a CoreOS root on a server with too little memory. The first
  boot copies the whole root into memory, about 4 GiB is needed, and with 2 GiB it stops in
  emergency mode with nothing to see from outside.

## 2.31.0 — a master comes back with its identity

- A master's WireGuard state is kept with the fleet's secrets, refreshed by `apply` and
  `enroll`, and put back on a machine that has none before its stack starts. A rebuilt
  master keeps its server key and every peer, with nobody re-enrolling. Its data backup
  could not do that: it goes over the mesh, and the mesh is that identity.
- kiwi-modules 0.17.0: `kiwi-stack restore vpn-server` says why it will not, and a
  restore starts the whole stack again afterwards.

## 2.30.0 — an encrypted install with no console

- A Debian host with `unlock.ssh` and `debian.encrypt` has SSH in its initramfs from the
  first boot on, so a machine at a provider needs no web console: `kiwi-server unlock
  <fleet> <host> --manual` hands the one-time key over. The host key is taken at that
  first contact, for a key that dies at the first apply, and is pinned from then on.
- `kiwi-server unlock --manual` tries again when the connection fails: only a key the
  initramfs refused counts as a try.
- A machine whose ring has to be forwarded in does not reboot by itself any more: it
  would wait at boot until someone opened it.

## 2.29.0 — the relay

- kiwi-modules 0.16.0. **`vpn-server.unlock_relay`** lets a machine behind NAT ask the
  master's public port for its ring, past a gate that holds only the addresses enrolled
  machines last connected from: an `unlock.via` of `http://<master>:<port>`. The `tang`
  module's rings are reachable at the node's mesh address for it.

## 2.28.0 — the initramfs key can do one thing

- The initramfs has a key of its own, `secrets/unlock_ed25519`, which opens no running
  machine, and its line in `authorized_keys` allows what unlocking needs and nothing else:
  a forced command, and on CoreOS one forwarded port and `unlock.ssh_from`. Dropbear has no
  `from=` and ignores a key that names one, so `ssh_from` is refused on Debian.
- CoreOS's initramfs sshd takes forced commands only and forwards inwards only.

## 2.27.0 — CoreOS through the tunnel too

- `unlock.ssh` works on CoreOS: `dracut-sshd`, layered, with a host key of its own, and
  `kiwi-server unlock` forwards a ring in through it. Its host key goes on record at the
  apply, and the reboot after that apply is the first with SSH in the initramfs.
- A CoreOS machine whose ring is only reached through a forwarded port can install:
  Ignition sizes the root partition, because the growing at the first boot takes a ring
  that is not there yet, and the ring is forwarded in to add the key for the console.
- `kiwi-server unlock` knows it got in from what the session prints, not from how long
  it lasted; `--port` is for an initramfs reached through another port.

## 2.26.0 — a disk that opens by itself

- kiwi-modules 0.15.0: the **`tang`** module serves rings.
- A host's **`unlock:`** block names the rings that may open its disk. On Debian `apply`
  binds the disk with Clevis and gives the initramfs network; its key stays in another
  keyslot for the console. On CoreOS Ignition binds it at install, and the first `apply`
  adds a key for the console.
- `unlock.via` names the way to a ring that is not on the LAN. `unlock.ssh` puts an SSH
  server in the initramfs (Debian), with its host key pinned while the machine can vouch
  for it. **`kiwi-server unlock <fleet> <host>`** forwards a ring in through it, and
  `--manual` hands over the disk's key instead.

## 2.25.0 — old backups go

- **`kiwi-server prune <fleet>`** removes old snapshots from the backup nodes and frees
  the space: seven days, four weeks and six months are kept, and `--keep-daily`,
  `--keep-weekly` and `--keep-monthly` change that; `--dry-run` only says what would go.
  It runs from the operator's machine because a machine can only add to its backups and
  the node has no keys. The password reaches the node on stdin, for that one run.

## 2.24.0 — backups of a machine's data

- kiwi-modules 0.14.0 and kiwi-stack 0.3.0. The module **`backup`** keeps a machine's
  data with restic, encrypted, on a node running the new module **`backup-server`**:
  every night and with `kiwi-stack backup`. `kiwi-stack restore MODULE...` puts the
  newest back and stops only those modules; `kiwi-stack snapshots` lists what is stored.
- Each machine has its own repository and can add to it but not delete. Passwords come
  from the fleet's seed, so the seed brings the backups back.
- A module says what it wants kept in a `backup:` block (docs/modules.md).

## 2.23.0 — folders the apps share

- kiwi-modules 0.13.0. **`cloud.shared_folders`** and **`cloud.read_only_folders`** put
  folders into every user's Nextcloud that other apps on the host can use.
  **`immich.external_dirs`** mounts one read-only for an External Library.
  **`paperless.user_id`** lets Paperless share a consume folder with Nextcloud, and
  **`paperless.filename_format`** gives its archive readable names.
- kiwi-modules 0.12.1: Paperless's login page no longer answers 403.

## 2.22.0 — OpenCloud, Immich and Paperless-ngx to try out

- kiwi-modules 0.12.0: three new modules for a node, `opencloud`, `immich` and
  `paperless`, each behind the reverse proxy with a setting for where its data lives.
  None is in a preset: add them to `modules:`.
- A new Nextcloud installs end-to-end encryption by default.

## 2.21.0 — a calendar's way to Nextcloud

- kiwi-modules 0.11.0. **`hub.caldav`** lets a hub's calendar reach a Nextcloud through
  the hub, from the mesh only, on a public hub too. **`cloud.cors_origins`** lets hubs on
  other addresses, such as Armory, use CalDAV from the browser.
- **Nextcloud sees who its clients are.** It recorded every client as `127.0.0.1`, so
  brute-force protection counted nothing. The gateway no longer hides LAN clients of
  published ports behind the bridge address.
- A master's containers can reach the mesh: the answers were being dropped.

## 2.20.0 — local traffic stays local

- **A node's Pi-hole and hosts file answer with the LAN address of the nodes on its
  own LAN**: machines with a static address in its subnet, worked out from the fleet
  file. A client at home went through the tunnel to the master and back to reach a
  machine on its own switch; on the test network a page went from 176 ms to 48 ms.
  Mesh clients still ask the master's Pi-hole and get mesh addresses.
- kiwi-modules 0.10.0, and with it 0.9.1: **the `gateway` module works for LAN
  clients**. Two of its rules were invalid and firewalld refused forwarded traffic, so
  it had never carried a real LAN client, and the rule keeping LAN clients off the
  container network was missing. A health check of the form `[CMD, ...]` now runs
  without a shell, so Collabora no longer reports unhealthy while working.

## 2.19.0 — a public relay knows its members

- kiwi-modules 0.9.0: `nostr-relay` authenticates connections (NIP-42), and who
  counts as a member is a setting, `members: approved | registered`. A signed-in
  member may post what other keys signed, as a hub's boards, groups and gift wraps
  are; anyone may send a gift wrap to a member or sign up with proof of work; bans
  beat membership. It runs our own strfry image, release 1.1.3 with a small patch,
  because stock strfry does not tell the write policy who a connection is.
- kiwi-stack 0.2.4: behind chrony a stack waits with `chrony-wait.service`.
  `systemd-time-wait-sync` sat out its 90 seconds at every boot there and failed.

## 2.18.0 — the app shows what the machines say

- **The app has an overview**: machines online, services running, what needs a
  look, a card per machine with memory and disk. Each host has an *Overview* tab
  beside its settings: system, load, memory, disks, every service, the tunnel, the
  web interfaces, when the role was applied. The sidebar row says whether the
  machine answers, how long it has been up and how many services run. It asks once
  a minute while it has the focus, and opens the fleet you had last.
- **`kiwi-server status --porcelain`** returns `state`, `problems`, `notes` and
  `facts` per host, from one read-only probe, for every host at once. What counts as
  a problem is decided there: a service down, a tunnel missing, a VPN client that
  does not forward, a disk filling up, a clock that is off. A machine that booted a
  moment ago is late, not broken, and a healthcheck still starting is not a failed
  unit.
- **`apply` and `status` find the machine**: its name, its static address, the
  master's public address or its mesh address, whichever answers. Only addresses
  whose host key is already on record count, so a stranger at the same address on
  another LAN never gets a role script; `--ssh` still says exactly where.
- `kiwi-server secrets status --porcelain`, with when the timer locks the store;
  the app has *Unlock* and *Lock*. `list --porcelain` adds the web names of a host,
  its update window and the fleet's domain and mesh.
- The app showed no help for settings whose text has `<hostname>` in it: fixed.

## 2.17.0 — secrets/ as one encrypted file

- **`kiwi-server secrets init|unlock|lock|status <fleet>`**: `secrets/` becomes
  `secrets.age`, encrypted with `age` to one key in the GNOME Keyring and one
  recovery key shown once. `init` checks that both open the file before it
  removes the plain files.
- `unlock` opens it into memory, in `$XDG_RUNTIME_DIR`, with `secrets` a link
  there, so everything else works as before. A timer locks it again after 8 hours
  (`--for`). A command that changed something saves it before it exits, so a
  power cut cannot take a new disk key with it.
- **A locked store stops every command that reads the fleet**, so nothing can
  make a new seed in place of the one locked away.
- Stores nothing by itself: a fleet stays plain files until `secrets init`.
- Not yet: `output/` in memory, an encrypted fleet file, adding a way of
  unlocking to an existing store, and the fleet backup in `age`.

## 2.16.0 — a public hub, and nothing else public with it

Carries kiwi-modules 0.8.0.

- **`reverse-proxy.public`**, for a master, publishes 80 and 443. A site whose
  name is outside the fleet's domain (`hub_domain`, `relay_domain`) is public;
  every other site, the admin pages among them, answers the mesh only, enforced
  in nginx per site.
- **Let's Encrypt** certificates for the public names, renewed by a host timer.
  nginx starts on the fleet's certificate until the first one is issued, and
  `acme_staging` tries it out on the test service.
- **A public relay is not an open one**: anyone may read, the mesh may post, and
  from the internet only the keys in `nostr-relay.write_pubkeys`. `open_writes`
  is the old behaviour, by name.
- **The relay reads the client's address from `X-Real-IP`**, not
  `X-Forwarded-For`, which a client can pre-fill.
- Run on a test master with real names. From outside: the hub and relay answer
  with a trusted certificate, `wg.` and `pihole.` answer 403, a signed post from
  a stranger is refused even with a forged address, and only 22, 80 and 443 are
  open.

## 2.15.0 — hubs live on masters

- **`hub` and `nostr-relay` are for masters now** (kiwi-modules 0.6.0): a hub may
  be public, and a master is the machine made to face the internet. A node that
  lists them is refused, and the node role's `hub` preset is gone; a master
  lists them in its `modules:`.
- Run on a test master: a device on the mesh gets the hub's page and config, the
  relay's NIP-11 document and a websocket, with the fleet CA verifying.
- Public hubs are not here yet: they need mesh-only rules on the master's other
  sites before 443 can be published (ROADMAP).

## 2.14.0 — measure the MTU instead of guessing it

- **`kiwi-server mtu <fleet> <node|device|address>`** sends don't-fragment
  pings through a tunnel and finds the largest multiple of 16 that arrives
  whole. A node measures from its own VPN client, because the client's firewall
  drops pings coming in; a device is measured from the WireGuard server.
- A reply that had to be reassembled does not count as fitting: WireGuard's
  outer packets may be split on the way even when the ping comes back.
- It says what to set. For a device, that device's own `MTU =`, with nobody
  else affected. For a node, `wg_mtu` on it and on its master; `--write` puts
  that in the fleet file, changing only those lines.

## 2.13.0 — a server at netcup without the web panel

- **`kiwi-server vps`** drives netcup's Server Control Panel API. `vps login`
  signs in through the device flow, so no password passes through here, and
  keeps a refresh token in `secrets/netcup.token`. `vps list` shows your
  servers, and `vps probe` what one really is, the `disk:` it needs among it.
- **`vps install <host>`** does what the first hardware round did by hand:
  upload the built ISO, boot it, wait for the installer's power-off, detach it,
  put the disk first again, remove the ISO from netcup's store, power on, and
  forget the machine's old SSH host keys. It asks before wiping, and refuses
  an ISO that carries the role's secrets or a `disk:` that is not the server's.
- A host names its server with `vps: { server: ... }`; a master is found by its
  `wg_host`.

## 2.12.0 — service passwords no longer have to be written down

- **Service passwords left out of the fleet file are generated** from
  `secrets/seed` (kiwi-modules 0.5.0): `wg_password`, `pihole_password`,
  `transmission_password`, `sftp_password`, and the cloud's and Portainer's
  `admin_password`. A password that is written down is used as before. Portainer
  left empty used to wait on its first-run page; it now starts with a generated
  password.
- **`kiwi-server secrets show <fleet> <host> [setting]`** prints a host's
  password, or the names of those it has. It reads the fleet on this machine.
- **`validate` warns** about passwords written in the fleet file that need not
  be, and about `admin.password`. A warning for now: refusing them waits for the
  secret store.
- `kiwi-server init` writes a fleet file with no passwords in it.
- Taking a password out on a running machine changes it. Nextcloud's and
  Portainer's are only read at their first start, so there the old one stays
  in force.

## 2.11.0 — a disk key that outlives nothing

- **`debian.encrypt` needs no passphrase**: kiwi-server makes a short one-time
  key per install (`secrets/luks/<host>.once`), typed at the first boot, and
  the first `apply` replaces it with a generated one
  (`secrets/luks/<host>.key`) before the role's secrets reach the machine. The
  keys travel on stdin and live on tmpfs on the way. A run that is cut off can
  be repeated.
- **A master also gets a new volume key** at that apply (`cryptsetup
  reencrypt`, some minutes; `debian.reencrypt` for any host). Replacing a
  passphrase keeps the volume key, so someone who held the ISO and the disk
  during the install could otherwise still decrypt later snapshots.
- A literal `debian.passphrase` still works, unchanged, and `validate` now
  warns that it is in the fleet file and on every ISO.

## 2.10.0 — install media carry no stack unless asked to

- **`build` and `script` make the base system by default**: hostname, network,
  the admin user and the update policy, with `apply` bringing the role and its
  stack afterwards over SSH. Before, a plain `build` put every secret of the
  stack on the ISO. `--bare` is still accepted and now only says the default.
- **`--offline` asks for the old behaviour by name**, for a machine that
  cannot be reached over SSH, and says when it is used that whoever holds the
  ISO or script holds those secrets.
- The GUI's Build makes base-system ISOs and says so; its script preview still
  shows the whole role.
- Not yet: a Debian host with `encrypt: true` still has its LUKS passphrase in
  the ISO's preseed, until the one-time key lands (ROADMAP, November wishes).

## 2.9.1 — what a reboot exposed

- **A master's mesh clients have internet after a reboot** (problem 61): the
  VPN client's namespace copied the host's `ip_forward` while it was still 0,
  so nothing could leave through it until the stack was restarted.
  `vpn-client` sets the sysctl itself. A master applied with an earlier
  version needs one re-apply.
- **Tor no longer races the tunnel** (problem 59): where it shares the
  WireGuard server's namespace, it may bind the mesh address before `wg0` has
  it.
- **The stack waits for the clock at boot** (problem 60): a machine that came
  up with its clock ahead and opened its tunnel before NTP stepped it back
  was shut out of the mesh until real time caught up. `kiwi-stack.service`
  starts after `time-sync.target`, bounded at 90 seconds where there is a VPN
  client.
- Carries kiwi-modules 0.4.3 and kiwi-stack 0.2.3 (`VENDORED`).

## 2.9.0 — a hub and its relay

- **A hub and its relay** (kiwi-modules 0.4.2): the `nostr-relay` module
  (strfry behind the reverse proxy at `relay.<host>`, a mesh name the
  fleet's DNS resolves and the fleet CA signs) and the `hub` module (a
  distribution built with kiwi-framework's `container/Dockerfile`, Armory's
  `ghcr.io/derlocke-ng/armory` by default, behind the proxy at
  `hub.<host>`; its relays, policy and public area set from the module's
  settings and written to the page's `hub.local.json` at start). The node
  role's `hub` preset is the two behind the VPN client: a private hub as it
  comes, semi-public with `public_relays` and `public_mounts` under `hub`.
  `hub` also has `account_mounts` and `require_account` (mounts that ask for
  an account, a username and password, not a key alone; for kiwi-framework
  0.19) and `space_relays` (a space's own relays).
- **`tests/check-version.sh` also compares the vendored kiwi-modules** with
  the package's own version: kiwi-modules 0.4.0 and 0.4.1 went out with the
  two disagreeing, and vendoring either would have carried that here.

## 2.8.0 — the stack network and the tunnels get an MTU each

- **The stack network and the tunnels have an MTU each** (problem 40):
  `bridge_mtu` (default 1500) for the stack's bridge and `wg_mtu` (default
  1376) for gluetun's `WIREGUARD_MTU`, wg-easy's `WG_MTU` and the server's
  own `wg0`. One `mtu` of 1412 for all three split every full-size tunnel
  packet in two on the bridge, which carries the encrypted packet, 60 bytes
  bigger over IPv4 (80 over IPv6) — TLS stalls and stuttering streams through
  the VPN on the v1 fleet.
- **WireGuard's padding counts**: a packet is padded to a multiple of 16 as
  far as the sending peer's MTU allows, so a peer with a bigger MTU (the
  commercial exit) grows it; on the v1 master 1352 became 1360 and 1420
  bytes on a 1412 bridge. `validate` refuses a `wg_mtu` whose padded packet
  plus 80 bytes does not fit `bridge_mtu` (at most 1408 under 1500), or one
  below 1280. 1376 is a multiple of 16: 1436 bytes over IPv4 fits a
  1452-byte DS-Lite path, 1456 over IPv6 a 1460 one.
- **The old `mtu`**: a fleet that still sets it gets it as `wg_mtu`, the
  bridge at 1500, and a warning to rename it; `mtu` and `wg_mtu` together
  are an error, and so is the usual `mtu: 1412`, which no longer fits once
  padded — drop it.
- **The WireGuard server's `wg0` is set to `wg_mtu`** in `start.sh`, after
  the iptables-nft shim: wg-quick sized it from the bridge (1332 under a 1412
  bridge) while the clients were told another MTU.
- **The MSS is set, both ways**: wg-easy's `start.sh` and the VPN client's
  post-rules set every forwarded SYN to `wg_mtu - 40` (`--set-mss`, which
  never raises a smaller one) instead of `--clamp-mss-to-pmtu`, which reads
  only the interface a SYN leaves by — on the v1 master the way back was
  wider than the exit and every first full-size upload segment bounced.
- **A node with the gateway module sets the TCP MSS** in its VPN client by
  default (`vpn-client.mss_clamp`, as on a master): LAN clients routed
  through it advertise MSS 1460, more than the tunnel carries. The host's
  own `mss_clamp: false` still wins.
- **Namespace sharers are never recreated alone**: a test keeps `--no-deps`
  out of kiwi-stack, lib.sh and the rendered stack (compose 2.21 gave a
  sharer the literal network mode `service:kmvpn-server`, and the v1
  master's Pi-hole stayed down ten minutes); the README and the runbook say
  how to recreate one by hand.
- **Upgrading a running master**: the new `wg_mtu` recreates wg-easy, and
  Pi-hole, Tor and nginx in its namespace with it (the apply restarts the
  whole stack). Client configs already issued keep their old `MTU =` until
  downloaded again from wg-easy; apply the nodes too, and re-run
  `kiwi-server openwrt` for a router.
- `kiwi-server openwrt` writes the master's `wg_mtu` as the router
  interface's MTU, not whatever an older client config says.
- tests/golden: the MTU lines of every stack, `ip link set dev wg0` in the
  master's `start.sh`, the clamp in m1's `post-rules.txt`.
- Carries kiwi-modules 0.3.0 (`VENDORED`), where the renderer and module
  side of this change is developed.
- Applied to the test fleet's two masters and a node before the release: no
  packet split through the mesh and the exit. The numbers are in ROADMAP
  problem 40, with problems 59 and 60, found on the way.

## 2.7.1 — the enroll tunnel beside an operator's ssh multiplexing

- **`enroll` works on an operator whose `~/.ssh/config` multiplexes sessions**:
  the tunnel to wg-easy is a channel-less `ssh -N -L`, and through an
  existing ControlMaster it ended at once with "Broken pipe" and exit 0, so
  `enroll` reported "the tunnel to port 51821 failed: ssh exited" while
  `apply` worked. Every connection kiwi-server opens now says
  `ControlMaster=no` (problem 54, found on the first device enrolment).
- **Clients in a group get their answers**: inside the WireGuard server the
  whole mesh is routed into `wg0`; before, only the server's own /24 was,
  and a device in the `devices` group received nothing back (problem 56,
  found with the first device). tests/golden/gate: one line of `start.sh`.
- **A node with only Portainer behind its proxy gets the mesh DNAT too**
  (`portainer.needs_vpn_dnat: true`, problem 58). Found with the same
  device: on uCore a mesh client's connection to a node's proxy died on the
  host's conntrack, which saw only the reply of the DNAT'd flow and dropped it
  as invalid — netavark's forward chain does that on every podman host,
  firewalld on top of it (problem 57). The role now tracks bridged container
  traffic (`br_netfilter`, `bridge-nf-call-iptables`) on every podman host
  with a mesh, and where firewalld runs trusts the mesh subnet like the stack
  network, so the client's own address still reaches nginx; the gateway
  module's masquerade for LAN clients leaves the stack network's and the
  mesh's traffic alone, which the tracking had exposed it to.
- **The split's flip**: kiwi-modules and kiwi-stack are developed and
  released in their own repositories; this tree carries them as vendored
  copies pinned by tag in `VENDORED`. `tools/vendor.sh` brings a release in
  (from a checkout next to this one or from github.com), `--check` reports
  a tree that differs from its pins and runs in CI; `tools/split-sync.sh`,
  which copied the other way, is gone.
- Runbook step 5 passed on a node: a module added and removed again, the
  runtime switched to docker and back, every time with `verify` green and
  the old stack stopped first; noted that a re-apply restarts every
  container of the stack, not only the changed one (problem 55).

## 2.7.0 — install media without the stack

- **`kiwi-server build --bare`** (and `render --bare`): the ISO carries the
  base system only — hostname, network, the admin user and keys, the update
  policy — and the machine comes up as `bare`; its role and every secret of
  its stack arrive afterwards with `kiwi-server apply` over SSH and stay
  0600 on the machine. For media that leave the house: a provider's ISO
  store, a stick in the post. Only the base system is validated, so a node's
  media can be built before the node is enrolled (its WireGuard config comes
  with `apply`, which checks the role's settings then). The per-host ISO with
  the stack embedded stays the offline path.
- **The install media power the machine off when the install is done**
  (`install.on_done: poweroff`, the default; `reboot` keeps the old
  behaviour): remove the stick or the provider's ISO, then power on. Media
  left first in the boot order would otherwise reinstall on every boot.
  Debian: `debian-installer/exit/poweroff`; CoreOS: the live ISO skips its
  reboot (`coreos.inst.skip_reboot`) and a post-install script turns the
  machine off. The install guard with a `--reinstall` override (ROADMAP,
  problem 14) stays open.
- **The first podman apply on a fresh machine no longer fails in the quadlet
  cleanup**: a grep over globs that matched nothing exited 2 and `pipefail`
  stopped the role (problem 41, found on hardware). `role.failed` and the
  error line now name the function the failing command sat in, not only
  the last step announced.
- **The master's WireGuard server comes up on an nf_tables host**: its
  `start.sh` uses `iptables-nft` where the image has it — inside the wg-easy
  container the legacy xtables fail with "Permission denied" on Debian 13 and
  Fedora hosts whatever modules are loaded (problem 42, found on the first
  master) — and `WG_POST_DOWN` is a no-op instead of wg-easy's legacy
  default. tests/golden/gate changes accordingly: two lines of `start.sh`
  and one environment line; nothing a client sees.
- **An encrypted Debian loads the console keymap into its initramfs**
  (`KEYMAP=y`, `update-initramfs -u` in the late command), so the LUKS
  prompt reads the keyboard the fleet file says, not US (problem 43, found
  on the first encrypted VM).
- **Containers without a fixed address no longer take the fixed ones**: the
  stack network has an `IPRange` (172.128.0.128/26 of a /24; the compose
  file's `ip_range`) for them, and a fixed address inside it is refused. On
  the first gateway+cloud node the Nextcloud AIO containers had taken .2,
  .4, .6 and .7 before the VPN client, Pi-hole, apache and Vaultwarden
  asked for them (problem 48). A changed `.network` quadlet now has its
  podman network recreated at apply (problem 49). tests/golden: the
  network unit and the compose network of every preset gain one line.
- **NAT out of the VPN client, at last**: in `post-rules.txt.j2` an inline
  `{% if %}` at the end of a comment line swallowed the newline before the
  masquerade rule, which became part of the comment in every rendered
  stack. gluetun's own traffic worked, everything forwarded through it —
  the mesh's internet on a master, the LAN's on a gateway node — left with
  a private source and never came back (problem 51, found on the first
  master with `wget` from the WireGuard server). tests/golden: the rule on
  its own line in every preset.
- **Pi-hole and podman's own resolver no longer fight over port 53**: with a
  resolver in the stack the role sets `dns_bind_port = 5533` in
  `containers.conf.d`, where podman moves aardvark-dns (problem 50).
- **The live installer ISO has a shell and shows the disks**: its own
  Ignition gives root and the admin user the fleet's console password and
  keys, and prints `lsblk` before the installer looks for `disk:`; a VPS
  whose disk is not where the fleet file says can be read at its console
  instead of guessed (problem 47).
- **A gateway node's VPN client can finish its handshake**: the gateway
  module's host rule that keeps LAN clients off the containers' addresses
  dropped every reply to what the containers started, the master's
  WireGuard answers among them, and every LAN connection to a published
  port; it now drops new, non-DNAT connections only (problem 52, found on
  the first gateway node). tests/golden/m1's gw.sh changes accordingly.
- **Pi-hole no longer answers a node's own names with a container address**:
  the `AddHost=` entries for the containers stay out of Pi-hole's container,
  which serves the fleet's records with `no-hosts` (problem 53, found on the
  first gateway node: LAN clients were sent to 172.128.0.5, then to the
  host's 127.0.0.1 entries podman had copied in).
- **The first boot waits for name resolution** (two minutes at most) before
  its first apt or pull: on the first machine booted from this tree, a
  Debian VPS, `network-online.target` was satisfied before the resolver
  was, and apt went on without updates (problem 38, found on hardware).
- **`apply`, `status`, `enroll` and the backup use the fleet's own key**:
  an `admin.ssh_keys` entry that is a `.pub` with its private key next to it
  (`secrets/admin_ed25519.pub`) is offered to the machines, and no other
  key then (`IdentitiesOnly`). Before, ssh offered whatever the operator's
  agent held, and the first apply to a VPS failed with "Permission denied
  (publickey)" because the fleet's key was not in the agent (problem 39,
  found on hardware).
- `tests/cli.sh` keeps the caller's Python user site, so a Jinja2 from
  `pip install --user` (the README's own line) is found.
- docs: the design after the October talks — the control node and the
  replaceable master, the bootc image (phoenix-server), names, the unlock
  with Tang rings and a netfilter relay, managed masters — in
  docs/design/control-plane.md; the test network and its machines in
  docs/HANDOFF.md; problems 32 to 36 and the owner's module wishes in
  docs/ROADMAP.md.

## 2.6.0 — the renderer and the machine side as repositories of their own

- **The renderer is its own package, `lib/kiwimodules`** (modules, quadlets,
  the settings schema, the profile loader, settings resolution, the stack
  manifest), the third step of the split (docs/ROADMAP.md). kiwi-server
  imports it; nothing in a rendered stack changed (tests/golden). The secret
  derivation and the seed id live there now, shared with the fleet.
- **`python3 -m kiwimodules render machine.yaml -o DIR`** renders one
  machine's stack from a one-machine file, without a fleet, into a bundle
  directory that `sudo kiwi-stack apply DIR` installs; `modules` and
  `profiles` list what there is. The same seed and settings give the same
  bundle kiwi-server renders for that host (tested byte for byte).
- The installer copies `lib/kiwimodules`; CI compiles it and shellchecks
  `roles/common/kiwi-stack`.
- **kiwi-modules 0.1.0 and kiwi-stack 0.1.0 exist** as repositories of their
  own, populated from this tree by `tools/split-sync.sh` (`--check` reports
  drift): the renderer with `kiwi-modules render` and the machine side with
  `kiwi-stack apply DIR`. kiwi-server stays the place both are developed
  until the first hardware round; the subtree flip comes after it.

## 2.5.0 — the stack is trustworthy before anything is built on it

The first bug round of the roadmap (docs/ROADMAP.md), on the rendered stacks
and the first boot; nothing here has booted on hardware yet.

- **The first boot can fail now.** The role script pulls every image one by
  one with retries before the first start, then `kiwi-stack verify` checks
  that every container runs; a stack that is not up fails the apply with the
  names, instead of "applied" with nothing running. A failed step leaves
  `/var/lib/kiwi-server/role.failed` (step, command, last log lines), which
  `kiwi-server status` shows. Quadlets have no start limit and pause 15 s
  between restarts, so a slow pull recovers without a reboot.
- **Secrets stay out of the unit files**: a secret-typed setting goes into a
  root-only `kiwi/env/<service>.env` referenced by `EnvironmentFile=`; the
  quadlets themselves are written 0600 now.
- **The master's Pi-hole moves to port 8081** inside the WireGuard server's
  namespace, which nginx shares and whose 80/443 it owns (`dns.web_port`,
  `FTLCONF_webserver_port`); the renderer refuses two modules binding the
  same port in one namespace (`network.listen_ports`, `derived:` values in
  module.yaml).
- **A node's own names resolve to the reverse proxy inside its containers**
  (`AddHost=`), the containers use the stack's Pi-hole as resolver (`DNS=`),
  the fleet CA is mounted into Nextcloud (`kiwi/ca/kiwiCA.crt`, the
  `TRUSTED_CACERTS_DIR` AIO reads), and the Nextcloud data directory carries
  its SELinux label. Collabora, Talk and notify-push can reach
  `https://cloud.<host>` from inside now.
- **No masquerade on DNAT'd mesh traffic** where the host routes the mesh back
  through the VPN client: services see the real client address, so rate
  limits and logs work per client.
- **Port ranges** (`27000-27100/udp`) in `extra_dnat_rules`, `open_ports` and
  published ports: iptables, firewalld and ufw spellings, overlap detection.
- **Re-apply hygiene under podman**: a container whose module left the stack
  is stopped and removed, and a changed `runtime:` or `docker_dir` stops the
  old stack before the new one takes its ports. Debian gets `catatonit` with
  podman (every `init: true` container needs it) and a podman older than
  4.4, which has no quadlets, is refused with the alternative named.
- **SSH host keys** live in `secrets/known_hosts`: accepted on first contact,
  a changed key is refused with the line to drop; a device name given to
  `enroll` must be a DNS label.
- **Generated secrets derive from hostname, module and key** — no longer from
  the fleet's host key and the role name, so `node-cloud` → `node` or a
  standalone render keeps every password (nothing had been applied with the
  old derivation). `secrets/seed` announces its creation, its id travels with
  the role script, and a machine refuses an apply under a different seed.
- **A misspelled fleet key is an error** (`rol:`, `netwrok:`, `admin.shh_keys`)
  with a suggestion; `kiwi-server <command> -h` shows that command's own
  usage and options.
- The role script is left 0600 on the machine after a run.
- **`auto_update: registry | off`** as a stack setting (default registry, as
  before); the images this project is sure about are pinned to a line:
  `weejewel/wg-easy:7`, `qmcgaw/gluetun:v3`, `nginx:1`,
  `portainer-ce:lts`. The others still follow `latest` until a verified pin
  list exists (ROADMAP, problem 13).
- **The host resolves through its own Pi-hole** on CoreOS and uCore
  (systemd-resolved: the stack's Pi-hole first, Quad9 behind it), so the
  fleet's names and the blocklist hold for the host itself, not only for its
  containers. Debian without systemd-resolved keeps its resolver.

## 2.4.0 — the fleet talks to its machines

- **A kiwi-updater app, properly**: the icon is kiwi-icons' kiwi-server
  (`svg/classic`, plus the PNG renders at 48 to 512 px), installed into the
  hicolor theme under the app id and shown by `kiwi-gui`, the app grid, the
  window and a new About dialog in the GUI's primary menu; `kiwi.manifest`
  lists `ssh` among the commands the app needs and describes what the app
  does now. Releases are version tags (`v2.4.0`), as kiwi expects.
- This release carries main's 2.2.0 (the Mullvad SOCKS5 records) on the
  podman runtime: the hosts directory is a read-only quadlet volume, the
  refresh timer is a host unit a re-apply removes again when
  `mullvad_socks` is turned off. From the review of that work: the refresh
  service is sandboxed (`ProtectSystem=strict`, one writable directory, no
  new privileges) and belongs to its timer, so an apply no longer waits for
  the download; the list URL is https (or a local file) only;
  `mullvad_socks_url` is required only while the records are on; the
  unchanged check uses coreutils alone; a directory that holds rendered files
  is made root's again on every apply. `kiwi-server doctor` reports the ssh
  client; a hand-installed Debian gets curl with podman.
- Fixed: the gateway preset's host unit (`kn-gateway.service`) required
  `docker.service`, which a podman node does not have, so systemd refused to
  start the LAN routing on every boot and apply; it now follows the runtime.
- **`kiwi-server apply <fleet> <host…>`**: the role script re-rendered and
  run on a running machine over SSH, as root, with `--force` — settings,
  modules and certificates change without a reinstall. `--no-run` only puts
  it at `/var/lib/kiwi-server/role.sh`; `--ssh USER@ADDR` reaches a machine
  whose name does not resolve yet.
- **`kiwi-server status`**: when the role was applied, the uptime and
  `kiwi-stack status` of every machine; `--porcelain` for the GUI.
- **`kiwi-server enroll <fleet> <host|name>`**: a WireGuard client on the
  master's wg-easy through its API, over an SSH tunnel to the master. A
  fleet host's config lands in `secrets/<hostname>.conf`, a device's in
  `secrets/devices/<name>.conf`; `--group` places the client in a client
  group's range, `--address` sets one, `--split` keeps a device's own
  internet, `--qr` prints the code for a phone, `--existing` fetches a
  config again.
- **`secrets/<hostname>.conf` is found by itself**: `vpn-client.wireguard_config`
  falls back to it (`fallback_file` on a file setting), so a node's block
  needs no line for it and its mesh address comes from the config.
- **`validate` renders the stack**: a module's `validate:` rules, a missing
  mesh address, two services on one port are reported by `validate` instead
  of first by `render` or `apply`. vpn-client checks that a custom provider
  has its config and a commercial one its key and addresses.
- GUI: Apply and Status on the host page.
- `lib/kiwiserver/remote.py` is the one place that talks SSH (the fleet
  backup uses it too); `lib/kiwiserver/wgeasy.py` speaks the weejewel
  wg-easy API.

## 2.3.0 — podman, one node role, Nextcloud without the master container

- **Podman quadlets** are the default runtime (`runtime: podman`): the
  rendered compose file is translated into `.container` and `.network` units
  under `/etc/containers/systemd`, started through `kiwi-stack.target`,
  updated by `podman auto-update`; `kiwi-stack` drives systemd instead of
  compose. `runtime: docker` keeps docker compose. Images are fully
  qualified; nginx resolves through the network's DNS; Portainer talks to the
  podman socket; gw.sh finds the bridge by the route.
- **Nextcloud from AIO's own containers**, no AIO master container, no docker
  socket: apache, nextcloud, database, redis, notify-push, and Collabora,
  OnlyOffice, Talk, recording, Imaginary, ClamAV, full-text search and the
  whiteboard as settings. Database and service secrets are generated from
  `secrets/seed` (a `secret` setting with `generate: true`), the same at
  every render. The AIO interface, its updater and its borg backup are gone;
  kiwi-stack and the coming backup module take their place.
- **One `node` role with presets** (`preset: gateway | cloud | minimal`;
  `modules:` replaces the list); `node-gw` and `node-cloud` stay as aliases.
- **The master's admin pages by name**: nginx runs inside the WireGuard
  server's namespace, `wg.<hostname>` and `pihole.<hostname>` answer on the
  mesh address only.
- **Client groups** (`vpn-server.groups`: subnet, reach, peers, internet,
  admin) replace the single isolated subnet, which stays as a legacy group.
- The renderer refuses two services on the same host port; `validate:` checks
  in module.yaml; an `object` setting type for structured settings.
## 2.2.0 — Mullvad's SOCKS5 proxies by name

- **`dns.mullvad_socks`**: Pi-hole answers for every Mullvad SOCKS5 proxy, as
  `de-fra-wg-socks5-001.relays.mullvad.net` and the short
  `de-fra-001.mullvad.<domain>` (`mullvad_socks_domain` to change it). gluetun
  refuses these private-range answers, so the names only resolved through the
  fallback resolvers until now. **On in the master preset**, whose exit is
  Mullvad; nodes ask the master. Set `mullvad_socks: false` in the master's
  `dns:` block to keep it off; the next apply removes the timer again.
  A master that got these records by hand from the mullvad-socks5 README
  (`pihole-mullvad-socks.timer`, `FTLCONF_misc_etc_dnsmasq_d`,
  `etc-dnsmasq.d/90-mullvad-socks.conf`) should undo that first: its
  [upgrade steps](https://github.com/derlocke-ng/mullvad-socks5#upgrading-from-the-earlier-instructions);
  kiwi-server does not remove units it did not install.
- A host timer (`km-mullvad-socks.timer`, every 6 hours) fetches the list from
  [mullvad-socks5](https://github.com/derlocke-ng/mullvad-socks5)
  (`mullvad_socks_url`) and swaps the file in `<docker_dir>/km-pihole/mullvad-socks/`,
  which Pi-hole sees **read-only** at `/etc/mullvad-socks` (`hostsdir`) and
  reloads without a restart. The directory is root's and outside Pi-hole's
  writable `/etc/dnsmasq.d`, so nothing in the container can swap a file under
  the refresh, which runs as root. Only `*.relays.mullvad.net` names at 10.x
  addresses are taken from the list, so it can never redirect another name; a
  download that is not such a list keeps the current records.
- **A re-apply removes host units it no longer renders**: a module dropped from
  a role, or a setting turned off, no longer leaves its units running outside
  `kiwi-stack`'s control.
- Modules: `when:` on `outputs` entries (templates and file settings) and on
  `storage.bind_mounts`, like the nginx blocks have; `pattern:` on a setting
  checks a string value against a regular expression at validate time.
- `kiwi-server roles -v` shows a role's preset value (`module_defaults`) where
  it has one, not the module's own default.
- `dns.extra_env: { FTLCONF_misc_dnsmasq_lines: … }` is added to the module's
  own dnsmasq lines; before, it replaced them (and with them `strict-order`
  and the fleet's `local=`/`server=` line), since one variable is set once.

## 2.1.2 — one resolver chain for the whole network

- **Nodes resolve through the master.** A node's Pi-hole asks the master's
  Pi-hole first, so the network shares one blocklist and every lookup leaves
  through the master's exit; Quad9 follows in strict order and only answers
  while the master is unreachable (`dns.fallback_dns`). Names under the
  fleet's domain are forwarded to the master and never leave it; the master
  marks the domain local. The master's address comes from the fleet's master
  host or the new stack setting `master_ip`.
- **A route into the mesh on every host.** `kiwi-stack start` routes the mesh
  subnet (`mesh_subnet`, default 10.8.0.0/16) through the VPN client, or
  through the WireGuard server on a master, so the host and its containers —
  the Pi-hole among them — reach the other nodes. The master masquerades that
  traffic as its mesh address.
- **The fleet's names in /etc/hosts** on every stack host, its own names
  pointing at itself, so backups and renewals resolve without a Pi-hole.
- gluetun's DNS-over-TLS provider defaults to Quad9; the gateway module's
  mesh subnet defaults to the stack's.
- **Names are `service.hostname.home`.** The example fleet moves from
  `.kiwi`, a real public TLD anyone can register names under, to `.home`,
  which ICANN will not delegate; the CA is constrained to it. No place label.
- **The domain is a choice, `home` the fallback.** `kiwi-server init
  --domain`, a question in the GUI's new-fleet dialog, and `validate` warns
  about public TLDs and `.lan`, and refuses `.local`.
- **The fleet backs itself up to its nodes.** `backup: { hosts: [...],
  passphrase_file: ... }` sends the fleet directory (fleet file, secrets, the
  CA — never output/ or ISOs), encrypted with openssl, to
  `/var/lib/kiwi-server/backups` on those nodes after every render or build;
  `kiwi-server backup` by hand or `--local` to a disk; `kiwi-server restore
  --from user@node` gets it back on a fresh machine over plain SSH.
- **`kiwi-server openwrt`** writes the uci script that joins an OpenWrt router
  to the mesh: a WireGuard client with the mesh routed (or everything, with
  `--full`), the firewall zone, and the dnsmasq forward of the fleet's names
  to the master's Pi-hole; or, with `--via`, a static route to a gateway
  node. [docs/routers.md](docs/routers.md) covers FritzBox and other routers.

## 2.1.1 — the review fixes

What the 2.1.0 review ([notes/REVIEW-2.1.0.md](notes/REVIEW-2.1.0.md)) found,
fixed. Nothing changes in the fleet file format; new settings have defaults.

- **First boot** (`roles/common/lib.sh`): `kiwi-stack start` no longer
  restarts the host units from inside its own service (a node-gw first boot
  hung forever); the docker group is copied from `/usr/lib/group` on Fedora
  CoreOS before `usermod`; a re-run restarts the stack instead of a no-op
  `enable --now`; the rendered files stay root's and only the data
  directories belong to the service user; `kiwi-stack vpn-restart` also
  restarts the containers in the VPN client's namespace; the timers' services
  wait for docker and the stack.
- **Renderer**: container addresses are computed inside the network (any
  subnet, not only /24), `container_ip` overrides are checked, a missing
  `vpn_ip` is reported as the setting to set — and read from the WireGuard
  config's `Address` when it is empty; `$` in settings survives compose
  interpolation; `key:` with no value means the default; `post_script` is
  pasted as written; the reverse-proxy `hsts` setting works; module.yaml
  mistakes are errors with a place, not tracebacks.
- **Stacks**: nginx resolves backends per request (`resolver` + variable), so
  it starts before Nextcloud AIO's containers exist; Pi-hole gets the fleet's
  names as `FTLCONF_dns_hosts` (Pi-hole 6 ignores a written `custom.list`),
  no `hostname:` together with `network_mode`, a DHCP server (`dhcp_*`) and
  a local-only web UI by default; the master's isolation rules come before
  the wg0 accept-all and isolated clients never reach the admin pages
  (`admin_from_mesh`); gw.sh's DROP is really first; gluetun's post-rules no
  longer accept every new forwarded flow; `label:disable` on the containers
  that mount the docker socket (SELinux); the AIO port and Pi-hole UI bind to
  127.0.0.1; JDownloader's web UI has a login; Portainer's admin password can
  be set before the first start; the SFTP password moved from the command line
  to `users.conf`; `extra_env` on every service module.
- **CLI / targets**: `script` prints only the script; TLS settings that cannot
  work fail `validate`; the fleet CA exists before the first script is
  rendered; host certificates last 825 days (`tls.cert_days`) and the CA can
  carry name constraints (`tls.name_constraints`); ISOs and the preseed copy
  are private; `updates.days: []` means every day inside the window;
  `debian.release` other than the current stable needs `debian.iso_url`; the
  preseed honours `admin.groups`; `service_user` defaults to the admin user.
- **GUI**: the module list applies on Enter, the fleet file keeps its mode,
  a failed save keeps the changes, the build dialog shows the disk from the
  file, the narrow layout shows the form, host names are checked, the script
  renders off the main thread, the missing-CLI dialog is visible.
- **Docs and tests**: firewall `scope` semantics documented, migration and
  mesh-SSH notes, bash completion for `ca`/`modules`; tests for the fleet
  scan (DNS records, CA, warnings), the kiwi-stack helper, the example fleet
  and every fix above.

## 2.1.0 — the kiwi-v2 modules, and GPL-3

- **Modules**: the kiwi-v2 module system lives in `modules/` and the renderer
  is part of the library (`kiwiserver.modules`): compose fragments are merged
  as YAML, the reverse proxy aggregates the blocks the other modules announce,
  module firewall rules flow into gluetun's post-rules, and a *bundle* of
  files, directories, ports, sysctls and host units is embedded into the role
  script. The host needs docker, nothing else.
- **Roles are module presets**: `master` (vpn-client, vpn-server, dns, tor),
  `node-gw` (vpn-client, dns, dhcp-relay, reverse-proxy, downloader, gateway,
  portainer, sftp), `node-cloud` (vpn-client, reverse-proxy, cloud, vault,
  portainer) — the live kiwi-master, kiwi-node-gw and kiwi-cloud, module by
  module. `modules:` in a host's block changes the list.
- **Settings from one place**: each `module.yaml` carries a `settings:` schema;
  validation, `kiwi-server roles -v` and the GUI's per-module groups come from
  it. Presets seed module settings with `module_defaults:`.
- **Filled in from the v2 TODO**: `start.sh` (double hop, isolated-client
  subnet) and `torrc` for the master, nginx server blocks per module, the
  gateway script with the live routing rules (table 128 with `throw`, LAN
  kept off the docker subnet), Pi-hole 6 variables, conditional WireGuard
  env vars for a custom config, DNAT to the proxy's real address.
- **New modules** the live setups ran but v2 lacked: `portainer`, `sftp`,
  `dhcp-relay`.
- **Fleet-wide knowledge**: every Pi-hole serves every host and service name
  at its mesh address (split-horizon DNS), the master's `start.sh` lets
  isolated clients reach every node, and one fleet CA (`kiwi-server ca`)
  issues `*.<hostname>` certificates for the reverse proxies and is trusted on
  every machine built.
- **On the host**: `kiwi-stack` (start, stop, restart, update, status, logs,
  vpn-restart), `kiwi-stack.service`, the daily VPN restart and weekly update
  timers the v1 cron jobs did, systemd-resolved's stub listener turned off
  where Pi-hole needs port 53, `docker compose config` validation at render.
- **License**: GPL-3.0-or-later, like the rest of the Kiwi Network apps.


## 2.0.0 — the kiwi-updater rewrite

A new tool under the old name. The 1.x script generated Fedora CoreOS ISOs from
a YAML file; 2.0 builds the machines of a Kiwi Network.

- **kiwi-updater app**: `kiwi.manifest`, `install.sh` (user scope, no root),
  `kiwi-server` CLI with bash completion and `kiwi-server-gui` (GTK4/libadwaita,
  app id `eu.kiwinetwork.KiwiServer`). `kiwi install kiwi-server` once it is in
  the catalog.
- **Three targets**: Fedora CoreOS (stable), uCore (CoreOS that rebases itself
  onto a `ghcr.io/ublue-os/ucore*` image on first boot) and Debian stable
  (trixie netinst, preseeded).
- **Roles** instead of a flat server list: `bare`, `node-cloud` (the kiwi-cloud
  stack: Nextcloud AIO, Vaultwarden, Portainer, nginx behind a gluetun
  WireGuard client) and `master` (wg-easy + Pi-hole baseline). A role is a
  directory with a `role.yaml` settings schema and an `apply.sh`; the schema
  validates the fleet file and builds the GUI form, so the reworked
  kiwi-node/kiwi-master drop in without GUI changes.
- **One role script per host**: self-contained bash, secrets embedded, run by
  `kiwi-role.service` on first boot — or by hand on any installed machine.
- **Unattended install media**: `coreos-installer iso customize --dest-device`
  for CoreOS/uCore; for Debian the preseed goes into the installer's initrd and
  the boot menus start the unattended entry, with `xorriso -boot_image any
  replay` keeping the stock BIOS/UEFI boot setup.
- **Automatic updates with a reboot window** on every target: Zincati
  `periodic` on CoreOS, staged rpm-ostree updates plus a reboot timer on uCore,
  unattended-upgrades plus a reboot-if-required timer on Debian.
- **Toolchain** runs natively when butane, coreos-installer and xorriso are
  installed, otherwise in a podman/docker image (`kiwi-server toolchain build`)
  — nothing is layered onto an ostree desktop.
- **Checks**: `validate` (exit 2 on errors), `doctor`, `show` (effective config,
  secrets masked), `--porcelain` JSON for the GUI; tests shellcheck every
  rendered script, validate every Butane config with `butane --strict`, and
  rebuild a mock Debian ISO.

Dropped from 1.x: the `global:`/`servers:` file format, the `kiwi-server-gen.sh`
wrapper and `iso ignition embed` (which only embedded a live config and still
asked you to run the installer). The per-server Butane extras (`files`,
`directories`, `units` (was `systemd_units`), `services`, `kernel_arguments`,
`luks`, `boot_device`) live on under `coreos:`.

## 1.0.0

Initial release: Fedora CoreOS / uCore ISO generator from a YAML file.
