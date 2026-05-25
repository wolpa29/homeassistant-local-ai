"""
core.processor — single source of truth for "transcript in → actions out".

This is the shared brain used by BOTH:
  - services/telegram_bot  (via handlers.py, which wraps the result in Telegram UI)
  - services/voice_gateway (via main.py, which returns the result as JSON)

It contains no framework-specific code: no Telegram, no FastAPI, no I/O besides
the LLM/HA calls already encapsulated in core.llm / core.ha.

The function `process_transcript()` returns a plain dict. Callers decide how to
present it (Markdown message, speaker TTS, JSON, etc.).

History
-------
`chat_id` is the history key. Two callers that pass the same `chat_id` see the
same conversation — this lets a Raspberry Pi and your Telegram bot share
history if you set DEVICE_ID on the RPi to your Telegram chat_id (see
services/voice_gateway/README.md). Different IDs = separate histories.
"""
import logging

from core.config import (
    MAX_ACTIONS_PER_COMMAND,
    FALLBACK_MODE,
    FALLBACK_REST_DOMAINS,
    FALLBACK_REST_MAX_ENTITIES,
    RAG_ENABLED,
    LLM_PREPROCESSOR,
    HISTORY_INCLUDE_ASSISTANT,
    HISTORY_APPEND_EXECUTIONS,
)
from core.llm import (
    parse_command,
    parse_command_with_states,
    parse_command_rag,
    smalltalk_reply,
    _load_entities,
    get_recent_user_messages,
    get_recent_assistant_replies,
    append_execution_summary,
    append_clarification_turn,
    get_history_snapshot,
    get_history_entity_ids,
)
from core.llm_lmstudio import fallback_via_mcp
from core.ha import call_service, get_all_states
from core.strings import t

logger = logging.getLogger(__name__)

# When transcripts are this short, enrich the RAG embed query with history
# context so follow-ups like "und wieder aus" still find the right entity.
_RAG_ENRICH_MAX_WORDS = 5


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------
# process_transcript() returns a dict with this structure:
#
#   {
#     "transcript":       str,            # the input transcript
#     "reply":            str,            # final natural-language reply (may be empty)
#     "actions_executed": [                # actions that were actually sent to HA
#       {"action": "turn_on", "entity_id": "light.kueche", "success": True},
#       ...
#     ],
#     "actions_ignored":  [                # actions dropped by MAX_ACTIONS_PER_COMMAND
#       {"action": "turn_on", "entity_id": "light.max"},
#       ...
#     ],
#     "error":            str | None,     # None on success; see ERROR_CODES below
#     "fallback_used":    str | None,     # "rest" | "mcp" | None
#   }
#
# Error codes (all result in actions_executed == [] and typically a user-visible hint):
#   "parse_failed"           — LLM did not return valid JSON
#   "no_match"               — primary + REST path found nothing and MODE!=2
#   "fallback_no_match"      — needs_fallback triggered, REST fallback found nothing
#   "needs_fallback_no_mode" — needs_fallback triggered but FALLBACK_MODE==0
#   "mcp_failed"             — Mode 2 MCP call failed
# ---------------------------------------------------------------------------


def _resolve_command(transcript: str, embed_query: str, chat_id: int) -> dict | None:
    """Pick the right LLM entry point (RAG or legacy) for the primary path.

    `embed_query` is what gets embedded for RAG retrieval (may be rewritten).
    `transcript` is what the parser LLM sees (original user text).

    RAG errors are caught and logged here, and the function falls back to the
    legacy entities.yaml path so one bad embedding call doesn't kill a command.

    The parser itself decides when to ask back: if it returns a non-empty
    `clarification_question` with no actions, we surface it via the same
    {"_clarify": ...} channel the rest of the pipeline already understands.
    """
    if RAG_ENABLED:
        try:
            from core.rag.index import query as rag_query, lookup_by_ids

            rag_entities = rag_query(embed_query)

            # Augment with entities from recent conversation history so that
            # follow-up commands using pronouns ("es", "er", "das") can still
            # resolve to the correct entity even when the RAG query misses it.
            history_ids = get_history_entity_ids(chat_id)
            if history_ids:
                existing_ids = {e["entity_id"] for e in rag_entities}
                missing = [eid for eid in history_ids if eid not in existing_ids]
                if missing:
                    extra = lookup_by_ids(missing)
                    if extra:
                        logger.info(
                            f"[Processor] Adding {len(extra)} history entity(s) to RAG candidates: "
                            f"{[e['entity_id'] for e in extra]}"
                        )
                        rag_entities = rag_entities + extra

            if rag_entities:
                logger.info(f"[Processor] RAG path | {len(rag_entities)} candidates")
                parsed = parse_command_rag(transcript, rag_entities, chat_id=chat_id, rewriter_query=embed_query)
                if parsed and parsed.get("clarification_question") and not parsed.get("actions"):
                    return {"_clarify": parsed["clarification_question"]}
                return parsed
            logger.warning("[Processor] RAG returned no candidates — legacy path")
        except Exception as e:
            logger.error(f"[Processor] RAG failed: {e} — legacy path")

    parsed = parse_command(transcript, chat_id=chat_id)
    if parsed and parsed.get("clarification_question") and not parsed.get("actions"):
        return {"_clarify": parsed["clarification_question"]}
    return parsed


