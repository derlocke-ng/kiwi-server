# Many machines, and services that move (design, 20 November 2026)

The owner, 20 November: the network grows, and managing it machine by machine does not
grow with it. He asked whether Ansible or Kubernetes should carry it, whether a service
can move to another machine with its key and its backups, and answered seven questions.
This design holds his answers and the order to build in. Only the first part is built.

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

## Decided (the owner, 20 November 2026)

| Question | Answer |
|---|---|
| Must a service survive a dead machine with nobody there? | Wanted. |
| May a failover lose data? | Live replication where possible. |
| Service names without the machine in them (`cloud.home`)? | Yes, as a feature: the network can be run by service as well as by machine. |
| May machines fetch their own settings? | Yes. |
| One control node, or one per site? | Both. |
| Load balancing? | Not now; later, for size. |
| One admin or a team? | Both, by the network's size. |

## The plan

**1. Machines fetch their settings.** Today the control node pushes over SSH. New: each
machine asks the control node on a timer whether its role script is still the one it has
(by fingerprint), fetches the new one and applies it. The control node renders, as now:
it holds the seed. The machine proves who it is with its certificate from the fleet's CA,
over the VPN only. A push stays for "now". What this gives: a machine that was off for a
week catches up by itself, and 70 machines cost the control node 70 small answers instead
of 70 SSH sessions.

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

## Order

1. Machines fetch their settings.
2. Names by service.
3. Move a service (plan, then the move), with a drill on the test machines.
4. Warm standby, then failover by itself with three voters.
5. Site controllers (controllers.md, step 4).
6. Live replication, part by part, each with its own drill.
7. Teams; load balancing.

## Not decided

- Which parts get live replication first. Proposed: the ones with a database of their own
  (accounts, git), then files.
- Whether a master may be the third voter for a site's failover, or only a machine at
  that site.
