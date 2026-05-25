"""
Telegram handlers — the Telegram-specific thin layer on top of core.processor.

All command logic (LLM, RAG, HA calls, fallbacks, history) lives in
core.processor. This module only:
  - downloads Telegram voice files,
  - sends intermediate status messages ("🎙️ Transkribiere..."),
  - formats the processor's result dict as Markdown for Telegram.

If you need to change *what* a command does, edit core/processor.py.
If you need to change *how it looks in Telegram*, edit _format_reply() below.
"""
import asyncio
import logging

from core.voice import ensure_voice_dir, transcribe_audio
from core.config import VOICE_REPLY_WITH_TRANSCRIPT, MAX_ACTIONS_PER_COMMAND, RAG_ENABLED
from core.processor import process_transcript_split
from core.strings import t

logger = logging.getLogger(__name__)


def _error_message(code: str) -> str:
    """Map a processor error code to a localized user-facing string."""
    mapping = {
        "parse_failed":           "error_parse_failed",
        "fallback_no_match":      "error_fallback_no_match",
        "needs_fallback_no_mode": "error_needs_fallback_no_mode",
        "no_match":               "error_no_match",
        "mcp_failed":             "error_mcp_failed",
    }
    key = mapping.get(code)
    return t(key) if key else ""


def _escape_markdown_text(text: str) -> str:
    """Escape user/model controlled text for Telegram legacy Markdown."""
    for ch in "\\`*_[":
        text = text.replace(ch, "\\" + ch)
    return text


def _format_reply(result: dict) -> str:
    """Turn a core.processor result dict into a Markdown Telegram message."""
    error = result.get("error")
    reply = result.get("reply", "")
    executed = result.get("actions_executed", [])
    ignored = result.get("actions_ignored", [])

    # Special case: "no_match" with a reply from the LLM — show the reply
    # (preserves the original "💬 {reply}" behaviour for chatty LLM responses).
    if error == "no_match" and reply:
        return t("reply_chat_prefix", reply=reply)

    err_msg = _error_message(error) if error else ""
    if err_msg:
        return err_msg

    parts: list[str] = []
    if reply:
        parts.append(_escape_markdown_text(reply))

    if executed:
        parts.append("")  # blank line between reply and action list
        for a in executed:
            status = a.get("status", "ok")
            icon = "✅" if status == "ok" else ("⏱️❌" if status == "timeout" else "❌")
            parts.append(f"{icon} `{a['action']}` -> `{a['entity_id']}`")

    if ignored:
        parts.append("")
        parts.append(t("ignored_by_limit", limit=MAX_ACTIONS_PER_COMMAND))
        for a in ignored:
            parts.append(f"❌ `{a['action']}` -> `{a['entity_id']}`")

    text = "\n".join(parts).strip()
    return text or t("error_default")


async def _dispatch(update, context, transcript: str, status_msg=None):
    """LLM reply → edit status_msg immediately; HA actions run in executor, then edit again."""
    loop = asyncio.get_running_loop()
    chat_id = update.effective_chat.id

    partial, execute_fn = await loop.run_in_executor(
        None, lambda: process_transcript_split(transcript, chat_id=chat_id)
    )

    early_text = partial.get("reply") or _error_message(partial.get("error", "")) or t("error_default")

    if status_msg:
        try:
            sent = await status_msg.edit_text(early_text)
        except Exception:
            sent = await status_msg.edit_text(early_text)
    else:
        try:
            sent = await update.message.reply_text(early_text)
        except Exception:
            sent = await update.message.reply_text(early_text)

    if execute_fn is None:
        return

    await loop.run_in_executor(None, execute_fn)

    final_text = _format_reply(partial)
    if final_text != early_text:
        try:
            await sent.edit_text(final_text, parse_mode="Markdown")
        except Exception:
            try:
                await sent.edit_text(final_text)
            except Exception:
                pass


async def handle_voice(update, context):
    if not update.message or not update.message.voice:
        return

    voice_dir = ensure_voice_dir()
    telegram_file = await context.bot.get_file(update.message.voice.file_id)
    file_path = voice_dir / f"{update.message.voice.file_unique_id}.ogg"
    await telegram_file.download_to_drive(custom_path=str(file_path))

    await update.message.reply_text(t("status_transcribing"))
    transcript = transcribe_audio(str(file_path))

    if not transcript:
        await update.message.reply_text(t("status_no_speech"))
        return

    if VOICE_REPLY_WITH_TRANSCRIPT:
        await update.message.reply_text(t("status_recognized", text=transcript))

    status_msg = await update.message.reply_text(t("status_analysing_command"))
    await _dispatch(update, context, transcript, status_msg=status_msg)


async def handle_text(update, context):
    if not update.message or not update.message.text:
        return

    status_msg = await update.message.reply_text(t("status_analysing"))
    await _dispatch(update, context, update.message.text.strip(), status_msg=status_msg)


async def handle_rag_rebuild(update, context):
    if not RAG_ENABLED:
        await update.message.reply_text(t("status_rag_disabled"))
        return

    await update.message.reply_text(t("status_rag_rebuilding"))
    try:
        from core.rag.index import build as rag_build, status as rag_status

        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(None, rag_build)
        info = rag_status()
        await update.message.reply_text(
            t("status_rag_ready", count=count, last=info.get("last_indexed", "?"))
        )
    except Exception as e:
        logger.error(f"[RAG Rebuild] Fehler: {e}")
        await update.message.reply_text(t("status_rag_failed", error=e))
