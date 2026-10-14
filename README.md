# Sistema Extensão FEQ · Atena

CRM operado por agentes de IA para captação de alunos dos cursos de Extensão da
Faculdade de Engenharia Química da Unicamp. Leads entram por campanhas Meta/Google,
agentes conversam pelo WhatsApp e a Central de Operação dá ao time o controle humano.

**Produção:** https://feq.vitoraugolis.com.br · **Specs:** `specs/` · **Contexto para IA:** `CLAUDE.md`

---

## Comece por aqui

1. **Leia o `CLAUDE.md`.** Ele é o contexto-mestre do projeto e o Claude Code carrega
   sozinho ao abrir a pasta. Explica a esteira do funil, as decisões já fixadas e as
   convenções. Não precisa ler as specs antes de mexer — o `CLAUDE.md` diz qual spec
   abrir para cada assunto.
2. **As specs em `specs/` são a fonte da verdade.** Se o código diverge da spec, a spec
   ganha. Se a spec está errada, corrija a spec *antes* de codar.
3. **Rode os testes.** São scripts stdlib, sem framework nem fixture mágica:

```bash
python3 -m pip install flask requests rdflib pyshacl
cp config.example.env .env      # preencha o que for usar; vazio = dry-run
for t in revisor orquestrador injetor agentes growth coleta_extecamp; do
  python3 atena/tests/test_$t.py
done
python3 atena/api/app.py        # Central em http://localhost:8095
```

Sem nenhuma chave preenchida o sistema **não quebra**: o cérebro cai no fallback
determinístico, o WhatsApp roda em dry-run e a publicação de LP não sai. É assim que se
desenvolve sem gastar token nem falar com lead de verdade.

## Três regras que valem mais que o resto

- **Nunca commite `.env` nem `atena/data/`.** O `.gitignore` já barra os dois. O banco
  tem nome, telefone e situação de pagamento de alunos reais.
- **Nada envia mensagem nem gasta dinheiro sem aprovação humana.** É a decisão de
  arquitetura mais importante do projeto (`specs/07`). Se sua mudança abre um caminho em
  que um agente fala com o lead sozinho, ela está errada.
- **Telefone é a chave de identidade.** Dedupe sempre por dígitos, nunca por nome —
  o mesmo aluno aparece grafado de três jeitos diferentes entre WhatsApp, LP e Extecamp.

## Mapa rápido

| Pasta | O que é |
|---|---|
| `specs/` | Especificações numeradas. `00` é a visão geral; comece por ela. |
| `atena/ontologia/` | Catálogo dos cursos em JSON-LD + SHACL. **Fonte da verdade sobre curso.** Só o Injetor escreve nele (`specs/13`). |
| `atena/core/` | Estado (SQLite), orquestrador, guardrails, autenticação. |
| `atena/agentes/` | Cérebro, ingestão, nutrição, curador, ontologista. |
| `atena/revisor/` | Revisor ancorado na ontologia — fail-closed: na dúvida, bloqueia. |
| `atena/coleta/` | Leitura do ADM Cursos da Extecamp (`specs/20`). |
| `atena/growth/` | Pacing de verba, Meta Ads/CAPI, landing pages (`specs/15`–`17`). |
| `atena/api/` + `atena/web/` | Central de Operação (Flask + SPA). |
| `atena/tests/` | Testes. Rodam sem rede e sem credencial. |
| `feq_*.py` | Legado v1, mantido só como referência. Não mexa. |

## Deploy

```bash
zsh atena/ops/deploy_vps.sh 144.33.22.236 ubuntu
```

O rsync **não sobrescreve** `.env`, `atena/data/` nem a ontologia: são dado vivo de
produção. A ontologia do repositório é semente de bootstrap — quem muda catálogo em
produção é o Injetor, pela Central, com validação e rollback.

## Licença

Projeto interno da Extensão FEQ · Unicamp. Uso restrito à equipe.
