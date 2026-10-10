"""Consome pagamento.aprovado, emite a nota fiscal, despacha e publica pedido.enviado."""

import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path.insert(0, str(BASE_DIR / "shared"))
sys.path.insert(0, str(HERE))

from utils import ensure_keys
import messaging as m
from delivery_repository import DeliveryRepository
from delivery_handlers import make_handler

SERVICE_NAME = "delivery"
log = logging.getLogger("delivery")


def main() -> None:
    m.configure_logging()
    repo = DeliveryRepository()
    publisher = m.Publisher(SERVICE_NAME, ensure_keys(SERVICE_NAME, BASE_DIR))
    delay = float(os.getenv("DELIVERY_DELAY_SECONDS", "3"))
    handler = make_handler(repo, publisher.publish, delay_seconds=delay)
    log.info("MS Entrega no ar (despacho simulado em %.1fs). Ctrl+C para sair.", delay)
    try:
        m.consume_forever(SERVICE_NAME, BASE_DIR, handler)
    except KeyboardInterrupt:
        log.info("encerrando")
    finally:
        publisher.close()


if __name__ == "__main__":
    main()