export interface Produto {
    nome: string;
    id: number;
    categoria: string;
    preco: number;
    quantidade: number;
}

export interface ItemPedidoInput {
    produto_id: number;
    quantidade: number;
}

export interface NovoPedidoPayload {
    client_id: string;
    itens: ItemPedidoInput[];
}

export interface InscricaoPromocaoPayload {
    client_id?: string;
    email: string;
    categorias: string[];
}

