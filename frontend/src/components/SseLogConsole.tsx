import type { PedidoEventData } from "../types/order";

interface SseLogConsoleProps {
  logs: PedidoEventData[];
  onLimparLogs: () => void;
}

export function SseLogConsole({ logs, onLimparLogs }: SseLogConsoleProps) {
  return (
    <section className="sse-section">
      <div className="sse-header">
        <h2>Eventos RabbitMQ (SSE)</h2>
        {logs.length > 0 && (
          <button className="btn-limpar-logs" onClick={onLimparLogs}>
            Limpar
          </button>
        )}
      </div>

      <div className="terminal-sse">
        {logs.length === 0 ? (
          <p className="terminal-vazio">
            Aguardando eventos dos microsserviços...
          </p>
        ) : (
          logs.map((log, index) => (
            <div key={`${log.order_id}-${index}`} className="log-item">
              <div className="log-topo">
                <span className="log-status">[{log.status}]</span>
                <span className="log-id">Pedido #{log.order_id}</span>
              </div>
              <div className="log-detalhe">{log.detalhe}</div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}