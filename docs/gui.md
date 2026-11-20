# The desktop app


New here? Start the app and choose **Start a new network**: it says what you
need, asks where the master runs and what your machine at home does, and
checks each answer before it goes on. What has an answer that works sits
under **More settings**. Then **Getting started** lists your machines, the master first, with one sentence
and one button each. When they run, **What next** offers the phone, backups
and a home server that looks after everything. **Join one with an invite
link** takes a link from a network's control page ([control.md](control.md)).

**Kiwi Server** in the app grid opens the fleet you had last. On the left the
overview, one row per host and the defaults. Each host row says whether the
machine answers, how long it has been up and how many of its services run.

## A new fleet

**New fleet** in the menu asks a few pages of questions (the fleet, logging
in, the master, the machines at home, secrets), each with a (?) that explains
it, then shows the fleet with its next steps.

## The overview

It counts machines, services and what needs a look, and says how old the oldest
last backup is. Every machine has a card: what it is for the others (control
node, gateway, keeps backups), its processor's load, its memory and its disks.
*How they are linked* is a picture of the network as a family tree. The masters
stand on top: this network's master, its standby beside it (dashed) and every
network linked to it (dotted, named with its master). Under them the machines
stand in a zone per site: at home, and every other place one stands (its *Site*
setting), with a line down from the master to each machine, green when the
machine answers and grey and broken when it does not; dashed to another site,
over the Internet. A linked network's violet dots go the way its traffic goes,
over the link into the master and beside the master's own line, to each machine
it may reach here (`share` in `federation.yaml`). What a linked network opens to
this one stands under it in a violet zone: its master's DNS says so over the
link, and the status asks it. Pointing at a machine or a linked network shows
its lines and dims the rest; a click on a machine opens it, on a linked network
Federation. The control page draws the same picture. The overview also lists what comes next for every machine not installed yet: enroll, build,
install, apply. *+* adds a host with the next free fruit (or fruit dish, for a
master) as its name.

*Needs attention* says, under each thing, what to do about it. A warning that is
so on purpose (a disk left unencrypted, say) has *Leave it out*: the machine's
setting `quiet` keeps it as a note from then on, and its settings bring it back.
Something broken never goes quiet.

**Messages.** The bell at the top right keeps everything the app said, with its
time and fleet, also after the app is closed: its short messages at the bottom
vanish after a few seconds. New and gone warnings of the machines go there too.
A number on the bell counts what came since it was last opened.

## How it talks to kiwi-server

The app runs `kiwi-server` for everything it does. What only reads (the list of machines,
a machine's logins, the store's state) goes to one `kiwi-server agent` that the app starts
and that ends with it: no new process for each click, and the list of machines is kept until
the fleet's folder changes. Everything that changes something runs as a command of its own.

## Preferences

The gear at the top of the list, or *Preferences* in the menu (Ctrl+,):

- **General**: the app's icon, the kiwi in any of kiwi-icons' colourways and
  styles (as in Kiwi Apps); light or dark; the messages kept under the bell;
  *Applying*: when this copy belongs to a control node, the machines are applied
  there and the log shows here (on by default).
- **Sharing**: another computer, a synced folder, a home server that looks after
  the network; *Sync now* and *Compare with the other copy*.
- **Security**: *Locking*, and what to do when a computer was lost or stolen.
- **Tools**: whether the build tools are here; render every machine; build every ISO.

The menu holds the rest: opening and starting networks, *This network* (what one
does with it now and then), *Manage* (who may do what), and *Sync now*.

## A host

A host has two tabs:

- *Overview* is what the machine says about itself: system, load, memory,
  disks, every service with its state, the tunnel, and the web interfaces to
  open from the mesh. *Logins…* opens the address, the user name and the
  password of each service. A password shows as dots until you reveal it, and
  a button copies it. See
  [Sign in to a service](machines.md#sign-in-to-a-service).
- *Settings* has a page per section (machine, role, each module, admin,
  network, updates, the target). Every field shows what it inherits; its "?"
  says what it is for. Changing the `modules` list or the target changes the
  pages.

## How it talks to the machines

Once a minute while the window has the focus the app asks
`kiwi-server status --porcelain`, which is also where it is decided what counts
as a problem. On a computer joined to a control node that is the control
node's own look: it watches the machines all the time, and they report to it
by themselves, so nothing is asked of the machines from here. The app also
follows the control node's changes (`kiwi-server control watch`): when a look,
a job or an apply there changes something, it asks within ten seconds instead
of at the next minute. Without a
control node, or when its word on a machine is older than 15 minutes, the
machines are asked from here, over SSH and read-only. A machine is reached at
its name, or at another address of it the fleet has already met, so `.home`
names need not resolve on this computer; `apply --ssh` once introduces a new
one.

A locked secret store shows a banner with *Unlock*, and the corner of the
sidebar locks it again.

## Actions

*Apply*, *Build* and *Render* run the CLI and stream its output. *Build* lists
every host with the disk it will wipe before it starts.

*Add a phone or laptop…* (in the menu) asks for a name and a group and shows the QR code
and the file to import. *Only the network
through the VPN* makes a split config, as `enroll --split` does; the dialog then shows the
NetworkManager line that keeps the computer's own names (see
[Joining the mesh](network.md#joining-the-mesh)).

The GUI writes plain YAML: comments in a hand-written fleet file are not kept
when it saves.
