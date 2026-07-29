"""
Atena — pacote raiz.

FUSO: o sistema opera em America/Sao_Paulo (BRT). O VPS roda em UTC, então
`datetime.now()` ingênuo gravava 3h adiantado (uma cadência das 08:00 BRT virava
11:00 no banco). Em vez de caçar as ~29 chamadas de now() espalhadas, o pacote
fixa o fuso do PROCESSO no import: qualquer `datetime.now()` de qualquer módulo
passa a devolver BRT — no VPS, no Mac ou onde for. Respeita TZ já definido no
ambiente (permite sobrescrever em teste/deploy).
"""
import os
import time

os.environ.setdefault("TZ", "America/Sao_Paulo")
if hasattr(time, "tzset"):          # POSIX (Linux/macOS); no Windows vira no-op
    time.tzset()
