#!/usr/bin/env python3
"""
Atena · Contatos oficiais da Extensão FEQ — fonte única.

Compartilhado por dois consumidores que precisam exatamente do mesmo dado:
  • transporte/whatsapp.py::enviar_contato — cartão de contato no WhatsApp.
  • growth/pdf_guia.py — página "Nossos contatos" no guia em PDF (spec do item 1).

Dados retirados literalmente da página de contato do "Guia do Curso" de referência
(material oficial já publicado pela Extensão FEQ) — não são inventados aqui.
"""

CONTATOS_OFICIAIS = {
    "secretaria": {
        "rotulo": "Secretaria da Extensão FEQ",
        "telefone": "+551935214132",
        "telefone_exibicao": "(19) 3521-4132",
        "email": "sextfeq@unicamp.br",
    },
    "whatsapp": {
        "rotulo": "WhatsApp da Extensão FEQ",
        "telefone": "+5519982620072",
        "telefone_exibicao": "(19) 98262-0072",
        "email": None,
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
    contato = {
        "name": {"formatted_name": c["rotulo"], "first_name": c["rotulo"]},
        "org": {"company": "Extensão FEQ Unicamp"},
        "phones": [{"phone": c["telefone_exibicao"], "wa_id": c["telefone"].lstrip("+"), "type": "WORK"}],
        "urls": [{"url": SITE, "type": "WORK"}],
    }
    if c["email"]:
        contato["emails"] = [{"email": c["email"], "type": "WORK"}]
    return contato
