#!/command/with-contenv bashio
# Auto-build the RAG index on container startup when RAG is enabled.
# Runs once per start; failures only log a warning and never block services.

if ! bashio::config.true 'rag.enabled'; then
    bashio::log.info "[rag-build] rag.enabled=false — skipping startup index build"
    exit 0
fi

source /usr/lib/homeassistant-ai-gateway/export-env.sh

bashio::log.info "[rag-build] RAG aktiv — starte automatischen Index-Rebuild ..."

cd /opt/gateway
if python3 -m core.rag.index; then
    bashio::log.info "[rag-build] Initialer RAG-Index-Rebuild abgeschlossen"
else
    bashio::log.warning "[rag-build] Initialer Rebuild fehlgeschlagen — Add-on startet trotzdem. Manuell ueber Telegram /rag_rebuild oder POST /rag_rebuild nachholen."
fi

exit 0
