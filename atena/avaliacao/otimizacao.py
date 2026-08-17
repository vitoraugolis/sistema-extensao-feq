#!/usr/bin/env python3
"""
Atena · Otimização do funil (spec 18 §3.2/3.3) — objetivo de CONTROLE explícito:

    maximizar inscritos por curso   ·   minimizar CPL

Por que este módulo existe agora e não antes: até a ficha da etapa 2 entrar no
sistema, "inscrito" era digitado à mão na aba Verificação. Rótulo digitado não
fecha ciclo de aprendizado — quem preenche erra, esquece e demora. Agora a
conclusão da etapa 2 É o rótulo: automática, com hora, curso e o que o lead
escolheu. É essa mudança que destrava a estrutura desenhada na spec 18 §3.3.

O que este módulo NÃO faz, de propósito:

1. **Não muda a operação sozinho.** Ele roda em modo SOMBRA até o gate de §3.4
   abrir (episódios e conversões suficientes). Antes disso o bandit é calculado e
   mostrado, mas quem escolhe a cadência continua sendo a CADENCIA fixa. Ligar
   otimizador com 2 amostras é ajustar ruído e chamar de inteligência.
2. **Não inventa CPL.** CPL = gasto/leads e o gasto vem de `ad_desempenho`, que é
   populado pela API da Meta. Sem META_ADS_TOKEN a tabela fica vazia — e aí o
   termo de custo do objetivo é declarado INDISPONÍVEL em vez de virar zero.
   Zero silencioso faria o objetivo parecer ótimo justamente quando se está cego.
3. **Não mistura o rótulo velho com o novo.** Antes do formulário de duas etapas,
   `inicio_inscricao` significava "clicou para ir à Extecamp"; hoje significa
   "entregou a ficha completa". São eventos com o mesmo nome e sentidos
   diferentes: contar os dois juntos envenenaria o treino. O corte é a data do
   primeiro `etapa1` observado — descoberta do dado, não constante cravada.
"""
import math
import random
import datetime

# Gate de §3.4: o otimizador só sai da sombra com evidência suficiente.
GATE_EPISODIOS = 100
GATE_CONVERSOES = 30
# Janela para creditar um toque: ficha concluída até 72h depois do toque conta como
# sucesso daquele toque. Fora disso, a atribuição vira suposição.
CREDITO_H = 72


def _agora(agora=None):
    return agora or datetime.datetime.now()


def corte_rotulo_novo(g):
    """Data a partir da qual `inicio_inscricao` significa "ficha completa entregue".

    É o primeiro `etapa1` que existe no banco: o evento só passou a ser gravado
    quando o formulário de duas etapas entrou no ar. Sem nenhum ainda -> None, e
    o módulo inteiro se declara sem dado em vez de chutar uma data."""
    with g._lock:
        r = g.conn.execute(
            "SELECT MIN(ts) FROM eventos_funil WHERE tipo='etapa1'").fetchone()
    return r[0] if r and r[0] else None


