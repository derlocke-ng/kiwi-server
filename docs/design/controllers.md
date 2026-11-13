# Controllers (proposal, 13 November 2026)

Asked for by the owner: several computers manage one fleet, they join through the control
node and are accepted, denied or removed there; the fleet's data syncs between them without
the backup server; and what a removed one knew can be rotated. Builds on
[control-plane.md](control-plane.md) and [../control.md](../control.md).

## Words

- **Controller**: a computer that holds the fleet and can act on it. The operator's desktop,
  a laptop, a control node. Each has a name, an age key (it opens the store) and an SSH key
  (the machines let it in).
- **Main controller**: manages the masters, the CA and who is a controller. One per fleet.
- **Site controller**: manages the nodes of one site (a home, an office). Optional.
- **One acts**: every host names the controller that applies to it and runs its timers
  (`controller:` on the host, the main one unless set). The others may look.

## Joining and removing

Built in 2.62.0, after the owner found the first version (a code, a command, an accept
step, a pull) far too much:

1. The page's **Add a computer** shows an invite link: the control node's address and a
   one-time code after the `#` (a browser never sends that part). Fifteen minutes, once,
   void after three wrong guesses.
2. In the app on the new computer: **Join a fleet**, paste. It makes its key (age, in its
   keyring), checks the fleet's CA against the code before sending anything, sends the
   public half, and fetches the fleet.

The invite is the owner's yes: it was made by someone logged in. There is no waiting list.
**Remove** takes the key out again, re-encrypts store and fleet file, and (later) opens a
rotation (below) for what that controller could read.

The controllers are the store's keys (`secrets.recipients`), so the list travels with the
fleet and every change of it is in the audit log. Scopes will need more than a name there.

## Sync

The main control node holds the truth. A controller talks to its API over the mesh and
proves who it is with its age key: the node encrypts a token to that key, and only its holder
can read and return it. No controller gets a shell on the control node for syncing. (Built in
2.61.0. The first version of this page had SSH signatures here; one key per controller is
simpler, and the machines' SSH access stays the fleet's shared admin key until rotation
brings a key per controller.)

- `control pull` and `control push` as today, by revision: a push must build on the
  revision the node has, otherwise pull first. What travels is encrypted, as today.
- Without a control node, or beside it: the same encrypted files in a folder that something
  else syncs (`control push --dir ~/Nextcloud/kiwi`, `pull --dir`). Nextcloud sees
  `fleet.yaml.age` and `secrets.age`, never a secret. Built in 2.72.0: a manifest written
  last says which files belong together (a folder caught mid-sync is refused); a computer
  asks to join by leaving its public key in `requests/`, and a computer that can open the
  store accepts or denies it (`control join --dir`, `control requests|accept|deny`; the app
  shows the request when it syncs).

## Rotation

Removing a key stops a controller from reading anything new. It does not forget what it
read. `kiwi-server rotate plan <controller>` lists what it could read, by its scope, and the
control page runs the list as jobs, each marked done:

| What | How | Cost |
|---|---|---|
| Its own access: store key, SSH key | removed at once | none |
| The control password | `serve --set-password` | a new login |
| The admin SSH key the fleet shared | gone: every controller has its own key | one apply per host |
| Generated secrets (database, admin and API passwords; all derive from the seed) | a new seed epoch; each module gets a `rotate` step that changes the password where it lives (a database keeps the old one otherwise) | an apply per host, services restart |
| WireGuard keys of masters and nodes | new keys, peers re-enrolled by the main controller | the mesh drops for a moment per host |
| Leaf certificates | reissued | none |
| The CA key (a main controller only) | a new CA; every device imports it | high: the reason a site controller never holds it |
| Disk keys, Tang rings | the existing swap and ring rotation | a reboot window for rings |
| Outside keys (Mullvad, the hoster's API token, the backup passphrase) | by hand, listed | varies |

Built in 2.73.0: the list (`kiwi-server rotate <fleet>`), the SSH keys (`rotate <fleet>
ssh`: a new admin key and SSH CA, the old ones kept as `.old` until every machine has
changed; the page's **Replace the keys**) and the control password (on the page). The
shared admin key stays one key for now: a key per controller comes with scopes.

A rotation that is open shows on the page until every line is done or waived, with who
waived it.

## Scopes: what a site controller holds

Not the fleet's store. Its own, with only its hosts' secrets:

- **Generated secrets** derive from the seed over host, module and key today, so whoever
  derives one host's needs the seed and has everyone's. New: a seed per host, derived from
  the fleet's (`derive(seed, "host", name)`); a site controller gets its hosts' seeds. This
  changes every generated secret once, so it comes with the first seed rotation, not before.
- **Certificates**: an intermediate CA per site, name-constrained to its hosts.
- **The mesh**: its nodes' WireGuard configs. Peers are added by the main controller (it
  holds the master); a site controller asks it through the API.

## Order

1. Controllers with their own keys; join, accept, deny, remove on the page; sync through
   the API by revision. Every controller is a main one at first (full trust).
2. The folder transport (`--dir`).
3. `rotate`: access keys and the control password, then the seed epoch with the modules'
   rotate steps, then WireGuard.
4. Scopes: site controllers, their stores, per-host seeds, intermediate CAs, one acting
   controller per host.

## Not decided

- Whether a site controller may enroll devices by itself (it would need a slice of the
  master's WireGuard API).
- Unattended unlock of a control node after a power cut (a Tang ring on another machine as
  one more key of the store).
