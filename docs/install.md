# Install

kiwi-server is a [kiwi-updater](https://github.com/derlocke-ng/kiwi-updater)
app: a CLI (`kiwi-server`) and a GTK4 desktop app (**Kiwi Server**,
`kiwi-server-gui`), installed into `~/.local`, no root.

## With kiwi

Register the Kiwi Network catalog once, then install:

```bash
kiwi catalog add https://github.com/derlocke-ng/kiwi-catalog.git   # once per machine
kiwi install kiwi-server
```

kiwi follows the latest version tag, updates it in the background and shows
the app with its icon in `kiwi-gui`. `kiwi info kiwi-server` and
`kiwi diff kiwi-server` show what an install or update would run.

## From the checkout

It is the same installer kiwi runs:

```bash
git clone https://github.com/derlocke-ng/kiwi-server.git && cd kiwi-server
./install.sh install            # ~/.local/bin/kiwi-server, kiwi-server-gui, the library
kiwi-server doctor              # python, pyyaml, build tools, container runtime
```

## What it needs

- `python3` with PyYAML and Jinja2 (`pip install --user jinja2` if the image
  lacks it)
- `openssl`
- `ssh`: `apply`, `status`, `enroll` and the fleet backup reach the machines
  over it
- the GUI: python3-gobject, GTK 4 and libadwaita
- building ISOs: `butane`, `coreos-installer` and `xorriso` (see below)

## The toolchain

| tool | used for |
|---|---|
| `butane` | Butane → Ignition (`--strict`) |
| `coreos-installer` | downloading and verifying the Fedora CoreOS ISO, `iso customize` |
| `xorriso`, `cpio`, `gzip`, `curl` | the Debian netinst rebuild and download |
| `xz`, `sfdisk`, `debugfs`, `e2fsck` | Armbian board images |

They run natively when installed, otherwise in a small podman/docker image,
so nothing has to be layered onto the desktop. On a desktop that does not
have them:

```bash
kiwi-server toolchain build     # one image with all of them, used automatically
```

`kiwi-server toolchain` shows which run natively and which would use the
container image; `--toolchain native` or `container` forces one. Inside the
container a tool sees the output and cache directories at the same paths, so
nothing else changes. On Fedora the three are `dnf install butane
coreos-installer xorriso`; on Bluefin/Silverblue the image is the way.
