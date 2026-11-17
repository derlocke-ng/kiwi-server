# Security

## Mind

- **The ISOs wipe the disk they are told to**, on boot, without a question.
  Label them. `disk:` is required for `build` for that reason.
- **The output holds secrets**: password hashes, WireGuard keys, TLS keys, the
  LUKS passphrase. Files are written 0600 and `output/` is in `.gitignore`;
  treat the ISOs the same way.
- **A role script runs as root** and does what the role says. Read
  `kiwi-server script fleet.yaml <host> --offline` before trusting a role you
  did not write, the same way you would read an installer.

## The secret store

The store keeps `secrets/` encrypted when you are not using it.

- `kiwi-server secrets init fleet.yaml` turns it into `secrets.age`, puts one
  key in the GNOME Keyring and shows you a recovery key once: save that one
  somewhere safe.
- `secrets unlock` opens it into memory for 8 hours (`--for`, `--identity
  FILE` for the recovery key); `secrets lock` closes it.
- A command run while it is locked refuses and says so.
- It needs `age` (`brew install age`); `age` and `tar` alone can open it by
  hand.
- While it is open, anything running as you can read it.

### The same fleet on another computer

1. Copy or sync the fleet folder (`fleet.yaml`, `secrets.age`,
   `secrets.recipients`; `output/` is made again).
2. Run `kiwi-server secrets join fleet.yaml laptop` there.
3. Run the line it prints on a computer that can open the store. That one
   encrypts the store for both.
4. With the new `secrets.age` back on the laptop, `secrets unlock` works there
   too.

`secrets remove fleet.yaml laptop` takes a computer out again. A copy it kept
still opens with its key: treat a lost computer's secrets as known. A
computer never saves over a `secrets.age` another one saved since it opened
it; it says so and keeps its changes in memory until you take the other copy.

## Generated passwords and the seed

- **Service passwords you leave out are generated** from `secrets/seed`, the
  same at every render, so the fleet file need not hold them.
  `kiwi-server secrets show fleet.yaml <host> [setting]` prints one.
  `kiwi-server logins fleet.yaml [host]` lists the ones a person types, with
  the address and the user name: see
  [Sign in to a service](machines.md#sign-in-to-a-service).
  `validate` warns about those still written in the file.
- Taking one out on a running machine changes that password; Nextcloud's and
  Portainer's are only read at their first start.
- The seed is made on first use and backed up with the fleet. A machine
  refuses an apply with a different seed: its databases keep the passwords
  they were created with.

## Encrypted disks

**Encrypted Debian disks** install with a one-time key. Type the one in
`secrets/luks/<host>.once` at the first boot, and the first `apply` replaces
it before anything secret reaches the machine. From then on the disk asks for
`secrets/luks/<host>.key`; losing that file locks the disk, so keep the fleet
backed up. A master also gets a new volume key at that apply, which takes
some minutes (`debian.reencrypt`).

Encrypting a CoreOS root takes about 4 GiB of memory at the first boot: on
less, use `target: debian`.

**The starter stick** encrypts too, before there is any ring: the machine's
TPM 2.0 opens the disk until the network takes it in. Then `apply` adds the key
for the console, binds the network's rings (or leaves the console key alone,
when there is no ring yet) and removes the TPM: from then on the TPM opens
nothing. A machine without a TPM is installed plain only after PLAIN at its
screen. A disk that opens only with its key (no ring) means the machine no
longer restarts by itself for updates: they are fetched and start with the
next restart, which someone does, with the key at the screen. A disk that is not encrypted is a warning on the control page's
overview ("Needs a look"), on the machine's page and in `status`.

### Unlocking at boot

A disk can open itself at boot. The `tang` module on one node serves a
*ring*, and another machine's `unlock:` block binds its disk to it:

```yaml
    papaya: { node: { modules: [vpn-client, tang] } }          # serves the ring infra on port 7500
    lychee: { debian: { encrypt: true }, unlock: { rings: [infra], ssh: true } }
```

- On a Debian machine `apply` does the binding. A CoreOS machine is bound by
  Ignition, so build its ISO with the ring running.
- Its key stays in another keyslot, in `secrets/luks/<host>.key`, so a ring
  that is gone costs a console and not the disk.
