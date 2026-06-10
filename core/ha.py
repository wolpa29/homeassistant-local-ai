import requests
import logging
from concurrent.futures import ThreadPoolExecutor
from core.config import HA_URL, HA_TOKEN, HA_SERVICE_TIMEOUT, HA_DRY_RUN

logger = logging.getLogger(__name__)


def call_service(domain: str, action: str, entity_id: str, service_data: dict | None = None) -> str:
    """HA Service aufrufen. Gibt 'ok', 'timeout' oder 'error' zurueck.

    HA_DRY_RUN=true: kein echter HTTP-Call - der Service-Call wird nur geloggt
    und mit 'ok' quittiert. State-Reads (get_state, get_states_bulk, get_all_states)
    sind davon nicht betroffen und liefern weiterhin echte Werte.
    """
    if HA_DRY_RUN:
        sd = f" {service_data}" if service_data else ""
        logger.info(f"[HA DRY-RUN] {domain}.{action} {entity_id}{sd}")
        return "ok"

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }
    service = "trigger" if action == "trigger" else action
    url = f"{HA_URL}/api/services/{domain}/{service}"

    body: dict = {"entity_id": entity_id}
    if service_data:
        body.update(service_data)

    try:
        r = requests.post(url, headers=headers, json=body, timeout=HA_SERVICE_TIMEOUT)
        if r.status_code in (200, 201):
            return "ok"
        logger.warning(f"[HA] Service-Call {domain}.{service} {entity_id} -> {r.status_code}: {r.text[:200]}")
        return "error"
    except requests.exceptions.Timeout:
        logger.error(f"[HA] Timeout bei Service Call {domain}.{service} {entity_id} (>{HA_SERVICE_TIMEOUT}s)")
        return "timeout"
    except Exception as e:
        logger.error(f"[HA] Fehler beim Service Call: {e}")
        return "error"


def get_all_states(domains: list[str] | None = None, max_entities: int = 0) -> list[dict]:
    """Alle Entity-Zustaende aus HA holen (fuer REST-Fallback Mode 1).

    domains: None oder [] -> kein Filter (alle Domains).
    max_entities: 0 -> kein Limit.
    Rueckgabe: kompakte Liste mit entity_id, state, friendly_name, unit, domain.
    """
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"{HA_URL}/api/states"

    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.warning(f"[HA] get_all_states Status {r.status_code}")
            return []
        raw = r.json()
    except Exception as e:
        logger.error(f"[HA] Fehler beim get_all_states: {e}")
        return []

    out: list[dict] = []
    for s in raw:
        entity_id = s.get("entity_id", "")
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domains and domain not in domains:
            continue
        attrs = s.get("attributes", {}) or {}
        out.append({
            "entity_id": entity_id,
            "state": s.get("state", ""),
            "friendly_name": attrs.get("friendly_name", ""),
            "unit": attrs.get("unit_of_measurement", ""),
            "domain": domain,
        })
        if max_entities and len(out) >= max_entities:
            break

    logger.info(
        f"[HA] get_all_states: {len(out)} Entities "
        f"(Filter: {domains or 'alle'}, Limit: {max_entities or 'kein'})"
    )
    return out


def get_ha_state(entity_id: str) -> str | None:
    result = get_state(entity_id)
    return result.get("state") if result else None


def trigger_automation(entity_id: str) -> bool:
    return call_service("automation", "trigger", entity_id) == "ok"


def get_state(entity_id: str) -> dict | None:
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"{HA_URL}/api/states/{entity_id}"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"[HA] get_state '{entity_id}' Status {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"[HA] Fehler beim State Fetch: {e}")
        return None


def notify_persistent(title: str, message: str, notification_id: str = "homeassistant_ai_gateway") -> None:
    """Create or replace a HA persistent notification (bell icon in the UI)."""
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{HA_URL}/api/services/persistent_notification/create"
    try:
        requests.post(url, headers=headers, json={
            "title": title,
            "message": message,
            "notification_id": notification_id,
        }, timeout=5)
    except Exception as e:
        logger.warning(f"[HA] persistent_notification fehlgeschlagen: {e}")


def get_states_bulk(entity_ids: list[str], max_workers: int = 8) -> dict[str, dict]:
    """Holt States fuer mehrere Entity-IDs parallel. Fehlende/fehlgeschlagene IDs fehlen im Ergebnis.

    Wird vom RAG-Parser genutzt, damit das LLM mit echten Werten und Attributen entscheiden kann.
    """
    if not entity_ids:
        return {}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(get_state, eid): eid for eid in entity_ids}
        for fut in futures:
            eid = futures[fut]
            try:
                res = fut.result(timeout=12)
                if res is not None:
                    out[eid] = res
            except Exception as e:
                logger.warning(f"[HA] get_states_bulk: '{eid}' fehlgeschlagen: {e}")
    return out
