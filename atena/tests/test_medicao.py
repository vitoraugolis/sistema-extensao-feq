#!/usr/bin/env python3
"""Testes da medição do funil (spec 18 §3.1) — offline, banco temporário."""
import sys
import datetime
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.growth.db import Growth
from atena.avaliacao.medicao import medir

AGORA = datetime.datetime(2026, 8, 2, 12, 0)


def _base():
    """Banco com uma coorte conhecida: 1 lead cru, 1 que só respondeu, 1 qualificado,
    1 que iniciou inscrição, 1 inscrito — mais 1 ANTIGO (fora da janela)."""
    caminho = tempfile.mktemp(suffix=".db")
    est, g = Estado(caminho), Growth(caminho)
    def novo(cid, fase, criado, curso="FEQ-0480", optout=0):
        est.upsert_lead(cid, nome=cid, telefone="+55199" + cid, curso=curso,
                        fase=fase, estado_conversa="aguardando_resposta", optout=optout)
        est.conn.execute("UPDATE leads SET criado=? WHERE card_id=?", (criado, cid))
        est.conn.commit()
    dentro = (AGORA - datetime.timedelta(days=3)).isoformat(timespec="seconds")
    fora = (AGORA - datetime.timedelta(days=200)).isoformat(timespec="seconds")
    novo("l1", "Lead", dentro)                       # nunca falou
    novo("l2", "Lead", dentro)                       # falou
    novo("l3", "Prospect", dentro)                   # qualificado
    novo("l4", "Lead", dentro)                       # iniciou inscrição (fase não subiu)
    novo("l5", "Inscrito", dentro, optout=1)         # inscrito (e opt-out)
    novo("velho", "Inscrito", fora)                  # fora da coorte
    est.add_conversa("l2", "lead", "oi, tenho interesse")
    est.add_conversa("l5", "lead", "quero me inscrever")
    g.add_evento("l4", "FEQ-0480", "inicio_inscricao", ts=dentro)
    g.add_evento("l5", "FEQ-0480", "inicio_inscricao", ts=dentro)
    return caminho


def _mapa(m):
    return {e["chave"]: e["n"] for e in m["etapas"]}


def test_coorte_ignora_lead_de_fora_da_janela():
    m = medir(dias=30, db=_base(), agora=AGORA)
    assert m["coorte"] == 5                       # o 'velho' (200 dias) não entra
    assert _mapa(m)["lead"] == 5


def test_etapas_sao_cumulativas_e_monotonicas():
    """Quem iniciou inscrição conta como qualificado e como respondeu, mesmo que a
    fase não tenha subido — senão o funil mostra etapa de baixo maior que a de cima."""
    m = medir(dias=30, db=_base(), agora=AGORA)
    n = _mapa(m)
    assert n["lead"] >= n["respondeu"] >= n["qualificado"] >= n["clicou"] >= n["inscrito"]
    assert n["clicou"] == 2                       # l4 e l5
    assert n["qualificado"] == 3                  # l3 (fase) + l4 e l5 (por terem avançado)
    assert n["respondeu"] == 4                    # l2 falou + os 3 que avançaram
    assert n["inscrito"] == 1 and n["matriculado"] == 0


def test_percentuais_e_maior_queda():
    m = medir(dias=30, db=_base(), agora=AGORA)
    et = {e["chave"]: e for e in m["etapas"]}
    assert et["lead"]["do_topo"] == 100.0 and et["lead"]["da_anterior"] is None
    assert et["respondeu"]["do_topo"] == 80.0          # 4/5
    assert et["inscrito"]["perdidos"] == 1             # 2 -> 1
    assert et["qualificado"]["da_anterior"] == 75.0    # 3/4


def test_saude_e_por_curso():
    m = medir(dias=30, db=_base(), agora=AGORA)
    assert m["saude"]["optout_pct"] == 20.0            # 1 de 5
    assert "desde_o_restart" in m["saude"]["fallback_deterministico"]
    c = {p["curso"]: p for p in m["por_curso"]}["FEQ-0480"]
    assert c["leads"] == 5 and c["inscritos"] == 1 and c["conversao_pct"] == 20.0


def test_filtro_por_curso_e_janela_vazia():
    caminho = _base()
    assert medir(dias=30, curso="FEQ-9999", db=caminho, agora=AGORA)["coorte"] == 0
    vazio = medir(dias=30, db=caminho, agora=AGORA + datetime.timedelta(days=400))
    assert vazio["coorte"] == 0 and vazio["etapas"] == []      # não estoura sem dado


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