- A machine that cannot reach the ring at boot, such as a master on the
  internet, is bound through `via: { infra: "http://127.0.0.1:7500" }` and
  opened with `kiwi-server unlock <fleet> <host>`. That forwards the ring in
  over SSH in its initramfs (`--manual` hands over the key instead, on
  Debian).
- `kiwi-server status` says when a machine is waiting like this, which it
  knows by the host key its initramfs shows.
- A reinstalled machine shows a new host key there: `unlock --manual` gives it
  the one-time key of its new ISO and nothing else, and no ring goes to it
  before its first apply.
- The login key for all this, `secrets/unlock_ed25519`, opens no running
  machine and can only run the unlock; on CoreOS `unlock.ssh_from` also says
  where from.
- A machine behind NAT that nobody can reach at boot asks its master instead:
  `vpn-server.unlock_relay: { "7500": "<mesh address>:7500" }` there, and
  `via: { infra: "http://<master>:7500" }` on the machine.
- A machine cannot serve its own ring. The ring named `operator` is served
  from the machine running kiwi-server while it is needed, for what no other
  machine can open.
- Back up the rings' keys: the `backup` module keeps them.

## SSH

- **The machines' SSH host keys** are kept in `secrets/known_hosts`. The first
  contact accepts a new machine's key; a changed key (a reinstall) is refused
  until you drop the old line (`ssh-keygen -R <host> -f secrets/known_hosts`).
- **SSH off port 22**: `admin: { ssh_port: 41022 }` moves sshd at the next
  apply without a lockout (both ports until the new one is proven).
- **fail2ban**: the `fail2ban` module (on every master, and by itself on a
  node whose sites strangers reach) bans an address after five failed logins
  in ten minutes, and drops an address opening more than 20 SSH connections a
  minute. Its rules follow what fail2ban itself, Debian, YunoHost and mailcow
  ship, so that using a service never gets anyone banned:
  - **A first ban is 10 minutes**; each time the same address comes back it
    grows, up to a week. Five bans in a day: out for a week, on every port.
  - **Never banned**: the mesh, the network's own machines (the master sees
    them at the VPN tunnels and tells its nodes), and, on machines at home, private addresses
    (`fail2ban: { ignore_home: false }` turns that last one off).
  - **SSH**: failed logins only (logins are by key), not every connection
    that closes early.
  - **At public sites** it reads each app's own log for wrong logins: Forgejo
    (ten tries), Vaultwarden, Nextcloud, Immich, Paperless (Jabber counts its own). It also bans
    scanners, an address refused forty times outside the apps' own paths, and
    a flood (600 refused requests in five minutes). A bad request alone (a
    400) is never a reason: real apps make them.
  - **On a node behind the master** the ban is nginx's (it closes the connection), since only nginx
    knows the visitor's address there.
  - `tests/drills/fail2ban.py` proves all of this from a stranger's address.
- **Rate limits**: a site that strangers reach (a public name, a hidden
  service) answers one address 20 times a second, with 80 at once; more gets
  "429 Too Many Requests". A page's own files (scripts, styles, pictures,
  fonts) are not counted, nor what sync clients and photo apps fetch in bulk
  (WebDAV, previews, thumbnails). A hidden service's visitors all look like one
  address, so there the limit is the site's, three times as high.
  `reverse-proxy: { rate_limit: 50 }` changes it, `0` switches it off. Sites of
  the VPN are not limited.
- **An SSH CA** (`kiwi-server sshca fleet.yaml init`, kept as
  `secrets/ssh_ca`): every apply then gives the machine a host certificate
  for its names and addresses, so it is known at a new address without
  trusting the first key shown there, and its sshd takes user certificates.
  - `sshca fleet.yaml sign ~/.ssh/id_ed25519.pub --days 30` lets a key in on
    every machine for that long.
  - `sshca fleet.yaml trust` puts the CA in your `~/.ssh/known_hosts`.
  - On the control page: Computers, "Let a key in for a while" makes the CA
    and signs a pasted public key; the certificate is shown once, to save next
    to the key. In the app: SSH certificates… in the menu.
  - `admin.ssh_keys` keep working beside it; the unlock at boot keeps its own
    key.
