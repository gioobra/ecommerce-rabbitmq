import type { Produto, NovoPedidoPayload, InscricaoPromocaoPayload } from "../types/inventory";
import type { Pedido } from "../types/order";

const API_URL = "http://localhost:8000";

export async function buscarProdutos(): Promise<Produto[]>{
    const response = await fetch(`${API_URL}/produtos`);

    if (!response.ok){
        throw new Error(`Erro na API: status ${response.status}`);
    }
    
    const dados: Produto[] = await response.json();
    return dados;
}

export async function buscarCategorias(): Promise<string[]>{
    const response = await fetch(`${API_URL}/categorias`);

    if (!response.ok){
        throw new Error(`Erro ao buscar categorias: status ${response.status}`);
    }

    const dados: string[] = await response.json();
    return dados;
}

export async function criarPedido(payload:NovoPedidoPayload): Promise<Pedido> {
    const response = await fetch(`${API_URL}/pedidos`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
    });
    
    if (!response.ok){
        throw new Error(`Falha ao criar pedido: status ${response.status}`);
    }
    const dados: Pedido = await response.json();
    return dados;
}

export async function cancelarPedido(orderID: string, clientID: string): Promise<Pedido>{
    const response = await fetch(`${API_URL}/pedidos/${orderID}?client_id=${clientID}`, {
        method: "DELETE"
    });
    
    if (!response.ok){
        throw new Error(`Falha ao cancelar pedido: status ${response.status}`);
    }

    const dados: Pedido = await response.json();
    return dados;
}

export async function cadastrarInteressePromocao(payload: InscricaoPromocaoPayload): Promise<void> {
    const response = await fetch(`${API_URL}/promocoes/interesse`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
    });

    if (!response.ok) {
        throw new Error(`Falha ao cadastrar interesse: status ${response.status}`);
    }
}