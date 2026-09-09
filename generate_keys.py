import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SERVICES: list[str] = [
    "order_service",
    "inventory_service",
    "payment_service",
    "delivery_service"
]

def setup_pki() -> None:
    generated_keys: dict[str, tuple[bytes, bytes]] = {}

    # 1. Gera pares de chaves para cada microsserviço
    for service in SERVICES:
        os.makedirs(service, exist_ok=True)
        os.makedirs(os.path.join(service, "public_keys"), exist_ok=True)

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        priv_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        pub_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        generated_keys[service] = (priv_pem, pub_pem)

        # Salva a chave privada na pasta do serviço dono
        with open(os.path.join(service, "private_key.pem"), "wb") as f:
            f.write(priv_pem)

    # 2. Distribui as chaves públicas de todos para as pastas de cada serviço
    for target_service in SERVICES:
        pub_dir = os.path.join(target_service, "public_keys")
        for source_service, (_, pub_pem) in generated_keys.items():
            file_path = os.path.join(pub_dir, f"{source_service}.pem")
            with open(file_path, "wb") as f:
                f.write(pub_pem)

    print("[PKI SETUP] Pastas, chaves privadas e anéis de chaves públicas gerados com sucesso!")

if __name__ == '__main__':
    setup_pki()