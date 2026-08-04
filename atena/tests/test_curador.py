#!/usr/bin/env python3
"""
Agente Curador (spec 18): transforma resolução HUMANA em lição do corpus.

O valor está no FILTRO. Copiar toda fala humana pioraria a recuperação — a maior
parte é operacional ("já te retorno", "bom dia"). Estes testes fixam o que entra,
o que fica de fora e a prioridade da lição sobre precedente comum.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.pop("OPENAI_API_KEY", None)      # corpus offline determinístico

from atena.core.estado import Estado
from atena.corpus.vetor import Corpus
from atena.corpus import curador
from atena.corpus import recuperacao as R


def _base():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="Ana", telefone="+55 19 90000", curso="FEQ-1000",
                    fase="Prospect", estado_conversa="aguardando_resposta")
    return est


def test_filtro_barato_descarta_ruido():
    assert not curador._fala_util("ok")
    assert not curador._fala_util("Bom dia!")
    assert not curador._fala_util("Vou verificar com a coordenação e já te retorno.")
    assert not curador._fala_util("[template feq_lead] FEQ-0480")
    assert curador._fala_util(
        "Após a inscrição, o pagamento por boleto leva de 1 a 2 dias úteis para ser "
        "reconhecido pelo sistema; no cartão, algumas horas.")


def test_candidatas_so_pega_fala_humana_com_pergunta_antes():
    est = _base()
    # ruído: humano, mas sem pergunta antes
    est.add_conversa("c1", "agente", "Olá! Sou o Vitor e vou te acompanhar por aqui, tudo bem?",
                     autor="humano:vitor", status="enviado")
    # o par que interessa
    est.add_conversa("c1", "lead", "Paguei a taxa mas não recebi nada, o que faço?")
    est.add_conversa("c1", "agente",
                     "O boleto leva de 1 a 2 dias úteis para o sistema reconhecer. Depois disso "
                     "você acessa a área do aluno da Extecamp para enviar os documentos.",
                     autor="humano:vitor", status="enviado")
    # fala do LLM não é lição (não foi humano que resolveu)
    est.add_conversa("c1", "lead", "E quanto custa o curso?")
    est.add_conversa("c1", "agente", "O curso sai em 20x de R$ 892,81 ou R$ 17.856,13 à vista.",
                     autor="llm", status="enviado")
    cands = curador.candidatas(est)
    assert len(cands) == 1
    assert "Paguei a taxa" in cands[0]["pergunta"]
    assert cands[0]["autor"] == "humano:vitor"


def test_edicao_humana_e_coordenacao_tambem_contam():
    """Quando o humano CORRIGE o texto do LLM, a correção é o sinal — e a resposta da
    coordenação é fonte autorizada. As duas entram como candidatas."""
    est = _base()
    est.add_conversa("c1", "lead", "Consigo assistir depois se faltar em uma aula?")
    est.add_conversa("c1", "agente",
                     "Sim: as aulas ao vivo ficam gravadas e liberadas na plataforma, então dá "
                     "para assistir depois no seu ritmo.", autor="llm+edicao:vitor", status="enviado")
    est.add_conversa("c1", "lead", "E tem desconto para servidor público?")
    est.add_conversa("c1", "agente",
                     "Não há desconto específico para servidor; o desconto vigente é de 10% para "
                     "alunos e ex-alunos da Unicamp.", autor="coordenacao", status="enviado")
    assert len(curador.candidatas(est)) == 2


def test_curar_grava_licao_e_e_idempotente():
    est = _base()
    est.add_conversa("c1", "lead", "Me inscrevi e não recebi retorno nenhum, é normal?")
    est.add_conversa("c1", "agente",
                     "É normal: a Secretaria de Extensão só entra em contato por e-mail após o "
                     "encerramento do período de inscrições, com as orientações seguintes.",
                     autor="humano:vitor", status="enviado")
    corpus = Corpus(":memory:")
    aceita = lambda par: {"util": True, "motivo": "processo recorrente",
                          "pergunta": "Depois de me inscrever, quando recebo retorno?",
                          "resposta": par["resposta"], "tema": "retorno pós-inscrição",
                          "revisar": False}
    r = curador.curar(est=est, corpus=corpus, julgador=aceita)
    assert r["licoes"] == 1 and r["descartadas"] == 0
    achado = corpus.buscar("quando recebo retorno da inscrição", k=1, tipo="licao")[0]
    assert achado["meta"]["origem"] == "humano"
    assert achado["meta"]["tema"] == "retorno pós-inscrição"
    assert curador.curar(est=est, corpus=corpus, julgador=aceita)["licoes"] == 0   # não duplica


def test_juizo_negativo_nao_polui_o_corpus():
    est = _base()
    est.add_conversa("c1", "lead", "Pode me ligar amanhã de manhã por favor?")
    est.add_conversa("c1", "agente",
                     "Claro, combinamos para amanhã às 10h então. Vou deixar anotado aqui na "
                     "sua ficha para não esquecer, pode ficar tranquila.",
                     autor="humano:vitor", status="enviado")
    corpus = Corpus(":memory:")
    recusa = lambda par: {"util": False, "motivo": "combinação de horário, caso único"}
    r = curador.curar(est=est, corpus=corpus, julgador=recusa)
    assert r["licoes"] == 0 and r["descartadas"] == 1
    assert corpus.estatisticas()["total"] == 0


def test_licao_tem_prioridade_sobre_precedente_comum():
    """A resolução humana deve vir antes do que o próprio agente já respondeu."""
    corpus = Corpus(":memory:")
    corpus.indexar([
        {"tipo": "qa", "chave": "qa:x", "texto": "prazo de pagamento da taxa de inscricao",
         "meta": {"curso": "FEQ-1000", "resposta": "R" * 60}},
        {"tipo": "licao", "chave": "licao:y", "texto": "prazo de pagamento da taxa de inscricao",
         "meta": {"curso": "FEQ-1000", "origem": "humano", "resposta": "L" * 60}},
    ])
    ps = R.precedentes("prazo de pagamento da taxa de inscricao", curso="FEQ-1000", corpus=corpus)
    assert ps and ps[0]["tipo"] == "licao"
    bloco = R.bloco_prompt("prazo de pagamento da taxa de inscricao", curso="FEQ-1000", corpus=corpus)
    assert "LIÇÃO" in bloco       # o prompt diz ao agente de onde veio


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
