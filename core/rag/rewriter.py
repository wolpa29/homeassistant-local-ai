"""
core.rag.rewriter — pre-RAG query normalization + intent classification.

Runs a small LLM call BEFORE the embedding step to:
  - classify intent (command | smalltalk | clarification)
  - fix typos and STT errors ("lciht" -> "licht")
  - resolve pronouns / follow-ups from recent history ("und wieder aus")
  - preserve the original meaning — never invent entities or actions

Returns a dict {"intent": str, "query": str}. On any error or empty output,
returns {"intent": "command", "query": <original transcript>} so the pipeline
never breaks (commands are the safe default — they go through the normal RAG
+ parser path).
"""
import json
import logging
import re

import requests
import yaml
from pathlib import Path

from core.config import (
    LLM_PREPROCESSOR,
    LLM_PREPROCESSOR_URL,
    LLM_PREPROCESSOR_API_KEY,
    LLM_PREPROCESSOR_MODEL,
    LLM_PREPROCESSOR_TIMEOUT,
    LLM_PREPROCESSOR_TEMPERATURE,
    LMSTUDIO_NO_THINK,
    LANGUAGE,
)
from core.llm import get_history_snapshot, _load_memory, format_history_block_for_llm
from core.strings import t

logger = logging.getLogger(__name__)

_VALID_INTENTS = {"command", "smalltalk"}

_prompts_cache: dict | None = None


def _load_prompts() -> dict:
    global _prompts_cache
    if _prompts_cache is None:
        path = Path(__file__).parent.parent / f"prompts_{LANGUAGE}.yaml"
        if not path.exists():
            path = Path(__file__).parent.parent / "prompts_de.yaml"
        _prompts_cache = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _prompts_cache


def _build_history_block(chat_id: int) -> str:
    """Build a chronologically-ordered history block for the rewriter prompt.

    Uses get_history_snapshot() to get messages in correct interleaved order
    (user, assistant, user, assistant, ...) so the LLM can properly resolve
    pronouns and back-references from the most recent context.
    """
    if chat_id == 0:
        return t("history_empty")

    snapshot = get_history_snapshot(chat_id)
    if not snapshot:
        return t("history_empty")

    return format_history_block_for_llm(snapshot)


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if LLM_PREPROCESSOR_API_KEY:
        h["Authorization"] = f"Bearer {LLM_PREPROCESSOR_API_KEY}"
    return h


def _safe_default(transcript: str) -> dict:
    return {"intent": "command", "query": transcript}


def rewrite_query(transcript: str, chat_id: int = 0) -> dict:
    """Classify intent and normalize the query for RAG.

    Returns {"intent": "command|smalltalk|clarification", "query": str}.
    Falls back to {"intent": "command", "query": <original>} on any error.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return _safe_default(transcript)

    if not LLM_PREPROCESSOR:
        return _safe_default(transcript)

    try:
        prompts = _load_prompts()
        history_block = _build_history_block(chat_id)
        system_prompt = prompts["query_rewriter"].format(
            history=history_block,
            transcript=transcript,
        )
        memory = _load_memory("pre_llm")
        if memory:
            system_prompt += "\n\n" + t("prompt_memory_header") + "\n" + memory
        if LMSTUDIO_NO_THINK and "no_think_suffix" in prompts:
            system_prompt += "\n\n" + prompts["no_think_suffix"]

        endpoint = f"{LLM_PREPROCESSOR_URL}/v1/chat/completions"
        payload = {
            "model": LLM_PREPROCESSOR_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript},
            ],
            "temperature": LLM_PREPROCESSOR_TEMPERATURE,
        }

        response = requests.post(
            endpoint, json=payload, headers=_headers(), timeout=LLM_PREPROCESSOR_TIMEOUT
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"] or ""

        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            logger.warning(f"[Rewriter] Kein JSON in Antwort: {content!r}")
            return _safe_default(transcript)

        parsed = json.loads(match.group())
        intent = (parsed.get("intent") or "").strip().lower()
        query = (parsed.get("query") or "").strip()

        if intent not in _VALID_INTENTS:
            logger.warning(f"[Rewriter] Unbekannter intent {intent!r} — fallback command")
            intent = "command"

        if not query:
            query = transcript

        logger.info(f"[Rewriter] '{transcript}' -> intent={intent} | query='{query}'")
        return {"intent": intent, "query": query}

    except Exception as e:
        logger.error(f"[Rewriter] Fehler ({e}) — fallback command + Originaltranskript")
        return _safe_default(transcript)
