#!/usr/bin/env python3
"""
Atena · Coleta — reconciliação do que a Extecamp diz com o funil (spec 20 §4).

A Extecamp é a fonte da verdade sobre INSCRIÇÃO/MATRÍCULA: é lá que o dinheiro
entra e o aluno existe. Aqui essa verdade vira card no funil, sem nunca duplicar
pessoa e sem apagar o que o operador já sabia.

Chave de identidade — o problema central deste módulo:
as abas do ADM Cursos mostram o NOME e mais nada. O sistema inteiro dedupe por
telefone (CLAUDE.md §7) e telefone não aparece nessas páginas. Então:
  1º) tenta casar com um card EXISTENTE por nome normalizado dentro do MESMO
      curso — é o lead que veio do WhatsApp/LP e agora apareceu na Extecamp;
  2º) só se não achar, cria card novo com id derivado de (curso, turma, nome),
      estável entre as abas — a mesma pessoa migrando de Inscritos para
      Matriculados cai no mesmo card em vez de virar dois.
Nome é chave mais fraca que telefone: homônimo no mesmo curso vira o mesmo card.
O caminho para fechar isso é o relatório de telefones/e-mails da própria
Extecamp (reltel.asp), ainda não mapeado — ver "Pendências" na spec 20.
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


def sincronizar(est, dados, agora=None):
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
        card_id = _existente_por_nome(est, nome, curso) or card_id_de(curso, turma, nome)
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
            # Card novo NASCE sem telefone: estas páginas não têm telefone. Fica
            # visível no funil (o operador precisa saber que a pessoa existe),
            # mas sem telefone nenhuma cadência o alcança — e é o correto: não
            # temos consentimento nem canal, só o fato de ele existir lá.
            est.upsert_lead(card_id, nome=nome, telefone="", curso=curso, fase=fase,
                            estado_conversa="aguardando_1o_contato", **campos)
            criados.append({"card_id": card_id, "nome": nome, "fase": fase})
            continue
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
