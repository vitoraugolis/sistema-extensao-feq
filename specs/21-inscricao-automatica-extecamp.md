# 21 — Inscrição automática na Extecamp (robô)

**Status:** implementado em 06/11/2026 (`atena/coleta/inscricao.py`, 24 checks em
`atena/tests/test_inscricao_extecamp.py`). Dry-run conferido contra o formulário real
(FEQ-0480/009). **Envio real ainda não feito**: ver § Ativação.

## Objetivo
A ficha que o lead entrega na etapa 2 da LP (spec 17) é lançada pelo robô no
formulário público de inscrição do curso — o mesmo que o lead preencheria — e o link
individual que a Extecamp devolve (`ok-inscricao_taxa.php?id1=…`) vai ao lead pelo
WhatsApp para ele validar a inscrição (termo + taxa). Substitui a transcrição manual.

## Fluxo na Extecamp (lido em 06/11/2026)
1. `GET /curso/inscricao/<SIGLA>/<TURMA>` → form que se auto-submete para
   `procura_aluno.php` (sigla, of, primeira=PRIMEIRA, cont=0).
2. `POST procura_aluno.php` → tela "Já fiz curso" (form1: `rg` → `procura_aluno.php?id=1`)
   e "Nunca fiz" (form2 → `inscricao.php?id1=…`).
3. **Ex-aluno**: busca por documento. Não achou = texto "Documento não localizado".
   Achou = pede a data de nascimento e abre a ficha pré-preenchida.
   Variantes testadas, nesta ordem (`variantes_documento`): como digitado, sem prefixo de
   UF, só dígitos, com hífen antes do dígito verificador, formato SP `12.345.678-9`,
   formato MG `12.345.678`, `MG…`/`MG-…`/`MG …` para emissor MG, CPF (CIN) com e sem máscara.
4. **Novo**: "Fazer primeiro cadastro" → ficha em branco.
5. `POST inscricao.php?id=1&…` com `cadastrar=cadastrar` → redireciona para
   `ok-inscricao*.php?id1=…` (sucesso). Sem esse link = falha.

A tela de data de nascimento do ex-aluno **não foi vista por dentro** (exige documento
cadastrado). O robô casa os campos por nome (dia/mes/ano ou nasc/data); tela desconhecida
vira pendência. Primeira inscrição real de ex-aluno: conferir e ajustar `_data_nascimento`.

## Mapeamento ficha → Extecamp (`valores_desejados`)
| Extecamp | Fonte | Regra |
|---|---|---|
| pf_nome, pf_email | etapa 1 (lead) | |
| DDD_CEL, celular | telefone do lead | `19` + `99999-0001` |
| pf_sexo | sexo | F/M (a Extecamp não aceita outro valor) |
| genero | genero | opções idênticas às da Extecamp (campo novo na LP) |
| do_ap_reg, pf_rg, pf_emissor | doc_tipo, doc_numero, doc_emissor | CIN: nº = CPF formatado |
| CPF1–4, DIA/MES/ANO | cpf, nascimento | |
| pf_estcivil, pf_naturae, pf_naturac | estado_civil, estado_nascimento, cidade_nascimento | |
| pf_nacion, pais_endereco | — | Brasil |
| pf_deficiencia/pf_def_qual | deficiencia | default NÃO |
| pf_cep … pf_cid | endereço | CEP completa rua/bairro/cidade (ViaCEP, como o navegador faz); cidade casada sem acento/caixa com `seleciona_cidade.php?id_estado=UF` |
| escolaridade | — | **Superior** |
| situacao_aluno | — | **Empregado** |
| textos acadêmicos/profissionais | — | **"Não informado"** |
| números (ano_conclusao, tempo_experiencia) | — | **"00"** |
| cidade_instituicao, pj_est/pj_cid | — | cidade/UF de moradia |
| forma_de_pagamento | forma_pagamento | casada pelo **nº de parcelas** (a LP mostra preço de campanha; a Extecamp, o de tabela) |
| misto, comprovante | — | NÃO; em nome do aluno |
| pf_fontinf, pf_tipo_contato | — | Redes sociais (RS), Celular |

Ex-aluno: o que a Extecamp trouxe preenchido **fica** (é o cadastro oficial); o robô só
completa o vazio, exceto as escolhas do curso (pagamento, misto, como soube, contato).

## Guardrails
- **Fail-closed**: qualquer obrigatório sem dado, opção não reconhecida, cidade fora da
  lista, tela desconhecida → `pendencias` (só nomes de campo, sem PII) e nada é enviado.
- Ex-aluno com nascimento recusado **não** cai para primeiro cadastro (duplicaria o aluno).
- Envio sem o link de volta → `robo_obs` começa com `ENVIADO` e o robô se recusa a repetir:
  a inscrição pode ter sido criada; humano confere no ADM Cursos e marca Transcrita.
- Lock por card: duas execuções simultâneas do mesmo lead são recusadas.
- Ficha básica (spec 17, só CPF/nascimento/pagamento) não tem os obrigatórios → sempre
  pendência; esses cursos seguem na transcrição manual.

## Integração
- `POST /api/inscricoes/<card_id>/extecamp` `{"valer": false|true}`. Central, aba
  Inscrições: **simular** (só leitura) e **Inscrever na Extecamp** (com confirmação).
- Sucesso = mesmo efeito de "Transcrita" (status, fase Inscrito, CAPI offline), com o
  link guardado em `inscricoes.link_extecamp` e enviado no template
  `feq_continuidade_inscricao` (nome, curso, link). Template não aprovado/opt-out: não
  envia, o link fica na ficha para envio manual.
- Falha → `inscricoes.robo_obs` + alerta `inscricao` na caixa de Notificações.
- **Automático**: `INSCRICAO_AUTO=1` no `.env` faz o robô rodar em thread assim que a
  etapa 2 chega (`/webhook/inscricao`). Sem a flag, só pelo botão.

## Ativação
1. Primeira inscrição real pelo botão, com um lead verdadeiro, conferindo no ADM Cursos.
2. Um ex-aluno real para validar a tela de nascimento.
3. Então `INSCRICAO_AUTO=1` no VPS.
