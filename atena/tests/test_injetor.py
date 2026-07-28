#!/usr/bin/env python3
"""Testes do Injetor (Ontologista + versionamento), em catálogo temporário."""
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.agentes.ontologista import Ontologista

ONT_DIR = Path(__file__).resolve().parents[1] / "ontologia"


def _setup():
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ONT_DIR / "catalogo.jsonld", tmp / "catalogo.jsonld")
    est = Estado(":memory:")
    est.upsert_lead("x1", nome="L", telefone="+55", curso="FEQ-0499",
                    fase="Em espera", estado_conversa="em_espera")
    onto = Ontologista(est, catalogo_path=tmp / "catalogo.jsonld", vocab_path=ONT_DIR / "vocabulario.ttl")
    return est, onto


def test_reabrir_curso_valida_e_propoe():
    est, onto = _setup()
    assert onto.ont.captavel("FEQ-0499")["status"] == "encerrada"
    p = onto.propor("A inscrição do FEQ-0499 reabriu até 30/08/2026.")
    assert p["status"] == "proposta"
    assert p["validacao"]["ok"] is True
    assert any(d["campo"] == "janelaInscricao.fim" and d["para"] == "2026-08-30" for d in p["diff"])
    assert any("Captabilidade" in i for i in p["impacto"])


def test_aprovar_aplica_e_torna_captavel():
    est, onto = _setup()
    p = onto.propor("A inscrição do FEQ-0499 reabriu até 30/08/2026.")
    r = onto.aprovar(p["id"])
    assert r["ok"] is True
    assert onto.ont.captavel("FEQ-0499")["status"] == "aberta"
    assert len(onto.ver.historico()) == 1


def test_rollback_restaura():
    est, onto = _setup()
    p = onto.propor("A inscrição do FEQ-0499 reabriu até 30/08/2026.")
    r = onto.aprovar(p["id"])
    onto.ver.rollback(r["versao"]["versao"])
    onto.ont = onto.ont.__class__(onto.catalogo_path, onto.vocab_path)
    assert onto.ont.captavel("FEQ-0499")["status"] == "encerrada"


def test_atualizacao_ambigua_pede_esclarecimento():
    est, onto = _setup()
    p = onto.propor("muda lá uma coisa no sistema")
    assert p["status"] == "esclarecer"


def test_carga_frase_natural_com_codigo_no_meio():
    # antes: os dígitos do código (0333) quebravam o \D do parser -> "esclarecer".
    est, onto = _setup()
    p = onto.propor("A carga horária do FEQ-0333 passou para 120h")
    assert any(d["campo"] == "cargaHoraria.horas" and d["para"] == 120 for d in p["diff"])
    p2 = onto.propor("Atualizar FEQ-0333 para 448 horas")
    assert any(d["campo"] == "cargaHoraria.horas" and d["para"] == 448 for d in p2["diff"])


def test_preco_coerente_valida_incoerente_barra():
    est, onto = _setup()
    # plano coerente (parcelas + à vista batem) -> passa SHACL
    p = onto.propor("FEQ-0499 agora é 20x de R$ 195,00 ou R$ 3.900,00 à vista")
    campos = {d["campo"]: d["para"] for d in p["diff"]}
    assert campos.get("planoPagamento.numParcelas") == 20
    assert campos.get("planoPagamento.valorParcela") == "195.00"
    assert campos.get("planoPagamento.aVista") == "3900.00"
    assert p["status"] == "proposta" and p["validacao"]["ok"] is True
    # mexer só no à vista quebra a trava numParcelas × valorParcela ≈ aVista
    p2 = onto.propor("Muda só o à vista do FEQ-0499 para R$ 3.900,00")
    assert p2["status"] == "invalida"


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
