"""Cliente HTTP para a API externa de e-mail (Resend)."""

import logging
import os
from pathlib import Path
from typing import Any

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

log = logging.getLogger("email_provider")

DEFAULT_RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM_EMAIL = "E-Commerce Promoções <onboarding@resend.dev>"


class EmailServiceError(Exception):
    """Erro de comunicação ou resposta de erro da API de e-mail."""


class ResendEmailClient:
    def __init__(
        self,
        api_key: str | None = None,
        from_email: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if api_key is not None:
            self.api_key = api_key.strip()
        else:
            self.api_key = os.getenv("RESEND_API_KEY", "").strip()

        if from_email is not None:
            self.from_email = from_email.strip()
        else:
            self.from_email = os.getenv("RESEND_FROM_EMAIL", DEFAULT_FROM_EMAIL).strip()

        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        else:
            self.base_url = os.getenv("RESEND_BASE_URL", DEFAULT_RESEND_URL).rstrip("/")

        self.client = client or httpx.Client(timeout=8.0)

    @property
    def is_live(self) -> bool:
        """Indica se há uma chave de API real configurada."""
        return bool(self.api_key and not self.api_key.startswith("mock") and not self.api_key.startswith("sim_"))

    def send_email(
        self,
        to: str | list[str],
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> dict[str, Any]:
        """Dispara um e-mail através da API do Resend (ou em modo simulação se sem API_KEY)."""
        destinatarios = [to] if isinstance(to, str) else to
        if not destinatarios:
            raise ValueError("Ao menos um destinatário deve ser informado.")

        payload: dict[str, Any] = {
            "from": self.from_email,
            "to": destinatarios,
            "subject": subject,
            "html": html_content,
        }
        if text_content:
            payload["text"] = text_content

        if not self.is_live:
            log.info(
                "[EMAIL SIMULADO] Destinatários: %s | Assunto: '%s' | De: %s",
                destinatarios,
                subject,
                self.from_email,
            )
            return {
                "id": f"sim_{destinatarios[0][:8]}",
                "simulated": True,
                "to": destinatarios,
                "subject": subject,
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = self.client.post(self.base_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            log.error("Erro de conexão com o Resend: %s", exc)
            raise EmailServiceError(f"Falha de conexão com a API de e-mail: {exc}") from exc

        if resp.status_code >= 400:
            msg = f"API Resend retornou HTTP {resp.status_code}: {resp.text[:300]}"
            log.error(msg)
            raise EmailServiceError(msg)

        try:
            data = resp.json()
            log.info("E-mail enviado com sucesso via Resend (id=%s) para %s", data.get("id"), destinatarios)
            return dict(data)
        except (ValueError, TypeError) as exc:
            raise EmailServiceError(f"Resposta inválida da API Resend: {resp.text[:200]}") from exc

    def close(self) -> None:
        self.client.close()
