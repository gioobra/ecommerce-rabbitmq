"""Handlers e motor de geração de promoções e disparo de notificações."""

import logging
import random
import time
import uuid
from typing import Any, Callable

from events import INTERESSE_PROMOCAO
from email_provider import ResendEmailClient, EmailServiceError
from promotion_repository import PromotionRepository

log = logging.getLogger("promotion_handlers")

DEFAULT_CATALOG: dict[str, list[str]] = {
    "Computadores": ["Notebook Gamer", "Notebook Ultrafino"],
    "Periféricos": ["Mouse Óptico", "Teclado Mecânico", "Headset 7.1", "Webcam Full HD"],
    "Monitores": ["Monitor 24pol IPS", "Monitor Gamer 144Hz"],
}

DESCONTOS_POSSIVEIS = [10, 15, 20, 25, 30, 40, 50]


class PromotionEngine:
    def __init__(
        self,
        repo: PromotionRepository,
        email_client: ResendEmailClient | None = None,
        catalogo: dict[str, list[str]] | None = None,
    ) -> None:
        self.repo = repo
        self.email_client = email_client or ResendEmailClient()
        self.catalogo = catalogo or DEFAULT_CATALOG

    def generate_promotion(self, categoria: str | None = None) -> dict[str, Any]:
        """Sorteia uma categoria (priorizando categorias com inscritos), produto e desconto."""
        if not categoria:
            categorias_com_inscritos = self.repo.list_categories_with_subscribers()
            if categorias_com_inscritos and random.random() < 0.8:
                # 80% das vezes sorteia uma categoria que já tem clientes aguardando ofertas
                categoria = random.choice(categorias_com_inscritos)
            else:
                categoria = random.choice(list(self.catalogo.keys()))

        produtos = self.catalogo.get(categoria) or ["Produto Promocional"]
        produto = random.choice(produtos)
        desconto = random.choice(DESCONTOS_POSSIVEIS)
        promo_id = f"PROMO-{uuid.uuid4().hex[:8].upper()}"

        return {
            "promocao_id": promo_id,
            "categoria": categoria,
            "produto": produto,
            "desconto": desconto,
            "gerada_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _build_email_html(self, promocao: dict[str, Any]) -> str:
        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 10px;">
            <h2 style="color: #2563eb; margin-top: 0;">🔥 Oferta Especial para Você!</h2>
            <p>Identificamos seu interesse na categoria <strong>{promocao['categoria']}</strong>.</p>
            <div style="background-color: #f8fafc; padding: 18px; border-radius: 8px; margin: 16px 0; border-left: 4px solid #16a34a;">
                <h3 style="margin: 0 0 8px 0; color: #0f172a;">{promocao['produto']}</h3>
                <p style="font-size: 24px; font-weight: bold; color: #16a34a; margin: 4px 0;">
                    {promocao['desconto']}% DE DESCONTO
                </p>
                <small style="color: #64748b;">Código: {promocao['promocao_id']}</small>
            </div>
            <p style="color: #475569; font-size: 14px;">
                Aproveite agora mesmo em nossa loja virtual antes que o estoque acabe!
            </p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
            <p style="font-size: 12px; color: #94a3b8;">
                Você recebeu este e-mail porque cadastrou seu interesse na categoria {promocao['categoria']}.
            </p>
        </div>
        """

    def notify_subscribers(self, promocao: dict[str, Any]) -> list[dict[str, Any]]:
        """Identifica os e-mails inscritos na categoria e envia a notificação via API externa."""
        categoria = promocao["categoria"]
        inscritos = self.repo.list_subscribers_by_category(categoria)
        if not inscritos:
            log.info("Nenhum inscrito na categoria '%s' para a promoção %s", categoria, promocao["promocao_id"])
            return []

        log.info(
            "Disparando promoção %s (%s - %d%% OFF) para %d inscrito(s): %s",
            promocao["promocao_id"],
            promocao["produto"],
            promocao["desconto"],
            len(inscritos),
            inscritos,
        )

        subject = f"⚡ {promocao['desconto']}% OFF em {promocao['produto']} na categoria {categoria}!"
        html_body = self._build_email_html(promocao)

        disparos = []
        for email in inscritos:
            try:
                res = self.email_client.send_email(to=email, subject=subject, html_content=html_body)
                disparos.append({"email": email, "sucesso": True, "detalhes": res})
            except EmailServiceError as exc:
                log.warning("Falha ao enviar e-mail para %s: %s", email, exc)
                disparos.append({"email": email, "sucesso": False, "erro": str(exc)})

        return disparos

    def generate_and_notify(self, categoria: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Gera a promoção e notifica os clientes inscritos."""
        promo = self.generate_promotion(categoria)
        disparos = self.notify_subscribers(promo)
        return promo, disparos


def make_handler(repo: PromotionRepository, engine: PromotionEngine | None = None):
    """Handler para processar eventos do RabbitMQ recebidos pelo MS Promoções."""
    def handler(event_type: str, payload: dict[str, Any], envelope: dict[str, Any]) -> None:
        if event_type != INTERESSE_PROMOCAO:
            log.debug("Evento ignorado: %s", event_type)
            return

        email = str(payload.get("email") or "").strip()
        categorias = payload.get("categorias")
        client_id = payload.get("client_id")

        if not email or "@" not in email:
            log.warning("Evento %s recebido com e-mail inválido: %r", event_type, email)
            return

        if not isinstance(categorias, list) or not categorias:
            log.warning("Evento %s recebido sem categorias válidas para o e-mail %s", event_type, email)
            return

        salvas = repo.save_interests(email, [str(c) for c in categorias], client_id=client_id)
        log.info("[INTERESSE REGISTRADO] Consumidor '%s' cadastrou interesse em: %s", email, salvas)

        # Se houver engine, pode disparar uma oferta imediata para a primeira categoria de interesse
        if engine and salvas:
            primeira_cat = salvas[0]
            log.info("Disparando oferta inicial imediata para '%s' na categoria '%s'", email, primeira_cat)
            try:
                engine.generate_and_notify(primeira_cat)
            except Exception as exc:
                log.warning("Erro ao disparar oferta inicial de boas-vindas: %s", exc)

    return handler
