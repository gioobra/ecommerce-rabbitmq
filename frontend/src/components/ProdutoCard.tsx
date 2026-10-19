import type { Produto } from "../types/inventory";

interface ProdutoCardProps {
  produto: Produto;
  qtdNoCarrinho: number;
  onAlterarQuantidade: (produtoId: number, delta: number) => void;
}

export function ProdutoCard({
  produto,
  qtdNoCarrinho,
  onAlterarQuantidade,
}: ProdutoCardProps) {
  return (
    <div className="produto-card">
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