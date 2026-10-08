"""
Mock de Pagamento: sistema EXTERNO que simula um provedor de pagamentos.
"""

import html
import logging
import os
import secrets
import threading
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

log = logging.getLogger("mock_payment")

PENDENTE, APROVADO, RECUSADO = "PENDENTE", "APROVADO", "RECUSADO"

# Situação da notificação da cobrança
WH_AGUARDANDO, WH_ENTREGUE, WH_REJEITADO, WH_FALHOU = "AGUARDANDO", "ENTREGUE", "REJEITADO", "FALHOU"

STYLE = """
body{font-family:system-ui,Segoe UI,sans-serif;background:#f1f4f8;margin:0;padding:40px 16px;display:flex;justify-content:center}
.card{background:#fff;border-radius:14px;padding:32px;max-width:420px;width:100%;box-shadow:0 4px 18px rgba(0,0,0,.1)}
h1{font-size:20px;margin:0 0 4px}.muted{color:#667;font-size:14px}.valor{font-size:34px;font-weight:700;margin:18px 0}
form{margin:10px 0}button{width:100%;padding:14px;font-size:16px;font-weight:600;border:0;border-radius:10px;cursor:pointer;color:#fff}
.ok{background:#1f9d55}.no{background:#d64545}.retry{background:#3b6fd4}.msg{padding:14px;border-radius:10px;margin-top:16px}
.msg.ok{background:#e6f6ec;color:#14663a}.msg.no{background:#fdeaea;color:#8c2a2a}.msg.warn{background:#fff4dc;color:#7a5200}
"""


