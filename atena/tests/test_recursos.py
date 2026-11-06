"""Vigia de recursos do VPS: limiares e cooldown (atena/ops/recursos.py)."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from atena.ops import recursos as R

ok = {"disco_pct": 40, "disco_livre_gb": 20, "carga15": 0.3, "cpus": 2, "mem_livre_mb": 600, "swap_pct": 5}
assert R.avisos(ok) == []
ruim = dict(ok, disco_pct=92, mem_livre_mb=80, swap_pct=70, carga15=4.0)
assert [t for t, _ in R.avisos(ruim)] == ["recurso_disco", "recurso_memoria", "recurso_swap", "recurso_cpu"]
assert R.avisos({k: v for k, v in ruim.items() if k not in ("mem_livre_mb", "swap_pct")})[-1][0] == "recurso_cpu"  # Mac: sem /proc


class Est:
    def __init__(self): self.cfg, self.alertas = {}, []
    def get_config(self, k): return self.cfg.get(k)
    def set_config(self, k, v): self.cfg[k] = v
    def add_alerta(self, tipo, texto): self.alertas.append(tipo)


R.medir = lambda raiz="/": ruim
e = Est()
assert len(R.checar(e, agora=1_000_000)) == 4
assert R.checar(e, agora=1_000_000 + 3600) == []                    # dentro do cooldown: silêncio
assert len(R.checar(e, agora=1_000_000 + R.COOLDOWN_S + 1)) == 4    # passou: avisa de novo
print("test_recursos: 6 checks OK")
