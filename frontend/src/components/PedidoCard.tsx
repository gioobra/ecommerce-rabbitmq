import type { Pedido } from "../types/order";

interface PedidoCardProps {
  pedido: Pedido;
  onCancelar: (orderId: string) => void;
}

// Estados em que o pedido já terminou ou não pode mais ser cancelado
const ESTADOS_NAO_CANCELAVEIS = [
  "PAGAMENTO_APROVADO",
  "ENVIADO",
  "PEDIDO_ENVIADO",
  "CANCELADO",
  "ESTOQUE_INDISPONIVEL",
  "PAGAMENTO_RECUSADO",
];

function obterClasseStatus(status: string): string {
  const s = status.toUpperCase();
  if (s.includes("ENVIADO") || s.includes("APROVADO")) return "status-sucesso";
  if (
    s.includes("CANCELADO") ||
    s.includes("INDISPONIVEL") ||
    s.includes("RECUSADO")
  ) {
    return "status-erro";
  }
  return "status-processando";
}

export function PedidoCard({ pedido, onCancelar }: PedidoCardProps) {
  const podeCancelar = !ESTADOS_NAO_CANCELAVEIS.includes(
    pedido.status.toUpperCase()
  );

  return (
    <div className="pedido-card">
      <div className="pedido-topo">
        <strong>Pedido #{pedido.order_id}</strong>
        <span className={`status-badge ${obterClasseStatus(pedido.status)}`}>
          {pedido.status}
        </span>
      </div>

      {/* Lista resumida dos itens solicitados */}
      <div className="pedido-detalhes">
        {pedido.valor_total != null && (
          <p>
            Valor Total:{" "}
            <strong>R$ {Number(pedido.valor_total).toFixed(2)}</strong>
          </p>
        )}

        {pedido.itens_confirmados && pedido.itens_confirmados.length > 0 && (
          <ul className="lista-itens-pedido">
            {pedido.itens_confirmados.map((item) => (
              <li key={item.produto_id}>
                {item.quantidade}x {item.nome} (R${" "}
                {Number(item.subtotal).toFixed(2)})
              </li>
            ))}
          </ul>
        )}

        {pedido.motivo && <p className="texto-erro">Motivo: {pedido.motivo}</p>}

        {pedido.itens_faltantes && pedido.itens_faltantes.length > 0 && (
          <div className="texto-erro">
            <strong>Itens em falta:</strong>
            <ul className="lista-itens-pedido">
              {pedido.itens_faltantes.map((f, idx) => (
                <li key={idx}>
                  Produto #{f.produto_id ?? "?"}: solicitou {f.solicitado},
                  disponível {f.disponivel} ({f.motivo})
                </li>
              ))}
            </ul>
          </div>
        )}

        {pedido.nota_fiscal && (
          <p className="texto-sucesso">
            📦 Nota Fiscal / Rastreio: <strong>{pedido.nota_fiscal}</strong>
          </p>
        )}
      </div>

      {/* Botões de ação (Pagar e Cancelar) */}
      <div className="pedido-acoes">
        {pedido.checkout_url && podeCancelar && (
          <a
            href={pedido.checkout_url}
            target="_blank"
            rel="noreferrer"
            className="btn-pagar"
          >
            💳 Abrir Link de Pagamento
          </a>
        )}

        {podeCancelar && (
          <button
            className="btn-cancelar"
            onClick={() => onCancelar(pedido.order_id)}
          >
            Cancelar Pedido
          </button>
        )}
      </div>
    </div>
  );
}