class NovaCobranca(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    valor: float = Field(gt=0)
    webhook_url: str = Field(pattern=r"^https?://")
    webhook_token: str = Field(min_length=8, max_length=200)


def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _page(titulo: str, corpo: str) -> HTMLResponse:
    doc = (f"<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(titulo)}</title><style>{STYLE}</style></head>"
           f"<body><div class='card'>{corpo}</div></body></html>")
    return HTMLResponse(doc)


def create_app(http_client: httpx.Client | None = None, public_url: str | None = None) -> FastAPI:
    app = FastAPI(title="Mock de Pagamento (sistema externo)")
    http = http_client or httpx.Client(timeout=5.0)
    base = (public_url or os.getenv("MOCK_PUBLIC_URL", "http://localhost:9000")).rstrip("/")
    cobrancas: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def _get(charge_id: str) -> dict[str, Any]:
        c = cobrancas.get(charge_id)
        if c is None:
            raise HTTPException(404, "Cobrança não encontrada")
        return c

    def _publico(c: dict[str, Any]) -> dict[str, Any]:
        return {k: c[k] for k in ("charge_id", "order_id", "valor", "status", "webhook", "checkout_url")}

    def _notificar(c: dict[str, Any]) -> None:
        """POST no webhook da loja. 2xx = entregue; 4xx = loja rejeitou; resto = tentar de novo."""
        try:
            r = http.post(
                c["webhook_url"],
                json={"charge_id": c["charge_id"], "order_id": c["order_id"], "status": c["status"]},
                headers={"X-Webhook-Token": c["webhook_token"]},
            )
        except httpx.HTTPError as exc:
            log.warning("webhook da cobrança %s falhou: %s", c["charge_id"], exc)
            c["webhook"], c["webhook_msg"] = WH_FALHOU, "loja indisponível"
            return
        if r.status_code < 300:
            c["webhook"], c["webhook_msg"] = WH_ENTREGUE, ""
        elif r.status_code < 500:
            try:
                detalhe = r.json().get("detail", "")
            except ValueError:
                detalhe = ""
            c["webhook"], c["webhook_msg"] = WH_REJEITADO, str(detalhe) or f"HTTP {r.status_code}"
        else:
            c["webhook"], c["webhook_msg"] = WH_FALHOU, f"HTTP {r.status_code}"

    def _resultado(c: dict[str, Any]) -> HTMLResponse:
        aprovado = c["status"] == APROVADO
        titulo = "Pagamento aprovado" if aprovado else "Pagamento recusado"
        classe = "ok" if aprovado else "no"
        cabeca = f"<h1>{titulo}</h1><p class='muted'>Pedido {html.escape(c['order_id'])}</p>"
        if c["webhook"] == WH_ENTREGUE:
            msg = f"<div class='msg {classe}'>A loja foi avisada. Você já pode fechar esta aba e voltar à loja.</div>"
        elif c["webhook"] == WH_REJEITADO:
            msg = (f"<div class='msg warn'>A loja não aceitou este pagamento: {html.escape(c['webhook_msg'])}. "
                   f"Se o pedido foi cancelado, nada será cobrado.</div>")
        else:
            rota = "aprovar" if aprovado else "recusar"
            msg = (f"<div class='msg warn'>Pagamento registrado, mas não foi possível avisar a loja "
                   f"({html.escape(c['webhook_msg'])}).</div>"
                   f"<form method='post' action='/checkout/{c['charge_id']}/{rota}'>"
                   f"<button class='retry' type='submit'>Tentar avisar a loja novamente</button></form>")
        return _page(titulo, cabeca + msg)

    def _decidir(charge_id: str, status: str) -> HTMLResponse:
        c = _get(charge_id)
        with lock:
            if c["status"] == PENDENTE:
                c["status"] = status
            elif c["status"] != status:
                return _page("Pagamento já finalizado",
                             f"<h1>Pagamento já finalizado</h1><div class='msg warn'>Esta cobrança já foi "
                             f"{c['status'].lower()}. A decisão não pode ser alterada.</div>")
            if c["webhook"] in (WH_AGUARDANDO, WH_FALHOU):
                _notificar(c)
            return _resultado(c)

    @app.post("/cobrancas", status_code=201)
    def criar_cobranca(body: NovaCobranca):
        charge_id = "ch_" + secrets.token_hex(6)
        c = {
            "charge_id": charge_id, "order_id": body.order_id, "valor": body.valor,
            "webhook_url": body.webhook_url, "webhook_token": body.webhook_token,
            "status": PENDENTE, "webhook": WH_AGUARDANDO, "webhook_msg": "",
            "checkout_url": f"{base}/checkout/{charge_id}",
        }
        cobrancas[charge_id] = c
        log.info("cobrança %s criada para o pedido %s (%.2f)", charge_id, body.order_id, body.valor)
        return {"charge_id": charge_id, "checkout_url": c["checkout_url"], "status": PENDENTE}

    @app.get("/cobrancas/{charge_id}")
    def consultar_cobranca(charge_id: str):
        return _publico(_get(charge_id))

    @app.get("/checkout/{charge_id}", response_class=HTMLResponse)
    def checkout(charge_id: str):
        c = _get(charge_id)
        if c["status"] != PENDENTE:
            return _resultado(c)
        corpo = (
            f"<h1>Mock de Pagamento</h1><p class='muted'>Pedido {html.escape(c['order_id'])}</p>"
            f"<div class='valor'>{_brl(c['valor'])}</div>"
            f"<p class='muted'>Simule a decisão do provedor de pagamentos:</p>"
            f"<form method='post' action='/checkout/{charge_id}/aprovar'>"
            f"<button class='ok' type='submit'>Pagamento Aprovado</button></form>"
            f"<form method='post' action='/checkout/{charge_id}/recusar'>"
            f"<button class='no' type='submit'>Pagamento Recusado</button></form>"
        )
        return _page("Checkout", corpo)

    @app.post("/checkout/{charge_id}/aprovar", response_class=HTMLResponse)
    def aprovar(charge_id: str):
        return _decidir(charge_id, APROVADO)

    @app.post("/checkout/{charge_id}/recusar", response_class=HTMLResponse)
    def recusar(charge_id: str):
        return _decidir(charge_id, RECUSADO)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()