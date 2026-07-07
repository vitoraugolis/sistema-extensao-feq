#!/usr/bin/env python3
"""
Atena · Versionamento da ontologia (snapshots em arquivo + rollback).
Cada commit do Injetor grava uma cópia datada do catálogo e registra autor/motivo
no manifesto — proveniência e reversibilidade exigidas pela governança (spec 13).
"""
import json
import time
import datetime
from pathlib import Path

AQUI = Path(__file__).parent


class Versionamento:
    def __init__(self, catalogo_path=None, versoes_dir=None):
        self.catalogo = Path(catalogo_path or AQUI / "catalogo.jsonld")
        self.dir = Path(versoes_dir or self.catalogo.parent / "versoes")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.dir / "manifest.json"

    def _load(self):
        return json.loads(self.manifest.read_text(encoding="utf-8")) if self.manifest.exists() else []

    def _save(self, m):
        self.manifest.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    def historico(self):
        return self._load()

    def aplicar(self, conteudo, autor, motivo):
        """Aplica `conteudo` ao catálogo, guardando a PRÉ-IMAGEM (estado anterior)
        como o snapshot da versão — assim rollback(versao) desfaz esta mudança."""
        m = self._load()
        vid = f"v{len(m) + 1:04d}"
        pre = self.catalogo.read_text(encoding="utf-8") if self.catalogo.exists() else ""
        arq = self.dir / f"catalogo_pre_{vid}_{int(time.time())}.jsonld"
        arq.write_text(pre, encoding="utf-8")   # estado ANTERIOR à mudança
        self.catalogo.write_text(conteudo, encoding="utf-8")
        entry = {"versao": vid, "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                 "autor": autor, "motivo": motivo, "pre_imagem": arq.name}
        m.append(entry)
        self._save(m)
        return entry

    def rollback(self, versao):
        """Restaura a pré-imagem da versão (desfaz a mudança correspondente)."""
        e = next((x for x in self._load() if x["versao"] == versao), None)
        if not e:
            return False
        conteudo = (self.dir / e["pre_imagem"]).read_text(encoding="utf-8")
        self.catalogo.write_text(conteudo, encoding="utf-8")
        return True
