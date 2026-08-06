#!/usr/bin/env python3
"""
Atena · Retentativa de entrega — não perder o lead porque o 1º template não chegou.

A Meta recusa por motivos DIFERENTES, e cada um pede um remédio diferente. Medido na
base em 05/08/2026 (41 leads com falha, 29 que nunca receberam nada):

  131026  "Message undeliverable"          21 leads  -> o NÚMERO não recebe naquela forma
  131049  "healthy ecosystem engagement"    6 leads  -> a Meta está segurando MARKETING p/ esse usuário
  130472  "number is part of an experiment" 2 leads  -> idem, usuário em experimento de marketing

Daí as duas regras:

1. 131026 é problema de NÚMERO, não de template — mandar outro texto para o mesmo
   número falha igual. No Brasil a causa quase sempre é o 9º dígito: o cadastro tem
   55DD9XXXXXXXX e a conta de WhatsApp está registrada como 55DDXXXXXXXX (ou o
   contrário). 16 dos 21 casos são exatamente celular COM o 9. Então retentamos na
   VARIANTE do número. Se ela entregar, o cadastro do lead é corrigido — os próximos
   contatos já vão para o número certo.

2. 131049/130472 é problema de CATEGORIA: a Meta limita marketing para aquele usuário.
   Como TODO template FEQ é MARKETING (conferido na WABA), insistir no mesmo minuto
   com outro template FEQ não muda nada. Retentamos espaçado no tempo e com um template
   COMPLEMENTAR, preferindo UTILITY quando houver um aprovado (ver UTILITY_SUGERIDO).

Esgotada a escada, o lead vira alerta para o operador em vez de morrer em silêncio.
"""
import re
import datetime

# Códigos da Meta -> família de tratamento.
NUMERO = (131026, 131052)          # não entregável / número inválido: mexer no número
PACING = (131049, 130472, 131047)  # limite de marketing / experimento: esperar e trocar de categoria

MAX_TENTATIVAS = 3                 # inclui a original: no máximo 2 retentativas por lead
ESPERA_H = {"numero": 0, "pacing": 24, "desconhecido": 6}   # horas antes de retentar

# Ordem de escalonamento dos templates na retentativa. O primeiro que ainda não foi
# tentado com aquele lead vence. Todos MARKETING hoje — por isso a espera importa mais
# que a troca; a troca só vira remédio de verdade quando existir um UTILITY aprovado.
ESCADA_TEMPLATES = ["feq_lead", "feq_abertura_curso", "feq_reengajamento",
                    "feq_lembrete_inscricao", "retomar_atendimento"]

# Template UTILITY sugerido ao operador (a criação passa pela UI, que exige aprovação da
# Meta). Enquanto não existir, o remédio do pacing é só o espaçamento no tempo.
UTILITY_SUGERIDO = {
    "nome": "feq_retorno_solicitacao",
    "categoria": "UTILITY",
    "corpo": ("Olá, {{1}}! Recebemos sua solicitação de informações sobre o curso {{2}} "
              "da Extensão FEQ Unicamp. Posso te enviar os detalhes de inscrição por aqui?"),
    "exemplos": ["Ana", "Gestão de Processos Químicos (FEQ-0480)"],
}

_TPL_RE = re.compile(r"\[template ([a-z0-9_]+)")


def familia(codigo):
    if codigo in NUMERO:
        return "numero"
    if codigo in PACING:
        return "pacing"
    return "desconhecido"


def variante_9(telefone):
    """A outra forma do MESMO celular brasileiro (com/sem o 9º dígito), ou None.

    Só mexe em celular BR: fixo não tem WhatsApp e estrangeiro não segue essa regra,
    então devolver None é o que impede a retentativa inútil.
    """
    d = re.sub(r"\D", "", telefone or "")
    if not d.startswith("55"):
        return None                       # estrangeiro: fora do escopo da regra
    local = d[2:]
    if len(local) == 11 and local[2] == "9":
        return "55" + local[:2] + local[3:]          # 55 DD 9XXXXXXXX -> 55 DD XXXXXXXX
    if len(local) == 10 and local[2] in "6789":
        return "55" + local[:2] + "9" + local[2:]    # celular antigo (8 díg) -> insere o 9
    return None                            # fixo (2..5) ou comprimento fora do padrão


def _tentativas(envios):
    """(qtd, destinos já usados, templates já usados, último envio) das tentativas feitas."""
    destinos = {e.get("destino") for e in envios if e.get("destino")}
    tpls = {m.group(1) for e in envios for m in [_TPL_RE.search(e.get("texto") or "")] if m}
    return len(envios), destinos, tpls, (envios[-1] if envios else None)


def _codigo_da_falha(envios):
    for e in reversed(envios):
        if e.get("status") == "falhou" and e.get("erro_codigo"):
            return e["erro_codigo"]
    return 0


