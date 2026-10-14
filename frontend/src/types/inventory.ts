interface Produto {
    nome: string;
    id: number;
    categoria: string;
    preco: number;
    quantidade: number;
}

interface ItemPedidoInput {
    produto_id: number;
    quantidade: number;
}

interface NovoPedidoPayload {
    client_id: number;
    itens: number;
}

