<p align="center">
  <img src="addon/logo.png" alt="Home Assistant AI Gateway" width="200"/>
</p>

# Home Assistant AI Gateway

Control your smart home with a **local LLM** — no cloud, no subscriptions. Connect LM Studio (or any OpenAI-compatible server) to Home Assistant and interact via Telegram, voice message, or microphone in any room.

Everything runs locally. Your data stays at home.

## How it works

1. **STT** — voice is transcribed by an external Whisper server.
2. **Preprocessor** — small LLM call classifies intent, fixes typos, rewrites the query using conversation history.
3. **RAG** — query is embedded and matched against your HA entity catalogue (vector search).
4. **LLM parser** — LM Studio sees the transcript, matched entities, and live states; decides what to do.
5. **HA action** — REST call to execute the result.
6. **Reply** — Telegram gets text; RPi/ESP32 gets WAV via TTS.

Telegram inline buttons bypass the LLM and call HA directly — instant and reliable for frequently used actions.

## Services

- **Telegram bot** — text or voice commands via chat. Inline button menus for direct HA actions.
- **Voice gateway** (port 8765) — HTTP API for RPi / ESP32 audio clients. Wake word → record → transcribe → reply via TTS.
- **Notify gateway** (port 8766) — HA posts notifications here; fans them out to Telegram and/or TTS speakers.

## Tested hardware

The full stack (LM Studio + Whisper + TTS + embedding) runs on a single machine:

- **RTX 2080 Ti (11 GB VRAM)** — Gemma 4 4B IT + nomic-embed-text-v2-moe + faster-whisper large-v3-turbo simultaneously. Context 8192. Good speed even with RAG + Preprocessor enabled.
- **RTX 3090 (24 GB VRAM)** — same stack, noticeably faster with more headroom for larger models.

## Repository layout

| Path | What's there |
|------|--------------|
| [`core/`](core) | Framework-agnostic brain: HA client, LLM, RAG, processor, prompts |
| [`services/`](services) | `voice_gateway`, `notify_gateway`, `telegram_bot` — bundled into the HA add-on |
| [`infra/`](infra) | `faster_whisper/` (STT) and `tts_server/` — run separately on any host |
| [`clients/`](clients) | `raspberry_pi/` — on-device wake-word + voice client |
| [`deploy/systemd/`](deploy/systemd) | systemd unit files + `install.sh` for bare-metal hosts |
| [`addon/`](addon) | HA Supervisor add-on packaging (Dockerfile, config.yaml, rootfs/) |
| [`docs/`](docs) | Architecture & workflow reference |

## Two ways to run

**1. HA Add-on** (recommended) — install via the Add-on Store from this repository. See [`addon/DOCS.md`](addon/DOCS.md) for the full setup guide.

**2. Bare-metal / systemd** — run the services directly on a host:

```bash
sudo deploy/systemd/install.sh
```

All services share a single `.env` file (see [`.env.example`](.env.example)).

## Infrastructure

Whisper and TTS run as separate Docker containers — ready-to-use setups included:

- **`infra/faster_whisper/`** — Whisper STT server. `docker-compose up -d`.
- **`infra/tts_server/`** — TTS server. `docker-compose up -d`.

Recommended: run both on the same machine as LM Studio to share the GPU.

## Raspberry Pi client

A voice client with wake-word detection is available for Raspberry Pi:

```bash
cd clients/raspberry_pi
bash install.sh
```

Configure the gateway URL and API key in `clients/raspberry_pi/.env`.

## Documentation

- [`addon/DOCS.md`](addon/DOCS.md) — full setup guide: entities.yaml, RAG, automation examples, troubleshooting
- [`docs/OVERVIEW.md`](docs/OVERVIEW.md) — architecture, request flow, module map, env var reference
