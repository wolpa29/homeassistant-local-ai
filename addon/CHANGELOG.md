# Changelog

## 1.4.0 - 2026-06-26

- Onboarding simplified: the README is now a linear, numbered setup guide (each step says what it does and which port it uses), with an "Add to my Home Assistant" button.
- New combined infra stack: `bash infra/start.sh` starts Whisper and TTS together, waits until both are healthy, and prints the exact URLs to paste into the add-on.
- Add-on config: advanced sections (RAG, Preprocessor, Fallback & History, Gateways) are now marked as skippable so beginners know the defaults are fine.
- Docs: new "Quick start (5 fields)" and "LM Studio in one minute" sections; the bare-metal/systemd guide moved out of the README into `docs/bare-metal.md`.
- Removed dead battery-monitor config (`CHECK_INTERVAL_SECONDS`, `BATTERY_THRESHOLD`).

## 1.3.7 - 2026-05-25

- Made LLM command parsing safer and more consistent.
- Sanitized execution history so internal `[OK]` logs are not copied into replies.
- Added action validation, shared JSON retry handling, and safer follow-up history.

## 1.2.7 - 2026-05-09

- Depersonalised the system prompts: all setup-specific examples (personal names, pool, upstairs/downstairs, blinds, room labels) removed from `prompts_de.yaml` and `prompts_en.yaml`. Only the universal HA contract remains (JSON schema, action names, domains, service_data).
- Restructured `pre_llm_memory.md` and `post_llm_memory.md`: default content is empty, with an expanded inspiration block inside HTML comments, users add their own setup hints (names, floors, nicknames, STT corrections, never-do rules) by uncommenting or rewriting.
- Documentation: new "Memory files, your per-setup tuning" section in DOCS.md and a short pointer from the README.

## 1.2.6 - 2026-05-09

- Auto-rebuild the RAG index on add-on startup when `rag.enabled=true`. Progress is visible in the add-on log; failures only log a warning and never block service startup.

## 1.2.5 - 2026-05-09

- Fix: corrected the GHCR image path after the GitHub account rename (`wolpay29` to `wolpa29`).

## 1.2.4 - 2026-05-09

- Telegram ReplyKeyboard now stays visible across actions; a fresh carrier message is sent after each action so the keyboard survives even with 24h chat auto-delete enabled.

## 1.2.3 - 2026-05-03

- Each config section now shows a summary of all its fields in the section description.
- Logo Update

## 1.2.1 - 2026-05-03

- Fix: field descriptions now appear above config inputs in HA UI (removed incorrect `fields:` wrapper from translations).
- Fix: bot token no longer appears in plaintext in addon logs.

## 1.2.0 - 2026-05-02

- Config UI now has 10 collapsible sections (Services, Telegram, Home Assistant, LM Studio, Whisper, TTS, Gateways, RAG, Preprocessor, Fallback & History).

## 1.1.0 - 2026-05-02

- Internal Whisper removed, external STT server only (`whisper.url`).
- Debian base image, fixes `sqlite-vec` install on aarch64.
- armv7 dropped, HAOS on RPi 3/4 uses aarch64.

## 1.0.0 - 2026-04-27

Initial release. Three services (`voice_gateway`, `notify_gateway`, `telegram_bot`) in one container under s6-overlay.
