interface Pedido{
    order_id: number
    cliente_id: number
    itens: Array<string>
    status: string
    valor_total: number
    itens_confirmados: Array<string>
    itens_faltantes: Array<string>
    checkout_url: string
    nota_fiscal: string
    motivo: string
    criado_em: string
    atualizado_em: string
    historico: Array<string>
}

interface SnapshotEventData{
    pedidos: Pedido[]
}

interface PedidoEventData{
    order_id: number
    status: string
    detalhe: string
    timestamp: number
    pedido: Pedido
}