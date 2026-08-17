#!/usr/bin/env python3
"""
Testes do Revisor ontológico + serviço (offline, sem rede).
Roda com:  pytest atena/tests/test_revisor.py   OU   python3 atena/tests/test_revisor.py
"""
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root no path

from atena.ontologia.servico import Ontologia
from atena.revisor.ontologico import revisar_estrutural

ONT = Ontologia()
HOJE_REF = datetime.date(2026, 6, 26)  # data fixa p/ testes de captabilidade


def _ok(codigo, texto):
    return revisar_estrutural(codigo, texto, ONT)["aprovado"]


# ----------------------------------------------------------------- grounding ok
def test_mensagem_correta_aprovada():
    assert _ok("FEQ-0480", "O FEQ-0480 são 19x de R$ 866,09, ou R$ 16.455,75 à vista.")


def test_carga_real_aprovada():
    assert _ok("FEQ-0480", "São 368 horas de curso.")


def test_data_real_aprovada():
    assert _ok("FEQ-0480", "As inscrições vão até 03/01/2027.")


# ------------------------------------------------------------- bloqueios (fatos)
def test_preco_inventado_bloqueia():
    assert not _ok("FEQ-0480", "O curso custa R$ 999,00.")


def test_data_inventada_bloqueia():
    assert not _ok("FEQ-0480", "As inscrições vão até 01/01/2030.")


def test_parcelas_incoerentes_bloqueia():
    # valor existe na base, mas o nº de parcelas não — a v1 deixava passar.
    assert not _ok("FEQ-0480", "Você pode pagar em 30x de R$ 866,09.")


def test_carga_inventada_bloqueia():
    assert not _ok("FEQ-0480", "O curso tem 500 horas no total.")


def test_carga_em_curso_sem_carga_bloqueia():
    # FEQ-0499 tem carga horária nula -> qualquer carga citada é fail-closed.
    assert not _ok("FEQ-0499", "São 80 horas de conteúdo.")


# ----------------------------------------- conhecimento multi-curso (revisão)
def test_fato_verdadeiro_de_outro_curso_aprovado():
    # Card FEQ-0480; mensagem cita dados REAIS do FEQ-0580 -> não pode bloquear.
    assert _ok("FEQ-0480", "O FEQ-0580 custa R$ 9.533,46 à vista, em 13x de R$ 733,34, com 166 horas.")


def test_fato_falso_de_outro_curso_bloqueia():
    assert not _ok("FEQ-0480", "O FEQ-0580 custa R$ 1.000,00.")


def test_parcelas_trocadas_entre_cursos_bloqueia():
    # nº de parcelas do 0480 com valor do 0580 -> incoerente mesmo na união.
    assert not _ok("FEQ-0480", "O FEQ-0580 sai em 19x de R$ 733,34.")


def test_curso_inexistente_citado_fail_closed():
    assert not _ok("FEQ-0480", "O FEQ-9999 custa R$ 500,00.")


# ------------------------- lead fora da base (spec: botão de WhatsApp sem curso) ---
# Sem curso associado (card novo do WhatsApp, número desconhecido, curso="" —
# ver atena/transporte/whatsapp.py) o operador precisa poder responder perguntas
# gerais. Bloquear TUDO só por faltar curso (comportamento antigo) impedia até
# um "oi, tudo bem?" de sair. Sem afirmação factual = nada pra verificar = aprova;
# com afirmação factual = continua fail-closed (não tem âncora pra checar).
def test_sem_curso_mensagem_neutra_aprovada():
    assert _ok("", "Oi! Tudo bem? Qual curso é do seu interesse?")


def test_sem_curso_pergunta_generica_aprovada():
    assert _ok("", "Temos cursos de extensão e formação de especialistas na área de "
                    "Engenharia Química. Me conta o que você procura que te ajudo a escolher!")


def test_sem_curso_com_preco_bloqueia():
    r = revisar_estrutural("", "O curso custa R$ 500,00.", ONT)
    assert not r["aprovado"] and "500,00" in r["bloqueios"][0]


def test_sem_curso_com_carga_bloqueia():
    assert not _ok("", "São 300 horas de curso.")


def test_sem_curso_mas_cita_curso_real_usa_a_ancora_dele():
    # mensagem sem curso no card, mas cita um código real -> tem âncora, tem que bater.
    assert _ok("", "O FEQ-0480 são 19x de R$ 866,09.")
    assert not _ok("", "O FEQ-0480 custa R$ 1,00.")


def test_curso_desconhecido_mensagem_neutra_aprovada():
    # não é só string vazia: um código que não existe também não tem âncora.
    assert _ok("FEQ-9999", "Oi! Em que posso te ajudar hoje?")


