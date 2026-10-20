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
        <h2>SUBSCRIPTION_ENGINE // EMAIL_NOTIFIER</h2>
        <p className="promocoes-subtitulo">
          Cadastre seu endereço de e-mail e selecione os canais de hardware para receber despachos de ofertas.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="promocoes-form">
        <div className="form-group">
          <label htmlFor="promo-email">
            <strong>TARGET_EMAIL:</strong>
          </label>
          <input
            id="promo-email"
            type="email"
            className="input-email-promocao"
            placeholder="operator@cyberspace.net"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={enviando}
            required
          />
        </div>

        <div className="form-group">
          <label>
            <strong>CHANNELS_OF_INTEREST:</strong>
          </label>
          {carregandoCategorias ? (
            <p className="texto-carregando">Escaneando categorias no estoque...</p>
          ) : categoriasDisponiveis.length === 0 ? (
            <p className="texto-carregando">Nenhum canal de hardware disponível.</p>
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
                  <span>[ {cat} ]</span>
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
          {enviando ? "PROCESSANDO..." : "[ REGISTRAR INSCRIÇÃO ]"}
        </button>

        {mensagemSucesso && (
          <div className="msg-promocao sucesso">
            [SUCCESS] {mensagemSucesso}
          </div>
        )}

        {mensagemErro && (
          <div className="msg-promocao erro">
            [ERROR] {mensagemErro}
          </div>
        )}
      </form>
    </section>
  );
}
