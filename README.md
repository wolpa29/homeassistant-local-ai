<p align="center">
  <img src="addon/logo.png" alt="Home Assistant AI Gateway" width="200"/>
</p>

<h1 align="center">Home Assistant AI Gateway</h1>

<p align="center">
  Control your smart home with a <b>local LLM</b>, by text, voice message, or a
  microphone in any room.<br/>
  No cloud. No subscriptions. Your data stays at home.
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fwolpa29%2Fhomeassistant-local-ai">
    <img src="https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg" alt="Add repository to your Home Assistant instance."/>
  </a>
</p>

> You say *"turn off the lights downstairs and set the bedroom to 20°"*, and the add-on figures out which entities you mean and does it. Locally, on your own GPU.

---

## What you need

- A machine with a GPU running **LM Studio**, **Whisper**, and **TTS**
- **Home Assistant** (Supervisor / HAOS)
- A **Telegram bot** (free, from [@BotFather](https://t.me/BotFather))

Text only? You can skip Whisper and TTS and just type to the bot.

---

## Setup

### Step 1: Start Whisper and TTS

```bash
bash infra/start.sh
```

Starts Whisper (port 10300) and TTS (port 10400), waits until both are healthy, and prints the exact URLs for Step 4. Skip this step for text-only. Needs Docker and an NVIDIA GPU, see [`infra/README.md`](infra/README.md).

### Step 2: Start LM Studio

Install [LM Studio](https://lmstudio.ai), load a **chat model** and an **embedding model**, start the **Local Server** (port 1234). The embedding model powers entity search (RAG). See [tested models](#tested-hardware).

### Step 3: Install the add-on

Click **[Add to my Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fwolpa29%2Fhomeassistant-local-ai)** (or go to **Settings, Add-ons, Add-on Store, three-dot menu, Repositories** and add this repo's URL), then install **Home Assistant AI Gateway** (ports 8765 + 8766).

### Step 4: Fill in 5 fields and start

In the add-on **Configuration** tab, set only these:

| Field | Example |
|-------|---------|
| `telegram.bot_token` | from [@BotFather](https://t.me/BotFather) |
| `telegram.chat_id` | your numeric Telegram user ID |
| `lmstudio.url` | `http://192.168.1.10:1234` |
| `whisper.url` | `http://192.168.1.10:10300/v1/audio/transcriptions` |
| `tts.url` | `http://192.168.1.10:10400/tts` |

Leave `home_assistant.token` **empty** (the supervisor token is used automatically). Press **Start**.

**Done.** Send the bot a message like *"turn on the kitchen light"*.

### Step 5: Adapt the LLM to your home (recommended)

Edit `/addon_configs/<slug>/userconfig/post_llm_memory.md` via **File Editor** or **Samba**. Add room names, device nicknames, "never do X" rules, STT corrections. These hints attach to every prompt, so the LLM knows your setup. Full guide: [Adapt the LLM to your home](addon/DOCS.md#memory-files-adapt-the-llm-to-your-home).

---

<details>
<summary><b>Step 6 (optional): Voice assistant in a room</b></summary>
<br/>

Install the wake-word client on a Raspberry Pi:

```bash
cd clients/raspberry_pi && bash install.sh
```

Wake word triggers recording, the gateway (port 8765) handles transcription and the LLM pipeline, TTS plays the reply. Configure the gateway URL and API key in `clients/raspberry_pi/.env`.

</details>

<details>
<summary><b>Step 7 (optional): Telegram quick-buttons and HA notifications</b></summary>
<br/>

- **Quick-action buttons**: edit `menus.yaml` for inline buttons that bypass the LLM (instant and reliable for frequent actions).
- **Notifications**: point HA automations at the notify gateway (port 8766) to fan out to Telegram and/or TTS speakers.

See [`addon/DOCS.md`](addon/DOCS.md) for both.

</details>

---

## How it works

Voice input is transcribed by Whisper, the intent is matched to HA entities via vector search (RAG), the LLM (LM Studio) decides the action, Home Assistant executes it, and the reply comes back as text in Telegram or audio via TTS. Everything runs locally, nothing leaves your network.

## Tested hardware

| GPU | Chat model | Notes |
|-----|------------|-------|
| RTX 2080 Ti (11 GB VRAM) | Gemma 4 4B IT | nomic-embed-text-v2-moe + faster-whisper large-v3-turbo simultaneously. Good speed with RAG and Preprocessor on. |
| RTX 3090 (24 GB VRAM) | same stack | Noticeably faster, more headroom for larger models. |

## Documentation

| | |
|--|--|
| [`addon/DOCS.md`](addon/DOCS.md) | Full setup guide, entities.yaml, RAG, LLM tuning, automation examples, troubleshooting |
| [`docs/OVERVIEW.md`](docs/OVERVIEW.md) | Architecture, request flow, module map |
| [`docs/bare-metal.md`](docs/bare-metal.md) | Run the services with systemd instead of the add-on |

## Repository layout

| Path | What's there |
|------|--------------|
| [`addon/`](addon) | HA add-on packaging, the recommended way to run this |
| [`core/`](core) | The brain: HA client, LLM, RAG, processor, prompts |
| [`services/`](services) | `voice_gateway`, `notify_gateway`, `telegram_bot` (bundled into the add-on) |
| [`infra/`](infra) | One-command Whisper and TTS Docker stack |
| [`clients/`](clients) | `raspberry_pi/`, on-device wake-word voice client |
| [`docs/`](docs) | Architecture reference and bare-metal guide |
