import type { Produto } from "../types/inventory";

interface ProdutoCardProps {
  produto: Produto;
  qtdNoCarrinho: number;
  onAlterarQuantidade: (produtoId: number, delta: number) => void;
}

const IMAGENS_PRODUTOS: Record<string, string> = {
  notebook: "/produtos/notebook.png",
  mouse: "/produtos/mouse.png",
  teclado: "/produtos/teclado.png",
  keyboard: "/produtos/keyboard.png",
  monitor: "/produtos/monitor.png",
  headset: "/produtos/headset.png",
  headphones: "/produtos/headphones.png",
  webcam: "/produtos/webcam.png",
};

export function ProdutoCard({
  produto,
  qtdNoCarrinho,
  onAlterarQuantidade,
}: ProdutoCardProps) {
  const chave = produto.nome.toLowerCase().trim();
  const imagemSrc = IMAGENS_PRODUTOS[chave] || `/produtos/${chave}.png`;

  return (
    <div className="produto-card">
      <div className="produto-imagem-container">
        <img
          src={imagemSrc}
          alt={produto.nome}
          className="produto-imagem"
          onError={(e) => {
            (e.currentTarget as HTMLElement).style.display = "none";
          }}
        />
      </div>

      <div className="produto-header">
        <h3 className="produto-nome">{produto.nome}</h3>
        <span className="produto-categoria">{produto.categoria}</span>
      </div>

      <p className="produto-preco">R$ {produto.preco.toFixed(2)}</p>
      <p className="produto-estoque">
        Disponível em estoque: <strong>{produto.quantidade}</strong>
      </p>

      <div className="produto-controles">
        <button
          className="btn-qtd"
          onClick={() => onAlterarQuantidade(produto.id, -1)}
          disabled={qtdNoCarrinho === 0}
        >
          -
        </button>
        <span className="qtd-valor">{qtdNoCarrinho}</span>
        <button
          className="btn-qtd"
          onClick={() => onAlterarQuantidade(produto.id, 1)}
        >
          +
        </button>
      </div>
    </div>
  );
}