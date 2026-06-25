# Home Assistant AI Gateway, Documentation

The idea behind this add-on is to make your Home Assistant installation smart by connecting it to a **local LLM**, no cloud, no subscriptions. You run the language model yourself (e.g. via LM Studio) and this add-on acts as the bridge: it takes your requests, finds the right entities, lets the LLM decide what to do, and executes the actions in HA.

Everything runs locally. Your data stays at home.

The add-on gives you three ways to interact with the LLM and your smart home:

- **Telegram bot**, text or voice commands. The LLM interprets the request, calls the right HA services, and replies. Inline buttons trigger actions directly without LLM.
- **Voice gateway**, HTTP API for RPi / ESP32. Transcribes audio via Whisper, runs the same pipeline, replies via TTS.
- **Notify gateway**, HA posts notifications here; fans them out to Telegram and/or TTS speakers.

---

## Tested hardware

The full stack (LM Studio + Whisper + TTS + embedding) was tested on a single machine:

- **RTX 2080 Ti (11 GB VRAM)**, runs Gemma 4 e4b it (chat model) + nomic-embed-text-v2-moe (embedding) + faster-whisper large-v3-turbo simultaneously. Context length 8192. Response speed is good even with RAG + Preprocessor enabled.
- **RTX 3090 (24 GB VRAM)**, same stack, noticeably faster responses and more headroom for larger models or longer context.

All three infra services (LM Studio, Whisper, TTS) should ideally run on the same GPU machine to avoid network overhead and share VRAM scheduling.

---

## Infrastructure

The add-on itself only needs LM Studio. Whisper and TTS run as separate Docker containers; the easiest way to start both together is:

```bash
bash infra/start.sh
```

