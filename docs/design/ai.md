# A local AI: the `ai` module

Asked for by the owner on 23 November 2026: a language model at home, on his gaming workstation,
as the back-end for his other computers, with opencode (a Flatpak) as the front-end. It reads
sensitive data (his files, before the move to v2), so nothing of it may leave the house.

Status: built (2.306.0: the module, `on_demand`, `kiwi-server ai key|connect|bench`). It runs on
the owner's workstation, lemonaide. `ai models`, `ai load`, `ai connect --apply` and a `bench`
that loads its models: built on 25 November 2026 and tested against a stand-in for Studio. The
same requests ran on lemonaide from the tool these commands replace (`kiwi-ai-opencode`); the
commands themselves have not loaded a model there yet.

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

## The loaded model

After a start Studio has no model loaded and answers 400 "No model loaded" to every chat. It
does not load one by itself when a request names it (its auto-switch is a setting, off). A
model is loaded through Studio's own `POST /v1/load`, with the API key:

- `model_path` and `gguf_variant`: the model and its quant. `/v1/models` lists a model with
  several quants as its id and once more as `id:QUANT` for each; kiwi-server shows it as one
  model with its quants.
- `max_seq_length`: the context. Studio's answer names the context it gave; kiwi-server says
  so when that is less than asked for.
- `cache_type_kv: q8_0`: the context cache at 8 bits. It takes half the memory of the card, so
  the model itself stays on the card (35B-A3B at 262k: 2.7 GB against 5.1 GB, 125 against 68
  tokens per second).
- `disable_vision: true` unless `--images` is given: the image reader takes memory of the card.
- `cache_ram: 16384`: memory in MiB for chats that are not in use at the moment. A chat that
  is still there answers at once; a dropped one is read again, minutes at 200k tokens.

`kiwi-server ai load` and `ai connect --apply` do this. Nothing named: the loaded model stays;
after a start, the model loaded last through kiwi-server, kept in `ai/<machine>.json` beside
the fleet file (model, quant, context, image reader). A loaded model is loaded again only when
another context or the image reader is asked for.

## Benchmarks

Studio has none. `kiwi-server ai bench FLEET MACHINE [MODEL…]`: the same prompts through the
API for each model that is downloaded: time to the first token, tokens per second when
writing, and a small set of tasks with known answers. Reading speed comes from llama-server's
own count (`timings` in the answer) over 4000 tokens, when Studio passes it on. Each model is
loaded in turn, all at the context of the model that was loaded, and that one is loaded again
at the end, also when a model failed. Every request carries `top_k` (20), see below. The
results are kept per machine and model, so models and machines can be compared over time (a
table on the control page later).

## opencode

`kiwi-server ai connect FLEET MACHINE` prints OpenCode's settings for the loaded model.
`--apply` loads the model and writes them into every OpenCode on the computer it runs on: the
Flatpak (`~/.var/app/ai.opencode.opencode/config/opencode/opencode.json`) and
`~/.config/opencode/opencode.json` when that folder exists. The rest of the file is kept, and
the file as it was is copied to `opencode.json.before-kiwi`. A file with comments is not
touched. What is written, and why (OpenCode 2.1.3, measured on 25 November 2026):

- `limit.context` from Studio's `/v1/status`, and `limit.output`. Without the limit OpenCode
  does not sum up a chat in time.
- `timeout: false`, `headerTimeout` and `chunkTimeout` of one hour, as numbers. OpenCode drops
  the whole provider without a word on `"chunkTimeout": false` and answers from its own cloud
  model.
- `options.top_k: 20`. Studio fills in `top_k 0` for a model it has no preset for when the
  client sends none: 91 against 125 tokens per second.
- `tool_call: true`, and the model as `model` and `small_model`: titles come from the same
  model, nothing leaves the house. `--local-only` adds `disabled_providers: ["opencode"]`,
  which hides OpenCode's cloud models.
- The network's CA: a copy of the certificate in `~/.local/share/kiwi/`, and for the Flatpak
  `NODE_EXTRA_CA_CERTS` as an override when it has none.
- The key as `{file:~/.config/kiwi/<machine>-ai.key}`, a copy with mode 0600 outside the
  secret store: OpenCode starts while the store is locked. Someone with this computer's disk
  gets that key, and with it the AI from the LAN or the VPN; no other secret of the fleet.

