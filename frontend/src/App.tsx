import { useState, useEffect } from 'react'
import './App.css'
import { buscarProdutos, criarPedido, cancelarPedido } from './services/api'
import type { Produto, ItemPedidoInput } from "./types/inventory"
import type { Pedido, SnapshotEventData, PedidoEventData } from "./types/order";

export default function App() {
  const [produtos, setProdutos] = useState<Produto[]>([]);
  const [clientId, setClientId] = useState<string>("client-01");
  const [sseConectado, setSseConectado] = useState<boolean>(false);
  const [carrinho, setCarrinho] = useState<ItemPedidoInput[]>([]);
  const [pedidos, setPedidos] = useState<Pedido[]>([]);
  const [logsSse, setLogsSse] = useState<PedidoEventData[]>([]);
  useEffect(() => {
    async function carregarCatalogo(){
      try {
        const dados = await buscarProdutos();
        setProdutos(dados);
      } catch (erro) {
        console.error("Erro ao carregar produtos:", erro);
      }
    }

    carregarCatalogo();
}, []);
function upsertPedido(pedidoAtualizado: Pedido) {
  setPedidos((pedidosAtuais) => {
  const jaExiste = pedidosAtuais.some(
    (p) => p.order_id === pedidoAtualizado.order_id
  );

  if (jaExiste) {
    return pedidosAtuais.map((p) =>
      p.order_id === pedidoAtualizado.order_id ? pedidoAtualizado : p
  );
  } else {
    return [pedidoAtualizado, ...pedidosAtuais];
  }
});
}

useEffect(() => {
  if (!clientId) return;

  const es = new EventSource(`http://localhost:8000/stream/${clientId}`);

  es.onopen = () => setSseConectado(true);
  es.onerror = () => setSseConectado(false);

  es.addEventListener("snapshot", (event: MessageEvent) => {
    const dados: SnapshotEventData = JSON.parse(event.data);
    setPedidos(dados.pedidos);
  });

  es.addEventListener("pedido", (event: MessageEvent) => {
    const dados: PedidoEventData = JSON.parse(event.data);
    upsertPedido(dados.pedido)

    setLogsSse((logsAtuais) => [dados, ...logsAtuais]);

    buscarProdutos().then(setProdutos).catch(console.error);
  });
  return () => {
    es.close();
    setSseConectado(false);
  };
}, [clientId]);

function alterarQuantidade(produtoId: number, delta: number) {
  setCarrinho((atual) => {
    const existente = atual.find((item) => item.produto_id == produtoId);
    if (existente) {
      const novaQtd = existente.quantidade + delta;
      if (novaQtd <= 0) {
        return atual.filter((item) => item.produto_id !== produtoId);
      }
      return atual.map((item) =>
        item.produto_id === produtoId ? { ...item, quantidade: novaQtd } : item
      );
    } else if (delta > 0) {
      return [...atual, { produto_id: produtoId, quantidade: delta }];
    }
    return atual;
  });
}

async function handleFinalizarPedido(){
  if (carrinho.length === 0) return;

  try {
    const novoPedido = await criarPedido({ client_id: clientId, itens: carrinho });
    upsertPedido(novoPedido)
    setCarrinho([]);
  } catch (erro){
    console.error("Erro ao finalizar pedido:", erro);
  }
}

async function handleCancelarPedido(orderId: string){
  try {
    const pedidoCancelado = await cancelarPedido(orderId, clientId)
    upsertPedido(pedidoCancelado);
  } catch (erro){
    console.error("Erro ao cancelar pedido:", erro);
  }
}

return (
    <div style={{ padding: "20px", fontFamily: "sans-serif", maxWidth: "1200px", margin: "0 auto" }}>
      {/* 1. CABEÇALHO DO CLIENTE E STATUS DO SSE */}
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #ccc", paddingBottom: "15px", marginBottom: "20px" }}>
        <h1>E-Commerce Microservices</h1>
        <div style={{ display: "flex", alignItems: "center", gap: "15px" }}>
          <label>
            <strong>Cliente ID: </strong>
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              style={{ padding: "5px" }}
            />
          </label>
          <span>
            {sseConectado ? "🟢 SSE Conectado" : "🔴 SSE Desconectado"}
          </span>
        </div>
      </header>

      {/* 2. SEÇÃO DO CATÁLOGO E CARRINHO */}
      <section style={{ marginBottom: "30px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2>Catálogo de Produtos</h2>
          <button
            onClick={handleFinalizarPedido}
            disabled={carrinho.length === 0}
            style={{
              padding: "10px 20px",
              fontSize: "16px",
              cursor: carrinho.length === 0 ? "not-allowed" : "pointer",
              backgroundColor: carrinho.length === 0 ? "#ccc" : "#28a745",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
            }}
          >
            Finalizar Pedido ({carrinho.reduce((acc, item) => acc + item.quantidade, 0)} itens)
          </button>
        </div>

        {produtos.length === 0 ? (
          <p>Carregando produtos do estoque...</p>
        ) : (
          <div style={{ display: "flex", gap: "15px", flexWrap: "wrap" }}>
            {produtos.map((p) => {
              const qtdNoCarrinho = carrinho.find((item) => item.produto_id === p.id)?.quantidade || 0;
              return (
                <div
                  key={p.id}
                  style={{
                    border: "1px solid #ccc",
                    borderRadius: "8px",
                    padding: "15px",
                    width: "200px",
                  }}
                >
                  <h3 style={{ margin: "0 0 10px 0", textTransform: "capitalize" }}>{p.nome}</h3>
                  <p style={{ margin: "5px 0" }}>Categoria: {p.categoria}</p>
                  <p style={{ margin: "5px 0" }}><strong>R$ {p.preco.toFixed(2)}</strong></p>
                  <p style={{ margin: "5px 0" }}>Estoque: {p.quantidade}</p>

                  {/* Controles de + e - do Carrinho */}
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "10px" }}>
                    <button onClick={() => alterarQuantidade(p.id, -1)} disabled={qtdNoCarrinho === 0}>
                      -
                    </button>
                    <strong>{qtdNoCarrinho}</strong>
                    <button onClick={() => alterarQuantidade(p.id, 1)}>
                      +
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* 3. PAINEL DUPLO: PEDIDOS À ESQUERDA E LOGS SSE À DIREITA */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px" }}>
        
        {/* Coluna de Pedidos */}
        <section>
          <h2>Meus Pedidos ({pedidos.length})</h2>
          {pedidos.length === 0 ? (
            <p>Nenhum pedido realizado para {clientId}.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "15px" }}>
              {pedidos.map((ped) => (
                <div
                  key={ped.order_id}
                  style={{
                    border: "1px solid #bbb",
                    borderRadius: "8px",
                    padding: "15px",
                    backgroundColor: "#f9f9f9",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong>Pedido #{ped.order_id}</strong>
                    <span style={{ padding: "4px 8px", borderRadius: "4px", backgroundColor: "#e2e8f0", fontWeight: "bold" }}>
                      {ped.status}
                    </span>
                  </div>

                  {ped.valor_total != null && (
                    <p>Valor Total: <strong>R$ {Number(ped.valor_total).toFixed(2)}</strong></p>
                  )}

                  {ped.motivo && (
                    <p style={{ color: "#d9534f" }}>Motivo: {ped.motivo}</p>
                  )}

                  {ped.nota_fiscal && (
                    <p style={{ color: "#28a745" }}>Nota Fiscal / Entrega: <strong>{ped.nota_fiscal}</strong></p>
                  )}

                  {/* Botões de Ação do Pedido: Pagar (se tiver checkout_url) e Cancelar */}
                  <div style={{ display: "flex", gap: "10px", marginTop: "10px" }}>
                    {ped.checkout_url && ped.status === "AGUARDANDO_PAGAMENTO" && (
                      <a
                        href={ped.checkout_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          padding: "6px 12px",
                          backgroundColor: "#007bff",
                          color: "#fff",
                          textDecoration: "none",
                          borderRadius: "4px",
                        }}
                      >
                        💳 Abrir Link de Pagamento
                      </a>
                    )}

                    <button
                      onClick={() => handleCancelarPedido(ped.order_id)}
                      style={{
                        padding: "6px 12px",
                        backgroundColor: "#dc3545",
                        color: "#fff",
                        border: "none",
                        borderRadius: "4px",
                        cursor: "pointer",
                      }}
                    >
                      Cancelar Pedido
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Coluna de Logs em Tempo Real do RabbitMQ + SSE */}
        <section>
          <h2>Eventos em Tempo Real (SSE)</h2>
          <div
            style={{
              border: "1px solid #333",
              borderRadius: "8px",
              padding: "10px",
              backgroundColor: "#1e1e1e",
              color: "#00ff88",
              fontFamily: "monospace",
              height: "400px",
              overflowY: "auto",
            }}
          >
            {logsSse.length === 0 ? (
              <p style={{ color: "#888" }}>Aguardando eventos do RabbitMQ...</p>
            ) : (
              logsSse.map((log, index) => (
                <div key={index} style={{ marginBottom: "10px", borderBottom: "1px dashed #444", paddingBottom: "5px" }}>
                  <div><strong>[{log.status}]</strong> Pedido #{log.order_id}</div>
                  <div style={{ color: "#ccc", fontSize: "13px" }}>{log.detalhe}</div>
                </div>
              ))
            )}
          </div>
        </section>

      </div>
    </div>
  );}