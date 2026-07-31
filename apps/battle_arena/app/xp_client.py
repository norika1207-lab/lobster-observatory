"""XP client — fire-and-forget upstream to shared_auth central XP ledger."""
import logging
import os
import httpx

_XP_URL = "http://127.0.0.1:8010/api/service/xp/record"
_SERVICE_KEY = os.environ.get("SERVICE_KEY")
if not _SERVICE_KEY:
    raise RuntimeError("SERVICE_KEY environment variable must be set")
_SITE = "charenix"

_log = logging.getLogger(__name__)


async def record_xp(
    username: str,
    action: str,
    bet_amount: float = 0.0,
    streak: int = 0,
    ref_id: str = "",
) -> None:
    if not username or username in ("AI", "TBD") or str(username).startswith("AI·"):
        return
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(
                _XP_URL,
                json={
                    "username": username,
                    "site": _SITE,
                    "action": action,
                    "bet_amount": float(bet_amount or 0),
                    "streak": int(streak or 0),
                    "ref_id": str(ref_id or ""),
                },
                headers={"X-Service-Key": _SERVICE_KEY},
            )
    except Exception as e:
        _log.debug(f"[xp] record skipped for {username}: {e}")
