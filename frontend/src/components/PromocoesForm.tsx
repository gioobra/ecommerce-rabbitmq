import { useState, useEffect, type FormEvent } from "react";
import { buscarCategorias, cadastrarInteressePromocao } from "../services/api";

interface PromocoesFormProps {
  clientId?: string;
}

export function PromocoesForm({ clientId }: PromocoesFormProps) {
  const [email, setEmail] = useState<string>("");
  const [categoriasDisponiveis, setCategoriasDisponiveis] = useState<string[]>([]);
  const [categoriasSelecionadas, setCategoriasSelecionadas] = useState<string[]>([]);
  const [carregandoCategorias, setCarregandoCategorias] = useState<boolean>(true);
  const [enviando, setEnviando] = useState<boolean>(false);
  const [mensagemSucesso, setMensagemSucesso] = useState<string | null>(null);
  const [mensagemErro, setMensagemErro] = useState<string | null>(null);

  useEffect(() => {
    async function carregar() {
      try {
        setCarregandoCategorias(true);
        const cats = await buscarCategorias();
        setCategoriasDisponiveis(cats);
        // Deixa todas marcadas por padrão para conveniência
        setCategoriasSelecionadas(cats);
      } catch (err) {
        console.error("Erro ao carregar categorias:", err);
        setMensagemErro("Não foi possível carregar as categorias de promoção.");
      } finally {
        setCarregandoCategorias(false);
      }
    }
    carregar();
  }, []);

  function toggleCategoria(cat: string) {
    setCategoriasSelecionadas((atuais) => {
      if (atuais.includes(cat)) {
        return atuais.filter((c) => c !== cat);
      }
      return [...atuais, cat];
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setMensagemSucesso(null);
    setMensagemErro(null);

    const emailLimpo = email.trim();
    if (!emailLimpo || !emailLimpo.includes("@")) {
      setMensagemErro("Por favor, informe um endereço de e-mail válido.");
      return;
    }

    if (categoriasSelecionadas.length === 0) {
      setMensagemErro("Selecione pelo menos uma categoria de interesse.");
      return;
    }

    try {
      setEnviando(true);
      await cadastrarInteressePromocao({
        email: emailLimpo,
        categorias: categoriasSelecionadas,
        client_id: clientId,
      });
      setMensagemSucesso(
        `Inscrição realizada com sucesso! As melhores ofertas serão enviadas para ${emailLimpo}.`
      );
      setEmail("");
    } catch (err) {
      console.error("Erro ao cadastrar interesse:", err);
      setMensagemErro("Falha ao cadastrar interesse. Tente novamente mais tarde.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="promocoes-section">
      <div className="promocoes-header">
        <h2>📬 Inscrição de Promoções</h2>
        <p className="promocoes-subtitulo">
          Cadastre seu e-mail e escolha as categorias para receber ofertas exclusivas diretamente na sua caixa de entrada.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="promocoes-form">
        <div className="form-group">
          <label htmlFor="promo-email">
            <strong>Seu E-mail:</strong>
          </label>
          <input
            id="promo-email"
            type="email"
            className="input-email-promocao"
            placeholder="exemplo@dominio.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={enviando}
            required
          />
        </div>

        <div className="form-group">
          <label>
            <strong>Categorias de Interesse:</strong>
          </label>
          {carregandoCategorias ? (
            <p className="texto-carregando">Carregando categorias disponíveis...</p>
          ) : categoriasDisponiveis.length === 0 ? (
            <p className="texto-carregando">Nenhuma categoria encontrada no estoque.</p>
          ) : (
            <div className="categorias-checkboxes">
              {categoriasDisponiveis.map((cat) => (
                <label key={cat} className="checkbox-item">
                  <input
                    type="checkbox"
                    checked={categoriasSelecionadas.includes(cat)}
                    onChange={() => toggleCategoria(cat)}
                    disabled={enviando}
                  />
                  <span>{cat}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        <button
          type="submit"
          className="btn-inscrever-promocao"
          disabled={enviando || carregandoCategorias || categoriasSelecionadas.length === 0}
        >
          {enviando ? "Cadastrando..." : "Cadastrar para Receber Ofertas"}
        </button>

        {mensagemSucesso && (
          <div className="msg-promocao sucesso">
            ✅ {mensagemSucesso}
          </div>
        )}

        {mensagemErro && (
          <div className="msg-promocao erro">
            ⚠️ {mensagemErro}
          </div>
        )}
      </form>
    </section>
  );
}
