# The desktop app


New here? Start the app and choose **Start a new network**: a few questions,
then **Getting started** lists your machines, the hub first, with one sentence
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

It counts machines, services and what needs a look, with a card per machine,
and lists what comes next for every machine not installed yet: enroll, build,
install, apply. *+* adds a host with the next free fruit (or fruit dish, for a
master) as its name.

## A host

A host has two tabs:

- *Overview* is what the machine says about itself: system, load, memory,
  disks, every service with its state, the tunnel, and the web interfaces to
  open from the mesh.
- *Settings* has a page per section (machine, role, each module, admin,
  network, updates, the target). Every field shows what it inherits; its "?"
  says what it is for. Changing the `modules` list or the target changes the
  pages.

## How it talks to the machines

The machines are asked once a minute while the window has the focus, over SSH
and read-only, through `kiwi-server status --porcelain`, which is also where
it is decided what counts as a problem. A machine is reached at its name, or
at another address of it the fleet has already met, so `.home` names need not
resolve on this computer; `apply --ssh` once introduces a new one.

A locked secret store shows a banner with *Unlock*, and the corner of the
sidebar locks it again.

## Actions

*Apply*, *Build* and *Render* run the CLI and stream its output. *Build* lists
every host with the disk it will wipe before it starts.

The GUI writes plain YAML: comments in a hand-written fleet file are not kept
when it saves.
