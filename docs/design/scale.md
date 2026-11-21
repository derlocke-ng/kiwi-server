# Many machines, and services that move (design, 20 November 2026)

The owner, 20 November: the network grows, and managing it machine by machine does not
grow with it. He asked whether Ansible or Kubernetes should carry it, whether a service
can move to another machine with its key and its backups, and answered seven questions.
This design holds his answers and the order to build in. Built since: the agent, move, standby and switch of a
part. The switch by itself was built and taken out again (step 4). The steps below say what is open.

The size to design for is in [ui.md](ui.md): about 70 machines and 350 devices at five
sites, three masters, linked networks beside it.

## Not Ansible, not one cluster over everything

- **Ansible** is the same kind of tool as kiwi-server: it pushes settings over SSH. It
  sends one task at a time where kiwi-server sends one finished script per machine, it
  needs everything in the fleet file written a second time as playbooks, and it does not
  retry a machine that was off, move a service or fail over either. (Python on uCore is
  not the reason: it can be added.)
- **Kubernetes or k3s over all sites** does not fit: its control servers must stand in one
  place with fast links between them, each wants about 2 GB of memory, and a service with
  data does not move by itself there either without replicated storage. Where one site has
  three real servers and wants what a cluster gives, k3s can be a module for that site.

## Built in 2.259: what held it back at 70 machines

- Jobs on different machines run side by side on the control node.
- An apply works on all its machines at once.
- An apply that did not reach a machine waits for it, and the control node applies it when
  the machine answers again.
- A role script is rendered once per change.

Still as before: one change to the fleet file renders every machine once (0.15 to 0.2 s
each), because a change on one machine can reach others (a new name the DNS machines must
know). Rendering only the machines a change reaches needs the renderer to record what each
script reads; that is its own step.

**The plan for that step (22 November 2026).** What one machine's script reads from the others
was looked up: every machine with a stack carries every DNS record, the master's facts, its
site's neighbours, the parts it keeps ready and the network's own CA. So a change of a name,
an address or a machine's parts reaches nearly every machine, and that stays a full render. The
common change is another: a setting of one part on one machine (a cloud's quota, a game's
map). For that:

