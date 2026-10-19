#!/usr/bin/env python3
"""
Atena · Growth — motor de ritmo e otimização de mídia (spec 15 §4).
Calcula a velocidade de inscrições exigida pela meta de segurança, pausa
conjuntos com CPL estourado e realoca verba do curso adiantado p/ o atrasado.
SUGERE sempre (acoes_orcamento + alerta); APLICA só com GROWTH_AUTO_APPLY=1.
"""
import os
import sys
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.agentes.ingestao import curso_de_texto

CPL_MAX = float(os.environ.get("GROWTH_CPL_MAX", "60"))                  # R$/lead
SHIFT_PCT = float(os.environ.get("GROWTH_SHIFT_PCT", "20"))              # % da verba do doador
ORCAMENTO_MIN = int(os.environ.get("GROWTH_BUDGET_MIN_CENTS", "1000"))   # centavos/dia por adset
AUTO = os.environ.get("GROWTH_AUTO_APPLY") == "1"


def calcular(meta_seg, inscritos, prazo, vel_7d, hoje=None):
    """Ritmo de um curso. prazo = date|None. Puro (testável sem I/O)."""
    hoje = hoje or datetime.date.today()
    faltam = max(0, meta_seg - inscritos)
    r = {"meta_seg": meta_seg, "inscritos": inscritos, "faltam": faltam,
         "vel_7d": round(vel_7d, 2)}
    if not faltam:
        return {**r, "status": "concluido", "dias": None, "vel_necessaria": 0, "razao": None}
    if not prazo:
        return {**r, "status": "sem_prazo", "dias": None, "vel_necessaria": None, "razao": None}
    dias = max(1, (prazo - hoje).days)
    vn = faltam / dias
    razao = vel_7d / vn
    status = "atrasado" if razao < 0.9 else ("adiantado" if razao > 1.25 else "no_ritmo")
    return {**r, "status": status, "dias": dias,
            "vel_necessaria": round(vn, 2), "razao": round(razao, 2)}


def sincronizar_insights(g, ads):
    """Meta Insights -> ad_desempenho (idempotente por dia×adset)."""
    n = 0
    for r in ads.insights():
        r["curso"] = curso_de_texto(r.get("campanha", ""), r.get("nome", ""))
        g.add_desempenho(r)
        n += 1
    return n


def _alerta(est, tipo, texto):
    if est:
        est.add_alerta(tipo, texto)


def regra_cpl(g, ads, est=None, dias=3, agora=None):
    """Pausa (ou sugere pausar) adset com CPL acima do teto na janela de N dias."""
    agg = {}
    for r in g.desempenho(dias=dias, agora=agora):
        a = agg.setdefault(r["objeto_id"], {"gasto": 0.0, "leads": 0, "nome": r["nome"]})
        a["gasto"] += r["gasto"]
        a["leads"] += r["leads"]
    acoes = []
    for oid, a in agg.items():
        if not oid or a["gasto"] < CPL_MAX:          # gasto mínimo p/ julgar
            continue
        cpl = a["gasto"] / a["leads"] if a["leads"] else None
        if cpl is not None and cpl <= CPL_MAX:
            continue
        if g.acao_recente("pausa_cpl", oid, dias=dias, agora=agora):
            continue
        aplicado = AUTO and ads.ativo and ads.pausar(oid).get("ok")
        det = (f"{a['nome'] or oid}: CPL {'R$%.2f' % cpl if cpl else 'infinito (0 leads)'} "
               f"> teto R${CPL_MAX:.2f} em {dias}d (gasto R${a['gasto']:.2f})")
        g.add_acao("pausa_cpl", oid, det, aplicado)
        _alerta(est, "cpl_estourado", ("PAUSADO — " if aplicado else "Sugestão de pausa — ") + det)
        acoes.append({"objeto_id": oid, "detalhe": det, "aplicado": bool(aplicado)})
    return acoes


def _adsets_do_curso(g, curso, dias=7, agora=None):
    return sorted({r["objeto_id"] for r in g.desempenho(dias=dias, agora=agora)
                   if r["curso"] == curso and r["objeto_id"]})


