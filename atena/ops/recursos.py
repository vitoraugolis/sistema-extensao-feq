"""Vigia de recursos do VPS (disco, memória, swap, CPU) — a máquina é compartilhada.

O tick de operações (`app.py::_operacoes`) chama `checar(est)` a cada ~10 min; cada
problema vira alerta na Central (e WhatsApp ao gestor, via `_despachar_alertas`),
no máximo 1 por tipo a cada 6h para não virar spam.

Só stdlib. Fora do Linux (Mac de dev) memória/swap ficam de fora — não há /proc.
Uso manual:  python3 -m atena.ops.recursos
"""
import os
import shutil
import time

# ponytail: limiares fixos pensados p/ a VM de 1 GB; vire config quando trocar de máquina
DISCO_MAX_PCT = 85        # disco / usado
MEM_MIN_MB = 120          # MemAvailable (abaixo disso a Atena começa a ir para o swap)
SWAP_MAX_PCT = 50
CARGA_MAX_POR_CPU = 1.5   # load average de 15 min / nº de CPUs
COOLDOWN_S = 6 * 3600


def medir(raiz="/"):
    d = shutil.disk_usage(raiz)
    m = {"disco_pct": round(100 * d.used / d.total, 1), "disco_livre_gb": round(d.free / 1e9, 1),
         "carga15": round(os.getloadavg()[2], 2), "cpus": os.cpu_count() or 1}
    try:
        with open("/proc/meminfo") as f:
            info = {l.split(":")[0]: int(l.split()[1]) for l in f}   # valores em kB
        m["mem_livre_mb"] = info["MemAvailable"] // 1024
        if info.get("SwapTotal"):
            m["swap_pct"] = round(100 * (info["SwapTotal"] - info["SwapFree"]) / info["SwapTotal"], 1)
    except OSError:
        pass
    return m


def avisos(m):
    """Medição -> lista de (tipo, texto). Função pura (testável)."""
    out = []
    if m["disco_pct"] >= DISCO_MAX_PCT:
        out.append(("recurso_disco", f"Disco {m['disco_pct']}% cheio (sobram {m['disco_livre_gb']} GB). "
                                     "Ver: sudo du -xh / --max-depth=2 | sort -h | tail"))
    if m.get("mem_livre_mb") is not None and m["mem_livre_mb"] < MEM_MIN_MB:
        out.append(("recurso_memoria", f"Só {m['mem_livre_mb']} MB de RAM disponível. "
                                       "Ver: ps -eo rss,comm --sort=-rss | head"))
    if m.get("swap_pct", 0) >= SWAP_MAX_PCT:
        out.append(("recurso_swap", f"Swap {m['swap_pct']}% em uso — a máquina está sem RAM e vai ficar lenta."))
    if m["carga15"] > CARGA_MAX_POR_CPU * m["cpus"]:
        out.append(("recurso_cpu", f"CPU sobrecarregada: carga {m['carga15']} (15 min) para {m['cpus']} CPU(s). "
                                   "Ver: top -o %CPU"))
    return out


def checar(est, agora=None):
    """Mede, e grava alerta para cada problema fora do cooldown. Devolve os tipos alertados."""
    agora = agora or time.time()
    feitos = []
    for tipo, texto in avisos(medir()):
        chave = f"op_alerta_{tipo}"
        if agora - float(est.get_config(chave) or 0) > COOLDOWN_S:
            est.set_config(chave, str(agora))
            est.add_alerta(tipo, texto)
            feitos.append(tipo)
    return feitos


if __name__ == "__main__":
    m = medir()
    print(m)
    for _, t in avisos(m) or [("", "tudo dentro dos limites")]:
        print("-", t)
