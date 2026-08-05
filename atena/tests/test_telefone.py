#!/usr/bin/env python3
"""Casamento de telefone tolerante ao 9º dígito móvel BR (wa_id do WhatsApp omite o 9)."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from atena.core.estado import Estado, _tel_key, normalizar_e164 as e164

ok = 0
def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)

# O bug real: formulário com 9, wa_id sem 9 -> mesma chave
check(_tel_key("+5579996529586") == _tel_key("557996529586"), "form(com 9) == wa_id(sem 9)")
check(_tel_key("+5549999548115") == _tel_key("554999548115"), "caso real 2")
check(_tel_key("+55 (19) 99999-0001") == _tel_key("5519999990001"), "formatação não importa")
# Não pode colidir pessoas diferentes
check(_tel_key("5579996529586") != _tel_key("5579996529587"), "números diferentes -> chaves diferentes")
check(_tel_key("5511999990001") != _tel_key("5519999990001"), "DDD diferente -> chave diferente")

# Fim a fim: lead criado com o número do formulário é achado pelo wa_id do WhatsApp
est = Estado(tempfile.mktemp(suffix=".db"))
est.upsert_lead("meta-5579996529586", nome="Ana", telefone="+5579996529586",
                curso="FEQ-0580", fase="Lead", estado_conversa="aguardando_resposta")
check(est.lead_por_telefone("557996529586") == "meta-5579996529586",
      "wa_id sem 9 encontra o lead do formulário (não cria órfão)")
check(est.lead_por_telefone("5579996529586") == "meta-5579996529586", "mesmo número exato também acha")
check(est.lead_por_telefone("5511987654321") is None, "número de outra pessoa não casa")


# --- Formato internacional: sem o 55 o link wa.me não abre a conversa ---------
# A regra é por COMPRIMENTO, não por prefixo, e não pode estragar estrangeiro.
check(e164("12997631036") == "5512997631036", "celular BR sem país ganha o 55")
check(e164("1621088346") == "551621088346", "fixo BR sem país ganha o 55")
check(e164("(19) 98867-3113") == "5519988673113", "máscara some")
check(e164("+5519996027820") == "5519996027820", "já internacional: só perde o +")
check(e164("557999711608") == "557999711608", "wa_id (12 díg, sem o 9) fica como está")
# DDD 55 = Santa Maria/RS. Checar "começa com 55" marcaria isto como pronto e o
# link nasceria quebrado; por comprimento (10 díg) ele é corrigido.
check(e164("5599999999") == "555599999999", "DDD 55 não é confundido com país")
# Há +351 (Portugal) na base: prefixar 55 às cegas destruiria o número.
check(e164("+351910800862") == "351910800862", "estrangeiro é preservado")
check(e164("") == "" and e164(None) == "", "vazio não quebra")

# Normalizar na ESCRITA: vale para ingestão, WhatsApp, LP e criação manual de uma vez
est2 = Estado(":memory:")
est2.upsert_lead("c1", nome="Bia", telefone="(19) 98867-3113", curso="FEQ-0480")
check(est2.get_lead("c1")["telefone"] == "5519988673113", "upsert grava internacional")
est2.set_lead("c1", telefone="11 3224-5100")
check(est2.get_lead("c1")["telefone"] == "551132245100", "set_lead também normaliza")
est2.upsert_lead("c2", nome="Rui", telefone="+351910800862", curso="FEQ-0480")
check(est2.get_lead("c2")["telefone"] == "351910800862", "estrangeiro não vira BR no banco")
# A dedupe olha DDD+8: normalizar não muda quem casa com quem.
check(est2.lead_por_telefone("19988673113") is None
      and est2.lead_por_telefone("1132245100") == "c1", "dedupe segue funcionando após normalizar")

print(f"\n{ok}/{ok} passaram.")
