"""Cliente REST do provedor de pagamento"""

import os
from typing import Any

import httpx


class ProviderUnavailable(Exception):
    """Provedor fora do ar ou com erro"""


class ProviderRejected(Exception):
    """Provedor recusou a requisição"""


class MockPaymentClient:
    def __init__(self, base_url: str | None = None, client: httpx.Client | None = None) -> None:
        self.base_url = (base_url or os.getenv("MOCK_PAYMENT_URL", "http://localhost:9000")).rstrip("/")
        self.client = client or httpx.Client(timeout=3.0)

    def create_charge(self, order_id: str, valor: float, webhook_url: str, webhook_token: str) -> dict[str, Any]:
        try:
            resp = self.client.post(f"{self.base_url}/cobrancas", json={
                "order_id": order_id, "valor": valor,
                "webhook_url": webhook_url, "webhook_token": webhook_token,
            })
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderRejected(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            return {"charge_id": str(data["charge_id"]), "checkout_url": str(data["checkout_url"])}
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderRejected(f"resposta inválida do provedor: {resp.text[:200]}") from exc