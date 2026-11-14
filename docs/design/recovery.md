# Autonomy and recovery (design, 15 November 2026)

The owner's aim: a server infrastructure that nearly runs itself. Updates, backups, disk
unlock and recovery happen without a person; people set the policy once and are asked only
where the decision is theirs. A home that burns down costs new hardware and a connection,
not the network.

## What runs by itself today

| What | How |
|---|---|
| System updates | staged by the system, restart in the machine's update window |
| Service updates | `kiwi-stack update` by timer (weekly) |
| Backups | every night, each machine to its backup machines; the network's own copy to the machines |
| Disk unlock | Tang rings; a machine opens when one ring answers |
| A RAM master after a power cut | `watch` sends its role again |
| A gateway's routing | watched twice a minute and put back |
| An ad blocker's blocklist | looked at hourly, fetched again when empty |

## What does not, and the ladder

Nobody acts when a machine is gone, nothing reaches a node once its master is gone, and a
lost site is rebuilt by a person, step by step. Recovery is a ladder; each rung is tried
before the next, and each has an owner-set permission:

1. **A service**: systemd restarts it (today). Its data back from a snapshot: a person.
2. **A machine**: restart it (a rented one through its provider, built in step 2 below);
   install it anew and give it its role; its data comes back from its backup machines by
   itself (today, proven by `tests/drills/regenerate-vm.sh`).
3. **The master**: restart at the provider; install anew there; then a standby or another
   provider. The nodes follow (rung 4 of the steps).
4. **A site**: every machine of it anew, the first one first, data from the backup machines
   at another site.
5. **What holds the network** (the control node, the computers): the network's copy and the
   recovery key.

## Who decides: the policy

`recovery:` in the fleet file's defaults says, per case, how long something must be gone
and what the control node may then do by itself:

```yaml
recovery:
  master: { after: 15, do: restart }     # minutes; tell | restart | reinstall
  node:   { after: 60, do: tell }
```

- `tell`: it is said on the page (and by a notification, once there are any). The default.
- `restart`: a power cycle through the provider. Destroys nothing.
- `reinstall`: install anew at the provider and set up. Destroys the server's disk, so it is
  a word of its own and never implied by `restart`.

The permission is the policy itself: only someone who can open the network's keys can
change the fleet file, and the control node can only act with what the store holds (the
provider's token). Guards that no policy switches off: every act is written to Activity;
the same machine is not acted on twice within an hour, nor more than three times a day; the
control node does nothing when it cannot reach the provider or anything else outside, since
then it is the one that is cut off.

## The emergency channel: Tor

Today every machine finds the others through the master. With the master gone, the control
node cannot reach a node at another site, and a node cannot learn where a new master is.

Every machine and the control node get an onion service for SSH and the control API. The
onion keys are derived from the network's seed, so the control node knows every machine's
address without asking it, and client authorization lets only the network's controllers
connect at all. No port is opened anywhere; Tor is slow, so it carries control traffic
only, never data.

- The control node reaches a machine over Tor when the mesh path fails (the last resort of
  `remote.reach`).
- A node whose tunnel has been down for some minutes asks the control node's onion where
  its master is now. The answer is signed with the network's key. A new master's address
  and key reach every node without a visit.
- Two sites that lose their master at once: each node still reaches the control node (at
  any site, or the owner's computer); it installs a new master at the provider and says so.

## A site is gone

Before: backups at another site, the network's copy at another site or computer, the
recovery key on paper. `kiwi-server recovery` checks the first two. After: new hardware and
a connection; install media from the control page, set up, the data comes back. The order
(what holds the network first, then the master if it was there, then the machines with
data) is what the recovery page lists.

## Steps

1. The readiness report, the recovery page, rebuilding a lost machine from the app and the
   page.
2. The policy, and the control node restarting a master at its provider by itself.
3. `reinstall` for a master; the same for a rented node.
4. The emergency channel: onion SSH on every machine, the control node's fallback.
5. Nodes ask the control node over Tor for their master and switch by themselves.
6. A standby master; a site rebuilt in one go from a generic image and a join token.

Open: notifications (mail, a push service, a Nostr message) for `tell`; who may answer an
`ask` from a phone.
