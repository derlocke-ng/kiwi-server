# A network from a phone: the starter image

Today a network starts on a Linux computer: `kiwi-server init`, or the app. Someone
with a phone, or a Windows PC, and a mini PC bought for the purpose has neither. The
starter image is for them: one download, written to a USB stick, and the rest happens in
a browser on the phone.

## What the person does

1. Downloads the starter image and writes it to a USB stick (on Windows with Rufus or
   balenaEtcher; from an Android phone with a stick on its USB port and EtchDroid).
2. Plugs the stick and a network cable into the machine and switches it on. Nothing to
   type, no screen needed.
3. Waits until the machine has installed itself and started again, then opens
   `http://kiwi.local` on the phone, which is on the same Wi-Fi. The machine's screen, if
   it has one, shows that address, its numeric address, a setup code and a QR code of the
   page.
4. Answers the questions on the page (the same as `kiwi-server init`), types the setup
   code, chooses a password, writes the recovery key down.
5. Presses the buttons the page shows next: set this machine up, add the phone to the
   VPN, later add more machines.

## What the image is

The stock Fedora CoreOS live ISO with one Ignition for the live system and one for the
installed system, made by `kiwi-server build --starter` and published by CI as a release
file. It carries no secret and no fleet: every copy is the same.

**The live system (the stick)** installs, and is careful about where:

- It lists the disks and leaves out the stick it started from and every removable or USB
  disk. With exactly one disk left it installs there. With none or several it does not
  install: it says so on the screen and on a small page at the machine's address
  ("two disks found: unplug the one that should stay as it is, or choose"), and waits.
- A disk that already holds a Kiwi system is not installed over. The live system says
  "this machine is installed already: take the stick out", and waits. A stick left in a
  machine whose firmware starts from USB first does not reinstall it every boot.
- A failure stops and says what failed. It does not restart into the same failure.
- When it is done it restarts into the installed system by itself, without firmware
  boot order (kexec), and says to take the stick out.

**The installed system** is a control node that waits to be set up:

- uCore, the address from the router (DHCP), the name `kiwi` announced on the home
  network (mDNS, `kiwi.local`).
- The control container (`serve --wait`) on port 80 and 8740: the first-run page. Its
  setup code is on the machine's screen and nowhere else; five wrong codes and the page
  waits.
- A key pair made at the first boot, private half readable by the control container
  only, public half authorized for the admin user: the control node's way to set up the
  machine it runs on. The role replaces it with the fleet's own key.
- No disk encryption yet: the unlock rings do not exist before there is a network. The
  page offers it later for the machines that are added, and a reinstall of this one once
  another machine can hold its ring.

## What the page does after the questions

The fleet the questionnaire makes has this machine in it as its first node and control
node, under the name the person gave it. "Set it up" applies its role to the host the
control container runs on, through the starter key, as `kiwi-server adopt` does for a
machine someone else installed.

The master is the one step that needs something outside the house:

- **A rented server**: the page signs in at the hoster (a link and a code, done on the
  phone), lists the servers, and installs the master there. The control image carries
  the tools that make its install media.
- **Not yet**: the network starts at home only. The services open on the home Wi-Fi by
  their names; the VPN, access from outside and the hidden services come when a master is
  added. A person can see what they are getting before they rent anything.

## How the stick's machine becomes the first node

The installed starter is plain Fedora CoreOS with one container. The network it makes
has this machine in it, and the same control container goes on running as that node's
control part. The handover has to be exact, since the control node sets up the machine
it runs on:

- The starter keeps the fleet where a node's control part keeps it
  (`/home/user/docker/kn-control`, the default `<docker dir>/kn-control`), and runs its
  container under the name and unit the stack uses (`kn-control`). The role's stack
  writes its own `kn-control.container` over the starter's; the store on disk is the
  same file. The container that runs keeps the starter's definition until it is
  restarted, which the last step does.
- The questionnaire on a starter knows "this machine": it is the first node, target
  `coreos`, its disk is not asked for, and its address is the one the router gave it,
  which it keeps from then on (fixed). The person names it.
