# The control node

One node at home runs kiwi-server by itself, so the fleet needs no desktop
that is always on. It has a page for the phone: `https://control.<host>`,
open to the mesh only.

It checks every machine every ten minutes and sends the role to RAM masters
that booted. The page:

| | |
|---|---|
| **Home** | how the network is, in one line; what wants a look and what that means; every machine; a tile per service |
| **A machine** | what it is, what runs on it; Check now, Preview, Apply, each with what it does |
| **Devices** | add a phone or laptop to the VPN: a QR code for the WireGuard app |
| **Backups** | when each machine was last saved and where; save the network itself |
| **Computers** | who may manage the network; add one with a link |
| **Activity**, **Help** | what was done by whom; how do I…, and what the words mean |

Changing settings and adding machines stays in the Kiwi Server app, which
sends changes here by itself.

## Set it up

In the app: menu, **A home server that looks after it…**. Pick the home
server, type a password twice. Done.

By hand, the same steps:

```yaml
papaya:
  node:
    modules: [vpn-client, reverse-proxy, control]
```

```bash
kiwi-server secrets init ~/kiwi/fleet.yaml            # once: the secrets become the encrypted store
kiwi-server serve ~/kiwi/fleet.yaml --set-password    # the password that opens it on the node
kiwi-server apply ~/kiwi/fleet.yaml papaya            # the module
kiwi-server control push ~/kiwi/fleet.yaml            # the fleet, encrypted, to the node
```

Then, from a device on the VPN: `https://control.papaya.<domain>`. The first
login opens the fleet.

## Secrets

Nothing rests on the node in the clear. Its disk holds `secrets.age` and
`fleet.yaml.age`, encrypted for the store's keys, and a salt.

| | |
|---|---|
| The password | makes an age key (scrypt, with the salt); that key is one of the store's keys. No hash of it and no key file is stored. |
| Logging in | opens the store and the fleet file into RAM inside the container. Job logs and rendered stacks stay in RAM too. |
| A restart, a power cut, **Lock** | locked again: the node does nothing by itself until the next login. |
| A stolen disk | holds encrypted files; each guess at the password costs scrypt (64 MiB). Use a long one: 12 characters at least. |

So after a power cut someone logs in once, from the phone. Or the node unlocks
by itself, with the help of another machine:

```yaml
papaya:
  unlock: { control: house }    # a Tang ring that another machine serves
```

After the next login the node keeps its key encrypted for that ring. After a
restart it asks the ring and opens with nobody there (seconds). If the ring's
machine is off it stays locked and the page says which machine it waits for.
A stolen disk alone opens nothing; a thief would also need to reach the ring
on your network, the same trade your encrypted disks make. The app's control
dialog offers this as "After a power cut".

## More computers

1. On the page: **computers**, **Add a computer**. Copy the link (it works once, for
   fifteen minutes).
2. On the new computer, in Kiwi Server: **Join a fleet**, paste the link.

That is all: the computer makes its own key (in its keyring), checks that the
link is from this fleet, fetches the fleet and opens it. Without the app:
`kiwi-server control join ~/kiwi/fleet.yaml <link>`.

**Remove** on the page takes its key out again: it reads nothing newer. What
it read before, it knows: see the next section.

## A computer was lost or stolen

Remove it (above), then replace what it knew. On the page, under
**Computers**; in the app, the menu's **A computer was lost or stolen…**

1. **The keys that open your machines.** Every machine gets a new admin SSH
   key and a new SSH CA. The old key keeps working until the last machine has
   changed, so a machine that is off does not lock you out: switch it on and
   do it again. Nothing restarts. A machine that is gone for good:
   `--waive NAME`.
2. **The page's password.** Current password, new password, done. The old
   one opens nothing any more.

```bash
kiwi-server rotate ~/kiwi/fleet.yaml          # what can be replaced, and when it last was
kiwi-server rotate ~/kiwi/fleet.yaml ssh      # new SSH keys on every machine
```

Not built yet: new passwords for the services (they derive from the seed),
new VPN keys for the machines, the key that unlocks disks at boot, a new
certificate authority. A key of your own in `admin.ssh_keys` is yours to
replace ([design/controllers.md](design/controllers.md)).

## Without a control node: a synced folder

Two computers can share a network through a folder that Nextcloud, Syncthing
or the like keeps the same on both. The network lies there encrypted: whoever
runs the syncing can delete it or put back an older copy, not read it.

1. On the computer that has the network: menu, **Keep it in a synced
   folder…** (`kiwi-server control push <fleet> --dir FOLDER`).
2. On the other computer: **Join**, name the folder. It asks to join
   (`kiwi-server control join <fleet> --dir FOLDER`).
3. The first computer shows the request the next time it syncs: **Accept** or
   **Deny** (`kiwi-server control requests|accept|deny <fleet> [name]`).
4. On the other computer, **Join** again: the network opens.

From then on both sync with that folder by themselves, like with a control
node. A folder caught in the middle of syncing is refused, not half taken.

## Staying in step

The app does it by itself: it fetches when a fleet is opened and sends after
a save. When two computers changed the fleet, it says so and changes nothing.
By hand:

```bash
kiwi-server control pull ~/kiwi/fleet.yaml    # before working here
kiwi-server control push ~/kiwi/fleet.yaml    # after changing the fleet here
```

Each refuses to overwrite a side that changed since the last sync; `--force`
overrides. The node takes a push while it is open. A new password
(`--set-password`, then push) locks it until that password logs in.

## Mind

- Never put the module on a rented master: while it is unlocked, the machine
  holds every secret of the fleet in its memory.
- Nodes take SSH on their mesh address (`vpn-client: { mesh_ssh: false }`
  turns it off), keys only: that is how the control node reaches them from
  anywhere.
- `control: { allow_from: 192.168.0.0/16 }` opens the page to one more
  network, e.g. the home LAN.

Design: [design/control-plane.md](design/control-plane.md).