def _build_embed_query(transcript: str, chat_id: int) -> tuple[str, str]:
    """Return (embed_query, intent). intent is always 'command' when rewriter off."""
    if LLM_PREPROCESSOR:
        from core.rag.rewriter import rewrite_query
        rw = rewrite_query(transcript, chat_id=chat_id)
        return rw.get("query") or transcript, rw.get("intent") or "command"

    embed_query = transcript
    if len(transcript.split()) <= _RAG_ENRICH_MAX_WORDS:
        context: list[str] = [m for m in get_recent_user_messages(chat_id) if m != transcript]
        if HISTORY_INCLUDE_ASSISTANT:
            context.extend(get_recent_assistant_replies(chat_id))
        if context:
            embed_query = " | ".join(context) + " → " + transcript
            logger.info(f"[Processor] RAG embed query enriched: '{embed_query}'")
    return embed_query, "command"


def _new_result(transcript: str) -> dict:
    return {
        "transcript": transcript,
        "reply": "",
        "actions_executed": [],
        "actions_ignored": [],
        "error": None,
        "fallback_used": None,
    }


def process_transcript_split(
    transcript: str, chat_id: int = 0
) -> "tuple[dict, Callable[[], dict] | None]":
    """LLM-Phase und HA-Ausführung getrennt.

    Gibt (partial_result, execute_fn) zurück:
    - partial_result hat `reply` bereits gesetzt (aus dem LLM), aber
      `actions_executed` ist noch leer.
    - execute_fn() führt die HA-Actions aus, füllt `actions_executed` und
      gibt partial_result zurück. Darf in einem Background-Thread aufgerufen
      werden, sobald die Antwort an den Caller gesendet wurde.
    - execute_fn ist None wenn keine Actions ausgeführt werden müssen
      (Smalltalk, Fehler, reine Statusantwort, MCP-Fallback).

    process_transcript() ist ein einfacher Wrapper darüber.
    """
    from typing import Callable  # lokaler Import vermeidet zirkulaere Abhaengigkeiten

    result = _new_result(transcript)
    logger.info(
        f"[Processor] chat={chat_id} | FALLBACK_MODE={FALLBACK_MODE} | "
        f"Transcript: '{transcript}'"
    )

    history_snapshot = get_history_snapshot(chat_id)
    embed_query, intent = _build_embed_query(transcript, chat_id)

    if intent == "smalltalk":
        logger.info(f"[Processor] Intent={intent} — routing to smalltalk LLM")
        chat_reply = smalltalk_reply(transcript, chat_id=chat_id)
        result["reply"] = chat_reply or ""
        if not chat_reply:
            result["error"] = "smalltalk_failed"
        return result, None

    command = _resolve_command(transcript, embed_query, chat_id)
    if not command:
        result["error"] = "parse_failed"
        return result, None

    if command.get("_clarify"):
        result["reply"] = command["_clarify"]
        append_clarification_turn(chat_id, transcript, command["_clarify"])
        return result, None

    reply = command.get("reply", "")
    actions = command.get("actions", [])
    fallback_states: list[dict] = []

    if any(a.get("action") == "needs_fallback" for a in actions):
        if FALLBACK_MODE == 1:
            fallback_states = get_all_states(FALLBACK_REST_DOMAINS or None, FALLBACK_REST_MAX_ENTITIES)
            fb = parse_command_with_states(
                transcript, fallback_states, chat_id=chat_id, prior_history=history_snapshot
            )
            if fb and fb.get("actions") and not any(a.get("action") == "needs_fallback" for a in fb["actions"]):
                command = fb
                reply = command.get("reply", "")
                actions = command.get("actions", [])
                result["fallback_used"] = "rest"
            else:
                result["error"] = "fallback_no_match"
                return result, None
        elif FALLBACK_MODE == 2:
            mcp_reply = fallback_via_mcp(transcript, chat_id=chat_id)
            result["reply"] = mcp_reply or ""
            result["fallback_used"] = "mcp" if mcp_reply else None
            if not mcp_reply:
                result["error"] = "mcp_failed"
            return result, None
        else:
            result["error"] = "needs_fallback_no_mode"
            return result, None

    if not actions:
        if FALLBACK_MODE == 1:
            fallback_states = get_all_states(FALLBACK_REST_DOMAINS or None, FALLBACK_REST_MAX_ENTITIES)
            fb = parse_command_with_states(
                transcript, fallback_states, chat_id=chat_id, prior_history=history_snapshot
            )
            if fb and fb.get("actions"):
                command = fb
                reply = command.get("reply", "")
                actions = command.get("actions", [])
                result["fallback_used"] = "rest"
            else:
                result["reply"] = reply
                result["error"] = "no_match"
                return result, None
        elif FALLBACK_MODE == 2:
            mcp_reply = fallback_via_mcp(transcript, chat_id=chat_id)
            result["reply"] = mcp_reply or ""
            result["fallback_used"] = "mcp" if mcp_reply else None
            if not mcp_reply:
                result["error"] = "mcp_failed"
            return result, None
        else:
            result["reply"] = reply
            if not reply:
                result["error"] = "no_match"
            return result, None

    # Reply aus dem LLM steht fest — ab hier kann der Caller die Antwort
    # bereits senden. Die eigentliche HA-Ausführung erfolgt in execute_fn.
    result["reply"] = reply

    def execute_fn() -> dict:
        for act in actions:
            entity_id = act.get("entity_id")
            action = act.get("action")
            domain = act.get("domain")
            service_data = act.get("service_data") if isinstance(act.get("service_data"), dict) else None

            if act.get("ignored"):
                result["actions_ignored"].append({"action": action, "entity_id": entity_id})
                continue
            if MAX_ACTIONS_PER_COMMAND > 0 and len(result["actions_executed"]) >= MAX_ACTIONS_PER_COMMAND:
                result["actions_ignored"].append({"action": action, "entity_id": entity_id})
                continue

            status = call_service(domain, action, entity_id, service_data)
            entry = {
                "action": action,
                "entity_id": entity_id,
                "success": status == "ok",
                "status": status,
            }
            if service_data:
                entry["service_data"] = service_data
            result["actions_executed"].append(entry)

        if HISTORY_APPEND_EXECUTIONS:
            parts = []
            for a in result["actions_executed"]:
                line = f"{a.get('action')} -> {a.get('entity_id')}"
                if isinstance(a.get("service_data"), dict) and a["service_data"]:
                    line += f" {a['service_data']}"
                s = a.get("status", "ok")
                if s == "ok":
                    line += " " + t("exec_status_ok")
                elif s == "timeout":
                    line += " " + t("exec_status_timeout")
                else:
                    line += " " + t("exec_status_error")
                parts.append(line)
            if parts:
                append_execution_summary(
                    chat_id,
                    t("exec_summary_marker") + " " + ", ".join(parts),
                    turn_id=command.get("_history_turn_id"),
                )

        return result

    return result, execute_fn


def process_transcript(transcript: str, chat_id: int = 0) -> dict:
    """Vollständiger synchroner Ablauf — LLM + HA-Ausführung in einem Aufruf.

    Für Caller die nicht splitten wollen (Tests, Legacy-Code).
    Voice-Gateway und Telegram nutzen process_transcript_split() direkt.
    """
    result, execute_fn = process_transcript_split(transcript, chat_id)
    if execute_fn:
        execute_fn()
    return result
