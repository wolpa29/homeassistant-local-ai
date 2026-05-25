import requests
import yaml
import json
import re
import logging
import threading
import uuid
from pathlib import Path
from core.config import (
    LMSTUDIO_URL, LMSTUDIO_MODEL, LMSTUDIO_TIMEOUT, LMSTUDIO_API_KEY,
    LMSTUDIO_TEMPERATURE, LMSTUDIO_NO_THINK,
    LLM_HISTORY_SIZE, MAX_ACTIONS_PER_COMMAND,
    HISTORY_INCLUDE_ASSISTANT,
    USERCONFIG_DIR,
    LANGUAGE,
)
from core.strings import t

logger = logging.getLogger(__name__)

# Gesprächsverlauf pro chat_id — nur aktiv wenn LLM_HISTORY_SIZE > 0
_history: dict[int, list] = {}
_history_lock = threading.RLock()

_STANDARD_DOMAIN_ACTIONS: dict[str, set[str]] = {
    "climate": {"set_temperature", "set_hvac_mode"},
    "cover": {"set_cover_position"},
    "fan": {"set_percentage"},
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "automation": {"trigger"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "script": {"turn_on"},
    "scene": {"turn_on"},
    "button": {"turn_on"},
    "media_player": {"turn_on", "turn_off"},
    "lock": {"turn_on", "turn_off"},
    "group": {"turn_on", "turn_off", "toggle"},
}


def _extract_json(text: str) -> dict | None:
    """JSON aus dem LLM-Output extrahieren.

    Versucht zuerst normales Matching. Falls die schließende } fehlt (Modell-Bug,
    finish_reason=stop aber unvollständiges JSON), werden fehlende } aufgefüllt.
    """
    # Fix 2: Markdown-Code-Fences entfernen (```json ... ``` oder ``` ... ```)
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```', '', text).strip()

    # Normalfall: vollständiges {…}
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Repair: { ohne schließendes } — fehlende Klammern auffüllen
    start = text.find('{')
    if start == -1:
        return None
    fragment = text[start:]
    depth = 0
    for ch in fragment:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
    if depth > 0:
        repaired = fragment + '}' * depth
        try:
            result = json.loads(repaired)
            logger.warning(f"[LLM] JSON repariert ({depth} fehlende '}}') — Modell hat JSON nicht abgeschlossen")
            return result
        except json.JSONDecodeError:
            pass
    return None


def _looks_like_execution_summary(text: str) -> bool:
    """Return true when model output imitates our internal execution receipt."""
    marker = re.escape(t("exec_summary_marker"))
    ok = re.escape(t("exec_status_ok"))
    error = re.escape(t("exec_status_error"))
    timeout = re.escape(t("exec_status_timeout"))
    return bool(
        re.search(rf"(^|\n)\s*{marker}\s+", text, flags=re.IGNORECASE)
        or re.search(rf"({ok}|{error}|{timeout})", text)
    )


def _execution_context_from_summary(line: str) -> str | None:
    """Convert an internal execution summary into LLM-safe context text."""
    marker = t("exec_summary_marker")
    stripped = (line or "").strip()
    if not stripped.lower().startswith(marker.lower()):
        return None

    status_map = {
        t("exec_status_ok"): t("exec_context_success"),
        t("exec_status_error"): t("exec_context_error"),
        t("exec_status_timeout"): t("exec_context_timeout"),
    }
    status_pattern = "|".join(re.escape(status) for status in status_map)
    body = stripped[len(marker):].strip()
    items: list[str] = []

    for match in re.finditer(rf"\s*(?P<body>.*?)(?P<status>{status_pattern})(?:,\s*|$)", body):
        action_body = match.group("body").strip().rstrip(",").strip()
        status = status_map.get(match.group("status"), match.group("status"))
        if " -> " not in action_body:
            continue
        action, entity_part = action_body.split(" -> ", 1)
        entity_id = entity_part.strip().split(" ", 1)[0]
        if not action or not entity_id:
            continue
        items.append(t("exec_context_item", status=status, action=action.strip(), entity_id=entity_id))

    if not items:
        return None
    return t("exec_context_prefix") + " " + "; ".join(items)


def _sanitize_execution_summaries(text: str, include_execution_summaries: bool = True) -> str:
    """Replace internal execution receipts with compact LLM-safe context lines."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        context = _execution_context_from_summary(line)
        if context:
            if include_execution_summaries:
                lines.append(context)
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _lmstudio_headers() -> dict:
    """Standard-Header fuer LM Studio /v1-Calls. Fuegt Bearer-Token an,
    wenn LM Studio Server-Auth aktiv ist (sobald MCP genutzt wird Pflicht)."""
    h = {"Content-Type": "application/json"}
    if LMSTUDIO_API_KEY:
        h["Authorization"] = f"Bearer {LMSTUDIO_API_KEY}"
    return h


def _new_turn_id() -> str:
    return uuid.uuid4().hex


def _call_llm_json(
    label: str,
    system_prompt: str,
    history: list,
    user_content: str,
    *,
    allow_plain_text_reply: bool = False,
) -> dict | None:
    endpoint = f"{LMSTUDIO_URL}/v1/chat/completions"
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            *_clean_history_for_llm(history),
            {"role": "user", "content": user_content},
        ],
        "temperature": LMSTUDIO_TEMPERATURE,
    }
    response = requests.post(endpoint, json=payload, headers=_lmstudio_headers(), timeout=LMSTUDIO_TIMEOUT)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    logger.info(f"[{label}] Antwort raw: {content}")

    result = _extract_json(content)
    if result is not None:
        return result

    stripped = (content or "").strip()
    if not stripped:
        logger.error(f"[{label}] Kein JSON und leere Antwort")
        return None

    logger.warning(f"[{label}] Kein JSON — starte strikten JSON-Retry")
    retry_system = system_prompt + "\n\n" + _build_prompt("json_retry")
    retry_payload = {
        "model": LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": retry_system},
            *_clean_history_for_llm(history, include_execution_summaries=False),
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
    }
    retry_response = requests.post(
        endpoint, json=retry_payload, headers=_lmstudio_headers(), timeout=LMSTUDIO_TIMEOUT
    )
    retry_response.raise_for_status()
    retry_content = retry_response.json()["choices"][0]["message"]["content"]
    logger.info(f"[{label}] Antwort raw retry: {retry_content}")

    result = _extract_json(retry_content)
    if result is not None:
        return result

    if _looks_like_execution_summary(stripped) or _looks_like_execution_summary(retry_content):
        logger.error(f"[{label}] Kein JSON; Antwort imitiert Ausfuehrungsstatus — verwerfe")
        return None

    if allow_plain_text_reply:
        logger.warning(f"[{label}] Kein JSON nach Retry — verwende plain text als reply: '{stripped}'")
        return {"reply": stripped, "actions": [], "clarification_question": ""}

    logger.error(f"[{label}] Kein JSON nach Retry")
    return None


def _entity_actions_map(entities: list[dict], id_key: str = "id") -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in entities:
        eid = e.get(id_key)
        if not eid:
            continue
        out[eid] = set(e.get("actions") or [])
    return out


def _validate_actions(
    result: dict,
    valid_ids: set[str],
    allowed_actions: dict[str, set[str]],
    label: str,
    *,
    allow_get_state: bool = False,
    allow_domain_defaults: bool = False,
) -> list[dict]:
    validated: list[dict] = []
    for act in result.get("actions", []):
        action = act.get("action")
        eid = act.get("entity_id")
        if action == "needs_fallback":
            validated.append(act)
            logger.info(f"[{label}] needs_fallback fuer '{eid or '?'}'")
            continue
        if action == "get_state" and not allow_get_state:
            logger.warning(f"[{label}] get_state ist kein HA-Service — Action ignoriert")
            continue
        if not eid:
            continue
        if eid not in valid_ids:
            logger.warning(f"[{label}] Halluzinierte Entity '{eid}' - ignoriert")
            continue
        if not act.get("domain") and "." in eid:
            act["domain"] = eid.split(".", 1)[0]
        domain = act.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")
        allowed = set(allowed_actions.get(eid) or [])
        if allow_domain_defaults:
            allowed.update(_STANDARD_DOMAIN_ACTIONS.get(domain, set()))
        if action not in allowed:
            logger.warning(
                f"[{label}] Nicht erlaubte Action '{action}' fuer '{eid}' "
                f"(erlaubt: {sorted(allowed) or '-'}) - ignoriert"
            )
            continue
        sd = act.get("service_data")
        if sd is not None and not isinstance(sd, dict):
            logger.warning(f"[{label}] service_data fuer '{eid}' kein Dict - verwerfe")
            act.pop("service_data", None)
        validated.append(act)
    return validated


def _apply_action_limit(actions: list[dict], label: str) -> list[dict]:
    if MAX_ACTIONS_PER_COMMAND > 0 and len(actions) > MAX_ACTIONS_PER_COMMAND:
        logger.warning(
            f"[{label}] Zu viele Aktionen ({len(actions)}), "
            f"begrenze auf {MAX_ACTIONS_PER_COMMAND}"
        )
        for i in range(MAX_ACTIONS_PER_COMMAND, len(actions)):
            actions[i]["ignored"] = True
    return actions


def _store_history_turn(chat_id: int, transcript: str, result: dict, assistant_content: str) -> str:
    turn_id = _new_turn_id()
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0:
        return turn_id
    with _history_lock:
        history = list(_history.get(chat_id, []))
        history.append({"role": "user", "content": transcript, "turn_id": turn_id})
        if HISTORY_INCLUDE_ASSISTANT:
            history.append({"role": "assistant", "content": assistant_content, "turn_id": turn_id})
        max_entries = LLM_HISTORY_SIZE * (2 if HISTORY_INCLUDE_ASSISTANT else 1)
        if len(history) > max_entries:
            history = history[-max_entries:]
        _history[chat_id] = history
    result["_history_turn_id"] = turn_id
    logger.info(f"[LLM] History fuer chat {chat_id}: {len(_history.get(chat_id, []))} Eintraege gespeichert")
    return turn_id


def _load_entities() -> list:
    path = USERCONFIG_DIR / "entities.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return (data or {}).get("entities") or []


_prompts_cache: dict | None = None
_memory_cache: dict[str, str] = {}


def _load_prompts() -> dict:
    global _prompts_cache
    if _prompts_cache is None:
        path = Path(__file__).parent / f"prompts_{LANGUAGE}.yaml"
        if not path.exists():
            path = Path(__file__).parent / "prompts_de.yaml"
        _prompts_cache = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _prompts_cache


def _load_memory(name: str) -> str:
    """Liest core/userconfig/<name>_memory.md (z.B. 'pre_llm', 'post_llm') als String.

    Liefert "" wenn Datei fehlt oder nur Kommentare/Whitespace enthaelt.
    HTML-Kommentare <!-- ... --> werden entfernt, damit reine Vorlagen-Files
    nichts an den Prompt anhaengen. Cached nach erstem Lesen.
    """
    if name in _memory_cache:
        return _memory_cache[name]
    path = USERCONFIG_DIR / f"{name}_memory.md"
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        cleaned = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()
    else:
        cleaned = ""
    _memory_cache[name] = cleaned
    return cleaned


def _build_prompt(key: str, memory: str | None = None, **kwargs) -> str:
    prompts = _load_prompts()
    text = prompts[key].format(**kwargs)
    if memory:
        mem = _load_memory(memory)
        if mem:
            text += "\n\n" + t("prompt_memory_header") + "\n" + mem
    if LMSTUDIO_NO_THINK:
        text += "\n\n" + prompts["no_think_suffix"]
    return text


def _format_curated_entity_with_state(e: dict, ha_state: dict | None) -> str:
    """Compact entity line for the primary-parser prompt with live state.

    Skips the `state:` field for pure trigger domains (script, scene, button)
    where the value is meaningless to the user, keeping the prompt compact.
    """
    eid = e["id"]
    domain = e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")
    keywords = ", ".join(e.get("keywords", []))
    actions = ", ".join(e.get("actions", [])) or "-"
    description = (e.get("description") or "").strip()
    meta = (e.get("meta") or "").strip()

    parts: list[str] = [f"- {eid}"]
    if description:
        parts.append(f"name: {description}")
    if keywords:
        parts.append(f"keywords: {keywords}")

    if domain in _STATEFUL_DOMAINS and ha_state is not None:
        attrs = ha_state.get("attributes", {}) or {}
        unit = attrs.get("unit_of_measurement") or ""
        human = _humanize_state(domain, ha_state.get("state"))
        if unit and human not in _HUMAN_STATE_WORDS:
            state_str = f"{human} {unit}".strip()
        else:
            state_str = human

        relevant = _RELEVANT_ATTRS_BY_DOMAIN.get(domain, ())
        attr_parts = [f"{k}={v}" for k in relevant if (v := attrs.get(k)) not in (None, "")]
        attrs_str = (" | " + ", ".join(attr_parts)) if attr_parts else ""
        parts.append(f"state: {state_str}{attrs_str}")
    elif domain in _STATEFUL_DOMAINS:
        parts.append(f"state: {t('state_unknown')}")

    parts.append(f"actions: {actions}")
    if meta:
        parts.append(f"note: {meta}")
    return " | ".join(parts)


def parse_command(transcript: str, chat_id: int = 0) -> dict | None:
    entities = _load_entities()
    valid_ids = {e["id"] for e in entities}
    allowed_actions = _entity_actions_map(entities, "id")

    # Live states parallel for all curated entities so the parser can answer
    # status questions directly (no get_state -> needs_fallback round-trip).
    # Stateless domains (script/scene/button) are still in the list, just
    # without a `state:` field.
    from core.ha import get_states_bulk
    stateful_ids = [
        e["id"] for e in entities
        if (e.get("domain") or (e["id"].split(".", 1)[0] if "." in e["id"] else "")) in _STATEFUL_DOMAINS
    ]
    states_map: dict[str, dict] = get_states_bulk(stateful_ids) if stateful_ids else {}
    if stateful_ids:
        logger.info(f"[LLM] States geholt: {len(states_map)}/{len(stateful_ids)}")

    entity_list = "\n".join(
        _format_curated_entity_with_state(e, states_map.get(e["id"])) for e in entities
    )

    system_prompt = _build_prompt("primary_parser", memory="post_llm", entity_list=entity_list)

    logger.info(
        f"[LLM] Transcript: '{transcript}' | Modell: {LMSTUDIO_MODEL} | "
        f"Server: {LMSTUDIO_URL} | History: {LLM_HISTORY_SIZE} | "
        f"MaxActions: {MAX_ACTIONS_PER_COMMAND}"
    )

    # History aufbauen (nur wenn aktiviert)
    history = []
    if LLM_HISTORY_SIZE > 0 and chat_id != 0:
        with _history_lock:
            history = list(_history.get(chat_id, []))

    try:
        result = _call_llm_json("LLM", system_prompt, history, transcript)
        if result is None:
            return None
        logger.info(f"[LLM] Parsed: {result}")

        clarification_q = (result.get("clarification_question") or "").strip()
        if clarification_q:
            logger.info(f"[LLM] Clarification vom Parser: '{clarification_q}'")
        result["clarification_question"] = clarification_q

        result["actions"] = _apply_action_limit(
            _validate_actions(result, valid_ids, allowed_actions, "LLM"),
            "LLM",
        )

        # History speichern (nur wenn aktiviert).
        # Assistant-Turn nur speichern wenn HISTORY_INCLUDE_ASSISTANT=true.
        if LLM_HISTORY_SIZE > 0 and chat_id != 0:
            _store_history_turn(chat_id, transcript, result, json.dumps(result, ensure_ascii=False))

        return result

    except requests.exceptions.HTTPError as e:
        logger.error(f"[LLM] HTTP-Fehler: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"[LLM] Fehler beim Parsen des JSONs: {e}")
        return None
    except Exception as e:
        logger.error(f"[LLM] Allgemeiner Fehler: {e}")
        return None


def get_history_snapshot(chat_id: int) -> list:
    """Return a shallow copy of the stored history before the current turn is added."""
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0:
        return []
    with _history_lock:
        return [dict(m) for m in _history.get(chat_id, [])]


def get_recent_user_messages(chat_id: int) -> list[str]:
    """Return all stored user messages from history for this chat (oldest first)."""
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0:
        return []
    with _history_lock:
        return [m["content"] for m in _history.get(chat_id, []) if m["role"] == "user"]


def _clean_history_for_llm(history: list, include_execution_summaries: bool = True) -> list:
    """Return history with assistant turns converted to plain German text.

    Raw LLM JSON in assistant turns confuses small models — they start echoing
    'reply:' and 'action:' literally in their own reply text. Extracting just
    the natural-language reply (and any appended execution summary) fixes this.
    """
    cleaned = []
    for m in history:
        if m["role"] != "assistant":
            cleaned.append(m)
            continue
        content = m["content"]
        reply_text = ""
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                reply_text = (json.loads(match.group()).get("reply") or "").strip()
            except (json.JSONDecodeError, AttributeError):
                pass
            trailing = content[match.end():].strip()
        else:
            trailing = content.strip()
        if not reply_text:
            reply_text = trailing
            trailing = ""
        if trailing:
            trailing = _sanitize_execution_summaries(trailing, include_execution_summaries)
        text = reply_text + ("\n" + trailing if trailing else "")
        cleaned.append({**m, "content": text or content})
    return cleaned


def format_history_block_for_llm(history: list) -> str:
    """Return the same sanitized history view as parser messages, as a text block."""
    cleaned = _clean_history_for_llm(history)
    if not cleaned:
        return t("history_empty")
    lines: list[str] = []
    user_label = t("history_user_label")
    assistant_label = t("history_assistant_label")
    for m in cleaned:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"{user_label} {content}")
        elif role == "assistant" and HISTORY_INCLUDE_ASSISTANT:
            lines.append(f"{assistant_label} {content}")
    return "\n".join(lines) if lines else t("history_empty")


def get_history_entity_ids(chat_id: int) -> list[str]:
    """Return entity_ids that appeared in recent assistant actions (oldest first).

    Used to augment RAG candidates so follow-up commands that use pronouns
    ('es', 'er', 'das') can still resolve to the correct entity even when the
    rewriter query misses it.
    """
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0:
        return []
    seen: list[str] = []
    with _history_lock:
        history = list(_history.get(chat_id, []))
    for m in history:
        if m["role"] != "assistant":
            continue
        match = re.search(r'\{.*\}', m["content"], re.DOTALL)
        if not match:
            continue
        try:
            for act in json.loads(match.group()).get("actions", []):
                eid = act.get("entity_id")
                if eid and eid not in seen:
                    seen.append(eid)
        except (json.JSONDecodeError, AttributeError):
            pass
    return seen


def get_recent_assistant_replies(chat_id: int) -> list[str]:
    """Return stored assistant context (oldest first).

    Assistant turns are raw JSON in history. This pulls out the 'reply' text
    plus anything appended after the JSON (e.g. execution summaries written by
    append_execution_summary() when HISTORY_APPEND_EXECUTIONS is active).
    """
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0:
        return []
    out: list[str] = []
    with _history_lock:
        history = list(_history.get(chat_id, []))
    for m in history:
        if m["role"] != "assistant":
            continue
        content = m["content"]
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                reply = (parsed.get("reply") or "").strip()
                if reply:
                    out.append(reply)
            except json.JSONDecodeError:
                pass
            trailing = content[match.end():].strip()
            if trailing:
                safe_trailing = _sanitize_execution_summaries(trailing)
                if safe_trailing:
                    out.append(safe_trailing)
        else:
            stripped = content.strip()
            if stripped:
                out.append(stripped)
    return out


def append_execution_summary(chat_id: int, summary: str, turn_id: str | None = None) -> None:
    """Append an execution-summary line to the most recent assistant entry in history.

    Called from handlers.py after actions run. No-op if history is disabled,
    the summary is empty, or there is no assistant turn to attach to.
    """
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0 or not summary:
        return
    with _history_lock:
        history = _history.get(chat_id)
        if not history:
            return
        target = None
        if turn_id:
            for msg in reversed(history):
                if msg.get("role") == "assistant" and msg.get("turn_id") == turn_id:
                    target = msg
                    break
        if target is None:
            for msg in reversed(history):
                if msg["role"] == "assistant":
                    target = msg
                    break
        if target is not None:
            target["content"] = target["content"].rstrip() + "\n" + summary


def append_clarification_turn(chat_id: int, transcript: str, question: str) -> None:
    """Persist a clarification round-trip in history so the follow-up turn keeps
    the original action intent (e.g. "Licht abschalten" → "Welches?" → "Bad UG").

    Without this, the ambiguous transcript and the clarification question are
    dropped, and the next parser call only sees the entity-naming reply.
    """
    if LLM_HISTORY_SIZE <= 0 or chat_id == 0 or not transcript:
        return
    turn_id = _new_turn_id()
    with _history_lock:
        history = list(_history.get(chat_id, []))
        history.append({"role": "user", "content": transcript, "turn_id": turn_id})
        if HISTORY_INCLUDE_ASSISTANT and question:
            history.append({"role": "assistant", "content": question, "turn_id": turn_id})
        max_entries = LLM_HISTORY_SIZE * (2 if HISTORY_INCLUDE_ASSISTANT else 1)
        if len(history) > max_entries:
            history = history[-max_entries:]
        _history[chat_id] = history
    logger.info(f"[LLM] Clarification-Turn fuer chat {chat_id} gespeichert ({len(history)} Eintraege)")


def smalltalk_reply(transcript: str, chat_id: int = 0) -> str | None:
    """Free-form chat reply for non-command intents (smalltalk / clarification).

    Uses the same LLM as the parser but a dedicated 'smalltalk' system prompt
    that produces casual German prose (no JSON, no actions). History is included
    so follow-ups feel coherent. Returns None on error so the caller can fall
    back gracefully.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return None

    system_prompt = _build_prompt("smalltalk")

    history = []
    if LLM_HISTORY_SIZE > 0 and chat_id != 0:
        with _history_lock:
            history = list(_history.get(chat_id, []))

    logger.info(
        f"[LLM Smalltalk] Transcript: '{transcript}' | Modell: {LMSTUDIO_MODEL} | "
        f"History: {len(history) // 2}"
    )

    try:
        endpoint = f"{LMSTUDIO_URL}/v1/chat/completions"
        payload = {
            "model": LMSTUDIO_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                *_clean_history_for_llm(history),
                {"role": "user", "content": transcript},
            ],
            "temperature": LMSTUDIO_TEMPERATURE,
        }
        response = requests.post(
            endpoint, json=payload, headers=_lmstudio_headers(), timeout=LMSTUDIO_TIMEOUT
        )
        response.raise_for_status()
        content = (response.json()["choices"][0]["message"]["content"] or "").strip()

        # Strip stray quotes / leading "Assistent:" labels some models add.
        content = content.strip('"').strip("'")
        content = re.sub(r"^(Assistent|Assistant)\s*:\s*", "", content, flags=re.IGNORECASE)

        if not content:
            logger.warning("[LLM Smalltalk] Leere Antwort")
            return None

        logger.info(f"[LLM Smalltalk] Antwort: {content}")

        if LLM_HISTORY_SIZE > 0 and chat_id != 0:
            turn_id = _new_turn_id()
            with _history_lock:
                history = list(_history.get(chat_id, []))
                history.append({"role": "user", "content": transcript, "turn_id": turn_id})
                if HISTORY_INCLUDE_ASSISTANT:
                    history.append({"role": "assistant", "content": content, "turn_id": turn_id})
                max_entries = LLM_HISTORY_SIZE * (2 if HISTORY_INCLUDE_ASSISTANT else 1)
                if len(history) > max_entries:
                    history = history[-max_entries:]
                _history[chat_id] = history

        return content

    except Exception as e:
        logger.error(f"[LLM Smalltalk] Fehler: {e}")
        return None


_RELEVANT_ATTRS_BY_DOMAIN: dict[str, tuple[str, ...]] = {
    "climate": (
        "current_temperature", "temperature", "hvac_mode", "hvac_modes",
        "hvac_action", "preset_mode", "preset_modes",
        "humidity", "current_humidity", "fan_mode",
    ),
    "cover":   ("current_position", "current_tilt_position"),
    "light":   ("brightness", "color_temp", "color_mode", "rgb_color"),
    "fan":     ("percentage", "preset_mode"),
    "media_player": ("media_title", "media_artist", "volume_level", "source"),
    "weather": ("temperature", "humidity", "wind_speed"),
}

# Domains where the live `state` value carries useful information for the LLM
# (status queries, conditional logic). For pure trigger domains like script /
# scene / button the state is meaningless to the user and we omit it from the
# prompt to keep the entity list compact.
_STATEFUL_DOMAINS: frozenset[str] = frozenset({
    "light", "switch", "sensor", "binary_sensor", "climate", "cover", "fan",
    "lock", "media_player", "automation", "input_boolean", "input_number",
    "input_select", "group", "weather", "person", "device_tracker", "sun",
})


_HUMAN_STATE_WORDS: frozenset[str] = frozenset({
    t("state_on"), t("state_off"),
    t("state_binary_on"), t("state_binary_off"),
    t("state_unknown"),
})


def _humanize_state(domain: str, state: str | None) -> str:
    if state is None:
        return t("state_unknown")
    if domain in ("light", "switch", "automation", "input_boolean", "fan", "media_player"):
        if state == "on":
            return t("state_on")
        if state == "off":
            return t("state_off")
    if domain == "binary_sensor":
        if state == "on":
            return t("state_binary_on")
        if state == "off":
            return t("state_binary_off")
    return str(state)


def _format_entity_with_state(e: dict, ha_state: dict | None) -> str:
    """Eine Entity-Zeile fuer den RAG-Prompt — mit aktuellem state und relevanten Attributen.

    e: Kandidat aus dem RAG-Index ({entity_id, friendly_name, domain, actions, meta}).
    ha_state: HA-Live-Antwort (state + attributes) oder None falls Abruf fehlschlug.
    """
    eid = e["entity_id"]
    domain = e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")
    name = e.get("friendly_name") or "-"

    if ha_state is None:
        state_str = "unbekannt"
        attrs_str = ""
    else:
        raw_state = ha_state.get("state")
        attrs = ha_state.get("attributes", {}) or {}
        unit = attrs.get("unit_of_measurement") or ""
        human = _humanize_state(domain, raw_state)
        state_str = f"{human} {unit}".strip() if unit and human not in _HUMAN_STATE_WORDS else human

        relevant = _RELEVANT_ATTRS_BY_DOMAIN.get(domain, ())
        parts = []
        for k in relevant:
            v = attrs.get(k)
            if v is None or v == "":
                continue
            parts.append(f"{k}={v}")
        attrs_str = (" | " + ", ".join(parts)) if parts else ""

    line = (
        f'- {eid} | name: {name} | state: {state_str}{attrs_str} | '
        f'actions: {", ".join(e.get("actions", [])) or "-"}'
    )
    if e.get("meta"):
        line += f' | note: {e["meta"]}'
    return line


def parse_command_rag(transcript: str, entities: list[dict], chat_id: int = 0, rewriter_query: str = "") -> dict | None:
    """RAG path: entity list mit aktuellen States, Attributen und expliziten actions.

    Expected shape of each dict in `entities`:
        {entity_id, friendly_name, domain, actions: list[str], meta: str}

    Vor dem LLM-Call werden States aller Kandidaten parallel aus HA geholt, sodass
    das LLM Statusabfragen direkt beantworten und Bedingungen/Berechnungen
    eigenstaendig durchfuehren kann (keine zweite get_state-Runde noetig).
    """
    if not entities:
        logger.warning("[LLM RAG] Keine RAG-Entities uebergeben")
        return None

    valid_ids = {e["entity_id"] for e in entities}
    allowed_actions = _entity_actions_map(entities, "entity_id")

    # States parallel fuer alle Kandidaten holen — verspaetete Imports gegen Zyklen.
    from core.ha import get_states_bulk
    states_map = get_states_bulk(list(valid_ids))
    logger.info(f"[LLM RAG] States geholt: {len(states_map)}/{len(valid_ids)}")

    entity_list = "\n".join(_format_entity_with_state(e, states_map.get(e["entity_id"])) for e in entities)

    system_prompt = _build_prompt("rag_parser", memory="post_llm", entity_list=entity_list)

    # History aufbauen (gleiche Logik wie parse_command)
    history = []
    if LLM_HISTORY_SIZE > 0 and chat_id != 0:
        with _history_lock:
            history = list(_history.get(chat_id, []))

    logger.info(
        f"[LLM RAG] Transcript: '{transcript}' | Entities: {len(entities)} | "
        f"Modell: {LMSTUDIO_MODEL} | History: {len(history) // 2}"
    )

    user_content = rewriter_query if rewriter_query else transcript

    try:
        result = _call_llm_json(
            "LLM RAG",
            system_prompt,
            history,
            user_content,
            allow_plain_text_reply=True,
        )
        if result is None:
            return None

        logger.info(f"[LLM RAG] Parsed: {result}")

        clarification_q = (result.get("clarification_question") or "").strip()
        if clarification_q:
            logger.info(f"[LLM RAG] Clarification vom Parser: '{clarification_q}'")
        result["clarification_question"] = clarification_q

        result["actions"] = _apply_action_limit(
            _validate_actions(result, valid_ids, allowed_actions, "LLM RAG"),
            "LLM RAG",
        )

        # History speichern (gleiche Logik wie parse_command).
        # Assistant-Turn nur speichern wenn HISTORY_INCLUDE_ASSISTANT=true.
        # Wir schreiben den validierten JSON (ohne halluzinierte Entities) in die
        # History, nicht den rohen content — so tauchen abgelehnte entity_ids
        # nicht in get_history_entity_ids() auf.
        if LLM_HISTORY_SIZE > 0 and chat_id != 0:
            _store_history_turn(chat_id, transcript, result, json.dumps(result, ensure_ascii=False))

        return result

    except requests.exceptions.HTTPError as e:
        logger.error(f"[LLM RAG] HTTP-Fehler: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"[LLM RAG] JSON-Parsing fehlgeschlagen: {e}")
        return None
    except Exception as e:
        logger.error(f"[LLM RAG] Fehler: {e}")
        return None


def parse_command_with_states(transcript: str, states: list[dict], chat_id: int = 0, prior_history: list | None = None) -> dict | None:
    """REST-Fallback (Mode 1): nutzt Live-Entities aus HA statt entities.yaml.

    Gleiches JSON-Output-Format wie parse_command(). entity_list wird aus den
    uebergebenen Live-States gebaut; Validierung gegen deren entity_ids.
    Wenn ein Treffer gefunden wird, ist das Ergebnis im handlers.py genauso
    ausfuehrbar wie das Ergebnis von parse_command()
    """
    if not states:
        logger.warning("[LLM Fallback REST] Keine Live-States uebergeben")
        return None

    valid_ids = {s["entity_id"] for s in states}
    allowed_actions = {
        s["entity_id"]: set(_STANDARD_DOMAIN_ACTIONS.get(s.get("domain", ""), set()))
        for s in states
    }

    entity_list = "\n".join(
        f'- {s["entity_id"]} | name: {s.get("friendly_name") or "-"} | '
        f'domain: {s["domain"]} | state: {s.get("state") or "-"}'
        for s in states
    )

    system_prompt = _build_prompt("fallback_rest", memory="post_llm", entity_list=entity_list)

    history = prior_history or []

    logger.info(
        f"[LLM Fallback REST] Transcript: '{transcript}' | Entities: {len(states)} | "
        f"Modell: {LMSTUDIO_MODEL} | History: {len(history) // 2}"
    )

    try:
        result = _call_llm_json("LLM Fallback REST", system_prompt, history, transcript)
        if result is None:
            return None

        result["actions"] = _apply_action_limit(
            _validate_actions(
                result,
                valid_ids,
                allowed_actions,
                "LLM Fallback REST",
                allow_domain_defaults=True,
            ),
            "LLM Fallback REST",
        )
        return result

    except requests.exceptions.HTTPError as e:
        logger.error(f"[LLM Fallback REST] HTTP-Fehler: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"[LLM Fallback REST] JSON-Parsing fehlgeschlagen: {e}")
        return None
    except Exception as e:
        logger.error(f"[LLM Fallback REST] Fehler: {e}")
        return None
