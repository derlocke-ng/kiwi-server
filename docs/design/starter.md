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

## Joining machines, and a lost site

More machines join with the same image: started on the home network, a second starter
finds the control node (`kiwi.local` is taken) and shows a join code; the control page
lists it under "a machine is waiting" and gives it a role. That is the generic image and
join token of [control-plane.md](control-plane.md), with the network already there.

A site that is gone is rebuilt the same way: the starter on new hardware, then "bring it
back from a copy" on its first-run page with the recovery key ([recovery.md](recovery.md)).

## What exists, and what is to build

| Piece | State |
|---|---|
| The first-run page, its setup code, the questionnaire | exists (2.81.0) |
| The machine steps on the page, install media in memory | exists |
| Bring a network back from a copy on the first-run page | exists |
| `adopt`: a machine someone else installed | exists, from a terminal |
| The starter image: build, disk choice, console message, mDNS, the starter key | to build |
| "This machine" as the first node in the page's questionnaire | to build |
| The hoster's sign-in on the page | to build |
| A network without a master | to design: what a node does with no VPN |
| A waiting machine that asks to join | to build (the join token) |
| A phone app | later: it talks to the control node's API, as the page does |

Ideas taken from KIWI NG's self-install images (another project, an OS image builder):
the disk filter, never reinstalling over itself, stopping on failure, and starting the
installed system without the firmware's boot order.