1. While a machine is rendered, the other machines' data it reads is written down: which
   machine, and which top-level key of its settings (`cfg`, its role block, a part's settings).
   The fleet-wide look the renderer takes once (records, masters, neighbours, relays) counts as
   read by every machine.
2. A machine's fingerprint is its own resolved settings, the release, roles, modules, the
   defaults, the secrets' tree, and the values of exactly what it read from the others.
3. A machine whose fingerprint is the same as at its last render is not rendered again. What
   it did not read cannot change its script, since a script is made only from what it reads.

Anything read in a way that is not written down (the fleet file's raw text, say) makes that
machine count as reading everything. Each step is checked against the full render: both must
give the same scripts for every change in the suite's fleets.

Built (2.295.0): reach.py writes the reads down, the fleet-wide look is fingerprinted by what it
found, and a render that made something new (a certificate) has every machine judged once more.
On the way to the machine the same idea holds (the owner, 22 November: "we don't need to update
the whole machine"): a role script that differs from the last applied one only in the stack's
lines runs with `--stack-only`, the stack's steps alone.

## Decided (the owner, 20 November 2026)

| Question | Answer |
|---|---|
| Must a service survive a dead machine with nobody there? | Wanted. |
| May a failover lose data? | Live replication where possible. |
| Service names without the machine in them (`cloud.home`)? | Yes, as a feature: the network can be run by service as well as by machine. |
| May machines fetch their own settings? | Yes. (Plan, 1: they report in, and the control node applies; why is said there.) |
| One control node, or one per site? | Both. |
| Load balancing? | Not now; later, for size. |
| One admin or a team? | Both, by the network's size. |

## The plan

**1. Machines report in.** Today the control node asks: it logs in to every machine every
ten minutes to see how it is, and to a machine whose apply waits every minute. New: each
machine tells the control node on a timer that it is up, signed with a key of its own that
derives from the seed, over the VPN only. The control node answers by applying it at once
when an apply waits for it. Later the same message carries the machine's state (what the
look asks over SSH today), so 70 machines cost the control node 70 small messages instead
of 70 logins.

The machine does not apply itself. An apply is more than the role script: the control node
also binds the disk to its rings, signs the SSH host certificate and tells the backup
servers, and those need keys only the control node holds. So the machine says "I am up",
and the control node applies.

Built so far (2.259, 2.260): an apply that did not reach a machine waits for it; the
control node asks that machine alone every minute and applies it when it answers.

**2. Names by service.** A part can have a name of its own beside its machine's:
`cloud.home` beside `cloud.papaya.home`. DNS, the reverse proxies and the certificates
follow the service. Links people keep do not change when the service moves.

**3. Move a service.** `kiwi-server move` for one part, as a plan first:

1. Check the target: space, architecture, ports, the parts it needs.
2. The part's secrets are kept for the target (they derive from machine, part and key
   today; `secrets/pins.json` holds what must stay).
3. Stop it at the source, one last backup, restore at the target from the source's
   backups (direct, when both stand on one LAN).
4. The service's name points at the target.
5. The target backs up into a store of its own, with its own key. The source's store is
   kept as history for a time, then pruned.

**4. A standby for a service, and failover.** In three levels, each a step on the one
before:

| Level | What the standby has | Lost at a failover | Needs |
|---|---|---|---|
| Cold | nothing; step 3 run from the last backup | up to a day | one click |
| Warm | the data, copied every few minutes | minutes | a second machine |
| Live | every write, as it happens | nothing | one site, LAN, a third voter |

- Warm works for every part the same way (snapshot, copy, a database dump with it), over
  the VPN and between sites.
- Live is per part: a database's own replication where it has one (PostgreSQL, MariaDB),
  a replicated disk between two machines at one site (DRBD) for the rest. Over home
  uplinks between sites it would make every write wait for the far side, so it is for
  machines that stand together.
- Failover by itself needs three who agree that the first machine is gone: the two
  machines and a third voter (the control node or a master; voting needs no keys). The
  machine that was replaced must not come back as the first: when it answers again it is
  told to stand by.

**5. A control node per site**, beside the main one:
[controllers.md](controllers.md), step 4 (scopes). It applies its site's machines with
its site's keys only, and keeps its site going while the VPN is cut.

**6. Teams.** Admins as accounts (the `admins` group exists), each change in the activity
log under its name (it is), rights per site, and changes to the fleet file merged on the
control node.

**7. Load balancing**, when a service needs it: the same part on several machines behind
the reverse proxy. Only for parts that keep no data of their own.

## Staying up: what operators do, and what fits here

Researched 20 November 2026 (the owner: "how do others handle 24/7 … it goes down, 5 seconds
later another container somewhere is up"). The sources are the projects' own documents.

**How a service stays up through updates and failures elsewhere:**

| Building block | What it needs |
|---|---|
| The same service twice behind a load balancer; update one, then the other | a service that keeps no data of its own, or shares it |
| One address that moves to a second machine (VRRP, keepalived) | two machines on one LAN; takes 3 to 4 seconds |
| A database that copies itself and switches (Patroni, Galera) | three voters; a switch takes 25 to 35 seconds |
| A disk that is written on two machines at once (DRBD) | two machines and a third voter, a LAN between them |
| Three voters | so that a machine that only lost its network stops writing, instead of two machines both believing they are the one |

**Five seconds** is real only when a second copy is already running: an address that moves,
a proxy that sends to the other one. That fits DNS, DHCP, the gateway and the reverse proxy.
A service with one writer (files and a database, as Nextcloud is by default) must be started
elsewhere with its data already there: tens of seconds with a replicated disk, under a
minute as an honest promise. Operators who restart a failed service elsewhere wait longer by
default (Proxmox about two minutes, Kubernetes five).

**Between sites, latency is the limit, not bandwidth.** On fibre two homes are 5 to 30 ms
apart. Writing on both sides at once makes every write wait that long (DRBD lost more than
half its write speed at 20 ms added). So between sites: copies that are seconds behind, for
files and disks; writing at once only for a small database. A gigabit line makes those copies
and a full restore fast, which is what counts there.

**What this becomes here**, per part, the owner choosing the level:

| Level | How | Down for | Lost |
|---|---|---|---|
| Backup (every part) | a backup store per service, restore tried on another machine | a restore | up to a day |
| Standby | the part is set up on a second machine, its image there, stopped; its data copied every minute (the owner's idea, 20 November). A switch starts it and moves the name. Also between sites. | about a minute | up to a minute |
| High availability, one site | a moving address, the data on two machines at once (a replicated disk, or the database's own replication), three voters | under a minute | nothing |
| Twice at once | the network's basics: DNS, DHCP, the gateway, the reverse proxy; later parts that can share their data | seconds | nothing |

- **The network's basics twice.** Two DNS machines are handed to every device already where
  two exist; one moving address makes the switch invisible. Two DHCP servers share the
  range. The default gateway is a moving address between two gateway machines: every device
  keeps the one gateway it knows, with nothing installed on it. A tool on a person's
  computer (kiwi-desktop) is for what the LAN cannot do: choosing the master to reach from
  outside.
- **Updates without an outage** follow from a standby: update the standby, switch, update
  the first.
- **A backup store per service**, not per machine (the owner's question). The service's
  history moves with it, a restore of one service is clean, a leaked key opens one service.
  The cost: nothing is shared between services' stores, and there are more keys and more
  stores to tidy; the keys derive from the seed and the control node tidies. The backup
  server stays append-only: a machine that was broken into cannot delete history.
- **A fast rebuild** is what the design has: the system is an image, the fleet file says
  what runs, only data is backed up. Images of whole machines are for machines nobody can
  describe.
- **Not used:** Ceph (4 GB of memory per disk, built for more machines), GlusterFS (fading),
  MinIO (no longer maintained), Longhorn (needs Kubernetes). For files across sites, Garage
  is the one made for home lines; for SQLite (Vaultwarden), Litestream copies every second.

## Order

1. Names by service (built, 2.263).
2. An agent on every machine (the owner, 20 November: "shouldn't all nodes run small
   services that are our healthcheck, up/down, update and so on system"). See below.
3. A backup store per service.
4. Move a service (built, 2.266 and 2.267: tried on the test machines).
5. Standby: the part's data copied every minute to a second machine, a switch by hand
   (built, 2.267). Then the part prepared on the standby and its name on both machines, so
   that a switch takes seconds; then the switch by itself, with three voters (the agents vote).
6. The network's basics twice: DNS, DHCP, the gateway, with a moving address.
7. Site controllers (controllers.md, step 4).
8. High availability at one site, part by part, each with its own drill.
9. Teams; load balancing.

## The agent

A small service on every machine, part of what every role installs. It replaces the control
node's logins for looking, and it is what failover needs on the machines themselves.

- **Reports.** Every minute it sends the control node how the machine is: what `status` reads
  over SSH today (services, disks, load, updates waiting, backups), signed with a key of the
  machine's own that derives from the seed. The control node stops logging in to look.
- **Says "I am up"** when the machine starts, so an apply that waited runs at once.
- **Takes orders** the control node signs: restart a service, start or stop a part (a
  standby), run the update now. An apply stays the control node's (it needs keys the
  machine does not have).
- **Watches its neighbours.** Each agent asks two or three others whether they answer. Three
  that agree a machine is gone is the vote a failover needs; one machine that lost its own
  network sees that it is the one cut off, and stops its standby parts' writers.
- **Speaks on the network's own ways only**: at one site over the LAN (the fleet's names
  resolve to LAN addresses there, from each machine's own hosts file, so neither the master
  nor a DNS machine is needed), between sites over the VPN. Always HTTPS with the fleet's CA,
  and signed with the machine's key: what it sends is encrypted and cannot be forged. It
  holds no secret of another machine.
- **When the master is gone** (the owner, 20 November): a site goes on by itself, its agents
  and its control node see each other on the LAN. Between sites the standby master
  (standby.md) is the way, and a control node per site keeps each site managed while the VPN
  is cut.

Built in steps: reports and "I am up" first (the look without logins), then orders, then the
neighbours' vote with the standby.

## Built and tried on real machines (2.267, 20 November 2026)

- **Move a part** (`kiwi-server move`): between a uCore and a Debian machine, both ways, with
  its data, secrets, name and a valid certificate (`tests/drills/part-move.sh`).
- **Standby** (`keep_ready`, `kiwi-server standby`, `kiwi-server switch`): the copy on the
  standby was seconds old, a change arrived within a minute, the standby's key could only
  read. A switch lost nothing with the old machine up, and worked with it down; the control
  node then took the part off the old machine when it came back (`tests/drills/part-standby.sh`).
- A switch takes six to eight minutes today, nearly all of it applying machines. That is a
  standby, not yet a failover in seconds.

## Next: a switch in seconds, then by itself

Measured on 20 November 2026 (Vaultwarden from a uCore machine to a Debian one, 2.269): the
part is away for 55 seconds, the time its standby takes to be applied. Its name follows as
the machines that answer for names are applied, the master first and the home's after it:
that is the rest of the six to eight minutes the command takes.

What makes a switch slow is that the standby machine learns about the part only at the
switch: its containers are made, its proxy gets the name, the machines that answer for names
are told. All of that can be there before:

1. **The part is prepared on the standby machine**: its containers made and stopped, its
   image there, its settings and secrets in place (rendered as "kept ready", a module mode
   kiwi-modules gets).
2. **Its name answers on both machines.** The standby's proxy has the name and its
   certificate already, and passes requests on to the machine that runs the part while that
   one is up. The machines that answer for names give out both addresses, so a device that
   cannot reach the first tries the second by itself, with no change to DNS at the moment of
   the switch.
3. **A switch is then two steps on the standby machine**: start the part's containers, and
   tell its proxy to answer itself. Seconds, and nothing else in the network changes at that
   moment. The fleet file follows afterwards.
Built (2.274.0 and after): 1 as `profiles: [kiwi-ready]` (quadlets without `[Install]`,
`kiwi-stack ready`); 2 differently from the plan above: the standby's proxy has the part's site
in a second config (`nginx-ready.conf`) that the switch puts in place, and the machines that
answer for names keep the names of parts kept ready in a hosts file of their own that the
switch rewrites (no second address in normal times, and no request passed between machines,
whose access rules would see the wrong address); 3 as `kiwi-stack ready start` plus that file.
Measured: Vaultwarden ran on its standby 6 s after the switch began, its name answered there
after 29 s (the container's own start).

4. **By itself**: dropped. Built from 2.276.0 (the control node switched a part when its machine
   had been silent five minutes) and rebuilt on permissions that run out ([authority.md](authority.md)),
   then taken out again in 2.294.0. The owner, 22 November 2026: a part with data is not taken over
   with an older copy, because the machine, back, holds a second version of the same database.
   What stays: the copy kept ready and the switch while the machine runs (step 3, nothing lost),
   `move` (from the machine's last backup), and a machine installed anew bringing its data back
   from its backups. The permissions stay in the code, unused, for automation that needs one actor
   at a time.

## Not decided

- Which parts get live replication first. Proposed: the ones with a database of their own
  (accounts, git), then files.
- ~~Whether a master may be the third voter~~: decided in authority.md (21 Nov): the witness
  is another machine at the site, the master only where there is none.
