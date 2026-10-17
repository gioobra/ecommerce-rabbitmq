import type { ItemPedidoInput } from "./inventory";

export interface Pedido{
    order_id: string;
    client_id: string;
    itens: ItemPedidoInput[];
    status: string;
    valor_total?: number | null;
    itens_confirmados?: Array<{
        produto_id: number;
        nome: string;
        quantidade: number;
        preco_unitario: number;
        subtotal: number;
    }> | null;
    itens_faltantes?: Array<{
        produto_id?: number;
        solicitado?: number;
        disponivel?: number;
        motivo: string;
    }> | null;
    checkout_url?: string | null;
    nota_fiscal?: string | null;
    motivo?: string | null;
    criado_em?: string;
    atualizado_em?: string;
    historico?: Array<{
        status: string;
        detalhe: string;
        timestamp: string | number;
    }>;
}

export interface SnapshotEventData{
    pedidos: Pedido[];
}

export interface PedidoEventData{
    order_id: string;
    status: string;
    detalhe: string;
    timestamp: string | number;
    pedido: Pedido;
}