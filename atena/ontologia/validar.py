#!/usr/bin/env python3
"""
Atena · Validação da fundação ontológica (offline, sem FARO/WhatsApp).
=====================================================================
1) Carrega catálogo (JSON-LD) + vocabulário (TTL).
2) Valida INTEGRIDADE via SHACL (deve conformar).
3) Teste NEGATIVO: injeta um plano de pagamento alucinado (30x R$ 866,09)
   e prova que a trava de coerência o REPROVA.
4) Demonstra as inferências de RUNTIME:
   - captabilidade: status da janela de inscrição vs. hoje (via Ontologia.captavel);
   - elegibilidade: nível do lead >= nível mínimo do curso (ordinal).

Uso:  python3 atena/ontologia/validar.py
"""
import sys, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import atena  # noqa: F401 — importar o pacote fixa o fuso do processo em BRT

from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import XSD
from pyshacl import validate

AQUI = Path(__file__).parent
FEQ = Namespace("https://feq.unicamp.br/ontologia#")


def carregar():
    g = Graph()
    g.parse(AQUI / "vocabulario.ttl", format="turtle")
    g.parse(AQUI / "catalogo.jsonld", format="json-ld")
    return g


def validar_shacl(data_graph, vocab):
    shapes = Graph().parse(AQUI / "shapes.ttl", format="turtle")
    conforma, _, texto = validate(
        data_graph, shacl_graph=shapes, ont_graph=vocab,
        inference="rdfs", advanced=True,
    )
    return conforma, texto


def teste_negativo(vocab):
    """Quebra a coerência de um plano e espera REPROVAÇÃO."""
    g = carregar()
    # Encontra o plano do FEQ-0480 e troca 19 -> 30 parcelas (alucinação clássica).
    plano = next(g.objects(FEQ["FEQ-0480"], FEQ.planoPagamento))
    g.set((plano, FEQ.numParcelas, Literal(30, datatype=XSD.integer)))
    conforma, _ = validar_shacl(g, vocab)
    return conforma  # esperado: False (a trava pegou)


def captabilidade(g, hoje):
    """Status pelo MOTOR de verdade (Ontologia.captavel), não por uma segunda
    implementação. Esta função reimplementava a regra em SPARQL e já vinha divergindo:
    não conhecia 'fora_do_prazo' (chamava de ENCERRADA) nem 'antecipada' (chamava de
    futura) — ou seja, o diagnóstico que existe para conferir o catálogo mostrava um
    estado diferente do que a operação usava."""
    from atena.ontologia.servico import Ontologia
    ont = Ontologia()
    linhas = []
    for cod in ont.cursos():
        cap = ont.captavel(cod, hoje)
        status = "ENCERRADA" if cap["status"] == "encerrada" else cap["status"]
        fim = cap.get("fim") or ""
        linhas.append((cod, status, "/".join(reversed(fim.split("-"))) if fim else "—"))
    return linhas


def elegibilidade(g, nivel_lead):
    """Para um nível de escolaridade do lead, diz curso a curso se é elegível."""
    q = """
    SELECT ?codigo ?ordemReq WHERE {
        ?c feq:codigo ?codigo ; feq:requisito/feq:nivelMinimo ?n .
        ?n feq:ordem ?ordemReq .
    } ORDER BY ?codigo """
    ordem_lead = int(next(g.objects(FEQ[nivel_lead], FEQ.ordem)))
    out = []
    for cod, ordemReq in g.query(q, initNs={"feq": FEQ}):
        out.append((str(cod), ordem_lead >= int(ordemReq)))
    return out


def main():
    hoje = datetime.date.today()
    g = carregar()
    vocab = Graph().parse(AQUI / "vocabulario.ttl", format="turtle")

    print(f"== Atena · validação da ontologia ({len(g)} triplas) · hoje={hoje:%d/%m/%Y} ==\n")

    ok, texto = validar_shacl(g, vocab)
    print(f"[1] SHACL integridade do catálogo: {'CONFORME ✓' if ok else 'VIOLA ✗'}")
    if not ok:
        print(texto); sys.exit(1)

    pegou = (teste_negativo(vocab) is False)
    print(f"[2] Teste negativo (30x R$ 866,09 no 0480): trava {'PEGOU ✓' if pegou else 'FALHOU ✗'}")

    print("\n[3] Captabilidade hoje (janela de inscrição):")
    for cod, status, fim in captabilidade(g, hoje):
        marca = "  " if status == "aberta" else "→ "
        print(f"    {marca}{cod}: {status} (inscrições até {fim})")

    print("\n[4] Elegibilidade de um lead com 'Ensino médio completo':")
    for cod, eleg in elegibilidade(g, "medio"):
        print(f"       {cod}: {'elegível' if eleg else 'INELEGÍVEL'}")

    print("\nOK." if ok and pegou else "\nFALHAS ACIMA.")
    sys.exit(0 if ok and pegou else 1)


if __name__ == "__main__":
    main()
