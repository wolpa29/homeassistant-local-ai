import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env robustly regardless of the current working directory.
# Priority:
#   1. DOTENV_PATH env var (explicit override)
#   2. <repo>/.env  (canonical location, shared by all services)
#   3. <repo>/services/telegram_bot/.env  (legacy fallback)
#   4. Whatever load_dotenv() finds walking up from CWD (last resort)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_env_candidates: list[Path] = []
if os.getenv("DOTENV_PATH"):
    _env_candidates.append(Path(os.environ["DOTENV_PATH"]))
_env_candidates.extend([
    _PROJECT_ROOT / ".env",
    _PROJECT_ROOT / "services" / "telegram_bot" / ".env",
])
for _candidate in _env_candidates:
    if _candidate.is_file():
        load_dotenv(_candidate)
        break
else:
    load_dotenv()

# UI language and LLM prompt language. "de" or "en".
LANGUAGE = os.getenv("LANGUAGE", "de").lower()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

# Shared secret sent as X-Api-Key by clients (RPi voice client, notify_gateway to tts).
# Empty = no auth (LAN-only deployments).
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")

# External TTS server (tts_server service).
# When set, the voice gateway calls this to synthesize replies and returns WAV
# directly to the Pi instead of JSON, no TTS processing on the Pi needed.
# Leave empty to keep returning JSON (Pi does TTS locally).
TTS_EXTERNAL_URL = os.getenv("TTS_EXTERNAL_URL", "")
TTS_EXTERNAL_VOICE = os.getenv("TTS_EXTERNAL_VOICE", "de_DE-thorsten-low")

# External Whisper (OpenAI-compatible /v1/audio/transcriptions endpoint).
WHISPER_EXTERNAL_URL = os.getenv("WHISPER_EXTERNAL_URL", "")
WHISPER_EXTERNAL_MODEL = os.getenv("WHISPER_EXTERNAL_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", LANGUAGE)

VOICE_REPLY_WITH_TRANSCRIPT = os.getenv("VOICE_REPLY_WITH_TRANSCRIPT", "true").lower() == "true"
VOICE_DOWNLOAD_DIR = os.getenv("VOICE_DOWNLOAD_DIR", "data/voice")

# LM Studio (OpenAI-kompatible /v1 API). Wird sowohl fuer den Primaerpfad
# (parse_command / format_state_reply) als auch fuer den MCP-Fallback (Mode 2)
# genutzt - gleiche Instanz, gleiches Modell.
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://10.1.10.78:1234")
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "qwen2.5-7b-instruct")
LMSTUDIO_TIMEOUT = int(os.getenv("LMSTUDIO_TIMEOUT", "30"))
# LMSTUDIO_API_KEY: Pflicht wenn LM Studio Server-Auth aktiv ist (fuer MCP noetig).
LMSTUDIO_API_KEY = os.getenv("LMSTUDIO_API_KEY", "")

HA_SERVICE_TIMEOUT = int(os.getenv("HA_SERVICE_TIMEOUT", "15"))

# Wenn true: call_service() loggt nur und schickt keine echten HA-Aufrufe.
# State-Reads bleiben aktiv, damit Statusabfragen weiter mit Live-Daten arbeiten.
# Praktisch fuer manuelle Experimente und die End-to-End-Test-Pipeline.
HA_DRY_RUN = os.getenv("HA_DRY_RUN", "false").lower() == "true"

LMSTUDIO_TEMPERATURE = float(os.getenv("LMSTUDIO_TEMPERATURE", "0.1"))

LMSTUDIO_NO_THINK = os.getenv("LMSTUDIO_NO_THINK", "true").lower() == "true"

# MCP (Mode 2) - Context-Groesse fuer /api/v1/chat
LMSTUDIO_CONTEXT_LENGTH = int(os.getenv("LMSTUDIO_CONTEXT_LENGTH", "8000"))

# MCP (Mode 2) - Whitelist der erlaubten HA-MCP-Tools.
# Leer/"{}"/"[]" = kein Filter (LM Studio erlaubt alle vom Server gemeldeten Tools).
_mcp_tools_raw = os.getenv(
    "LMSTUDIO_MCP_ALLOWED_TOOLS",
    "HassTurnOn,HassTurnOff,HassCancelAllTimers,HassBroadcast,"
    "HassClimateSetTemperature,HassLightSet,GetDateTime,GetLiveContext"
).strip()
if _mcp_tools_raw in ("", "{}", "[]"):
    LMSTUDIO_MCP_ALLOWED_TOOLS: list[str] = []
else:
    LMSTUDIO_MCP_ALLOWED_TOOLS = [t.strip() for t in _mcp_tools_raw.split(",") if t.strip()]

LLM_HISTORY_SIZE = int(os.getenv("LLM_HISTORY_SIZE", "0"))
MAX_ACTIONS_PER_COMMAND = int(os.getenv("MAX_ACTIONS_PER_COMMAND", "0"))

# RAG mode, replaces the entities.yaml -> LLM step when enabled.
# Set RAG_ENABLED=false to keep the legacy behaviour completely unchanged.
RAG_ENABLED = os.getenv("RAG_ENABLED", "false").lower() == "true"
_RAG_DB_DEFAULT = str(_PROJECT_ROOT / "data" / "rag" / "entities.sqlite")
_raw_rag_db = os.getenv("RAG_DB_PATH", "")
if _raw_rag_db:
    _p = Path(_raw_rag_db)
    RAG_DB_PATH = str(_p if _p.is_absolute() else _PROJECT_ROOT / _p)
