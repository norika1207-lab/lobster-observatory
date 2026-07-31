"""Card Collection client — fire-and-forget to shared_auth collection module."""
import logging
import os
import httpx

_COLLECTION_URL = "http://127.0.0.1:8010/api/service/collection/record_bet"
_BATTLE_URL     = "http://127.0.0.1:8010/api/service/collection/record_battle"
_SERVICE_KEY    = os.environ.get("SERVICE_KEY")
if not _SERVICE_KEY:
    raise RuntimeError("SERVICE_KEY environment variable must be set")
_SITE           = "charenix"
_SITE_LABEL     = "Charenix"

_log = logging.getLogger(__name__)


async def record_card_bet(
    username: str,
    entity_id: str,
    card_name: str,
    card_img: str = "",
    sport: str = "",
    bet_amount: float = 0.0,
    extra: dict | None = None,
) -> None:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(
                _COLLECTION_URL,
                json={
                    "username": username,
                    "entity_id": entity_id,
                    "card_name": card_name,
                    "card_img": card_img,
                    "site": _SITE,
                    "site_label": _SITE_LABEL,
                    "sport": sport,
                    "bet_amount": float(bet_amount or 0),
                    "extra": extra or {},
                },
                headers={"X-Service-Key": _SERVICE_KEY},
            )
    except Exception as e:
        _log.debug(f"[card] record_bet skipped for {username}: {e}")


async def record_battle_win(username: str, entity_id: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(
                _BATTLE_URL,
                json={"username": username, "entity_id": entity_id, "site": _SITE},
                headers={"X-Service-Key": _SERVICE_KEY},
            )
    except Exception as e:
        _log.debug(f"[card] record_battle skipped for {username}: {e}")
