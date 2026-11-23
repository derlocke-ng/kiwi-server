# A local AI: the `ai` module

Asked for by the owner on 23 November 2026: a language model at home, on his gaming workstation,
as the back-end for his other computers, with opencode (a Flatpak) as the front-end. It reads
sensitive data (his files, before the move to v2), so nothing of it may leave the house.

Status: built (2.306.0: the module, `on_demand`, `kiwi-server ai key|connect|bench`); not yet run on
the workstation.

## What runs

- **Unsloth Studio** in a container: its web UI and its OpenAI-compatible API on one port (8000).
  The API: `/v1/chat/completions`, `/v1/responses`, `/v1/messages`, `/v1/models`, … with a
  bearer key. GGUF models run through llama.cpp's server, safetensors too; they come from
  Hugging Face. Image `unsloth/unsloth` (NVIDIA, driver 570.26 or newer) or
  `unsloth/unsloth-rocm` (AMD). Studio's UI is AGPL-3.0, the library Apache-2.0.
- **Its login**: the user `unsloth` with a password from the seed (`UNSLOTH_STUDIO_PASSWORD`,
  read when its data volume is new). The **API key** is made in Studio (Settings → API) and shown
  once: `kiwi-server ai key` puts it into the store.
- **Studio's own tools are off.** Its web search and code execution are on by default in the
  container; anyone with the key could run code on the workstation. The module starts it with
  `--disable-tools`. (How the image takes that flag: to check before the first build.)
- **Folders**: the models in a data folder of their own (big, not backed up: they download
  again); Studio's accounts and chats (`/opt/unsloth-studio`) backed up like any part.

## Who reaches it

- `ai.listen: [lan, vpn]`, either or both: a site `ai.<machine>.home` of the machine's reverse
  proxy, at home and through the VPN. Never public, never through Tor.
- JupyterLab (port 8888 in the image) is not opened.

## Only when someone wants it

The workstation is off most of the time. A machine setting says so (`on_demand: true`): off is
its normal state, not a problem. The control page, the app and `status` say "off, on demand";
no alarm, no runbook case, no backup overdue; its updates and backups run when it is on. The
app's and the page's Wake (Wake-on-LAN) start it.

## The workstation

Booted into a system of the fleet on a disk of its own, next to the games' system: uCore
(its NVIDIA variant brings the driver and the container toolkit), encrypted, opened by the
rings like every other machine. The GPU goes into the container (Podman: CDI). Podman against
running on the host: to measure, once it runs.

## Benchmarks

Studio has none. `kiwi-server ai bench FLEET MACHINE [MODEL…]`: the same prompts through the
API for each model that is downloaded: time to the first token, tokens per second when reading
and when writing, at a short and a long context, and a small set of tasks with known answers.
The results are kept per machine and model, so models and machines can be compared over time
(a table on the control page later).

## opencode

`kiwi-server ai connect FLEET MACHINE` writes opencode's provider block: the base URL
`https://ai.<machine>.home/v1`, the key from the store, the models. opencode runs on the
computer where the data is; it sends what it reads to the model at home, nothing else. To go
through sh3's files: mounted read only on that computer.

## The owner's workstation (23 November 2026)

- AMD Radeon RX 9070 XT (RDNA 4, 16 GB), Ryzen 9 7900X, 64 GB ECC DDR5-4800: `unsloth/unsloth-rocm`
  with `/dev/kfd` and `/dev/dri`. 16 GB on the card: models of about 14B fully on it at 8 bits, larger
  ones at 4 bits or partly in the 64 GB of memory.
- Two SSDs: the system of the fleet goes on the 500 GB one, named by its `/dev/disk/by-id/…` path in
  `disk:` (never `auto`: the 2 TB one holds his own system, Bluefin-DX, and is not touched). uCore
  brings its own boot partition on that disk; the firmware's boot menu picks which system starts.

## Open

- Which models.
- How `unsloth/unsloth-rocm` takes `--disable-tools`, whether its API runs without the UI, and
  whether its ROCm knows RDNA 4 (gfx1201: ROCm 6.4 or newer).
- Podman or the host: measured on the workstation.