# ------------------------------------------- fonte extra (resposta da coordenação)
def test_fonte_extra_autoriza_fato_humano():
    from atena.revisor.ontologico import revisar_estrutural
    msg = "O material complementar custa R$ 150,00."
    assert not revisar_estrutural("FEQ-0480", msg, ONT)["aprovado"]  # sem fonte -> bloqueia
    r = revisar_estrutural("FEQ-0480", msg, ONT,
                           fonte_extra="coordenação: o material complementar custa R$ 150,00")
    assert r["aprovado"]                                             # com fonte humana -> passa


# ------------------------------------------------- regressão: carga × relógio
def test_horario_de_aula_nao_e_carga():
    # BUG da v1: "9h", "12h" eram comparados com a carga (398h) e bloqueavam.
    assert _ok("FEQ-0360", "As aulas são aos sábados, das 9h às 12h e das 13h às 16h.")


def test_horario_com_minutos_nao_bloqueia():
    assert _ok("FEQ-0480", "As aulas são segundas e quartas, das 19h30 às 22h30.")


# ----------------------------------------------------------- serviço: ontologia
def test_data_sem_ano_tambem_e_verificada():
    """Precedente do corpus (spec 18) traz data de turma antiga em DD/MM. Antes,
    só DD/MM/AAAA era checado e '07/08' chegava ao lead como se fosse verdade."""
    assert not _ok("FEQ-0480", "As inscrições se encerram em 07/08.")
    assert _ok("FEQ-0480", "As inscrições vão até 03/01.")            # data real do curso
    assert _ok("FEQ-0480", "As inscrições vão até 03/01/2027.")
    # não pode virar caça-fantasma: proporção não é data
    assert _ok("FEQ-0480", "Já temos 20/26 vagas preenchidas.")


def test_captabilidade_temporal():
    assert ONT.captavel("FEQ-0655", HOJE_REF)["status"] == "encerrada"
    assert ONT.captavel("FEQ-0480", HOJE_REF)["status"] == "aberta"


def test_elegibilidade_ordinal():
    assert ONT.elegivel("Ensino Médio Completo", "FEQ-0480") is False
    assert ONT.elegivel("Superior Completo", "FEQ-0480") is True


# --------------------------- confia_valor_humano (negociação real, 10/08/2026) ---
def test_humano_pode_negociar_valor_fora_da_base():
    r = revisar_estrutural("FEQ-0480", "Consegui fechar com você por R$ 692,87.", ONT,
                           confia_valor_humano=True)
    assert r["aprovado"], r["bloqueios"]
    assert any("negociado por humano" in a for a in r["ancoras"])


def test_humano_pode_negociar_parcela_fora_da_base():
    r = revisar_estrutural("FEQ-0480", "Fica em 12x de R$ 500,00 no seu caso.", ONT,
                           confia_valor_humano=True)
    assert r["aprovado"], r["bloqueios"]


def test_sem_flag_continua_bloqueando_preco_mesmo_texto():
    """Mesma mensagem, SEM confia_valor_humano: comportamento de antes, intocado —
    é o que protege o caminho do LLM (que nunca passa a flag)."""
    assert not revisar_estrutural("FEQ-0480", "Consegui fechar com você por R$ 692,87.",
                                  ONT)["aprovado"]


def test_confia_valor_humano_nao_libera_data_inventada():
    """Negociação é sobre preço/parcela — data é fato estrutural do curso. Um humano
    digitando uma data errada ainda é bloqueado (a correção é editar a ontologia)."""
    assert not revisar_estrutural("FEQ-0480", "As inscrições vão até 30/12/2099.", ONT,
                                  confia_valor_humano=True)["aprovado"]


def test_confia_valor_humano_nao_libera_carga_inventada():
    assert not revisar_estrutural("FEQ-0480", "São 999 horas de curso.", ONT,
                                  confia_valor_humano=True)["aprovado"]


def test_confia_valor_humano_nao_libera_link_errado():
    """Mesmo bug que já aconteceu de verdade (6 leads do FEQ-0480 receberam a turma
    errada) — link continua travado mesmo com um humano digitando por cima."""
    url_errado = "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/001"
    assert not revisar_estrutural("FEQ-0480", f"Aqui está o link: {url_errado}", ONT,
                                  confia_valor_humano=True)["aprovado"]


def test_confia_valor_humano_nao_bloqueia_valor_real_da_base():
    """Preço que JÁ bate com a base continua sendo registrado como tal (não como
    'negociado'), mesmo com a flag ligada — a flag afrouxa, não confunde a origem."""
    r = revisar_estrutural("FEQ-0480", "O à vista é R$ 16.455,75.", ONT, confia_valor_humano=True)
    assert r["aprovado"]
    assert any("negociado" not in a and "16.455,75" in a for a in r["ancoras"])


# --------------------------------------------------------- runner sem pytest
if __name__ == "__main__":
    casos = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for fn in casos:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            falhas += 1
            print(f"FAIL  {fn.__name__}  {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO  {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{len(casos) - falhas}/{len(casos)} passaram.")
    sys.exit(1 if falhas else 0)
