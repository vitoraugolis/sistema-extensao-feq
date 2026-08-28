#!/usr/bin/env python3
"""
Atena · Contatos oficiais da Extensão FEQ — fonte única.

Compartilhado por dois consumidores que precisam exatamente do mesmo dado:
  • transporte/whatsapp.py::enviar_contato — cartão de contato no WhatsApp.
  • growth/pdf_guia.py — página "Nossos contatos" no guia em PDF (spec do item 1).

Dados retirados literalmente da página de contato do "Guia do Curso" de referência
(material oficial já publicado pela Extensão FEQ) — não são inventados aqui.
E-mail da secretaria atualizado para extfeq@unicamp.br em 28/08/2026, por confirmação
direta do usuário (divergia do sextfeq@unicamp.br do Guia).
"""

CONTATOS_OFICIAIS = {
    "secretaria": {
        "rotulo": "Secretaria da Extensão FEQ",
        "telefone": "+551935213911",
        "telefone_exibicao": "(19) 3521-3911",
        "email": "extfeq@unicamp.br",
        # reteste via API em 28/08/2026 (enviar_template) devolveu wa_id igual ao
        # telefone e message_status "accepted" — o número passou a ter WhatsApp
        # (o bug de 10/08/2026, "este número não está no WhatsApp", não se repetiu).
        "whatsapp": True,
    },
    "whatsapp": {
        "rotulo": "WhatsApp da Extensão FEQ",
        "telefone": "+5519982620072",
        "telefone_exibicao": "(19) 98262-0072",
        "email": None,
        "whatsapp": True,
    },
}

SITE = "https://extensao.feq.unicamp.br"
INSTAGRAM = "@extensaofequnicamp"


def payload_contato(chave):
    """Monta o objeto 'contacts' no formato da WhatsApp Business Cloud API.
    None se a chave não existir — quem chama decide como falhar."""
    c = CONTATOS_OFICIAIS.get(chave)
    if not c:
        return None
    fone = {"phone": c["telefone_exibicao"], "type": "WORK"}
    if c.get("whatsapp"):
        # wa_id só entra quando o número TEM conta de WhatsApp de verdade — postar wa_id
        # para um número sem WhatsApp faz o app do lead tentar resolver aquele ID e
        # mostrar "este número não está no WhatsApp" (bug real, 10/08/2026).
        fone["wa_id"] = c["telefone"].lstrip("+")
    contato = {
        "name": {"formatted_name": c["rotulo"], "first_name": c["rotulo"]},
        "org": {"company": "Extensão FEQ Unicamp"},
        "phones": [fone],
        "urls": [{"url": SITE, "type": "WORK"}],
    }
    if c["email"]:
        contato["emails"] = [{"email": c["email"], "type": "WORK"}]
    return contato
