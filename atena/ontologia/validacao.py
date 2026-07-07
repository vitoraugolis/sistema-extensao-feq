#!/usr/bin/env python3
"""
Atena · Validação SHACL reutilizável (usada pelo CLI validar.py e pelo Injetor).
"""
from pathlib import Path
from rdflib import Graph
from pyshacl import validate

AQUI = Path(__file__).parent


def grafo_de_catalogo(catalogo_dict_ou_path, vocab_path=None):
    """Constrói o grafo de dados (catálogo + vocabulário) para validar/consultar."""
    import json
    g = Graph()
    g.parse(vocab_path or AQUI / "vocabulario.ttl", format="turtle")
    if isinstance(catalogo_dict_ou_path, (dict, list)):
        g.parse(data=json.dumps(catalogo_dict_ou_path), format="json-ld")
    else:
        g.parse(catalogo_dict_ou_path, format="json-ld")
    return g


def validar(data_graph, vocab_graph=None, shapes_path=None):
    """Roda SHACL. Retorna (conforma: bool, texto: str)."""
    vocab_graph = vocab_graph or Graph().parse(AQUI / "vocabulario.ttl", format="turtle")
    shapes = Graph().parse(shapes_path or AQUI / "shapes.ttl", format="turtle")
    conforma, _, texto = validate(
        data_graph, shacl_graph=shapes, ont_graph=vocab_graph,
        inference="rdfs", advanced=True,
    )
    return conforma, texto
