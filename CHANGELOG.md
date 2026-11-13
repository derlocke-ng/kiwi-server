# Changelog

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
