# Home Assistant AI Gateway Add-on

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

## Quick start

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, paste `https://github.com/wolpa29/homeassistant-ai-gateway`.
2. Install **Home Assistant AI Gateway**.
3. Open **Configuration**, fill in at minimum:
   - `telegram` → `bot_token`, `chat_id`
   - `lmstudio` → `url`
   - `whisper` → `url` (leave empty for text-only use)
4. **Start** the add-on.

See the **Documentation** tab for the full reference including entities.yaml, RAG setup, automation examples, and the per-setup memory files (`pre_llm_memory.md` / `post_llm_memory.md`) where you teach the assistant about your house — names, floors, device nicknames, recurring STT errors.

## Infrastructure

Whisper and TTS run as separate Docker containers — ready-to-use setups included in `infra/`:

- **`infra/faster_whisper/`** — Whisper STT server. `docker-compose up -d`.
- **`infra/tts_server/`** — TTS server. `docker-compose up -d`.

Recommended: run both on the same machine as LM Studio to share the GPU.

For the Raspberry Pi voice client an install script is available: `clients/raspberry_pi/install.sh`.