- "Set it up" for this machine needs no install media (`kiwi-server next`, with
  `KIWI_STARTER=1` in the starter's container: the fleet's machine with the control part
  is this one). Three steps:
  1. What Ignition would have written at an install is put in place over SSH to
     127.0.0.1 with the starter key (`adopt.run_coreos`): the files, the units, the
     fleet's keys for the admin user. An encrypted disk or kernel arguments are refused:
     they need an install.
  2. The role runs as a unit of the machine itself (`kiwi-self-apply`), since it replaces
     the container the command runs in.
  3. When the role is in, `/usr/local/bin/kiwi-starter-done` (part of the starter image)
     takes away what the starter needed: its key, port 80, the message on the screen,
     the mDNS name. It does nothing unless the stack has taken over the control part, so
     a role that failed leaves the starter's page reachable. Then it restarts
     `kn-control`: the page is now at `https://control.<name>.<domain>`, on the VPN, its
     keys locked until someone logs in there.
- The mark on the boot partition stays: the stick never installs over this machine.
- Its disk is not encrypted (the starter installs plain Fedora CoreOS). The network's
  keys rest encrypted in the store as on every control node; the data of its services
  does not.
- A node needs the master before it can be set up (its place on the VPN is made there).
  The page says so, as it does for every node: the master's step comes first.

## The master from the page

- **Sign in at the hoster.** The page shows a link and a code (the hoster's device
  sign-in); the person opens it on the phone, the page waits and then lists the servers
  of the account. Choosing one makes it the master and installs it: the control image
  makes the install media in memory, uploads them, installs, and applies the master
  role. This is `vps login`, `vps list` and `vps install`, with buttons.
- **A server the person set up elsewhere**: its address is typed, its install media are
  downloaded from the page and started in the hoster's panel, as today.
- **No master yet** stays a later phase. A node's parts assume the VPN today (each takes
  its place in the VPN client's network); a home-only start means deciding what a node
  is without one, and how it moves when a master comes.

## Joining machines, and a lost site

More machines join with the same image: started on the home network, a second starter
shows its address and setup code, as the first one did. On the network's control page,
Machines, "A machine with the starter stick is waiting" takes both: the control node
sends the code to the waiting machine with the network's public key, the waiting machine
checks the code (once; five wrong ones and it waits), puts the key in through its starter
key and answers with its SSH host keys, which the control node keeps for its address. Then
`kiwi-server adopt` gives it its role, and `kiwi-starter-done joined` takes the starter's
page, key and control container away. The code is the person's presence at the machine;
the host keys tie the rest to that machine. Plain HTTP on the home network, as the
first-run page is.

A site that is gone is rebuilt the same way: the starter on new hardware, then "bring it
back from a copy" on its first-run page with the recovery key ([recovery.md](recovery.md)).

## What exists, and what is to build

| Piece | State |
|---|---|
| The first-run page, its setup code, the questionnaire | exists (2.81.0) |
| The machine steps on the page, install media in memory | exists |
| Bring a network back from a copy on the first-run page | exists |
| `adopt`: a machine someone else installed | exists, from a terminal |
| The starter image: build, disk choice, console message, mDNS, the starter key | built: `kiwi-server build --starter`, tried in a VM |
| A yes before a disk that holds something is erased (keyboard, or a page) | built (2.104.0), tried in a VM |
| "This machine" as the first node in the page's questionnaire, and its "Set it up" | built (2.107.0), tried in a VM with a rented master and a hard reset after (2.107.1, `starter-vm.sh first-node`) |
| The hoster's sign-in on the page | built (2.146.0): "Sign in and pick it" in the first-run questions; the login is sealed with the network, so the master's step installs it |
| A network without a master | built in part (2.149.0): "No master yet" in the questions; adding a master later is to build ([no-master.md](no-master.md)) |
| A waiting machine, taken in from the control page (its address and setup code) | built (2.123.0) |
| A phone app | later: it talks to the control node's API, as the page does |

Ideas taken from KIWI NG's self-install images (another project, an OS image builder):
the disk filter, never reinstalling over itself, stopping on failure, and starting the
installed system without the firmware's boot order.
