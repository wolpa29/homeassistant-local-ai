# Changelog

## 1.5.1 - 2026-06-26

- `advanced.history_size` default 0 -> 2.

## 1.5.0 - 2026-06-26

- Config field names prefixed with status dots (required / optional / advanced); descriptions reformatted.
- Added German translation (`translations/de.yaml`).

## 1.4.1 - 2026-06-26

- Fix: renamed internal lib dir and service run scripts to match repo name; fixes startup crash on fresh installs.

## 1.4.0 - 2026-06-26

- Combined infra stack: `infra/start.sh` starts Whisper + TTS and prints their URLs.
- Advanced config sections marked as skippable.
- Removed dead battery-monitor config (`CHECK_INTERVAL_SECONDS`, `BATTERY_THRESHOLD`).

## 1.3.7 - 2026-05-25

- Safer LLM command parsing; added action validation and JSON retry handling.
- Sanitized execution history so internal `[OK]` logs are not copied into replies.

## 1.2.7 - 2026-05-09

- Depersonalised system prompts (`prompts_de.yaml`, `prompts_en.yaml`); only the universal HA contract remains.
- `pre_llm_memory.md` / `post_llm_memory.md` default to empty for per-setup tuning.

## 1.2.6 - 2026-05-09

- Auto-rebuild RAG index on startup when `rag.enabled=true`; failures only warn, never block.

## 1.2.5 - 2026-05-09

- Fix: corrected GHCR image path after account rename (`wolpay29` -> `wolpa29`).

## 1.2.4 - 2026-05-09

- Telegram ReplyKeyboard stays visible across actions.

## 1.2.3 - 2026-05-03

- Section descriptions now summarise their fields.

## 1.2.1 - 2026-05-03

- Fix: field descriptions render in HA UI (removed wrong `fields:` wrapper).
- Fix: bot token no longer logged in plaintext.

## 1.2.0 - 2026-05-02

- Config UI split into 10 collapsible sections.

## 1.1.0 - 2026-05-02

- Internal Whisper removed, external STT only (`whisper.url`).
- Debian base image (fixes `sqlite-vec` on aarch64); armv7 dropped.

## 1.0.0 - 2026-04-27

- Initial release. Three services (`voice_gateway`, `notify_gateway`, `telegram_bot`) in one container under s6-overlay.