def _gasto_diario(g, curso, dias=7, agora=None):
    gasto = sum(r["gasto"] for r in g.desempenho(dias=dias, agora=agora) if r["curso"] == curso)
    return gasto / dias


def realocar(g, ads, ritmos, est=None, agora=None):
    """Move SHIFT_PCT% da verba diária do curso mais adiantado p/ o mais atrasado."""
    atras = sorted((c for c, r in ritmos.items() if r["status"] == "atrasado"),
                   key=lambda c: ritmos[c]["razao"])
    adiant = sorted((c for c, r in ritmos.items() if r["status"] in ("adiantado", "concluido")),
                    key=lambda c: -(ritmos[c]["razao"] or 999))
    if not (atras and adiant):
        return None
    receptor, doador = atras[0], adiant[0]
    chave = f"{doador}->{receptor}"
    if g.acao_recente("realocacao", chave, dias=1, agora=agora):   # no máx. 1 realocação/dia por par
        return None
    base = _gasto_diario(g, doador, agora=agora)       # proxy da verba: gasto médio 7d
    mover = base * SHIFT_PCT / 100
    det = (f"mover ~R${mover:.2f}/dia ({SHIFT_PCT:.0f}% de R${base:.2f}) "
           f"de {doador} ({ritmos[doador]['status']}) para {receptor} (atrasado, "
           f"precisa {ritmos[receptor]['vel_necessaria']}/dia, faz {ritmos[receptor]['vel_7d']}/dia)")
    aplicado = False
    if AUTO and ads.ativo:
        aplicado = _aplicar_realocacao(g, ads, doador, receptor)
    g.add_acao("realocacao", chave, det, aplicado)
    _alerta(est, "realocacao_verba", ("APLICADO — " if aplicado else "Sugestão — ") + det)
    return {"doador": doador, "receptor": receptor, "detalhe": det, "aplicado": aplicado}


def _aplicar_realocacao(g, ads, doador, receptor):
    """Corta SHIFT_PCT% de cada adset do doador (respeitando o piso) e distribui
    o total igualmente entre os adsets do receptor. Verbas lidas ao vivo."""
    origem, destino = _adsets_do_curso(g, doador), _adsets_do_curso(g, receptor)
    if not (origem and destino):
        return False
    delta = 0
    for oid in origem:
        atual = ads.orcamento(oid)
        if not atual:
            continue    # ponytail: adset com CBO (verba na campanha) fica de fora
        novo = max(ORCAMENTO_MIN, int(atual * (1 - SHIFT_PCT / 100)))
        if novo < atual and ads.definir_orcamento(oid, novo).get("ok"):
            delta += atual - novo
    if not delta:
        return False
    por_adset = delta // len(destino)
    for oid in destino:
        atual = ads.orcamento(oid)
        if atual:
            ads.definir_orcamento(oid, atual + por_adset)
    return True


def passada(g, ads, ont=None, est=None, agora=None):
    """Passada diária completa: prazos <- ontologia, insights, ritmo, CPL, realocação."""
    agora = agora or datetime.datetime.now()
    for c in g.cursos():                     # prazo: ontologia é a fonte; coluna é cache
        if not ont:
            break
        try:
            fim = ont.curso(c["curso"]).get("inscricoes_fim")
            if fim and fim != c.get("prazo"):
                g.set_prazo(c["curso"], fim)
        except Exception:
            pass                             # curso fora da ontologia: fica o prazo manual
    n_ins = sincronizar_insights(g, ads)
    ritmos = {}
    for c in g.cursos():
        prazo = datetime.date.fromisoformat(c["prazo"]) if c.get("prazo") else None
        ritmos[c["curso"]] = calcular(c["meta_seg"], g.inscritos(c["curso"]), prazo,
                                      g.vel_7d(c["curso"], agora), agora.date())
        # curso atrasado NÃO vira alerta (poluía a caixa de notificações, um por curso
        # por dia): o ritmo aparece no Painel/Medição. CPL e realocação seguem alertando —
        # são decisões de verba.
    return {"insights": n_ins, "ritmos": ritmos,
            "cpl": regra_cpl(g, ads, est, agora=agora),
            "realocacao": realocar(g, ads, ritmos, est, agora=agora)}
