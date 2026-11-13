# Disk encryption

kiwi-server can install machines with an encrypted disk (LUKS) and let them
unlock it at boot without anyone typing a key. This page explains the keys
kiwi-server keeps, how a machine opens its disk through a *Tang ring*, and
what to do when a machine waits at boot.

## Encrypted Debian disks

`debian: { encrypt: true }` installs Debian on an encrypted disk. The install
uses a one-time key: at the first boot, type the key from
`secrets/luks/<host>.once`. The first `apply` replaces it before anything
secret reaches the machine. From then on the disk's key is
`secrets/luks/<host>.key`.

**Warning.** Losing `secrets/luks/<host>.key` locks the disk for good. Keep
the fleet backed up; see [Backups](backups.md#the-fleet-backup).

A master also gets a new volume key at that first apply, which takes some
minutes (`debian.reencrypt`, on for masters unless set).

Encrypting a Fedora CoreOS root needs about 4 GiB of memory at the first
boot. On a machine with less, use `target: debian`.

## Unlock at boot with Tang

*Tang* is a small server that lets a machine unlock its disk at boot while it
can reach the server, without a key being typed. The `tang` module on a node
serves one or more *rings*; a ring is one Tang server with its own keys. A
machine's `unlock:` block binds its disk to rings:

```yaml
hosts:
  papaya: { node: { modules: [vpn-client, tang] } }       # serves the ring infra on port 7500
  lychee: { debian: { encrypt: true }, unlock: { rings: [infra], ssh: true } }
```

On a Debian machine, `apply` does the binding. A Fedora CoreOS machine is
bound by Ignition, so build its ISO while the ring runs. The machine's key
stays in another keyslot, in `secrets/luks/<host>.key`, so a ring that is gone
costs you a trip to the console and not the disk.

A machine cannot serve its own ring. The ring named `operator` is served from
the computer that runs kiwi-server, while it is needed, for machines that no
other machine can open.

The `backup` module keeps the rings' keys. Without them, the machines bound to
a ring need their key typed in.

## Machines that cannot reach a ring at boot

A machine that cannot reach the ring at boot, such as a master on the
Internet, is bound through a forwarded address:

```yaml
unlock: { rings: [infra], via: { infra: "http://127.0.0.1:7500" }, ssh: true }
```

It then waits at boot, and you open it from your computer:

```bash
kiwi-server unlock fleet.yaml sorbet
```

`unlock` logs in to the machine's initramfs (the small system that runs before
the disk is open) over SSH and forwards the ring into it. On Debian,
`--manual` hands over the disk's key instead. `kiwi-server status` says when a
machine is waiting like this; it recognises the machine by the SSH host key
its initramfs shows.

A machine behind NAT that nobody can reach at boot asks its master instead.
Set `vpn-server.unlock_relay: { "7500": "<mesh address>:7500" }` on the master
and `via: { infra: "http://<master>:7500" }` on the machine.

## The unlock key

The SSH key for all of this, `secrets/unlock_ed25519`, logs in to no running
machine; it can only run the unlock. On Fedora CoreOS, `unlock.ssh_from`
also limits where it may come from.

## A reinstalled machine

A reinstalled machine shows a new host key in its initramfs.
`kiwi-server unlock --manual` gives it the one-time key of its new ISO and
nothing else, and no ring is forwarded to it before its first apply.
