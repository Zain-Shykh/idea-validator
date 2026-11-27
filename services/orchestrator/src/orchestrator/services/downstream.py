"""The services M3 calls, each with its own timeout (M3-3, §21.4). A peer without a URL fails
like a peer that is down.

Retries (429, 5xx, timeouts; at most 2) are ``common.http``'s, and every call also has a limit
over all its attempts. The scorers and the scouts are never repeated by the HTTP client: the
first job may still be running and spending.
"""

import asyncio
from typing import Any

import httpx

from common.http import ServiceClient, ServiceError
from orchestrator.settings import OrchestratorSettings

# peer -> (settings field, timeout of one attempt in seconds, limit over all attempts)
PEERS: dict[str, tuple[str, float, float]] = {
    "profile": ("profile_url", 5.0, 15.0),
    # 15 s keeps the verdict call and the final trace flush inside the 30 s shutdown window
    "verdict": ("verdict_url", 5.0, 15.0),
    "kb": ("kb_url", 10.0, 25.0),
    "critic": ("critic_url", 10.0, 25.0),
    "ideation": ("ideation_url", 60.0, 90.0),
    "validators": ("validators_url", 600.0, 600.0),
    "scouts": ("scouts_url", 120.0, 120.0),
    "analyst": ("analyst_url", 10.0, 25.0),
    "agentops": ("agentops_url", 3.0, 3.0),
}
# M12 judges up to 10 claims one after another within its own 120 s
CHECK_TIMEOUT_S = 150.0


class Peers:
    def __init__(
        self, settings: OrchestratorSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._clients = {
            name: ServiceClient(
                url,
                timeout=httpx.Timeout(timeout, connect=3.0),
                retries=0 if name == "agentops" else 2,
                transport=transport,
            )
            for name, (field, timeout, _) in PEERS.items()
            if (url := getattr(settings, field))
        }

    def client(self, peer: str) -> ServiceClient:
        if peer not in self._clients:
            raise ServiceError("UNAVAILABLE", f"{PEERS[peer][0].upper()} is not set")
        return self._clients[peer]

    async def request(
        self,
        peer: str,
        method: str,
        path: str,
        *,
        limit_s: float | None = None,
        **kwargs: Any,
    ) -> Any:
        client = self.client(peer)
        try:
            async with asyncio.timeout(limit_s or PEERS[peer][2]):
                return await client.request(method, path, **kwargs)
        except TimeoutError as exc:
            raise ServiceError("TIMEOUT", f"{method} {path} took too long") from exc

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
