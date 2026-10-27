#!/usr/bin/env python3
"""
Submete à Meta o template feq_inscricao_efetuada (aviso de inscrição efetuada, que sai
quando a ficha é marcada como transcrita na Central). Fica PENDING até a Meta aprovar;
enquanto isso a Central marca a ficha normalmente e avisa que o lead não foi notificado.

  python3 -m atena.ops.criar_template_inscricao     # sem token: dry-run (mostra o payload)
"""
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import atena  # noqa: F401  fuso BRT
from atena.transporte.whatsapp import WhatsApp, CORPO_INSCRICAO_EFETUADA

if __name__ == "__main__":
    wa = WhatsApp(None, None)
    r = wa.criar_template("feq_inscricao_efetuada", "UTILITY", CORPO_INSCRICAO_EFETUADA,
                          exemplos=["Mariana",
                                    "Engenharia de Processos Químicos (FEQ-1000)",
                                    "mariana@email.com"])
    print(json.dumps(r, ensure_ascii=False, indent=2))
