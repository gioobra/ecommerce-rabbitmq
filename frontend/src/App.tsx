import { useState, useEffect, useRef } from "react";
import "./App.css";
import { buscarProdutos, criarPedido, cancelarPedido } from "./services/api";
import type { Produto, ItemPedidoInput } from "./types/inventory";
import type {
  Pedido,
  SnapshotEventData,
  PedidoEventData,
} from "./types/order";
import { ProdutoCard } from "./components/ProdutoCard";
import { PedidoCard } from "./components/PedidoCard";
import { SseLogConsole } from "./components/SseLogConsole";
import { PromocoesForm } from "./components/PromocoesForm";

export default function App() {
  const [produtos, setProdutos] = useState<Produto[]>([]);
  const [clientId, setClientId] = useState<string>("cliente-01");
  const [sseConectado, setSseConectado] = useState<boolean>(false);
  const [carrinho, setCarrinho] = useState<ItemPedidoInput[]>([]);
  const [pedidos, setPedidos] = useState<Pedido[]>([]);
  const [logsSse, setLogsSse] = useState<PedidoEventData[]>([]);
  const openedPaymentTabsRef = useRef<Set<string>>(new Set());

  // 1. Carrega o catálogo ao abrir a página
  useEffect(() => {
    async function carregarCatalogo() {
      try {
        const dados = await buscarProdutos();
        setProdutos(dados);
      } catch (erro) {
        console.error("Erro ao carregar produtos:", erro);
      }
    }
    carregarCatalogo();
  }, []);

  // 2. Faz upsert de um pedido na lista de estado
  function upsertPedido(pedidoAtualizado: Pedido) {
    setPedidos((pedidosAtuais) => {
      const jaExiste = pedidosAtuais.some(
        (p) => p.order_id === pedidoAtualizado.order_id
      );
      if (jaExiste) {
        return pedidosAtuais.map((p) =>
          p.order_id === pedidoAtualizado.order_id ? pedidoAtualizado : p
        );
      }
      return [pedidoAtualizado, ...pedidosAtuais];
    });
  }

  // 3. Conecta ao SSE do Gateway quando o clientId muda
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
      upsertPedido(dados.pedido);
      setLogsSse((logsAtuais) => [dados, ...logsAtuais]);
      buscarProdutos().then(setProdutos).catch(console.error);

      // Requisito: ao finalizar pedido, recebe URL do Mock de Pagamento e abre nova aba para simular pagamento
      if (
        dados.pedido.checkout_url &&
        !openedPaymentTabsRef.current.has(dados.order_id)
      ) {
        openedPaymentTabsRef.current.add(dados.order_id);
        window.open(dados.pedido.checkout_url, "_blank");
      }
    });

    return () => {
      es.close();
      setSseConectado(false);
    };
  }, [clientId]);

  // 4. Handlers de interação do usuário
  function alterarQuantidade(produtoId: number, delta: number) {
    setCarrinho((atual) => {
      const existente = atual.find((item) => item.produto_id === produtoId);
      if (existente) {
        const novaQtd = existente.quantidade + delta;
        if (novaQtd <= 0) {
          return atual.filter((item) => item.produto_id !== produtoId);
        }
        return atual.map((item) =>
          item.produto_id === produtoId
            ? { ...item, quantidade: novaQtd }
            : item
        );
      } else if (delta > 0) {
        return [...atual, { produto_id: produtoId, quantidade: delta }];
      }
      return atual;
    });
  }

  async function handleFinalizarPedido() {
    if (carrinho.length === 0) return;
    try {
      const novoPedido = await criarPedido({
        client_id: clientId,
        itens: carrinho,
      });
      upsertPedido(novoPedido);
      setCarrinho([]);
    } catch (erro) {
      console.error("Erro ao finalizar pedido:", erro);
    }
  }

  async function handleCancelarPedido(orderId: string) {
    try {
      const pedidoCancelado = await cancelarPedido(orderId, clientId);
      upsertPedido(pedidoCancelado);
    } catch (erro) {
      console.error("Erro ao cancelar pedido:", erro);
    }
  }

  const totalItensCarrinho = carrinho.reduce(
    (acc, item) => acc + item.quantidade,
    0
  );

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="header-brand">
          <h1>M3RC4D0 L33T</h1>
          <span className="brand-tag">[CYB3R_ST0R3 // ROOT_ACCESS]</span>
        </div>
        <div className="cliente-bar">
          <label>
            <strong>CLIENT_ID: </strong>
            <input
              className="input-cliente"
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="ex: cliente-01"
            />
          </label>
          <span 
            className={`sse-status ${sseConectado ? "online" : "offline"}`}
            title="Conexão Server-Sent Events (SSE) em tempo real com o API Gateway"
          >
            <span className="sse-dot"></span>
            {sseConectado ? "SSE: CONNECTED" : "SSE: DISCONNECTED"}
          </span>
        </div>
      </header>

      <section className="catalogo-section">
        <div className="catalogo-topo">
          <h2>C4T4L0G0 // HARDWARE_INVENTORY</h2>
          <button
            className="btn-finalizar"
            onClick={handleFinalizarPedido}
            disabled={carrinho.length === 0}
          >
            EXECUTAR CHECKOUT ({totalItensCarrinho} itens)
          </button>
        </div>

        {produtos.length === 0 ? (
          <p className="texto-carregando">Carregando catálogo do estoque...</p>
        ) : (
          <div className="catalogo-grid">
            {produtos.map((p) => {
              const qtdNoCarrinho =
                carrinho.find((i) => i.produto_id === p.id)?.quantidade || 0;
              return (
                <ProdutoCard
                  key={p.id}
                  produto={p}
                  qtdNoCarrinho={qtdNoCarrinho}
                  onAlterarQuantidade={alterarQuantidade}
                />
              );
            })}
          </div>
        )}
      </section>

      <PromocoesForm clientId={clientId} />

      <div className="painel-inferior">
        <section className="pedidos-section">
          <h2>HISTÓRICO DE PEDIDOS // PID_TRACKER ({pedidos.length})</h2>
          {pedidos.length === 0 ? (
            <p className="texto-vazio">Nenhum pedido registrado para o operador {clientId}.</p>
          ) : (
            <div className="pedidos-lista">
              {pedidos.map((ped) => (
                <PedidoCard
                  key={ped.order_id}
                  pedido={ped}
                  onCancelar={handleCancelarPedido}
                />
              ))}
            </div>
          )}
        </section>

        <SseLogConsole logs={logsSse} onLimparLogs={() => setLogsSse([])} />
      </div>
    </div>
  );
}