def plano(lead, envios, agora=None, templates_meta=None, promo_tpl=None):
    """O que fazer com este lead agora. Devolve dict com acao:
       'reenviar' (destino + template) · 'esperar' (falta tempo) · 'humano' (escada esgotada).
    """
    agora = agora or datetime.datetime.now()
    n, destinos, tpls, ultimo = _tentativas(envios)
    if not ultimo or ultimo.get("status") != "falhou":
        return {"acao": "nada"}
    if n >= MAX_TENTATIVAS:
        return {"acao": "humano", "motivo": f"{n} tentativas sem entrega"}

    fam = familia(_codigo_da_falha(envios))
    try:
        horas = (agora - datetime.datetime.fromisoformat(ultimo["ts"])).total_seconds() / 3600
    except (TypeError, ValueError):
        horas = 999
    if horas < ESPERA_H[fam]:
        return {"acao": "esperar", "faltam_h": round(ESPERA_H[fam] - horas, 1), "familia": fam}

    if fam == "numero":
        alvo = variante_9(lead.get("telefone"))
        if not alvo:
            return {"acao": "humano",
                    "motivo": "número não recebe no WhatsApp e não é celular BR (fixo, "
                              "estrangeiro ou digitação incorreta) — sem variante para tentar"}
        if alvo in destinos:
            return {"acao": "humano", "motivo": "as duas formas do número (com e sem o 9º dígito) falharam"}
        # o número é que está errado, não o texto: mantém o template que já falhou
        tpl = next(iter(tpls), ESCADA_TEMPLATES[0])
        return {"acao": "reenviar", "destino": alvo, "template": tpl, "familia": fam,
                "motivo": "variante do 9º dígito"}

    # campanha vigente: o template dedicado do curso vem primeiro — é a oferta mais
    # forte que temos para recuperar esse lead, e some sozinho quando a promoção acaba
    tpl = promo_tpl if (promo_tpl and promo_tpl not in tpls) else _proximo_template(tpls, templates_meta)
    if not tpl:
        return {"acao": "humano", "motivo": "todos os templates aprovados já foram tentados"}
    return {"acao": "reenviar", "destino": lead.get("telefone"), "template": tpl, "familia": fam,
            "motivo": "template complementar após espera"}


def _proximo_template(usados, templates_meta=None):
    """Próximo template ainda não tentado. UTILITY primeiro: é a única categoria que
    escapa do limite de marketing que causou a recusa."""
    aprovados = None
    if templates_meta:
        aprovados = {t["name"] for t in templates_meta if t.get("status") == "APPROVED"}
        utility = [t["name"] for t in templates_meta
                   if t.get("status") == "APPROVED" and t.get("category") == "UTILITY"
                   and t.get("language", "").startswith("pt") and t["name"] not in usados]
        if utility:
            return utility[0]
    for nome in ESCADA_TEMPLATES:
        if nome not in usados and (aprovados is None or nome in aprovados):
            return nome
    return None




def rodar(est, ont, wa, agora=None, limite=20):
    """Uma passada de retentativas. Chamada pelo agendador; idempotente."""
    from atena.agentes.nutricao import _elegivel     # mesmo critério da reativação
    from atena.transporte.whatsapp import montar_params, template_promocional
    feitos, humanos, esperando = [], [], 0
    templates_meta = wa.templates_listar() if hasattr(wa, "templates_listar") else None
    # curso encerrado não tem oferta a fazer: retentar mandaria template de turma fechada
    candidatos = [l for l in est.leads_sem_entrega() if _elegivel(l, ont)]
    for lead in candidatos[:limite]:
        envios = est.envios_do_lead(lead["card_id"])
        p = plano(lead, envios, agora, templates_meta,
                  template_promocional(lead.get("curso"), ont, wa))
        if p["acao"] == "esperar":
            esperando += 1
        elif p["acao"] == "humano":
            # uma vez só por lead: o alerta é o registro de que a escada acabou
            if not (est.get_lead(lead["card_id"]).get("motivo") or "").startswith("entrega:"):
                est.set_lead(lead["card_id"], motivo=f"entrega: {p['motivo']}")
                est.add_alerta("entrega_impossivel",
                               f"Lead {lead.get('nome') or lead['card_id']} "
                               f"({lead.get('telefone')}) não recebe no WhatsApp: {p['motivo']}. "
                               f"Vale tentar por e-mail ou telefone.")
                humanos.append(lead["card_id"])
        elif p["acao"] == "reenviar":
            params = montar_params(p["template"], lead, ont)
            if params is None:
                # a Meta recusa variável vazia (curso sem data de inscrição na ontologia):
                # cai no template sem variável em vez de queimar a tentativa num 400
                p["template"], params = "retomar_atendimento", []
            r = wa.enviar_template(p["destino"], p["template"], params=params)
            if r.get("ok"):
                est.add_conversa(lead["card_id"], "agente",
                                 f"[template {p['template']}] retentativa ({p['motivo']})",
                                 wamid=r.get("wamid"), status="enviado", autor="template",
                                 destino=p["destino"])
                est.log_acao("reenvio", f"retentativa {p['familia']} -> {p['destino']}", lead["card_id"])
                feitos.append(lead["card_id"])
    if feitos or humanos:
        est.log_acao("sistema", f"reenvio: {len(feitos)} retentativa(s), {len(humanos)} para humano")
    return {"reenviados": feitos, "humano": humanos, "esperando": esperando}
