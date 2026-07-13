#!/usr/bin/env python3
"""Casamento de telefone tolerante ao 9º dígito móvel BR (wa_id do WhatsApp omite o 9)."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from atena.core.estado import Estado, _tel_key

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

print(f"\n{ok}/{ok} passaram.")
