# One truth, permissions and policy (design, 21 November 2026)

This design replaces the way kiwi-server decides things by itself. On 20 and
21 November ten releases added a switch of a part to its standby that happens
by itself. A review found ten ways for it to go wrong, a sync asked the owner
to choose between copies that differed in nothing he had decided, and each fix
was another special case. The owner, 21 November: "the whole system is kinda
flawed … we are patching small things, but the main problem lies elsewhere."
Nothing of this design is built. New features that act by themselves wait
until its first two steps are.

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
- A part with no standby needs no permission: no other machine could run it.
- The same rule decides which control node acts when there are two (step 4).

What goes: the hold in the agent's answer, the marks for switched off, seen and
switched, and the standby's check whether it reaches the machine.

### 3. Policy: one table per part

Each part says four things. Modules bring the defaults, the fleet file changes
them, and one engine reads them.

| Question | Answers |
|---|---|
| Where may it run? | on its machine only (a gateway, a site's DNS, an unlock ring), or it can move |
| How many copies? | one; one with a standby; two at once (a part that keeps no data) |
| Its machine is lost | wait; switch to the standby; ask a person |
| Its machine reaches no other voter | keep running (two copies are possible) or stop (none is possible) |

The last question has no answer that is right for every part: software cannot
tell a dead machine from a cut cable. The owner's default, 21 November: keep
running, except for a part with a standby that switches by itself. The page
shows for every part one sentence made from its row: what happens when its
machine stops. A drill per kind of row proves it.

For machines the table that exists stays (`recovery:`): tell, or restart at
the provider, after so many minutes.

### 4. A second control node

The computer that manages the network runs the same control node, in the same
container, while it is on. It holds the keys already. With one writer and a
permission for "the control node that acts" this is safe: the voters are the
control node at home, the computer, and a witness as in step 2. The control
node's own machine then restarts or hands over as its row says, or waits for
the OK of the second control node, of kiwi-server or of an app (the owner,
21 November).

## Order

1. One truth. The observed and remembered data move first (they cause the
   conflicts), then computers send changes instead of files.
2. Permissions, for the parts that have a standby. The switch by itself is
   rebuilt on them and loses its special cases.
3. The table, and the sentence on the page.
4. The second control node.

Each step ends with its drills on real machines, and is released as one piece.

## Not decided

- How long a permission lasts. It bounds how long a part is away after a power
  cut (today six minutes) and how often the voters talk.
- What a part does that waits for a person's answer and gets none for a day.
- Whether a computer may write while the control node is unreachable but
  running (two writers for a while), or only keep its list.
