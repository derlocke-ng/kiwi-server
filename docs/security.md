# Security

A fleet folder holds every secret of your network: passwords, WireGuard keys,
TLS keys and disk keys. kiwi-server installs machines that wipe disks and run
scripts as root. This page explains what kiwi-server does to protect the
secrets, and what you have to keep in mind.

## The ISOs wipe disks

**Warning.** An install ISO wipes the disk it was built for as soon as a
machine boots from it, without asking. Label your sticks. `build` requires the
`disk:` setting for that reason.

## The output holds secrets

The rendered files contain password hashes, WireGuard keys, TLS keys and the
LUKS keys. They are written with mode 0600, and `output/` is in `.gitignore`.
While the secret store is open, the rendered files are kept in memory only,
and only the ISOs are written to disk. An ISO or script made with `--offline`
carries the role's secrets: keep it off a provider's ISO store and delete it
once the machine is installed.

## The secret store

The secret store keeps `secrets/` encrypted while you are not using it. It
needs [age](https://github.com/FiloSottile/age), from your distribution or
with `brew install age`.

```bash
kiwi-server secrets init fleet.yaml      # turn secrets/ into secrets.age
kiwi-server secrets unlock fleet.yaml    # open it into memory for 8 hours
kiwi-server secrets lock fleet.yaml      # close it
kiwi-server secrets status fleet.yaml    # open or locked, and who can open it
```

`secrets init` puts one key in your desktop keyring (the GNOME Keyring) and
shows a **recovery key** once. Save the recovery key somewhere safe, on paper
or in a password manager: without it, a lost keyring means lost secrets.

`secrets unlock` opens the store into memory. `--for TIME` changes the 8
hours (`0` keeps it open), and `--identity FILE` opens it with the recovery key
instead of the keyring. A command that needs the secrets while the store is
locked refuses and says so. `age` and `tar` alone can open the store by hand.

**Warning.** While the store is open, any program that runs as you can read
it.

## The same fleet on another computer

You can work on one fleet from several of your computers:

1. Copy or sync the fleet folder to the new computer: `fleet.yaml`,
   `secrets.age` and `secrets.recipients`. `output/` is made again.
2. On the new computer, run `kiwi-server secrets join fleet.yaml laptop`. It
   prints a `secrets add` line.
3. Run that line on a computer that can open the store. It encrypts the store
   for both computers.
4. Bring the new `secrets.age` back to the new computer. `secrets unlock` now
   works there too.

`kiwi-server secrets remove fleet.yaml laptop` takes a computer out again. A
copy that computer kept still opens with its key, so treat the secrets of a
lost computer as known.

A computer never saves over a `secrets.age` that another computer saved after
it was opened here. It says so and keeps its changes in memory until you take
the other copy.

## Generated passwords and the seed

Service passwords that you leave out of the fleet file are generated from
`secrets/seed`, the same at every render. The seed is made on first use and
backed up with the fleet.

- `kiwi-server secrets show fleet.yaml <host> [setting]` prints a password;
  without a setting it lists their names.
- `validate` warns about passwords that are still written in the fleet file.
- Taking a password out of the fleet file on a running machine changes it.
  Nextcloud's and Portainer's admin passwords are only read at their first
  start.
- A machine refuses an apply made with a different seed, because its databases
  keep the passwords they were created with.

## A role script runs as root

A role script runs as root and does what the role says. Before you trust a
role that you did not write, read it, the same way you would read an
installer:

```bash
kiwi-server script fleet.yaml <host> --offline
```

## SSH host keys

The machines' SSH host keys are kept in `secrets/known_hosts`. The first
contact accepts a new machine's key. A changed key, after a reinstall for
example, is refused until you remove the old line:

```bash
ssh-keygen -R <host> -f secrets/known_hosts
```

## SSH off port 22 and fail2ban

`admin: { ssh_port: 41022 }` moves sshd to another port at the next apply,
without locking you out. sshd listens on both ports until kiwi-server has
logged in over the new one, and only then is port 22 closed.

The `fail2ban` module, which is part of every master, bans an address for an
hour after five failed logins in ten minutes, and drops new SSH connections
from an address that opens more than 20 a minute. The mesh is never banned.
On a master with public sites it also bans scanners and bad requests.

## An SSH CA

With an SSH certificate authority (CA), the fleet signs the machines' host
keys and your login keys:

```bash
kiwi-server sshca fleet.yaml init                                # make the CA, kept as secrets/ssh_ca
kiwi-server sshca fleet.yaml sign ~/.ssh/id_ed25519.pub --days 30   # let a key in on every machine for 30 days
kiwi-server sshca fleet.yaml trust                               # trust the CA in ~/.ssh/known_hosts
```

From then on, every apply gives the machine a host certificate for its names
and addresses. A machine is then recognised at a new address without trusting
the first key shown there, and its sshd accepts user certificates signed by
the CA. `--days` is 30 unless given, and `--user` sets the user the key logs in
as. Keys in `admin.ssh_keys` keep working beside the CA. The unlock at boot
keeps its own key; see [Disk encryption](disk-encryption.md#the-unlock-key).
