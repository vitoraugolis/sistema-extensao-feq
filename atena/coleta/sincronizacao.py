#!/usr/bin/env python3
"""
Atena · Coleta — reconciliação do que a Extecamp diz com o funil (spec 20 §4).

A Extecamp é a fonte da verdade sobre INSCRIÇÃO/MATRÍCULA: é lá que o dinheiro
entra e o aluno existe. Aqui essa verdade vira card no funil, sem nunca duplicar
pessoa e sem apagar o que o operador já sabia.

Chave de identidade, em ordem de força:
  1º) TELEFONE, vindo do "Relatório de telefones e e-mails" do próprio
      oferecimento. É a chave de todo o sistema (CLAUDE.md §7) e a única que
      reencontra com certeza o lead que já falava conosco pelo WhatsApp. Por
      isso a coleta lê o relatório ANTES da listagem: sabendo o telefone na hora
      de criar o card, não se cria duplicata para fundir depois;
  2º) nome normalizado dentro do MESMO curso, quando o relatório não trouxe o
      contato daquela pessoa;
  3º) só então um card novo, com id derivado de (curso, turma, nome), estável
      entre as abas — quem migra de Inscritos para Matriculados muda de coluna
      em vez de virar dois cards.
"""
import re
import json
import hashlib
import unicodedata
import datetime

# Aba da Extecamp -> coluna do nosso funil. A Extecamp manda nestas três: quem
# está lá está lá, e o funil só reflete.
FASE_POR_ABA = {"inscritos": "Inscrito", "matriculados": "Matriculado",
                "cancelados": "Perdido"}


def _agora():
    return datetime.datetime.now().isoformat(timespec="seconds")


def normalizar_nome(nome):
    """Maiúsculas, sem acento e sem espaço duplo — 'BÁRBARA  DÜREN' e
    'Barbara Duren' são a mesma pessoa para efeito de casamento."""
    t = unicodedata.normalize("NFD", (nome or "").strip())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t).upper()


def card_id_de(curso, turma, nome):
    """Id estável e determinístico: a MESMA pessoa no MESMO oferecimento gera o
    mesmo id em qualquer aba e em qualquer coleta futura. É o que faz a coleta
    ser idempotente sem precisar de tabela de 'já processados'."""
    chave = f"{curso}|{turma}|{normalizar_nome(nome)}"
    return "ext-" + hashlib.sha1(chave.encode()).hexdigest()[:14]


def _existente_por_nome(est, nome, curso):
    """Card que JÁ existe para esta pessoa neste curso (veio do WhatsApp/LP).
    Restringe ao curso de propósito: casar só por nome atravessaria cursos e
    juntaria homônimos que não têm nada a ver um com o outro."""
    alvo = normalizar_nome(nome)
    if not alvo:
        return None
    for l in est.listar_leads():
        if normalizar_nome(l.get("nome")) != alvo:
            continue
        if (l.get("curso") or "") in ("", curso):
            return l["card_id"]
    return None


def indexar_contatos(contatos):
    """Lista do relatório -> {nome normalizado: {telefone, email}}."""
    from atena.core.estado import normalizar_e164
    idx = {}
    for c in contatos or []:
        chave = normalizar_nome(c.get("nome"))
        if not chave:
            continue
        # CELULAR na frente do fixo: o canal do sistema é WhatsApp, e o relatório
        # costuma trazer os dois. Celular normalizado tem 13 dígitos (55+DD+9XXXXXXXX),
        # fixo tem 12 — guardar o fixo daria um card que nenhuma cadência alcança.
        nums = [normalizar_e164(t) for t in (c.get("telefones") or [])]
        tel = next((n for n in nums if len(n) == 13), nums[0] if nums else "")
        idx[chave] = {"telefone": tel, "email": (c.get("emails") or [""])[0]}
    return idx


def identificar(est, curso, turma, nome, contato=None):
    """card_id desta pessoa. Telefone primeiro — é o que reencontra o lead que já
    conversava conosco e evita um segundo card para a mesma pessoa."""
    tel = (contato or {}).get("telefone")
    if tel:
        canonico = est.lead_por_telefone(tel)
        if canonico:
            return canonico
    return _existente_por_nome(est, nome, curso) or card_id_de(curso, turma, nome)


