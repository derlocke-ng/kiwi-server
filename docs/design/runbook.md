# A runbook: what happens in which case (design, 22 November 2026)

The owner, 22 November: admins should decide "what happens in what case with what machine", and
"every node can handle autonomous if necessary on patterns and permission which are set before".
This page is the plan for that. Built: step 1 (the lines checked and shown) and `part-down` of step 2.

## What it is

A runbook is a short table in the fleet file. Each line names a case, the machines or parts it is
for, and what happens. The cases and the actions are fixed lists (authority.md §4: no rule
language); the admins choose among them. What no line covers is told, and nothing is done.

```yaml
defaults:
  runbook:
    - { case: part-down,   for: "role:node",  do: restart, after: 2,  tries: 3 }
    - { case: disk-full,   for: all,          do: prune-images, at: 90 }
    - { case: update-failed, for: all,        do: roll-back }
    - { case: machine-gone, for: sorbet,      do: restart, after: 15 }
    - { case: machine-gone, for: "part:git",  do: tell }
```

`for`: `all`, a machine's name, `role:…`, `site:…`, `part:…`, as the page's filters.
The first line that fits wins. `after` in minutes, `tries` before a person is told instead.

## Who acts

- **The machine itself**, for what it sees on itself: a part down, its disk filling, an update
  that failed, being cut off from the network. Its agent carries the lines that are for it (the
  apply writes them to the machine) and acts without the control node. This is the owner's
  "every node can handle autonomous".
- **The control node**, for what a machine cannot see on itself: a machine gone (the diagnosis
  first, as today), a site silent, a backup overdue elsewhere.

Every action is in the machine's journal and, at its next report, in Activity, as one record per
case (what was seen, which line, what was done, how it ended).

## Permissions

An action that only one machine may take at a time, say a part started on a second machine,
asks for a permission first (lease.py, built in 2.287.0 and unused since 2.294.0): it acts only
while the voters named in its line give it. Lines that need none (a restart on the machine
itself, pruning its images) act at once. Nothing irreversible is in the lists unless an admin
writes it per machine.

## The lists (first version)

| Case | Seen by | Actions |
|---|---|---|
| part-down | the machine | restart, tell |
| disk-full | the machine | prune-images, prune-snapshots, tell |
| update-failed | the machine | roll-back (the image before), tell |
| cut-off | the machine | keep-running, stop (a part with a permission), tell |
| machine-gone | the control node | restart (at its provider), tell |
| backup-overdue | the control node | backup-now, tell |
| certificate-due | the control node | apply, tell |

Each case gets a drill that causes it on the test machines before its line is offered
(authority.md §4: the lists come from building and testing).

## Order

1. The table in the fleet file, checked; the page and the app show it per machine ("When it
   stops" grows into "What happens").
2. The machine's agent: part-down, disk-full, update-failed, each with its drill.
3. The control node's cases through the same table (machine-gone replaces `recovery:`, which
   stays as the default lines).
4. Permissions for the lines that name voters.