# --------------------------------------------------------------- objetivo
def objetivo(g, ont=None, agora=None, cpl_alvo=60.0, peso_cpl=1.0):
    """J = Σ_curso (inscritos/meta) − peso · (CPL/CPL_alvo), por curso e agregado.

    Os dois termos são reportados separados, e o de custo carrega `disponivel`.
    Quem consome decide o que fazer com objetivo parcialmente observável — o que
    não pode é receber um número inteiro fingindo que os dois lados foram medidos.
    """
    agora = _agora(agora)
    corte = corte_rotulo_novo(g)
    por_curso, sem_custo = [], 0
    for m in g.cursos():
        curso = m["curso"]
        meta = m.get("meta_seg") or m.get("meta") or 0
        fichas = fichas_entregues(g, curso, desde=corte)
        confirmados = g.inscritos(curso)
        with g._lock:
            r = g.conn.execute(
                "SELECT COALESCE(SUM(gasto),0), COALESCE(SUM(leads),0)"
                " FROM ad_desempenho WHERE curso=?", (curso,)).fetchone()
        gasto, leads = float(r[0] or 0), int(r[1] or 0)
        tem_custo = gasto > 0 and leads > 0
        cpl = (gasto / leads) if tem_custo else None
        if not tem_custo:
            sem_custo += 1
        ganho = (fichas / meta) if meta else 0.0
        custo = (peso_cpl * cpl / cpl_alvo) if cpl else None
        por_curso.append({
            "curso": curso, "meta": meta,
            "fichas_etapa2": fichas, "inscritos_confirmados": confirmados,
            "ganho": round(ganho, 4),
            "cpl": round(cpl, 2) if cpl else None,
            "custo": round(custo, 4) if custo is not None else None,
            "j_parcial": round(ganho - (custo or 0), 4),
            "custo_observado": tem_custo,
        })
    return {
        "objetivo": "maximizar inscritos por curso · minimizar CPL",
        "corte_rotulo_novo": corte,
        "j_ganho": round(sum(c["ganho"] for c in por_curso), 4),
        "j_custo": (None if sem_custo == len(por_curso)
                    else round(sum(c["custo"] or 0 for c in por_curso), 4)),
        "cpl_disponivel": sem_custo < len(por_curso),
        "cursos_sem_custo": sem_custo,
        "motivo_sem_cpl": ("nenhuma linha em ad_desempenho — o termo de custo do "
                           "objetivo está INERTE até a sincronização com a Meta Ads "
                           "(META_ADS_TOKEN/META_AD_ACCOUNT_ID) alimentar gasto e leads")
                          if sem_custo == len(por_curso) else None,
        "por_curso": sorted(por_curso, key=lambda c: -c["j_parcial"]),
    }


def fichas_entregues(g, curso=None, desde=None):
    """Fichas da etapa 2 = o rótulo positivo. Conta CARDS distintos, não envios:
    o lead que refez o formulário não vale dois."""
    q = ("SELECT COUNT(DISTINCT i.card_id) FROM inscricoes i"
         " WHERE 1=1")
    p = []
    if curso:
        q += " AND i.curso=?"
        p.append(curso)
    if desde:
        q += " AND i.criado>=?"
        p.append(desde)
    with g._lock:
        return int(g.conn.execute(q, p).fetchone()[0] or 0)