opencode runs on the computer where the data is; it sends what it reads to the model at home,
nothing else. To go through sh3's files: mounted read only on that computer.

## The owner's workstation (23 November 2026)

- AMD Radeon RX 9070 XT (RDNA 4, 16 GB), Ryzen 9 7900X, 64 GB ECC DDR5-4800: `unsloth/unsloth-rocm`
  with `/dev/kfd` and `/dev/dri`. 16 GB on the card: models of about 14B fully on it at 8 bits, larger
  ones at 4 bits or partly in the 64 GB of memory.
- Two SSDs: the system of the fleet goes on the 500 GB one, named by its `/dev/disk/by-id/…` path in
  `disk:` (never `auto`: the 2 TB one holds his own system, Bluefin-DX, and is not touched). uCore
  brings its own boot partition on that disk; the firmware's boot menu picks which system starts.

Measured on 25 November 2026: `peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP`, quant `UD-Q4_K_S`,
at 262144 tokens with the settings above writes 47 tokens per second (33 with a full context),
reads 200k tokens in 225 s and recalls 10 of 12 facts from a 160k log. `UD-Q2_K_XL` fits the
card whole and writes 125, but recalls 7 of 12. Chosen: `UD-Q4_K_S`.

## llama.cpp alone: `engine: llama`

Asked for by the owner on 27 November 2026: a small model on sh3 as a backup, for small tasks
and talk, on the graphics chip inside its processor. Studio does not fit there: its image is
about 40 GB and it wants a graphics card.

Status: built (2.309.0, kiwi-modules 0.125.0). Tried on a Debian node with the processor alone,
the Vulkan image on the workstation; not yet run on sh3 through the module (sh3 is still a v1
machine).

- **What runs**: llama.cpp's server from its own image (`ghcr.io/ggml-org/llama.cpp`):
  `server-vulkan` for `gpu: vulkan` and `amd` (`/dev/dri` into the container), `server` for
  `none`, `server-cuda` for `nvidia`. The same API as Studio on the same port, and its chat page.
- **Models**: named in `ai.models` as on Hugging Face (`user/model:quant`), written into a
  preset file (`models.ini`). The server runs in its several-models mode with one model in
  memory at a time: the first is loaded at the start, another with the request that names it.
  They download at their first use, into the folder Studio uses too and in the same layout, so
  what one downloaded the other finds.
- **Memory**: a model nobody asked for `idle_minutes` leaves the memory and comes back with the
  next request (2 to 3 seconds for a 4B model from the page cache). sh3 runs other services:
  nothing is held that is not used.
- **Its key** is a setting made from the seed (`api_key`), not one made on a page: `ai connect`
  and `ai bench` take it from the fleet, `logins` lists it, `ai key` has nothing to take.
- **The commands**: `ai models`, `ai connect` and `ai bench` work as with Studio. Nothing is
  chosen per load: the fleet names the models and their context (`ai.context`; with 0 the
  loaded model says how much it has). `ai load` loads a model ahead of its first request
  (`POST /models/load`).
- **Off, always**: its tools (they read and write files and run commands), its agent mode, its
  MCP proxy, and `--connect` (a tunnel). Nothing in the module can switch them on.
- **Backup**: nothing. Models download again, and the page keeps its chats in the browser.

Measured on sh3 (Ryzen 7 PRO 5750G, Vega 8 through Vulkan, 27 November 2026), tokens per second:

| Model (Q4_K_M) | Writes, graphics chip | Writes, processor | Reads, graphics chip | Reads, processor |
|---|---|---|---|---|
| Gemma 3 4B | 17.2 | 15.7 | 227 | 100 |
| Qwen3 8B | 9.5 | 8.1 | 103 | 52 |

The processor-only run took all eight cores (load 8). Switching between the two models took 2
seconds.

## Open

- The chosen model as a setting of the module in the fleet file, loaded by the machine itself
  after a start. Today someone runs `ai load`.
- The same on the control page and in the app: choose a model, load it.
- How `unsloth/unsloth-rocm` takes `--disable-tools`, whether its API runs without the UI, and
  whether its ROCm knows RDNA 4 (gfx1201: ROCm 6.4 or newer).
- Podman or the host: measured on the workstation.
