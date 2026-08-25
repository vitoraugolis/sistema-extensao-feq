#!/usr/bin/env python3
"""
Atena · Medição do funil (spec 18 §3.1).

O que este módulo NÃO faz: otimizar. Otimização (bandit/GA) está atrás do gate de
§3.4 — hoje há 1 conversão em 253 leads, e otimizar com isso é ajustar ruído.
Aqui só medimos, com honestidade metodológica:

  • COORTE, não período solto. As taxas olham os leads CRIADOS na janela e seguem
    o que aconteceu com eles. Dividir conversões-do-mês por leads-do-mês é o erro
    clássico que infla ou afunda a taxa quando o volume de entrada muda.
  • ETAPAS CUMULATIVAS. Um lead que clicou em inscrição obviamente passou por
    "respondeu", mesmo que a fase dele não tenha sido atualizada. Cada etapa é a
    UNIÃO do próprio sinal com os das etapas mais profundas — assim o funil é
    monotônico de verdade e não mostra etapa de baixo maior que a de cima.
  • Conexão de LEITURA separada (WAL aceita leitor concorrente), então medir
    nunca trava a escrita do atendimento.
"""
import sys
import sqlite3
import pathlib
import datetime
import statistics

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

def _db_padrao():
    """Segue o MESMO banco do Estado. Hardcodear o caminho aqui já fez a medição
    ler outro arquivo que não o do sistema — uma fonte só evita medir o vazio."""
    from atena.core.estado import DEFAULT_DB
    return DEFAULT_DB

# fase é o marco DURÁVEL do lead (estado_conversa é o estado corrente da conversa,
# e os dois divergem de propósito: dá para ser Prospect e estar aguardando resposta)
# Derivadas da esteira, não escritas à mão: quando entraram as fases da Extecamp
# (Taxa aceita, Taxa paga, Documento entregue), uma lista fixa teria feito o
# inscrito "sumir" da métrica ao avançar de degrau — queda silenciosa de conversão.
from atena.core.estado import ESTEIRA, alcancou             # noqa: E402
FASES_QUALIFICADO = tuple(f for f in ESTEIRA if alcancou(f, "Prospect"))
FASES_INSCRITO = tuple(f for f in ESTEIRA if alcancou(f, "Inscrito"))

# O rótulo importa: `inicio_inscricao` é gravado quando o lead ENVIA O FORMULÁRIO DA
# NOSSA LP e é encaminhado à Extecamp — não é inscrição feita lá. É exatamente neste
# repasse que o funil sangra, e é o que o item 2a (coletor Extecamp) vai instrumentar.
ETAPAS = [
    ("lead", "Leads na coorte", "todos os leads criados na janela"),
    ("respondeu", "Responderam", "mandaram ao menos uma mensagem"),
    ("qualificado", "Qualificados", "avançaram de fase (Prospect+) ou foram adiante"),
    ("clicou", "Iniciaram inscrição", "enviaram o formulário da LP e foram para a Extecamp"),
    ("inscrito", "Inscritos", "inscrição confirmada (hoje verificada à mão)"),
    ("matriculado", "Matriculados", "matrícula efetivada"),
]


