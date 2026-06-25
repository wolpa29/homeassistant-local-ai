#!/command/with-contenv bashio
# Prepare the persistent /data layout and warn on missing required config.
set -e

mkdir -p /data/voice /data/rag

bashio::log.info "homeassistant-local-ai: data dir ready (/data/voice, /data/rag)"

# User-editable config files in /config (= /addon_configs/<slug>/ on the host,
# visible via Samba and the official "File editor" addon).
# Defaults are baked into the image; we only seed missing files so the user's
# edits survive addon updates.
USER_DIR=/config/userconfig
mkdir -p "${USER_DIR}"
for f in entities.yaml entities_blacklist.yaml pre_llm_memory.md post_llm_memory.md; do
    if [ ! -f "${USER_DIR}/${f}" ]; then
        cp "/opt/gateway/core/userconfig/${f}" "${USER_DIR}/${f}"
        bashio::log.info "seeded ${USER_DIR}/${f} from defaults"
    fi
done

if [ ! -f /config/menus.yaml ]; then
    cp /opt/gateway/services/telegram_bot/menus.yaml /config/menus.yaml
    bashio::log.info "seeded /config/menus.yaml from defaults"
fi

bashio::log.info "homeassistant-local-ai: user config ready at /addon_configs/<slug>/"

# Hard-fail when an enabled service is missing required config. The HA UI
# cannot enforce conditional "required" fields, so we validate here. The user
# sees the failure in the add-on log and the addon stays stopped until fixed.
ERRORS=0
fail() { bashio::log.fatal "$1"; ERRORS=$((ERRORS+1)); }

if bashio::config.true 'services.telegram_bot'; then
    bashio::config.has_value 'telegram.bot_token' \
        || fail "services.telegram_bot=true but telegram.bot_token is empty (get one from @BotFather)"
    [ "$(bashio::config 'telegram.chat_id')" != "0" ] \
        || fail "services.telegram_bot=true but telegram.chat_id is 0 (your numeric Telegram user id)"
fi

if bashio::config.true 'services.voice_gateway' || bashio::config.true 'services.telegram_bot'; then
    bashio::config.has_value 'lmstudio.url' \
        || fail "lmstudio.url is empty — required when voice gateway or telegram bot is enabled"
fi

if bashio::config.true 'services.voice_gateway' || bashio::config.true 'services.notify_gateway'; then
    bashio::config.has_value 'whisper.url' \
        || bashio::log.warning "whisper.url is empty — voice features (audio upload) will be unavailable"
fi

if bashio::config.true 'rag.enabled'; then
    bashio::config.has_value 'rag.embed_url' \
        || bashio::log.notice "rag.embed_url empty — falling back to lmstudio.url for embeddings"
    if [ ! -s /data/rag/entities.sqlite ]; then
        bashio::log.notice "RAG aktiv, aber Index noch leer - Rebuild ueber Telegram /rag_rebuild oder POST /rag_rebuild starten (Primary-Parser uebernimmt solange als Backup)"
    fi
fi

if bashio::config.true 'preprocessor.enabled'; then
    bashio::config.has_value 'preprocessor.url' \
        || bashio::log.notice "preprocessor.url empty — falling back to lmstudio.url"
    bashio::config.has_value 'preprocessor.model' \
        || bashio::log.notice "preprocessor.model empty — falling back to lmstudio.model"
fi

if [ "${ERRORS}" -gt 0 ]; then
    bashio::exit.nok "Fix ${ERRORS} configuration error(s) in the Configuration tab and restart the add-on."
fi

bashio::log.info "homeassistant-local-ai: cont-init complete"
