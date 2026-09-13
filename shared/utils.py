"""
Módulo compartilhado de criptografia assimétrica (RSA) para assinatura e
verificação de eventos entre os microsserviços do sistema de e-commerce.
"""

import json
import base64
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.exceptions import InvalidSignature

SERVICES: list[str] = [
    "order",
    "inventory",
    "payment",
    "delivery",
    "promotion",
]

KEY_SIZE: int = 2048
PRIVATE_KEY_FILENAME: str = "private_key.pem"


def _service_keys_dir(base_dir: Path, service_name: str) -> Path:
    """
    Retorna o caminho .../ _service / keys para um serviço.
    """
    return base_dir / f"{service_name}_service" / "keys"


def _public_keys_dir_of(base_dir: Path, service_name: str) -> Path:
    """
    Retorna a pasta public_keys/ DENTRO da pasta de um serviço específico,
    onde ficam guardadas as chaves públicas dos demais.
    """
    return _service_keys_dir(base_dir, service_name) / "public_keys"


def _generate_key_pair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    '''
    Gera um novo par de chaves RSA
    '''
    private_key: RSAPrivateKey = rsa.generate_private_key(
        public_exponent=65537,
        key_size=KEY_SIZE,
    )
    return private_key, private_key.public_key()


def _save_private_key(private_key: RSAPrivateKey, path: Path) -> None:
    '''
    Serializa e salva a chave privada em disco
    '''
    path.parent.mkdir(parents=True, exist_ok=True)
    pem_bytes: bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem_bytes)


def _load_private_key(path: Path) -> RSAPrivateKey:
    '''
    Carrega uma chave privada previamente salva em disco.
    '''
    pem_bytes: bytes = path.read_bytes()
    private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    return private_key 


def _public_key_to_pem(public_key: RSAPublicKey) -> bytes:
    '''
    Serializa uma chave pública para o formato PEM (SubjectPublicKeyInfo).
    '''
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _distribute_public_key(
    base_dir: Path,
    owner_service: str,
    public_key: RSAPublicKey,
) -> None:
    '''
    Salva a chave pública dentro da pasta public_keys/
    '''
    pem_bytes: bytes = _public_key_to_pem(public_key)

    for service_name in SERVICES:
        target_dir: Path = _public_keys_dir_of(base_dir, service_name)
        target_dir.mkdir(parents=True, exist_ok=True)

        target_file: Path = target_dir / f"{owner_service}_public.pem"
        target_file.write_bytes(pem_bytes)


def ensure_keys(service_name: str, base_dir: Path) -> RSAPrivateKey:
    '''
    Garante que o serviço possua um par de chaves.

    - Se a chave privada ainda não existir em disco, gera um novo par RSA
    - Se já existir, apenas carrega a privada do disco

    '''
    keys_dir: Path = _service_keys_dir(base_dir, service_name)
    private_key_path: Path = keys_dir / PRIVATE_KEY_FILENAME

    if private_key_path.exists():
        print(f"[CRYPTO] Chaves de '{service_name}' já existem. Carregando do disco...")
        return _load_private_key(private_key_path)

    print(f"[CRYPTO] Nenhuma chave encontrada para '{service_name}'. Gerando novo par RSA...")
    private_key, public_key = _generate_key_pair()

    _save_private_key(private_key, private_key_path)
    _distribute_public_key(base_dir, service_name, public_key)

    print(f"[CRYPTO] Par de chaves de '{service_name}' gerado e distribuído com sucesso.")
    return private_key


def load_public_key(service_name: str, requester_service: str, base_dir: Path) -> RSAPublicKey | None:
 
    public_key_path: Path = _public_keys_dir_of(base_dir, requester_service) / f"{service_name}_public.pem"

    if not public_key_path.exists():
        return None

    pem_bytes: bytes = public_key_path.read_bytes()
    public_key = serialization.load_pem_public_key(pem_bytes)
    return public_key 


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    '''
    Serializa o payload de forma consistente, para que calculem
    o mesmo hash/assinatura sobre o mesmo conteúdo.
    '''
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(private_key: RSAPrivateKey, payload: dict[str, Any]) -> str:
    '''
    Gera o hash do payload e o assina com a chave privada do produtor.
    '''
    message: bytes = _canonical_bytes(payload)

    signature: bytes = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    return base64.b64encode(signature).decode("utf-8")


def verify_signature(public_key: RSAPublicKey, payload: dict[str, Any], signature_b64: str) -> bool:
    '''
    Verifica se a assinatura corresponde ao payload, usando a chave pública
    do produtor.
    '''
    try:
        message: bytes = _canonical_bytes(payload)
        signature: bytes = base64.b64decode(signature_b64)

        public_key.verify(
            signature,
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False
    except Exception as e:
        print(f"[CRYPTO] Erro inesperado ao verificar assinatura: {e}")
        return False