def _conn(db=None):
    c = sqlite3.connect(f"file:{db or _db_padrao()}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _horas(a, b):
    try:
        return (datetime.datetime.fromisoformat(b) - datetime.datetime.fromisoformat(a)
                ).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _pct(n, base):
    return round(100.0 * n / base, 1) if base else None


def medir(dias=30, curso=None, db=None, agora=None):
    agora = agora or datetime.datetime.now()
    desde = (agora - datetime.timedelta(days=dias)).isoformat(timespec="seconds")
    con = _conn(db)
    try:
        filtro_curso = " AND curso = ?" if curso else ""
        args = [desde] + ([curso] if curso else [])

        coorte = {r["card_id"]: dict(r) for r in con.execute(
            f"SELECT card_id, curso, fase, estado_conversa, optout, criado "
            f"FROM leads WHERE criado >= ?{filtro_curso}", args)}
        ids = set(coorte)
        if not ids:
            return {"periodo": {"dias": dias, "desde": desde, "curso": curso},
                    "coorte": 0, "etapas": [], "tempos": {}, "saude": {}, "serie": [],
                    "por_curso": []}

        def _dentro(rows, campo="card_id"):
            return {r[campo] for r in rows if r[campo] in ids}

        falou = _dentro(con.execute(
            "SELECT DISTINCT card_id FROM conversas WHERE papel='lead'"))
        clicou = _dentro(con.execute(
            "SELECT DISTINCT card_id FROM eventos_funil WHERE tipo='inicio_inscricao'"))
        conf = _dentro(con.execute(
            "SELECT DISTINCT card_id FROM eventos_funil WHERE tipo='inscricao_confirmada'"))

        matriculado = {i for i in ids if coorte[i]["fase"] == "Matriculado"}
        inscrito = {i for i in ids if coorte[i]["fase"] in FASES_INSCRITO} | conf | matriculado
        qualificado = {i for i in ids if coorte[i]["fase"] in FASES_QUALIFICADO} | clicou | inscrito
        respondeu = falou | qualificado          # etapas cumulativas (ver docstring)
        clicou = clicou | inscrito

        conj = {"lead": ids, "respondeu": respondeu, "qualificado": qualificado,
                "clicou": clicou, "inscrito": inscrito, "matriculado": matriculado}
        etapas, anterior = [], None
        for chave, rotulo, desc in ETAPAS:
            n = len(conj[chave])
            etapas.append({"chave": chave, "rotulo": rotulo, "descricao": desc, "n": n,
                           "do_topo": _pct(n, len(ids)),
                           "da_anterior": _pct(n, anterior) if anterior is not None else None,
                           "perdidos": (anterior - n) if anterior is not None else None})
            anterior = n
        # ---------------------------------------------------------- tempos (mediana)
        pri_lead, pri_agente = {}, {}
        for r in con.execute("SELECT card_id, papel, MIN(ts) t FROM conversas "
                             "GROUP BY card_id, papel"):
            if r["card_id"] in ids:
                (pri_lead if r["papel"] == "lead" else pri_agente)[r["card_id"]] = r["t"]
        pri_clique = {}
        for r in con.execute("SELECT card_id, MIN(ts) t FROM eventos_funil "
                             "WHERE tipo='inicio_inscricao' GROUP BY card_id"):
            if r["card_id"] in ids:
                pri_clique[r["card_id"]] = r["t"]

        def _mediana(mapa):
            vals = [h for i, t in mapa.items()
                    if (h := _horas(coorte[i]["criado"], t)) is not None and h >= 0]
            return round(statistics.median(vals), 1) if vals else None

        tempos = {"ate_1o_contato_h": _mediana(pri_agente),
                  "ate_resposta_do_lead_h": _mediana(pri_lead),
                  "ate_clique_h": _mediana(pri_clique),
                  "amostras": {"1o_contato": len(pri_agente), "resposta": len(pri_lead),
                               "clique": len(pri_clique)},
                  # honestidade: lead que NASCE no formulário da LP tem criado ≈ evento,
                  # então essa mediana tende a 0 por construção, não por velocidade nossa
                  "nota_clique": "leads originados na própria LP começam em 0h por construção"}

        # ------------------------------------------------- saúde do agente/guardrails
        dec = con.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN aprovado_revisor=0 THEN 1 ELSE 0 END) bloq,"
            " SUM(CASE WHEN acao='escalar_humano' THEN 1 ELSE 0 END) esc,"
            " SUM(CASE WHEN acao='consultar_coordenacao' THEN 1 ELSE 0 END) coord"
            " FROM decisoes WHERE ts >= ?", (desde,)).fetchone()
        n_dec = dec["n"] or 0
        saude = {
            "decisoes": n_dec,
            "revisor_bloqueio_pct": _pct(dec["bloq"] or 0, n_dec),
            "escalonamento_pct": _pct(dec["esc"] or 0, n_dec),
            "consulta_coordenacao_pct": _pct(dec["coord"] or 0, n_dec),
            "optout_pct": _pct(sum(1 for i in ids if coorte[i]["optout"]), len(ids)),
            "fallback_deterministico": _fallbacks(),
        }

        # ------------------------------------------------------------------ série
        serie = {}
        for sql, chave, campo in (
                (f"SELECT substr(criado,1,10) d, COUNT(*) n FROM leads "
                 f"WHERE criado >= ?{filtro_curso} GROUP BY d", "leads", None),
                ("SELECT substr(ts,1,10) d, COUNT(*) n FROM decisoes "
                 "WHERE ts >= ? AND acao='qualificar' GROUP BY d", "qualificacoes", None),
                ("SELECT substr(ts,1,10) d, COUNT(*) n FROM eventos_funil "
                 "WHERE ts >= ? AND tipo='inicio_inscricao' GROUP BY d", "cliques", None)):
            a = args if chave == "leads" else [desde]
            for r in con.execute(sql, a):
                serie.setdefault(r["d"], {"dia": r["d"], "leads": 0,
                                          "qualificacoes": 0, "cliques": 0})[chave] = r["n"]

        # ------------------------------------------------------------- por curso
        por_curso = {}
        for i in ids:
            c = coorte[i]["curso"] or "(sem curso)"
            p = por_curso.setdefault(c, {"curso": c, "leads": 0, "qualificados": 0,
                                         "cliques": 0, "inscritos": 0})
            p["leads"] += 1
            p["qualificados"] += i in qualificado
            p["cliques"] += i in clicou
            p["inscritos"] += i in inscrito
        for p in por_curso.values():
            p["conversao_pct"] = _pct(p["inscritos"], p["leads"])

        return {"periodo": {"dias": dias, "desde": desde, "curso": curso},
                "coorte": len(ids), "etapas": etapas, "tempos": tempos, "saude": saude,
                "serie": sorted(serie.values(), key=lambda x: x["dia"]),
                "por_curso": sorted(por_curso.values(), key=lambda x: -x["leads"])}
    finally:
        con.close()


def _fallbacks():
    """Quedas do cérebro no determinístico. É um deque em memória: zera no restart,
    e a UI diz isso. Persistir exigiria escrever a cada turno de conversa — custo
    que não se paga para um indicador de saúde de curto prazo."""
    try:
        from atena.agentes.cerebro import FALLBACKS
        return {"desde_o_restart": len(FALLBACKS)}
    except Exception:
        return {"desde_o_restart": None}


if __name__ == "__main__":
    import atena
    import json
    print(json.dumps(medir(dias=int(sys.argv[1]) if len(sys.argv) > 1 else 30),
                     ensure_ascii=False, indent=2))
