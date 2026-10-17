#!/usr/bin/env bash
set -e

# Diretório raiz do projeto
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Cores ANSI
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GW="\033[1;36m"      # Ciano (Gateway :8000)
C_INV="\033[1;32m"     # Verde (Estoque :8001)
C_PAY="\033[1;33m"     # Amarelo (Pagamento :8002)
C_MOCK="\033[1;35m"    # Magenta (Mock Payment :9000)
C_DEL="\033[1;34m"     # Azul (Entrega)
C_FRONT="\033[1;96m"   # Ciano claro (Frontend :5173)

# Detecta ambiente virtual Python
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
    UVICORN="$ROOT_DIR/.venv/bin/uvicorn"
else
    PYTHON="python3"
    UVICORN="uvicorn"
fi

export PYTHONUNBUFFERED=1

PIDS=()

cleanup() {
    echo -e "\n${C_BOLD}🛑 Encerrando todos os serviços...${C_RESET}"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo -e "${C_BOLD}✅ Todos os serviços foram encerrados.${C_RESET}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Função auxiliar para rodar um serviço com prefixo colorido nos logs
run_service() {
    local name="$1"
    local color="$2"
    shift 2
    (
        "$@" 2>&1 | sed -u "s/^/$(printf "${color}[%-12s]${C_RESET} " "$name")/"
    ) &
    PIDS+=($!)
}

echo -e "${C_BOLD}🐰 1/3 Verificando RabbitMQ (Docker)...${C_RESET}"
if command -v docker >/dev/null 2>&1; then
    docker compose up -d rabbitmq
    echo -n "   Aguardando RabbitMQ ficar saudável"
    for _ in {1..30}; do
        if docker inspect -f '{{.State.Health.Status}}' ecommerce-rabbitmq 2>/dev/null | grep -q "healthy"; then
            echo -e " ${C_INV}OK!${C_RESET}"
            break
        fi
        echo -n "."
        sleep 1
    done
    echo ""
fi

echo -e "${C_BOLD}🔑 2/3 Sincronizando chaves RSA entre os microsserviços...${C_RESET}"
"$PYTHON" -c "
import sys
from pathlib import Path
sys.path.insert(0, 'shared')
from utils import ensure_keys, SERVICES
base = Path('.').resolve()
for svc in SERVICES:
    ensure_keys(svc, base)
"

echo -e "${C_BOLD}🚀 3/3 Iniciando serviços (Pressione Ctrl+C para parar tudo)...${C_RESET}\n"

# 1. MS Estoque (:8001)
run_service "INVENTORY" "$C_INV" \
    "$UVICORN" inventory_service.inventory_app:app --port 8001 --log-level info

# 2. Mock Payment (:9000)
run_service "MOCK-PAY" "$C_MOCK" \
    "$UVICORN" mock_payment.mock_app:app --port 9000 --log-level info

# 3. MS Pagamento (:8002)
run_service "PAYMENT" "$C_PAY" \
    "$UVICORN" payment_service.payment_app:app --port 8002 --log-level info

# 4. MS Entrega (Consumer RabbitMQ)
run_service "DELIVERY" "$C_DEL" \
    "$PYTHON" delivery_service/delivery_main.py

# 5. API Gateway / Pedidos (:8000)
run_service "GATEWAY" "$C_GW" \
    "$UVICORN" order_service.gateway_app:app --port 8000 --log-level info

# 6. Frontend Vite (:5173) - desative passando --no-frontend se quiser só o backend
if [[ "$*" != *"--no-frontend"* ]] && [ -d "$ROOT_DIR/frontend" ]; then
    run_service "FRONTEND" "$C_FRONT" \
        npm --prefix "$ROOT_DIR/frontend" run dev
fi

echo -e "${C_BOLD}🌐 Endpoints ativos:${C_RESET}"
echo -e "   • Frontend:      http://localhost:5173"
echo -e "   • API Gateway:   http://localhost:8000"
echo -e "   • MS Estoque:    http://localhost:8001"
echo -e "   • MS Pagamento:  http://localhost:8002"
echo -e "   • Mock Payment:  http://localhost:9000"
echo -e "   • RabbitMQ UI:   http://localhost:15672 (guest/guest)\n"

wait
