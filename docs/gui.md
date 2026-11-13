# The app

**Kiwi Server** is the desktop app for kiwi-server. It works on the same fleet
file as the command-line tool and runs the tool for every action, so both
always agree. This page describes what the app shows and what it does. It
needs GTK 4 and libadwaita; see [Install](install.md#requirements).

## The window

**Kiwi Server** in the app grid opens the fleet you worked on last. On the
left are the overview, one row per host and the defaults. Each host row says
whether the machine answers, how long it has been up and how many of its
services run.

The **overview** counts machines, services and what needs a look, with a card
per machine. For every machine that is not installed yet, it lists what comes
next: enroll, build, install, apply. *+* adds a host with the next free fruit
as its name, or fruit dish for a master.

A **host** has two tabs:

- *Overview* is what the machine says about itself: system, load, memory,
  disks, every service with its state, the tunnel, and the web interfaces to
  open from the mesh. Each host's page also shows its backups and can back it
  up or restore modules.
- *Settings* has a page per section: the machine, the role, each module, the
  admin user, the network, the updates and the target. Every field shows what
  it inherits, and its (?) says what it is for. Changing the `modules` list or
  the target changes the pages.

## A new fleet

*New fleet* in the menu asks a few pages of questions: the fleet, logging in,
the master, the machines at home and the secrets. Each question has a (?) that
explains it. Then the app shows the fleet with its next steps. These are the
same questions `kiwi-server init` asks in the terminal.

## How the app asks the machines

While the window has the focus, the app asks the machines once a minute, over
SSH and read-only, through `kiwi-server status --porcelain`. That command also
decides what counts as a problem. A machine is reached at its name, or at
another address of it that the fleet has already met, so `.home` names do not
need to resolve on your computer. To introduce a new machine, run
`kiwi-server apply --ssh` once.

A locked secret store shows a banner with *Unlock*, and the corner of the
sidebar locks it again.

## Actions

*Apply*, *Build* and *Render* run the command-line tool and show its output as
it is produced. Before *Build* starts, it lists every host with the disk it
will wipe.

The menu also has *Federation…*, which lists the partner networks and what
each may reach here, and *SSH certificates…*, which makes the SSH CA, trusts
it on this computer and signs a key for a number of days.

**Warning.** The app writes plain YAML. Comments in a fleet file that you
wrote by hand are lost when the app saves it.