# --------------------------------------------------------------- episódios
def episodios(g, desde=None, credito_h=CREDITO_H):
    """Um episódio = um toque de recuperação + o desfecho observado depois dele.

    Só conta toques a partir do corte do rótulo novo: antes disso não havia ficha
    para observar, então o desfecho seria sempre "não converteu" por construção —
    o pior tipo de rótulo, o que parece dado e é artefato.
    """
    corte = desde or corte_rotulo_novo(g)
    if not corte:
        return []
    with g._lock:
        toques = g.conn.execute(
            "SELECT card_id, curso, ts FROM eventos_funil"
            " WHERE tipo='recuperacao_enviada' AND ts>=? ORDER BY ts", (corte,)).fetchall()
        fichas = g.conn.execute(
            "SELECT card_id, MIN(criado) FROM inscricoes WHERE criado>=? GROUP BY card_id",
            (corte,)).fetchall()
    quando_ficha = {r[0]: r[1] for r in fichas}
    out = []
    for card_id, curso, ts in toques:
        # ordem do toque dentro da cadência do lead -> qual template foi usado
        with g._lock:
            n = g.conn.execute(
                "SELECT COUNT(*) FROM eventos_funil WHERE card_id=? AND"
                " tipo='recuperacao_enviada' AND ts<=?", (card_id, ts)).fetchone()[0]
        variante = variante_do_toque(int(n) - 1)
        fim = quando_ficha.get(card_id)
        sucesso = False
        if fim and fim >= ts:
            try:
                dt = (datetime.datetime.fromisoformat(fim)
                      - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600
                sucesso = dt <= credito_h
            except ValueError:
                sucesso = False
        out.append({"card_id": card_id, "curso": curso, "ts": ts,
                    "toque": int(n), "variante": variante, "sucesso": sucesso})
    return out


def variante_do_toque(indice):
    """Variante = o template daquele degrau da cadência. É o espaço de decisão real
    do agente (spec 18 §3.3): pequeno e discreto, por isso bandit e não deep RL."""
    from atena.growth.funil import CADENCIA
    if 0 <= indice < len(CADENCIA):
        return CADENCIA[indice][0]
    return "desconhecida"


# --------------------------------------------------------------- bandit
def bandit(g, desde=None):
    """Thompson sampling Beta-Bernoulli por variante de template.

    Escolhido (e não deep RL) porque o espaço de ação tem 3 templates, os
    episódios são longos e o dado é escasso — condições em que bandit dá resposta
    útil com dezenas de amostras e PPO não daria com milhares.
    """
    eps = episodios(g, desde=desde)
    braços = {}
    for e in eps:
        b = braços.setdefault(e["variante"], {"variante": e["variante"], "n": 0, "sucessos": 0})
        b["n"] += 1
        b["sucessos"] += 1 if e["sucesso"] else 0
    for b in braços.values():
        # Beta(1,1) = prior uniforme: sem dado, nenhuma variante nasce favorita
        a, beta = 1 + b["sucessos"], 1 + (b["n"] - b["sucessos"])
        b["alpha"], b["beta"] = a, beta
        b["media_posterior"] = round(a / (a + beta), 4)
        # desvio da Beta: a barra de erro que impede ler 1/1 como "100% de conversão"
        b["desvio"] = round(math.sqrt(a * beta / ((a + beta) ** 2 * (a + beta + 1))), 4)
    return {"episodios": len(eps), "conversoes": sum(1 for e in eps if e["sucesso"]),
            "bracos": sorted(braços.values(), key=lambda b: -b["media_posterior"])}


def amostrar(g, desde=None, rng=None):
    """Uma extração de Thompson: sorteia θ~Beta(α,β) por braço e devolve o melhor.
    É o que o otimizador ESCOLHERIA. Em modo sombra, a escolha é só registrada."""
    est = bandit(g, desde=desde)
    if not est["bracos"]:
        return None
    r = rng or random
    sorteio = [(r.betavariate(b["alpha"], b["beta"]), b["variante"]) for b in est["bracos"]]
    sorteio.sort(reverse=True)
    return {"escolhida": sorteio[0][1],
            "sorteio": [{"variante": v, "theta": round(t, 4)} for t, v in sorteio]}


# --------------------------------------------------------------- gate
def gate(g, desde=None):
    """§3.4: o otimizador só assume o controle com evidência. Antes disso, sombra.

    O terceiro critério da spec ("rótulo automático, não digitado") passou a ser
    atendido pela ficha da etapa 2 — por isso o gate agora mede só volume.
    """
    est = bandit(g, desde=desde)
    eps, conv = est["episodios"], est["conversoes"]
    liberado = eps >= GATE_EPISODIOS and conv >= GATE_CONVERSOES
    return {
        "liberado": liberado,
        "modo": "controle" if liberado else "sombra",
        "episodios": eps, "episodios_necessarios": GATE_EPISODIOS,
        "conversoes": conv, "conversoes_necessarias": GATE_CONVERSOES,
        "faltam_episodios": max(0, GATE_EPISODIOS - eps),
        "faltam_conversoes": max(0, GATE_CONVERSOES - conv),
        "rotulo_automatico": True,
        "nota": ("Em SOMBRA: o bandit calcula e registra a escolha, mas quem manda na "
                 "cadência continua sendo a CADENCIA fixa. Sai da sombra sozinho quando "
                 "os dois contadores baterem." if not liberado else
                 "Gate aberto: há evidência suficiente para o bandit assumir a escolha "
                 "de variante. A troca continua exigindo ação humana."),
    }


def painel(g, ont=None, agora=None):
    """Tudo que a aba de Otimização mostra, numa chamada."""
    return {"objetivo": objetivo(g, ont, agora=agora),
            "bandit": bandit(g), "gate": gate(g),
            "sugestao_sombra": amostrar(g)}