def sincronizar(est, dados, contatos=None, agora=None):
    """Aplica UMA aba de UM oferecimento ao funil. Idempotente.

    Devolve o que MUDOU, não o que foi visto: rodar de novo sem novidade na
    Extecamp devolve tudo zerado, e é assim que o operador percebe que a coleta
    não trouxe nada novo em vez de reler a lista inteira.
    """
    agora = agora or _agora()
    aba = dados.get("aba")
    fase = FASE_POR_ABA.get(aba)
    curso, turma = dados.get("curso"), dados.get("turma")
    if not fase or not curso:
        return {"ok": False, "erro": f"aba desconhecida ou curso ausente ({aba}/{curso})"}

    criados, movidos, atualizados, inalterados = [], [], [], []
    for item in dados.get("itens", []):
        nome = item.get("nome") or ""
        if not nome:
            continue
        contato = (contatos or {}).get(normalizar_nome(nome)) or {}
        card_id = identificar(est, curso, turma, nome, contato)
        lead = est.get_lead(card_id)
        estado_ext = {
            "aba": aba, "curso": curso, "turma": turma,
            "numero": item.get("numero"),
            "taxa": item.get("taxa"), "documentacao": item.get("documentacao"),
            "situacao": item.get("situacao"), "acao": item.get("acao"),
            "desde": item.get("inscrito_em") or item.get("matriculado_em")
                     or item.get("cancelado_em"),
            "coletado_em": agora,
        }
        campos = {"extecamp_json": json.dumps(estado_ext, ensure_ascii=False)}
        if not lead:
            # Nasce JÁ com o contato quando o relatório do oferecimento o trouxe.
            # Sem contato o card ainda vale (o operador precisa saber que a pessoa
            # existe), mas nenhuma cadência o alcança — e é o correto: sem canal
            # não há como falar, e sem opt-in não se deveria.
            est.upsert_lead(card_id, nome=nome, telefone=contato.get("telefone") or "",
                            email=contato.get("email") or "", curso=curso, fase=fase,
                            estado_conversa="aguardando_1o_contato", **campos)
            criados.append({"card_id": card_id, "nome": nome, "fase": fase,
                            "com_contato": bool(contato.get("telefone"))})
            continue
        # card que já existia e ainda não tinha contato: completa, NUNCA sobrescreve
        # (o telefone que o operador já usou vale mais que o cadastro da Extecamp)
        for campo in ("telefone", "email"):
            if contato.get(campo) and not (lead.get(campo) or "").strip():
                campos[campo] = contato[campo]
        antes_fase = lead.get("fase")
        antes_ext = lead.get("extecamp_json") or ""
        if antes_fase != fase:
            est.set_lead(card_id, fase=fase, **campos)
            movidos.append({"card_id": card_id, "nome": nome, "de": antes_fase, "para": fase})
        elif antes_ext != campos["extecamp_json"]:
            # mesma fase, mas a situação dentro da Extecamp mudou (ex.: taxa
            # vencida virou paga) — é justamente o que muda a ação do operador
            ant = {}
            try:
                ant = json.loads(antes_ext) if antes_ext else {}
            except ValueError:
                ant = {}
            if (ant.get("taxa"), ant.get("documentacao")) != (estado_ext["taxa"],
                                                              estado_ext["documentacao"]):
                est.set_lead(card_id, **campos)
                atualizados.append({"card_id": card_id, "nome": nome,
                                    "de": ant.get("situacao"), "para": estado_ext["situacao"]})
            else:
                est.set_lead(card_id, **campos)   # só carimba a data da coleta
                inalterados.append(card_id)
        else:
            inalterados.append(card_id)

    return {"ok": True, "curso": curso, "turma": turma, "aba": aba,
            "totais_extecamp": dados.get("totais", {}),
            "vistos": len(dados.get("itens", [])),
            "criados": criados, "movidos": movidos, "atualizados": atualizados,
            "inalterados": len(inalterados)}


def resumo(*resultados):
    """Junta várias abas/cursos num número só — o que a tela mostra no fim."""
    r = {"criados": 0, "movidos": 0, "atualizados": 0, "vistos": 0, "cursos": set(), "erros": []}
    for x in resultados:
        if not x or not x.get("ok"):
            r["erros"].append((x or {}).get("erro", "falha desconhecida"))
            continue
        r["criados"] += len(x["criados"])
        r["movidos"] += len(x["movidos"])
        r["atualizados"] += len(x["atualizados"])
        r["vistos"] += x["vistos"]
        r["cursos"].add(f"{x['curso']}/{x['turma']}")
    r["cursos"] = sorted(r["cursos"])
    return r
