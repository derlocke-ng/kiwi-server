# One truth, permissions and policy (design, 21 November 2026)

This design replaces the way kiwi-server decides things by itself. On 20 and
21 November ten releases added a switch of a part to its standby that happens
by itself. A review found ten ways for it to go wrong, a sync asked the owner
to choose between copies that differed in nothing he had decided, and each fix
was another special case. The owner, 21 November: "the whole system is kinda
flawed … we are patching small things, but the main problem lies elsewhere."
Step 1 is built (2.286.0, below); the rest is not. New features that act by
themselves wait until steps 1 and 2 are.

## What is wrong

kiwi-server began as a tool a person runs: it renders a role and applies it.
That part works. Acting by itself was added one case at a time, and a system
that acts by itself needs three things the tool never had.

**One truth.** Today the fleet is a set of files that exists on every computer
that manages the network and on the control node (the machine at home that
keeps the network when no computer is on). The copies are merged file by file.
What a person decided and what a machine observed are in the same files:

| Kind | Examples today | Where |
|---|---|---|
| Decided by a person | `fleet.yaml`, `federation.yaml`, passwords kept in the store | every copy, merged |
| Made once | the seed, the CA, SSH and WireGuard keys | every copy, merged |
| Observed | host keys, `macs.json` (with when each was last seen), a provider's login token that changes when used | every copy, merged, and so in conflict all the time |
| Remembered | `.kiwi-state.json`: when a machine was applied, which is gone, held back, switched off, switched | each copy has its own, not synced |
| Looked up | the control node's last look, its list of the fleet, its waiting applies | the control node's RAM |

Two copies that both observe something differ at once, and a sync has to ask
which counts. A command run on a computer remembers what it did in that
computer's own file, where the control node never sees it.

