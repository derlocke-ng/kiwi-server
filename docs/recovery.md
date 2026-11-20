# When something breaks

This page is for the day something is lost: a service's data, a machine, the
master, a phone, the computer that manages the network, or a whole home. For
each case it says what happens by itself and what you do: in the app, on the
control page and in a terminal.

Look before anything breaks. The report **Recovery** says whether
you could bring your network back today, and what is missing:

- On the control page: **Recovery** in the menu, under Safety.
- In the app: the menu, **Recovery…**
- In a terminal:

```bash
kiwi-server recovery ~/kiwi/fleet.yaml
```

The report reads only what is on your computer or control node: the fleet
file, the fleet's state file and what the machines said at the last look. It
contacts no machine. A green line is ready, an amber line is a gap and says
what to do, a grey line is something the report cannot tell from there.

## What the report looks at

| Check | A gap when |
|---|---|
| The network's copy | no copy was made, or the newest is older than 14 days. `kiwi-server backup` notes where and when it stored one; so does **Fetch a copy** on the control page. |
| Each machine that keeps data | it has no backup part, no backup server, a backup server that is only itself, or a server that does not list it as a client. After a look at the machines: its last backup is older than 36 hours. |
| An encrypted disk | all its rings are on one other machine. If that machine is lost, the disk waits at its next start until someone types its key. |
| The master | the fleet has no copy of its VPN state, or it runs in memory and nothing sends it its role after a restart. The report also says whether it can be installed again from here. |
| The network's keys | besides the recovery key, only one computer or control node can open them. |
| The recovery key | never checked: a reminder to keep it where you can find it. |

A machine keeps data when one of its parts declares something to back up:
Nextcloud, Vaultwarden, Immich, Paperless and the others. The VPN state of
the master is kept by the fleet itself, not by a backup.

## A service's data is wrong or was deleted

**By itself.** Every machine with the backup part saves its data every
night, to its backup machines. Nothing is brought back by itself.

**What you do.** For one file, look in the service first: Nextcloud, Immich
and the others keep their own trash and versions. For a whole service as it
was on an earlier day, bring that part back from a snapshot. What it has now
is replaced.

- In the app: the machine's page, **Snapshots**, then **Restore** at a time.
- On the control page: **Backups**, then **Bring something back** at the
  machine.
- In a terminal:

```bash
kiwi-server backups ~/kiwi/fleet.yaml papaya vault --restore --at "2026-11-12 18:00"
```

