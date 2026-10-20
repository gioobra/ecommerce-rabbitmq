"""Ponto de entrada do MS Promoções: consome interesse.promocao e dispara notificações via Resend."""

import logging
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path.insert(0, str(BASE_DIR / "shared"))
sys.path.insert(0, str(HERE))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

from utils import ensure_keys
import messaging as m
from promotion_repository import PromotionRepository
from email_provider import ResendEmailClient
from promotion_handlers import PromotionEngine, make_handler

SERVICE_NAME = "promotion"
log = logging.getLogger("promotion")


def main() -> None:
    m.configure_logging()
    repo = PromotionRepository()
    email_client = ResendEmailClient()
    engine = PromotionEngine(repo=repo, email_client=email_client)

    # Garante chaves RSA do microsserviço
    ensure_keys(SERVICE_NAME, BASE_DIR)

    # Configura handler de eventos
    handler = make_handler(repo, engine=engine)

    # Inicia o consumer do RabbitMQ em thread daemon
    m.start_consumer_thread(SERVICE_NAME, BASE_DIR, handler)

    intervalo = float(os.getenv("PROMOTION_INTERVAL_SECONDS", "20"))
    log.info(
        "MS Promoções ativo. Escutando 'interesse.promocao' e gerando promoções a cada %.1fs.",
        intervalo,
    )
    if email_client.is_live:
        log.info("API de e-mail conectada ao Resend (remetente: %s)", email_client.from_email)
    else:
        log.info("Modo de simulação de e-mail ativo (sem RESEND_API_KEY). Os disparos serão exibidos no log.")

    try:
        while True:
            time.sleep(intervalo)
            # Verifica se existem assinantes para notificar
            categorias_ativas = repo.list_categories_with_subscribers()
            if categorias_ativas:
                log.info("Ciclo periódico: gerando promoção para categorias com clientes interessados...")
                engine.generate_and_notify()
            else:
                log.debug("Nenhum consumidor cadastrado ainda. Aguardando inscrições...")
    except KeyboardInterrupt:
        log.info("Encerrando MS Promoções.")


if __name__ == "__main__":
    main()
