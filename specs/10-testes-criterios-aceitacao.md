# 10 — Testes e Critérios de Aceitação

## Filosofia
Cada componente tem um endpoint de simulação que permite testar **offline**, sem depender
de Meta/WhatsApp. Validar o caminho de dados antes de ligar tráfego real.

## Fase 1 — Ingestão (`:8080`)
```bash
# criação + dedupe (rodar 2x; 2º = atualizado)
curl -X POST localhost:8080/simulate -H 'Content-Type: application/json' \
  -d '{"nome":"Maria","telefone":"+55 (19) 99999-0001","email":"m@ex.com","curso":"FEQ-0480"}'
# ler o card e conferir campos
curl -s "$FARO_BASE/api-cards-get?card_id=<id>" -H "Authorization: Bearer $FARO_KEY" | python3 -m json.tool
```
- AC1 2ª chamada → `"acao":"atualizado"` (sem duplicar).
- AC2 Card em "Lead" com todos os campos preenchidos.

## Fase 2 — Revisor (`:8090`)  ✅ testado (estrutural: 6/6 PASS)
```bash
curl -X POST localhost:8090/reviewer -H 'Content-Type: application/json' \
  -d '{"curso":"FEQ-0480","texto":"O curso custa R$ 999,00 e vai até 01/01/2030."}'  # bloqueia
curl -X POST localhost:8090/reviewer -H 'Content-Type: application/json' \
  -d '{"curso":"FEQ-0480","texto":"A taxa é R$ 69,00 e são 19x de R$ 866,09."}'       # aprova
```
- AC1 Bloqueia preço/data/carga fora da base.
- AC2 Aprova valores reais do curso.

## Fase 2 — Cérebro (`:8090`)
```bash
curl -X POST localhost:8090/converse -H 'Content-Type: application/json' \
  -d '{"curso":"FEQ-0480","mensagem_lead":"tenho superior em engenharia, quero o curso de IA"}'
curl -X POST localhost:8090/converse -H 'Content-Type: application/json' \
  -d '{"curso":"FEQ-0480","mensagem_lead":"tenho ensino médio, posso fazer?"}'
```
- AC1 Superior → `qualificar`/`continuar`. Médio → `descartar` (gentil, sem inventar).
- AC2 "quero sair" → `encerrar_optout`.
- AC3 Toda mensagem retornada passou pelo Revisor (`revisor.aprovado=true`).

## Teste unitário do Revisor estrutural (sem rede)
Importar `revisor_estrutural(curso, texto)` e checar `aprovado`/`bloqueios`. Casos mínimos:
preço real (✓), preço inventado (✗), data real (✓), data inventada (✗), carga em curso de
carga nula (✗), carga real (✓).

## Critérios de homologação do use case (Cenário A)
| Critério | Métrica |
|---|---|
| Deduplicação | 0 leads duplicados no FARO. |
| Grounding | 0 mensagens com preço/data/requisito fora do catálogo. |
| Qualificação | inelegível nunca vira Prospect. |
| Opt-out | 100% honrado, imediato. |
| Portão humano | nenhuma matrícula/gasto de ads autônomo. |
| SLA | cards parados detectados por `watch-late` e reengajados. |

## Sugestão para o Claude Code
Ao implementar a Fase 2b (transporte) ou as Fases 3/4, escrever primeiro o teste de
simulação correspondente (endpoint `/...` offline), depois a integração externa.