More in [Backups](backups.md#restoring).

## A machine is dead or was replaced

**By itself.** Nothing installs it again by itself, and the other machines
keep running. Once it is installed anew and set up, a machine with the backup
part finds itself in its backups and brings its data back by itself, then
backs up as before. A machine without backups starts empty.

**What you do.** Install it anew under the same name: make its install
media, start the machine from them (its disk is erased), then set it up. The
new installation's SSH key is taken as the machine's.

A machine that still answers, and is installed anew on purpose, should save its
data first: it gets back what its last backup holds, and backups are made once
a night. "Save its data now" is the first thing its page offers; in a terminal:

```bash
kiwi-server backups ~/kiwi/fleet.yaml papaya --now
```

Do not take a machine down on the day and hour of the update window
(`updates`, Sunday 03:30 unless you changed it): the others may restart then,
and one whose unlock ring is on the machine you took down would wait locked.

- In the app: the machine's menu, **This machine was lost or replaced…**:
  **Make…**, start the machine, **Set it up**.
- On the control page: the machine's page, **This machine is lost or was
  replaced**, with the same steps as for its first install.
- In a terminal, first the install media:

```bash
kiwi-server build ~/kiwi/fleet.yaml papaya
```

Then, when the machine is installed and on again:

```bash
kiwi-server next ~/kiwi/fleet.yaml papaya --yes
```

A machine whose router gives it its address is set up at the address it has
now: `kiwi-server apply <fleet> papaya --ssh core@<its address> --new-host-key`.

A machine that only waits at start with a locked disk is not lost. **Unlock
it** on its page, or `kiwi-server unlock <fleet> <machine>`. When the
machines with its rings are gone, `--manual` types its key
([Security](security.md)).

## The master is dead

**By itself.** The machines at home keep running and keep their data. Until
the master is back, nothing reaches them from outside, and your devices have
no VPN. A master that runs in memory gets its role back by itself after a
restart, when a control node or a timer on your computer watches it
(`kiwi-server watch <fleet> --timer on`). The fleet keeps a copy of the
master's VPN state: a master that is set up again knows every device and
machine, so nobody needs a new code.

**What you do.** A master at a hoster that Kiwi Server installs at (netcup)
is installed again with one button. Everything on that server is erased.

- In the app: the master's menu, **This machine was lost or replaced…**,
  **Install…**
- On the control page: the master's page, **This machine is lost or was
  replaced**, **Install it at netcup**.
- In a terminal:

```bash
kiwi-server next ~/kiwi/fleet.yaml sorbet --yes
```

Another master is installed from its install media, as a machine above: at a
hoster, upload them as an ISO and start the server from it.

A new server with another address: change the master's address in its
settings first. Your devices find it again by themselves only when that
address is a name (`vpn-server.wg_host`) that you point to the new server.
Otherwise each device needs its code again.

**Before it happens: a standby.** A second machine can hold the master's place
while it is gone: the same master, with its own disk and address. Write it under
the master in the fleet file, install it from its media like any machine, and
apply the master (that applies the standby too):

```yaml
  sorbet:
    role: master
    standby:
      sorbet-b:
        target: debian
        network: { dhcp: false, address: 192.168.2.70/24, gateway: 192.168.2.1 }
```

The master's machines move to the standby by themselves three minutes after the
master is gone, and back when it returns. Phones and laptops follow when the
master's address (`vpn-server.wg_host`) is a name at a dynamic DNS provider and
the control node may change it: put the provider's update address in
`secrets/ddns.url`, with `{address}` where the new address goes, for example

```
https://www.duckdns.org/update?domains=mykiwi&token=TOKEN&ip={address}
```

Then, once the master is gone for its `recovery.master.after` minutes, the name
points at the standby, and back when the master answers again. A device follows
when its VPN app connects anew. A standby at home runs no Tor.
([design](design/standby.md))

## A phone or laptop was lost or stolen

**By itself.** Nothing. It stays on your VPN until you remove it.

**What you do.** Remove it. From that moment its key no longer works, even
with the WireGuard app still on it.

- In the app: the menu, **Add a phone or laptop…**, then remove it from the
  list.
- On the control page: **Devices**, **Remove**.
- In a terminal:

```bash
kiwi-server enroll ~/kiwi/fleet.yaml annas-phone --remove
```

If the laptop also managed the network, do what the next case says too.

## A computer that manages the network was lost or stolen

**By itself.** Nothing. It can still open the network's keys, and it knows
what it saw.

**What you do.** Remove it, so it can no longer open the keys or get anything
new. Then replace the keys that open your machines, and the secrets inside
your services. If it knew the password of the control page, change that too.

- In the app: the menu, **A computer was lost or stolen…**
- On the control page: **Controllers**, **Remove**, then the steps under
  **If a computer was lost or stolen**.
- In a terminal:

```bash
kiwi-server secrets remove ~/kiwi/fleet.yaml laptop
```

```bash
kiwi-server rotate ~/kiwi/fleet.yaml ssh
```

```bash
kiwi-server rotate ~/kiwi/fleet.yaml seed
```

What is replaced and what is not yet: [A computer was lost or
stolen](control.md#a-computer-was-lost-or-stolen).

## The computer or control node that holds the network is gone

**By itself.** The machines keep running and keep saving their data; nothing
on them changes. Nobody can change the network until it is back. Another
computer that can open the network's keys goes on as before.

**What you do.** Bring the network back from a copy, with the recovery key.
Then remove the lost one and replace the keys, as in the case above.

- On a new control node: its first page, **I already have a network**
  ([Bring a network back from a copy](control.md#bring-a-network-back-from-a-copy)).
- In the app on a computer: the menu, **Restore a network from a copy…**
- In a terminal, from a machine that keeps copies:

```bash
kiwi-server restore --from core@192.168.1.30 --into ~/kiwi
```

The report says which machine has the newest copy. Keep one copy away from
your machines too: **Fetch a copy** on the control page, or
`kiwi-server backup <fleet> --local <folder>` for a USB stick.

## A whole home is gone

This is the case of new hardware, and maybe a new Internet connection.

**By itself.** Nothing at home runs any more. A rented master keeps running. Backups and copies kept on
machines in that home are gone with them; the ones kept elsewhere are not.

**What you do.** Go in this order:

1. The network itself, from a copy kept elsewhere, with the recovery key, on
   a new computer or control node (the case above).
2. With a new connection, change the machines' home addresses in their
   settings before you make their install media.
3. The master, if it was lost too.
4. The machines that keep the backups, and the ones whose rings open other
   disks.
5. The other machines, one after the other. Each one whose backups are left
   brings its data back by itself.

The report lists the machines in this order, each with its commands. A
network brought back on a new computer does not know which machines were set
up, so the app's **Getting started** and the control page's **Still to set
up** list the steps for every machine. Where a machine is listed as set up,
its **This machine is lost or was replaced** has them.
