#!/usr/bin/env python3
"""Testes do corpus vetorial (spec 18 §1) — offline, sem chave de API."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.pop("OPENAI_API_KEY", None)      # força o caminho offline determinístico

from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.corpus.vetor import Corpus, anonimizar, embutir, DIM
from atena.corpus import ingestao

ONT = Ontologia()


def test_anonimiza_pii_antes_de_indexar():
    """O corpus indexa COMO conversamos, não quem é a pessoa (LGPD, spec 18 §1.6)."""
    t = anonimizar("Zap +55 (19) 99999-0001, email ana@x.com.br, CPF 123.456.789-00")
    assert "99999" not in t and "ana@x.com.br" not in t and "123.456" not in t
    assert "[telefone]" in t and "[email]" in t and "[cpf]" in t
    # token de URL não pode vazar para o índice
    assert "segredo" not in anonimizar("acesse https://x.com/a?token=segredo123")


def test_embedding_normalizado_e_deterministico():
    (v1,), modelo = embutir(["curso de engenharia de processos"])
    (v2,), _ = embutir(["curso de engenharia de processos"])
    assert modelo == "hashing-offline" and len(v1) == DIM
    assert list(v1) == list(v2)                       # determinístico
    norma = sum(x * x for x in v1) ** 0.5
    assert abs(norma - 1.0) < 1e-5                    # normalizado -> cosseno = produto escalar


def test_indexar_e_idempotente_e_busca_ordena():
    c = Corpus(":memory:")
    docs = [{"tipo": "qa", "chave": "k1", "texto": "o curso tem certificado da unicamp",
             "meta": {"curso": "FEQ-0480"}},
            {"tipo": "qa", "chave": "k2", "texto": "qual o valor da mensalidade do curso",
             "meta": {"curso": "FEQ-0480"}},
            {"tipo": "duvida", "chave": "k3", "texto": "existe desconto para servidor publico",
             "meta": {"curso": "FEQ-1000"}}]
    assert c.indexar(docs) == 3
    assert c.indexar(docs) == 0                       # mesma chave não duplica
    r = c.buscar("certificado da unicamp", k=3)
    assert r[0]["chave"] == "k1"                      # o mais parecido vem primeiro
    assert r[0]["escore"] >= r[-1]["escore"]          # ordenado desc
    assert len(c.buscar("qualquer coisa", k=2)) <= 2  # respeita o k
    # texto vazio não entra (não há o que indexar)
    assert c.indexar([{"tipo": "qa", "chave": "k4", "texto": "   "}]) == 0


def test_filtros_de_tipo_e_curso():
    c = Corpus(":memory:")
    c.indexar([{"tipo": "qa", "chave": "a", "texto": "aulas gravadas", "meta": {"curso": "FEQ-1000"}},
               {"tipo": "qa", "chave": "b", "texto": "aulas gravadas", "meta": {"curso": "FEQ-0480"}},
               {"tipo": "duvida", "chave": "c", "texto": "aulas gravadas", "meta": {"curso": "FEQ-1000"}}])
    assert {x["chave"] for x in c.buscar("aulas", k=9, curso="FEQ-1000")} == {"a", "c"}
    assert {x["chave"] for x in c.buscar("aulas", k=9, tipo="duvida")} == {"c"}
    assert {x["chave"] for x in c.buscar("aulas", k=9, tipo="qa", curso="FEQ-0480")} == {"b"}


def test_ingestao_extrai_qa_duvida_curso_e_episodio():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="Ana", telefone="+55 19 99999-0001", curso="FEQ-0480",
                    fase="Prospect", estado_conversa="qualificacao")
    est.add_conversa("c1", "lead", "O curso emite certificado da Unicamp no final?")
    est.add_conversa("c1", "agente", "Emite sim! O certificado é de Extensão pela Unicamp, "
                                     "emitido pela Extecamp ao concluir o curso.", status="enviado")
    est.add_conversa("c1", "lead", "ok")                      # curto: não vira QA
    est.add_conversa("c1", "agente", "Combinado!", status="enviado")
    est.add_conversa("c1", "lead", "Qual o prazo final para a inscrição deste curso?")
    est.add_conversa("c1", "agente", "Proposta ainda não enviada ao lead — não é precedente")
    did = est.add_duvida("c1", "Servidor público tem desconto?", contexto="x")
    est.responder_duvida(did, "Sim, 10% para alunos e ex-alunos Unicamp.")

    corpus = Corpus(":memory:")
    r = ingestao.rodar(est=est, ont=ONT, corpus=corpus)
    assert r["qa"]["novos"] == 1                # só o par com resposta ENVIADA e pergunta real
    assert r["duvida"]["novos"] == 1
    assert r["curso"]["novos"] >= 8             # portfólio da ontologia
    assert r["episodio"]["novos"] == 1

    qa = corpus.buscar("certificado", k=1, tipo="qa")[0]
    assert "certificado" in qa["texto"].lower()
    assert "Extecamp" in qa["meta"]["resposta"]           # resposta viaja no meta
    ep = corpus.buscar("certificado", k=1, tipo="episodio")[0]
    assert ep["meta"]["desfecho"] == "Prospect"           # rótulo p/ a avaliação (§3)
    assert "99999" not in ep["texto"]                     # PII some também no episódio
    assert ingestao.rodar(est=est, ont=ONT, corpus=corpus)["qa"]["novos"] == 0   # idempotente


def test_estatisticas_e_limpeza():
    c = Corpus(":memory:")
    c.indexar([{"tipo": "qa", "chave": "x", "texto": "pergunta de teste sobre o curso"},
               {"tipo": "curso", "chave": "y", "texto": "ficha do curso de teste"}])
    s = c.estatisticas()
    assert s["total"] == 2 and s["por_tipo"] == {"qa": 1, "curso": 1}
    assert s["dim"] == DIM and s["modelos"] == ["hashing-offline"]
    assert c.limpar(tipo="qa") == 1 and c.estatisticas()["total"] == 1


if __name__ == "__main__":
    casos = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for fn in casos:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            falhas += 1; print(f"FAIL  {fn.__name__}  {e}")
        except Exception as e:
            falhas += 1; print(f"ERRO  {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{len(casos) - falhas}/{len(casos)} passaram.")
    sys.exit(1 if falhas else 0)