**Authority.** A part (a module on a machine) starts because its machine
started. When it should not run there any more, something has to tell the
machine to stop it. Until that arrives the part runs on two machines. The rules
added on 20 and 21 November (held back, switched off on purpose, asleep,
another site, the copy's age) each close one way into that state.

**Policy.** What happens when a machine does not answer is a chain of
conditions in `watchdog.py`. Every new case is another condition, and nothing
says for one part, in one place, what will happen to it.

## The design

### 1. One truth: the control node writes, everyone else sends changes

- The control node is the only writer of the fleet. A computer does not change
  its copy and send files. It sends the change itself ("this setting of this
  machine is now this"), through the API the page already uses, and gets the
  new fleet back.
- What is observed or remembered leaves the fleet's files. It lives on the
  control node, comes from the machines' reports and the jobs it runs, and
  computers read it through the API. No copy of it is merged anywhere.
- Every computer still holds a whole encrypted copy of what was decided and
  made, fetched after each change: the network must survive the loss of the
  control node, and [recovery](recovery.md) brings it back from such a copy.
- A computer that cannot reach the control node keeps its changes as a list
  and sends them when it can. Two changes to the same setting are the only
  conflict left, and the question shows both values.
- A network without a control node is one computer that is the writer. The code
  is the same; the writer is local.

What goes: the file-by-file merge (`merge.py`), "changed on both sides" for
anything but one setting, and each computer's own `.kiwi-state.json`.

**As built (2.286.0).** What is remembered has one keeper (`fleetstate.py`).
A change is worked out as steps down to single lines ("applied, lime: this
time"); on a computer joined to a control node the steps wait in
`control/state-out.json`, every sync sends them (`POST /api/v1/state`) and
takes the control node's whole state back. The first exchange of a copy that
kept its own until then hands over what is newer there. Only one sync of a
folder runs at a time. The observed files stay in the store but merge by
themselves since 2.284.0: host keys line by line, the MAC list by when each
entry was seen, a provider's login by its time. The fleet files merge setting
by setting on the computer, and the control node takes a push only when it
builds on its newest copy: that is the one writer in practice, so the only
question left is one setting changed on both sides. Sending single settings
instead of the file is not needed for that and is not built.

### 2. Permissions: a part runs only while its machine may run it

- For a part that can run on more than one machine (it has a standby), the
  machine needs a permission that runs out. The stack starts the part only
  with a permission that is still good, and stops it when it runs out.
- A permission is given by a majority of three voters: the machine that runs
  the part, its standby, and a witness. The machine keeps the part while it
  reaches one other voter. The standby takes the part when it and the witness
  agree that the machine is gone, and not before the machine's permission has
  run out. So a machine that comes back, or that lost its network, has stopped
  the part by itself, and nobody has to tell it.
- The witness is another node at the same site: it sees the same network and
  cannot be reached from the Internet (the owner, 21 November: "why don't use
  other nodes?"). The master is the witness only for a site with fewer than
  three machines, or a pair at two sites. A vote says "I reach this machine" or
  "I do not", signed with the machine's own key. It carries no data and needs
  no key of the network, so the master still holds none. A witness that lies
  can cause a switch only when the standby does not reach the machine either,
  and can hold a switch up. It reads nothing.
- A permission lasts three minutes and is renewed every minute, with the
  agent's report (the owner, 21 November). Its length is how long a part is
  away after a power cut: the standby may take it only when it has run out, so
  about four minutes, where the switch by itself took six. One renewal that is
  lost stops nothing.
- A part with no standby needs no permission: no other machine could run it.
- The same rule decides which control node acts when there are two (step 5).

What goes: the hold in the agent's answer, the marks for switched off, seen and
switched, and the standby's check whether it reaches the machine.

### 3. Policy: one table per part

Each part says six things. Modules bring the defaults (a site's DNS and the
gateway must be up, game servers can wait), the fleet file changes them per
part, and one engine reads them.

| Question | Answers |
|---|---|
| Where may it run? | on its machine only (a gateway, a site's DNS, an unlock ring), or it can move |
| How many copies? | one; one with a standby; two at once (a part that keeps no data) |
| Its machine is lost | wait; switch to the standby; ask a person |
| Its machine reaches no other voter | keep running (two copies are possible) or stop (none is possible) |
| How much is it needed? | must be up; normal; can wait |
| A question about it gets no answer | remind (after 1, 6 and 24 hours), or act anyway after a time the row names |

The last question has no answer that is right for every part: software cannot
tell a dead machine from a cut cable. The owner's default, 21 November: keep
running, except for a part with a standby that switches by itself. The page
shows for every part one sentence made from its row: what happens when its
machine stops. A drill per kind of row proves it.

For machines the table that exists stays (`recovery:`): tell, or restart at
the provider, after so many minutes.

**What is done by itself** follows from two things: how much the part or
machine is needed, and whether the action can be undone. A restart can be
undone, a switch to a fresh copy can, a reinstall or a switch to an old copy
cannot. Needed and undoable: it is done. Not undoable: a person is asked, and
how much it is needed decides whether the question is repeated or, after the
time its row names, acted on. Nothing that cannot be undone is ever done
without a person.

### 4. A diagnosis before a decision

The owner, 21 November, on a master that does not answer: "check what's up, do
we need the standby master, is the second master running, is it just a normal
reboot (proper logging of what happens), did it maybe crash, status netcup
check maybe, send alert." The checks exist today in separate places. They
become one procedure that runs in a fixed order and ends in one conclusion:

| Check, in this order | Conclusion | What follows |
|---|---|---|
| Was it told to restart, update or switch off? | planned | wait, no message |
| Does its boot system answer? | it waits for its disk's key | its ring is forwarded |
| What does its provider say? | switched off; or running and silent | started; or restarted |
| Do other machines at its site answer? | the whole site is away | nothing to restart: said so |
| Does its standby or a witness reach it? | only the way from here is cut | nothing is done |
| None of these | not known | nothing is touched: a message with what was found, and the actions as buttons |

Meanwhile a standby master takes the devices, as today.

- **A machine says when it goes down.** Its agent sends a last report with the
  reason (a restart for an update, `power off`, a shutdown at its console). A
  restart and a crash are then no longer the same silence.
- **One record per case**: what was found, the conclusion, what was done and
  how it ended, in Activity and in the message. For example: "sorbet does not
  answer. netcup says it runs, it was not told to restart, papaya answers: it
  hangs. Restarted, the first of three tries."
- **A fixed list, no rule language**: about ten events (a machine silent, a
  part down on a machine that runs, a disk filling, a backup overdue, a
  certificate running out, a copy kept ready that is old, a site silent, the
  control node silent, an update that failed, an apply that failed), about
  seven conclusions and six actions. What fits none of them is "not known".
  That is the answer to what cannot be foreseen: nothing is touched, and a
  person is told with everything that was found.
- **The lists come from building and testing** (the owner, 21 November), not
  from this page. Every event gets a drill that causes it for real on the test
  machines, and the checks, the conclusion and the action are written from
  what the drill shows. A case that ends as "not known" on a real network is
  looked at afterwards: it becomes a new conclusion with its drill, or stays
  what it is.

### Emergency messages

How long a question waits depends on how bad the case is and on what can be
done about it (the owner, 21 November). So there is no one time after which
kiwi-server acts, but steps:

| The case | What is done by itself | The message |
|---|---|---|
| Something that can be undone helps (a restart, a switch to a fresh copy) | it is done | afterwards, as today |
| Only something that cannot be undone helps, and the part can wait | nothing | a question, repeated after 1, 6 and 24 hours |
| Only something that cannot be undone helps, and the part must be up | nothing | an emergency message |

An **emergency message** goes to every admin of the network at once, on every
way that is set (mail, ntfy at its highest priority, the webhook, the phone
app), and is repeated until one of them says "I have it" on the page, in the
app or by the link in the message. It names the case, what was found, and the
actions as buttons. The admins are the people in the accounts' group of
admins where the network has accounts, and the addresses in `notify:`
otherwise. Beside them a network can name a team for emergencies: other
members of the network, or people of the company that runs it (the owner,
21 November). They get the same message and can say "I have it"; the actions
stay with the admins, so a team member who is no admin sees what happened and
whom to reach. Whether an action that cannot be undone may ever follow an
emergency message nobody answered is a setting per part, off unless the owner
turns it on.

### 5. A second control node

The computer that manages the network runs the same control node, in the same
container, while it is on. It holds the keys already. With one writer and a
permission for "the control node that acts" this is safe: the voters are the
control node at home, the computer, and a witness as in step 2. The control
node's own machine then restarts or hands over as its row says, or waits for
the OK of the second control node, of kiwi-server or of an app (the owner,
21 November).

**A person outranks the automation** (the owner, 21 November: "in an emergency
it should have the higher power").

- The control node is gone: the computer becomes the control node that acts, by
  the vote above.
- The control node runs and the computer does not reach it: the computer keeps
  its changes as a list. A person can still force an action on the machines
  the computer does reach. That is said in so many words before it is done, it
  is written down, and it raises a counter that every copy of the fleet
  carries. When the two meet again the lower counter gives way: the control
  node takes over what the person did.

A person who holds the keys always can act. The system records it and follows.

## Order

1. One truth. The observed and remembered data move first (they cause the
   conflicts), then computers send changes instead of files.
2. Permissions, for the parts that have a standby. The switch by itself is
   rebuilt on them and loses its special cases.
3. The table and the sentence on the page; the diagnosis, the last report of a
   machine that goes down, and the record of each case.
4. The second control node, and the forced action of a person.

Each step ends with its drills on real machines, and is released as one piece.

## Decided (the owner, 21 November 2026)

- New features that act by themselves wait for steps 1 and 2.
- Witnesses are other nodes of the site; the master only where there is none.
- A machine that reaches no other voter keeps its parts running, except a part
  with a standby that switches by itself.
- A permission lasts three minutes.
- Modules bring how much a part is needed; the fleet file changes it per part.
- A person's forced action counts, and the control node takes it over.
- `power off` holds a switch (built in 2.284.0).

## Not decided

Nothing that blocks steps 1 and 2. The lists of step 4 are written as its
drills are built.
