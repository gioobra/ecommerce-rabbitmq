"""Ponto de entrada do MS Promoções: consome interesse.promocao e dispara notificações via Resend."""

import logging
import os
import signal
import sys
import threading
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

    stop_event = threading.Event()

    def _signal_handler(sig, frame):
        log.info("Sinal %d recebido. Encerrando MS Promoções...", sig)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

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
        while not stop_event.is_set():
            if stop_event.wait(timeout=intervalo):
                break
            # Verifica se existem assinantes para notificar
            categorias_ativas = repo.list_categories_with_subscribers()
            if categorias_ativas:
                log.info("Ciclo periódico: gerando promoção para categorias com clientes interessados...")
                engine.generate_and_notify()
            else:
                log.debug("Nenhum consumidor cadastrado ainda. Aguardando inscrições...")
    except KeyboardInterrupt:
        log.info("Encerrando MS Promoções (KeyboardInterrupt).")
    finally:
        email_client.close()
        log.info("MS Promoções encerrado com sucesso.")


if __name__ == "__main__":
    main()