This starts Whisper (port 10300) and TTS (port 10400), waits until both are healthy, and prints the exact URLs to paste into the add-on config. See [`infra/README.md`](https://github.com/wolpa29/homeassistant-local-ai/blob/main/infra/README.md) for GPU requirements and manual setup.

**Recommended:** run all three (LM Studio, Whisper, TTS) on the same GPU machine so they share VRAM scheduling. The add-on then only needs the IP of that machine.

For the **Raspberry Pi** voice client, an install script is available:

```bash
cd clients/raspberry_pi
bash install.sh
```

It installs all dependencies and sets up the client. Configure the gateway URL and API key in `clients/raspberry_pi/.env`.

---

## Quick start (5 fields)

1. **Settings, Add-ons, Add-on Store, three-dot menu, Repositories**, add `https://github.com/wolpa29/homeassistant-local-ai`.
2. Install **Home Assistant AI Gateway** and open the **Configuration** tab.
3. Fill in these five fields, everything else has working defaults you can leave alone:

   | Field | Example | Notes |
   |-------|---------|-------|
   | `telegram.bot_token` | `123456:ABC...` | from @BotFather |
   | `telegram.chat_id` | `123456789` | your numeric Telegram user ID |
   | `lmstudio.url` | `http://192.168.1.10:1234` | LM Studio local server |
   | `whisper.url` | `http://192.168.1.10:10300/v1/audio/transcriptions` | leave empty for text-only |
   | `tts.url` | `http://192.168.1.10:10400/tts` | leave empty for text-only |

4. Leave `home_assistant.token` **empty**, the add-on uses the auto-injected supervisor token.
5. **Start** the add-on and send the bot a message like "turn on the kitchen light".

> Tip: run `bash infra/start.sh` on your GPU machine to start Whisper and TTS in one go.
> The script prints the exact `whisper.url` and `tts.url` to paste above.

## LM Studio in one minute

1. Install [LM Studio](https://lmstudio.ai) on the machine with your GPU.
2. Download a **chat model** (the tested ones are listed under Tested hardware in the README) and an **embedding model** (e.g. `nomic-embed-text-v2-moe`). The embedding model is what RAG uses to find entities.
3. Open the **Local Server** tab, load the chat model, and press **Start Server**. It listens on port `1234` by default.
4. Put `http://<that machine's IP>:1234` into `lmstudio.url`. If RAG uses the same instance, leave `rag.embed_url` empty and it reuses this URL.

That is all the add-on needs from the LLM side. No auth is required unless you enabled it in LM Studio (then set `lmstudio.api_key`).

---

## How to configure, pick your setup

- **RAG off**, define devices in `entities.yaml`. The full list with live states is sent to the LLM on every request. Good for small, fixed setups.
- **RAG on**, all HA entities are indexed in a vector DB. Each request finds the most relevant ones automatically. Add keywords/metadata in `entities.yaml` to improve results.
- **RAG + Preprocessor on**, a small extra LLM call rewrites the request using conversation history before the vector search. Best for vague or follow-up commands.
- **History on**, passes previous turns to the LLM so follow-ups work in context ("turn it off again", "and the kitchen too").

**Fallback mode** _(beta, not well tested yet)_, what happens when no matching entity is found:

- **Mode 0 (default)**, returns an error.
- **Mode 1**, fetches all live HA states and retries with the full list. Works but can produce large prompts.
- **Mode 2**, hands off to LM Studio with the HA MCP server. Requires LM Studio auth and a running HA MCP server.

---

## Configuration

**Required fields**, the add-on refuses to start if these are missing:

- `telegram.bot_token`, required when `services.telegram_bot` is on
- `telegram.chat_id` (≠ 0), required when `services.telegram_bot` is on
- `lmstudio.url`, required when `services.voice_gateway` or `services.telegram_bot` is on

**Auto-fallbacks**, leave these blank to inherit from LM Studio:

- `ha_token`: uses the auto-injected supervisor token
- `whisper.url`: voice input disabled, text commands still work
- `rag.embed_url`: reuses `lmstudio.url`
- `preprocessor.url/model/api_key`: reuses the corresponding `lmstudio.*` value

---

## entities.yaml

Edit via **File Editor** or **Samba** at `/addon_configs/<slug>/userconfig/entities.yaml`.

This file serves two purposes:
- **RAG off**, the entire list is passed to the LLM on every request.
- **RAG on**, entries enrich the auto-indexed HA entities with better keywords, metadata, and restricted actions.

### What flows into the RAG vector index (embed text)

When RAG indexes an entity, it builds an embed text from: entity ID, friendly name, area, domain, description, and keywords. The richer this text, the better the vector search finds it. Entries in `entities.yaml` extend or override what HA provides.

### What the LLM sees when an entity is retrieved (metadata)

After the vector search, the LLM receives for each matched entity: friendly name, area, current state + attributes, available actions, and the `meta` field. The `meta` field is free text, use it to give the LLM extra context it wouldn't have from state alone (conditions, rules, unit explanations).

### Actions

If you define `actions` in `entities.yaml`, they **replace** the default HA actions for that entity. This lets you restrict what the LLM can do (e.g. allow only `turn_on`/`turn_off`, not `toggle`) or add custom actions. If you leave `actions` empty, the full set of HA actions is available.

### Examples

```yaml
entities:
  # Simple switch
  - id: switch.garden_pump
    description: "Garden irrigation pump"
    keywords: ["pump", "irrigation", "garden water"]
    actions: ["turn_on", "turn_off"]
    domain: switch
    meta: "Only turn on when weather is dry. Maximum 30 minutes runtime."

  # Sensor (read-only, no actions)
  - id: sensor.plug_jbl_power
    description: "JBL speaker power consumption"
    keywords: ["JBL", "speaker", "power"]
    actions: []
    domain: sensor
    meta: "Above 5 W = playing. Below 1 W = standby."

  # Button
  - id: button.front_door_bell
    description: "Front door bell trigger"
    keywords: ["doorbell", "ring", "front door"]
    actions: ["press"]
    domain: button
    meta: ""

  # Automation, restrict to trigger only
  - id: automation.good_night
    description: "Good night routine"
    keywords: ["good night", "sleep", "night mode"]
    actions: ["trigger"]
    domain: automation
    meta: "Turns off all lights and locks the front door."
```

---

## entities_blacklist.yaml

Entities matching these patterns are excluded from the RAG index and never shown to the LLM. Accepts exact IDs or globs.

```yaml
blacklist:
  - sensor.zigbee2mqtt_bridge_state
  - sensor.*_battery_level
  - automation.test_*
```

---

## Memory files: adapt the LLM to your home

The built-in prompts only cover the universal Home Assistant contract (JSON shape, action names, domains, service-data parameters). Anything specific to **your** house, names of people, floor labels, nicknames for devices, recurring STT errors, belongs in two editable files instead of in the code:

Edit via **File Editor** or **Samba** at `/addon_configs/<slug>/userconfig/`:

- **`pre_llm_memory.md`**, appended to the query rewriter, runs **before** the RAG search. Right place for STT corrections, alternative spellings, household-specific pronoun rules.
- **`post_llm_memory.md`**, appended to every parser prompt. Right place for selection preferences, area-mapping rules, and never-do rules.

Both files ship with example blocks wrapped in `<!-- ... -->`. Anything inside those markers is ignored, copy the snippets you need outside the markers and adjust them for your home, or replace the file entirely. Add-on updates do not overwrite your edits.

```markdown
## STT corrections
- "livingroom", "lvng room" -> "living room"
- "pump" without context -> garden irrigation pump

## Selection rules
- "all off" -> only switch group entities, do not fan out.
- "Rollo Paul" -> the blind IN Paul's room, not a person named Paul.

## Floors / areas
- "upstairs", "OG" -> entities with Area="OG"
- "downstairs", "EG" -> entities with Area="EG"

## Never do
- Never trigger automation.vacation_mode without asking first.
```

After editing: restart the add-on.

---

## RAG index

Trigger a rebuild after changing `entities.yaml`: send `/rag_rebuild` in Telegram or `POST /rag_rebuild` to the voice gateway.

**Status sensor + rebuild button in HA**, add to `configuration.yaml` (remove `headers:` if `voice_api_key` is empty):

```yaml
rest:
  - resource: "http://localhost:8765/rag_status"
    scan_interval: 60
    headers:
      X-Api-Key: "<your api key>"
    sensor:
      - name: "RAG Index Entities"
        unique_id: rag_index_entities
        value_template: "{{ value_json.count }}"
        unit_of_measurement: "entities"
        icon: mdi:database-search
      - name: "RAG Last Indexed"
        unique_id: rag_last_indexed
        value_template: "{{ value_json.last_indexed }}"
        icon: mdi:clock-outline

rest_command:
  rag_rebuild:
    url: "http://localhost:8765/rag_rebuild"
    method: POST
    headers:
      X-Api-Key: "<your api key>"
```

Dashboard card:

```yaml
type: entities
title: RAG Index
entities:
  - entity: sensor.rag_index_entities
  - entity: sensor.rag_last_indexed
  - type: button
    name: Rebuild
    tap_action:
      action: perform-action
      perform_action: rest_command.rag_rebuild
      data: {}
```

---

## Examples

### Doorbell announcement (notify_gateway to TTS + Telegram)

```yaml
# configuration.yaml
rest_command:
  notify_assistant:
    url: "http://localhost:8766/notify"
    method: POST
    content_type: "application/json"
    payload: >-
      {"message": "{{ message }}",
       "targets": [{"type": "telegram"}, {"type": "tts"}]}
```

```yaml
# automation
- alias: Doorbell
  trigger:
    - platform: state
      entity_id: binary_sensor.doorbell
      to: "on"
  action:
    - service: rest_command.notify_assistant
      data:
        message: "Jemand klingelt an der Tür."
```

### Send a text command from an HA automation

```yaml
# configuration.yaml
rest_command:
  voice_command:
    url: "http://localhost:8765/text"
    method: POST
    content_type: "application/json"
    payload: '{"text": "{{ text }}", "device_id": "ha"}'
```

```yaml
# automation
- alias: Leaving home
  trigger:
    - platform: state
      entity_id: person.paul
      to: "not_home"
  action:
    - service: rest_command.voice_command
      data:
        text: "Turn off all lights and the TV"
```

### Telegram quick-action button (menus.yaml)

Inline buttons bypass the LLM, instant and reliable for frequently used actions.

```yaml
menus:
  - name: "Lights"
    buttons:
      - label: "💡 Living room on"
        action:
          domain: light
          service: turn_on
          entity_id: light.living_room
        response: "Living room light on."
      - label: "🌙 All lights off"
        action:
          domain: light
          service: turn_off
          entity_id: light.all
        response: "All lights off."
```

---

## Troubleshooting

- **bot_token empty**, fill in `telegram.bot_token` and restart.
- **401 from HA**, leave `ha_token` blank to use the supervisor token.
- **No transcription**, check `whisper.url` points at a running server and `language` matches your speech.
