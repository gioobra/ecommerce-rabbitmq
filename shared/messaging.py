import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import pika
from pika.exceptions import AMQPError
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from utils import sign_payload, verify_signature, load_public_key
from events import EXCHANGE_NAME, EVENT_PRODUCERS, QUEUES, SERVICE_QUEUES

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

log = logging.getLogger("messaging")

ENVELOPE_FIELDS = ("event_type", "producer", "message_id", "timestamp", "payload", "signature")
Handler = Callable[[str, dict[str, Any], dict[str, Any]], None]


class TransientError(Exception):
    """Falha temporária, a mensagem volta para a fila."""


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logging.getLogger("pika").setLevel(logging.WARNING)


def _conn_params() -> pika.ConnectionParameters:
    return pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST", "localhost"),
        port=int(os.getenv("RABBITMQ_PORT", "5672")),
        credentials=pika.PlainCredentials(
            os.getenv("RABBITMQ_USER", "guest"), os.getenv("RABBITMQ_PASS", "guest")
        ),
        heartbeat=60,
        blocked_connection_timeout=30,
    )


def declare_topology(channel) -> None:
    """Declara exchange, todas as filas e bindings """
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="direct", durable=True)
    for queue, routing_keys in QUEUES.items():
        channel.queue_declare(queue=queue, durable=True)
        for rk in routing_keys:
            channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue, routing_key=rk)



def build_envelope(
    producer: str, private_key: RSAPrivateKey, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    
    body = {
        "event_type": event_type,
        "producer": producer,
        "message_id": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "payload": payload,
    }
    return {**body, "signature": sign_payload(private_key, body)}


_key_cache: dict[tuple[str, str, str], RSAPublicKey] = {}
_key_lock = threading.Lock()


def _get_public_key(producer: str, requester: str, base_dir: Path) -> RSAPublicKey | None:
    cache_key = (str(base_dir), requester, producer)
    with _key_lock:
        if cache_key not in _key_cache:
            key = load_public_key(producer, requester, base_dir)
            if key is None:  # não guarda "ausente": a chave pode aparecer depois
                return None
            _key_cache[cache_key] = key
        return _key_cache[cache_key]


def verify_envelope(
    envelope: Any, routing_key: str, requester: str, base_dir: Path
) -> tuple[bool, str]:
    """Valida estrutura, autorização do produtor e assinatura. Retorna (ok, motivo)."""
    if not isinstance(envelope, dict) or any(f not in envelope for f in ENVELOPE_FIELDS):
        return False, "envelope malformado"
    if not isinstance(envelope["payload"], dict):
        return False, "payload malformado"
    if envelope["event_type"] != routing_key:
        return False, "event_type diverge da routing key"

    allowed = EVENT_PRODUCERS.get(routing_key)
    if allowed is None:
        return False, f"evento desconhecido '{routing_key}'"
    if envelope["producer"] != allowed:
        return False, f"'{envelope['producer']}' não pode publicar '{routing_key}'"

    public_key = _get_public_key(allowed, requester, base_dir)
    if public_key is None:
        return False, f"chave pública de '{allowed}' ausente"

    body = {f: envelope[f] for f in ENVELOPE_FIELDS if f != "signature"}
    if not verify_signature(public_key, body, envelope["signature"]):
        return False, "assinatura inválida"
    return True, "ok"



class Publisher:

    def __init__(self, service_name: str, private_key: RSAPrivateKey) -> None:
        self.service_name = service_name
        self._private_key = private_key
        self._lock = threading.Lock()
        self._conn: pika.BlockingConnection | None = None
        self._channel = None

    def _ensure_connected(self) -> None:
        if self._conn and not self._conn.is_closed and self._channel and not self._channel.is_closed:
            return
        self._conn = pika.BlockingConnection(_conn_params())
        self._channel = self._conn.channel()
        declare_topology(self._channel)

    def _close_quietly(self) -> None:
        try:
            if self._conn and not self._conn.is_closed:
                self._conn.close()
        except Exception:
            pass
        self._conn = self._channel = None

    def publish(self, routing_key: str, payload: dict[str, Any]) -> str:
        
        if EVENT_PRODUCERS.get(routing_key) != self.service_name:
            raise ValueError(f"'{self.service_name}' não pode publicar '{routing_key}'")

        envelope = build_envelope(self.service_name, self._private_key, routing_key, payload)
        body = json.dumps(envelope).encode("utf-8")

        with self._lock:
            for attempt in (1, 2):
                try:
                    self._ensure_connected()
                    self._channel.basic_publish(
                        exchange=EXCHANGE_NAME,
                        routing_key=routing_key,
                        body=body,
                        properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                    )
                    log.info("publicado %s (id=%s)", routing_key, envelope["message_id"][:8])
                    return envelope["message_id"]
                except (AMQPError, OSError) as exc:
                    log.warning("falha ao publicar (tentativa %d): %s", attempt, exc)
                    self._close_quietly()
                    if attempt == 2:
                        raise
        raise RuntimeError("inalcançável")

    def close(self) -> None:
        with self._lock:
            self._close_quietly()



def consume_forever(service_name: str, base_dir: Path, handler: Handler) -> None:
    """Consome a fila do serviço , reconectando se cair."""
    queue = SERVICE_QUEUES[service_name]

    def on_message(ch, method, properties, body: bytes) -> None:
        try:
            envelope = json.loads(body.decode("utf-8"))
        except ValueError:
            log.warning("[SEGURANÇA] mensagem não-JSON descartada")
            ch.basic_ack(method.delivery_tag)
            return

        ok, reason = verify_envelope(envelope, method.routing_key, service_name, base_dir)
        if not ok:
            log.warning("[SEGURANÇA] %s descartado: %s", method.routing_key, reason)
            ch.basic_ack(method.delivery_tag)
            return

        try:
            handler(envelope["event_type"], envelope["payload"], envelope)
        except TransientError as exc:
            log.warning("falha temporária em %s (%s); mensagem devolvida à fila", method.routing_key, exc)
            time.sleep(2)
            ch.basic_nack(method.delivery_tag, requeue=True)
        except Exception:
            log.exception("erro no handler de %s; mensagem descartada", method.routing_key)
            ch.basic_nack(method.delivery_tag, requeue=False)
        else:
            ch.basic_ack(method.delivery_tag)

    while True:
        try:
            conn = pika.BlockingConnection(_conn_params())
            channel = conn.channel()
            declare_topology(channel)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=queue, on_message_callback=on_message)
            log.info("consumindo '%s'", queue)
            channel.start_consuming()
        except (AMQPError, OSError) as exc:
            log.warning("conexão perdida (%s); nova tentativa em 5s", exc)
            time.sleep(5)


def start_consumer_thread(service_name: str, base_dir: Path, handler: Handler) -> threading.Thread:
    """Roda o consumer em thread daemon """
    thread = threading.Thread(
        target=consume_forever, args=(service_name, base_dir, handler),
        name=f"{service_name}-consumer", daemon=True,
    )
    thread.start()
    return thread