else:
    RAG_DB_PATH = _RAG_DB_DEFAULT
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "15"))
RAG_KEYWORD_BOOST = float(os.getenv("RAG_KEYWORD_BOOST", "0.3"))
# Wenn > 0: Kandidaten mit Distance > RAG_DISTANCE_THRESHOLD werden verworfen
# (mindestens der beste Kandidat bleibt aber immer erhalten). Wirkt zusaetzlich
# zu RAG_TOP_K - das jeweils strengere Limit greift. Default 0.0 = aus.
RAG_DISTANCE_THRESHOLD = float(os.getenv("RAG_DISTANCE_THRESHOLD", "0.0"))

# Embedding model host, separate from chat LM Studio so you can use a different
# server for embeddings if you want. Defaults fall back to the chat LM Studio.
RAG_EMBED_URL = os.getenv("RAG_EMBED_URL", "") or LMSTUDIO_URL
RAG_EMBED_API_KEY = os.getenv("RAG_EMBED_API_KEY", "") or LMSTUDIO_API_KEY
RAG_EMBED_TIMEOUT = int(os.getenv("RAG_EMBED_TIMEOUT", "") or str(LMSTUDIO_TIMEOUT))
RAG_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "text-embedding-nomic-embed-text-v2-moe")
RAG_EMBED_DIM = int(os.getenv("RAG_EMBED_DIM", "768"))

# LLM Preprocessor, runs a small LLM call before the main pipeline to classify
# intent (command | smalltalk), fix typos / STT errors, and resolve pronouns from
# history. Independent of RAG_ENABLED. Each setting falls back to the main
# LMSTUDIO_* equivalent if empty.
LLM_PREPROCESSOR = os.getenv("LLM_PREPROCESSOR", "false").lower() == "true"
LLM_PREPROCESSOR_URL = os.getenv("LLM_PREPROCESSOR_URL", "") or LMSTUDIO_URL
LLM_PREPROCESSOR_API_KEY = os.getenv("LLM_PREPROCESSOR_API_KEY", "") or LMSTUDIO_API_KEY
LLM_PREPROCESSOR_MODEL = os.getenv("LLM_PREPROCESSOR_MODEL", "") or LMSTUDIO_MODEL
LLM_PREPROCESSOR_TIMEOUT = int(os.getenv("LLM_PREPROCESSOR_TIMEOUT", "") or str(LMSTUDIO_TIMEOUT))
LLM_PREPROCESSOR_TEMPERATURE = float(os.getenv("LLM_PREPROCESSOR_TEMPERATURE", "0.1"))

# UNIVERSAL (both RAG modes): if true, assistant turns are stored in history so
# the LLM sees its own prior replies on the next turn. In RAG mode those replies
# are additionally used to enrich the embed query for short follow-ups. If false,
# only user turns are stored, the LLM has no memory of what it previously said.
HISTORY_INCLUDE_ASSISTANT = os.getenv("HISTORY_INCLUDE_ASSISTANT", "true").lower() == "true"

# UNIVERSAL (both RAG modes): if true, the execution summary
# ("ausgefuehrt: turn_on -> light.kueche_decke, ...") is appended to the stored
# assistant turn. Requires HISTORY_INCLUDE_ASSISTANT=true, otherwise there is
# no assistant turn to append to.
HISTORY_APPEND_EXECUTIONS = os.getenv("HISTORY_APPEND_EXECUTIONS", "false").lower() == "true"

# Fallback-Modus wenn parse_command() keine Action findet:
#   0 = aus (bisheriges Verhalten, keine Treffer -> Fehlermeldung)
#   1 = einfacher Fallback: alle HA-Entities per REST holen und LLM erneut fragen
#   2 = MCP-Fallback: LM Studio mit konfiguriertem HA-MCP-Server aufrufen
FALLBACK_MODE = int(os.getenv("FALLBACK_MODE", "0"))

# Mode 1 - REST Fallback Filter/Limit.
# Leer/"{}"/"[]" bei DOMAINS = kein Domain-Filter (alle werden uebergeben).
# 0 bei MAX_ENTITIES = kein Limit.
FALLBACK_REST_MAX_ENTITIES = int(os.getenv("FALLBACK_REST_MAX_ENTITIES", "0"))
_fb_domains_raw = os.getenv(
    "FALLBACK_REST_DOMAINS",
    "light,switch,sensor,binary_sensor,climate,automation,cover"
).strip()
if _fb_domains_raw in ("", "{}", "[]"):
    FALLBACK_REST_DOMAINS: list[str] = []
else:
    FALLBACK_REST_DOMAINS = [d.strip() for d in _fb_domains_raw.split(",") if d.strip()]

# User-editable Config-Dateien.
# Bare-Metal: Defaults im Repo. Addon: zeigt auf /config (=/addon_configs/<slug>/),
# damit der User die Files via HA File Editor / Samba bearbeiten kann.
USERCONFIG_DIR = Path(
    os.getenv("USERCONFIG_DIR", str(_PROJECT_ROOT / "core" / "userconfig"))
)
TELEGRAM_MENUS_PATH = Path(
    os.getenv(
        "TELEGRAM_MENUS_PATH",
        str(_PROJECT_ROOT / "services" / "telegram_bot" / "menus.yaml"),
    )
)