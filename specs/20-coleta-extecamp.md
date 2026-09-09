# 20 — Coleta de Dados: integração com o ADM Cursos da Extecamp

**Módulos:** `atena/coleta/` (parsers + reconciliação) · `atena/web/coletor-extecamp.js`
(coletor) · aba **Coleta de Dados** na Central. **Status:** implementado; leitura apenas.

## 1. Por que existe

A Extecamp é a **fonte da verdade sobre inscrição e matrícula** — é lá que o aluno
existe de verdade e o dinheiro entra. Até aqui o sistema só sabia o que ele mesmo tinha
causado (lead da campanha, ficha da LP) e dependia de alguém abrir o ADM Cursos e olhar.
Quem aceitou o termo da taxa, quem deixou o boleto vencer, quem já pode enviar
documentos: tudo isso estava fora do alcance dos agentes.

## 2. A estratégia — e por que não é a óbvia

O login do ADM Cursos exige **humano + verificação anti-robô**. Isso elimina de saída a
solução preferida (o VPS raspando sozinho com `requests`) e define todo o resto:

> A sessão autenticada vive no **navegador do operador**. O coletor tem de rodar
> **dentro da aba da Extecamp** — de qualquer outra origem o navegador recusa ler
> aquelas páginas, e faz certo.

Alternativas descartadas:

| Caminho | Por que não |
|---|---|
| `requests` no VPS | não passa pelo captcha; exigiria guardar a senha da Extecamp |
| Copiar o cookie de sessão para a Central | pede ao operador que manuseie credencial; quebra a cada relogin |
| Playwright/Selenium local | dependência pesada contra CLAUDE.md §7, e no VPS não há humano para logar |
| `fetch` da Central para a Extecamp | bloqueado por CORS — e deve ser mesmo |

**Desenho adotado: coletor burro, backend esperto.** Um bookmarklet injeta o coletor na
aba já logada. Ele faz uma coisa só: baixa a página, manda o HTML para a Central e
pergunta *"e agora?"*. Quem conhece a estrutura do ADM Cursos é o backend, em Python
testado contra fixtures reais. HTML de terceiro muda sem avisar, e essa lógica não pode
morar como JavaScript sem teste no navegador de ninguém.

```
operador → "Logar no sistema" → login + captcha (na Extecamp)
        → "Abrir sessão de coleta" → token 12h + bookmarklet
        → clica o bookmarklet DENTRO do ADM Cursos
              coletor: baixa página → POST /api/coleta/pagina → recebe próximas URLs → repete
```

O **token** (não o cookie da Central) autentica a escrita: a Central e a Extecamp são
origens diferentes e nossa sessão não chega lá.

## 3. O que é lido

Roteiro: entradas (`cursos-em-inscricao.asp`, `admcursosand_ext_pre.asp`) → unidade
(FEQ) → oferecimentos → **os 3 relatórios de contato** → as 3 abas de cada um.

**Os relatórios vêm primeiro, e isso é a decisão de desenho mais importante daqui.**
Cada aba tem o seu "Relatório de telefones e e-mails" (`reltel.asp`, `reltel_mat.asp`,
`reltel_canc.asp`) e é ele — não a listagem — que traz o **telefone**. Como telefone é a
chave de identidade de todo o sistema (CLAUDE.md §7), lê-lo *antes* de criar qualquer
card faz o lead que já falava conosco pelo WhatsApp ser **reencontrado** em vez de virar
um segundo card para fundir depois. Foi assim que o caso real apareceu no teste: o card
do WhatsApp dizia "Barbara D." e a Extecamp diz "BÁRBARA ABADE MENDES DÜREN" — nome
nunca casaria; telefone casa.

O parser dos relatórios é **cego ao layout**: casa pelo *formato* do dado (e-mail e
telefone brasileiro têm forma inequívoca) em vez de posição de coluna, então o mesmo
código serve às três telas. Duas travas obrigatórias:
- **o rodapé é cortado antes de procurar contato** — os telefones da própria secretaria
  aparecem em toda página do sistema e, sem isso, todo inscrito herdaria o número dela
  (trava estrutural, não lista de números proibidos: continua valendo se eles trocarem);
- **celular na frente do fixo** — o canal é WhatsApp; guardar o fixo daria um card que
  nenhuma cadência alcança.

**A cor da linha é a única fonte do estado da taxa** — não existe coluna de texto com
esse dado. Combinada com o *Status documentação*, ela vira a ação do operador:

| Cor | Estado | Documentação | Ação |
|---|---|---|---|
| cinza `#A9A9A9` | não aceitou o termo | — | cobrar o aceite |
| amarelo `#FFFF00` | aceitou, não pagou | — | lembrar do boleto |
| vermelho `#F78181` | taxa venceu | — | pedir novo boleto à secretaria |
| verde `#58FA58` | taxa paga | PENDENTE | orientar envio de documentos |
| verde | taxa paga | EM ANÁLISE | aguardar a secretaria |
| verde | taxa paga | APROVADO | aguardar o 1º boleto do curso |

Detalhes que o parser precisa respeitar (todos verificados contra HTML real):
- **iso-8859-1**: ler como UTF-8 corromperia todo nome acentuado — a maioria deles.
- **HTML malformado** (`<tr>`/`<td>` sem fechar, `<form>` dentro de `<table>`): o parser
  é um `HTMLParser` tolerante; regex quebraria no primeiro oferecimento diferente.
- **Tokens opacos**: `sigla`/`of`/`id1..id4` são binário url-encoded e viajam
  **intactos** — decodificar muda o valor e a URL seguinte cai em outro curso.
- Os totais aparecem com rótulos diferentes por aba e um deles com typo do próprio
  sistema (*"Tota de cancelados"*) — casar por radical, não por frase.

**Escopo**: só os oferecimentos que este sistema opera (`cursos_metas`). A FEQ tem 49
cursos em inscrição e 220 em andamento; varrer tudo seriam ~800 requisições ao servidor
da Extecamp para trazer turmas que não estão em campanha nenhuma. `todos:true` libera.

## 4. Como vira funil

A esteira ganhou os degraus que só o ADM Cursos revela. Antes, tudo entre inscrever-se
e matricular-se era um "Inscrito" só — e era exatamente aí que o funil sangrava sem que
ninguém visse onde.

| Aba | Cor / documentação | Coluna do funil |
|---|---|---|
| Inscritos | cinza (não aceitou o termo) | **Inscrito** |
| Inscritos | amarelo (aceitou, não pagou) | **Taxa aceita** |
| Inscritos | vermelho (aceitou, venceu) | **Taxa aceita** |
| Inscritos | verde + doc PENDENTE | **Taxa paga** |
| Inscritos | verde + doc EM ANÁLISE / APROVADO | **Documento entregue** |
| Matriculados | — | **Matriculado** |
| Cancelados | — | **Cancelado** |

Duas decisões que parecem detalhe e não são:
- **vermelho continua em "Taxa aceita"**. A pessoa *aceitou* o termo; o que venceu foi o
  boleto. Rebaixá-la para "Inscrito" apagaria um degrau que ela subiu — o que fazer com
  ela vem da *ação*, não da coluna.
- **"Cancelado" é coluna separada de "Perdido"**. Perdido é perda nossa (gate acadêmico,
  opt-out); Cancelado é quem já tinha se inscrito e desistiu. Problemas diferentes, donos
  diferentes, e juntá-los esconderia a evasão pós-inscrição.

**Fase é ordinal, não string.** Quem precisa saber "esse lead é inscrito?" usa
`estado.alcancou(fase, "Inscrito")`, nunca uma lista fixa: a contagem de inscritos é
cumulativa, senão o número *cairia* toda vez que alguém avançasse de degrau. Foi o que
teria acontecido em `medicao.py` e no `/api/stats`, silenciosamente.

**Identidade, em ordem de força:**
1. **telefone** do relatório de contato — reencontra com certeza o lead que já falava
   conosco, mesmo com o nome grafado de outro jeito;
2. **nome normalizado** dentro do mesmo curso, quando o relatório não trouxe aquela pessoa;
3. só então card novo, com id derivado de `(curso, turma, nome)`, estável entre as abas:
   quem migra de Inscritos para Matriculados **muda de coluna em vez de virar dois cards**.

Quem não aparece no relatório fica **sem telefone** e a tela diz isso — não se inventa
contato. O que já era nosso nunca é sobrescrito: telefone e nome que o operador já usou
valem mais que o cadastro da Extecamp.

Idempotência sem tabela de controle: o próprio id determinístico + comparação do estado
guardado (`leads.extecamp_json`) decidem se houve mudança. Recoletar sem novidade
devolve tudo zerado — é assim que o operador sabe que não veio nada novo.

## 5. Conferido contra o sistema real (30/08/2026)

A pendência principal desta spec ("ninguém viu a página verdadeira") foi fechada: com um
login humano no ADM Cursos, os nove oferecimentos que este sistema opera foram lidos e
**em todos o parser leu exatamente o total que a própria página declara**, e o relatório
de contatos devolveu exatamente o mesmo número de pessoas da listagem.

O que a página real ensinou, e que a spec não sabia:

- **A listagem "em andamento" liga por `cursoand.asp`, não `curso.asp`.** O parser aceitava
  só o segundo, então devolvia zero oferecimento para todo curso já iniciado — em silêncio,
  porque lista vazia é indistinguível de "nenhum curso nosso aqui". Corrigido
  (`_RE_LINK_CURSO`) e coberto por teste. As colunas das duas listagens são idênticas.
- **Uma listagem traz TODAS as turmas do curso** (a FEQ-0480 tem cinco em andamento, desde
  2024), e o mesmo curso aparece nas duas listas. Só a **turma corrente** é funil: as
  anteriores trariam coortes inteiras de 2024/2025 para dentro do pipeline de captação e
  inflariam a conversão com gente que campanha nenhuma trouxe. A regra é *maior turma por
  curso* (`"018" > "017"`, string zero-padded), com a trava `coleta_alvos` valendo entre as
  duas listagens e zerada a cada sessão de coleta.
- **As listagens não paginam** no volume real: 43 matriculados vieram numa página só, e a
  aba de matriculados chega a declarar *"de 0 páginas"*. O `_paginas` devolve 1 e nada é
  perdido — mas a suposição "20 em 20" da spec original não se confirmou.
- **Os relatórios de contato não têm o `<div id="rodape">`** — são telas de impressão, sem
  a moldura do site. A trava do rodapé fica inerte neles (nenhum telefone institucional
  aparece na página). Mantida assim mesmo: é defesa barata e as *listagens* têm o rodapé.
- O typo do próprio sistema (**"Tota de cancelados"**) existe mesmo, e os totais aparecem
  com rótulos diferentes por aba ("Total de alunos: 43" na de matriculados) — casar por
  radical estava certo.
- O `id` da unidade é binário url-encoded de verdade (`%81g%C8%CA`), e a conta da
  coordenação enxerga **uma** unidade só (a FEQ) — a lista de unidades com um item é o
  escopo do login, não um filtro nosso.

**Retrato de 30/08/2026** (baseline para conferir a próxima coleta):

| Curso | Turma | Inscritos | Matriculados | Cancelados | Mín. | Máx. |
|---|---|---|---|---|---|---|
| FEQ-1000 | 018 | 14 | 43 | 27 | 46 | 90 |
| FEQ-0480 | 009 | 21 | 0 | 1 | 45 | 60 |
| FEQ-0580 | 001 | 13 | 0 | 8 | 20 | 60 |
| FEQ-0670 | 001 | 11 | 0 | 1 | 20 | 60 |
| FEQ-0360 | 007 | 8 | 0 | 0 | 20 | 60 |
| FEQ-0331 | 004 | 7 | 0 | 1 | 15 | 60 |
| FEQ-0299 | 001 | 0 | 1 | 1 | 1 | 300 |
| FEQ-0295 | 001 | 0 | 0 | 0 | 20 | 60 |
| FEQ-0499 | 006 | 0 | 0 | 0 | 20 | 40 |

Divergências de cadastro que a coleta revelou:
- **FEQ-0499 está em inscrição e é a turma 006**; `cursos_metas` diz `001`. (Fecha também o
  risco aberto no CLAUDE.md §10 sobre a disponibilidade do curso.)
- **FEQ-0333 e FEQ-0655 não aparecem em nenhuma das duas listagens** — não há oferecimento
  em inscrição nem em andamento.

### 5.1 Por que a primeira coleta real não funcionou

Quatro falhas em série, todas **silenciosas** — nenhuma dava erro na cara do operador.
Ficam registradas porque o modo de falha é o mesmo sempre que este desenho
(código nosso rodando na origem de terceiro) encostar em algo novo.

| # | O que estava errado | Como aparecia |
|---|---|---|
| 1 | **O atalho apontava para `http://`.** `request.url_root` saiu http mesmo com o `ProxyFix` — o `X-Forwarded-Proto` do Caddy não chegou como esperado. Script http em página https é conteúdo ativo misto. | O navegador bloqueia sem erro visível: **clicar o atalho não fazia absolutamente nada**. Nem painel, nem aviso. |
| 2 | **`/api/coleta/pagina` exigia sessão da Central.** `LIVRE` só tinha `/api/login` e `/health`, e o guard barra tudo sob `/api` — mas o coletor vem da origem da Extecamp, onde o cookie não existe (é *por isso* que o token existe). | Preflight CORS → 401, o `fetch` nem saía. A rota nunca funcionou desde que foi escrita. |
| 3 | **O erro acusava o sistema errado.** Falhar ao ler da Extecamp e falhar ao entregar na Central caíam no mesmo `catch`, rotulado com o nome do `.asp`. | `⚠ cursos-em-inscricao.asp: Failed to fetch` mandava procurar o problema na Extecamp, quando o problema era nosso. |
| 4 | **Sessão vencida da Extecamp não dá 401.** Dá **200 com ~190 bytes** de `<meta refresh>` para `/admcursos/index_seg.htm`. | O parser acha zero linha em toda página e a coleta reporta *"0 inscritos"* em todos os cursos — que é bem diferente de "não estou vendo nada". |

O que ficou no lugar: `_base_coleta()` promove o domínio público a https (loopback continua
http, que é o correto em dev); `/api/coleta/pagina` está em `LIVRE` e se autentica pelo
token (`/api/coleta`, que devolve PII de lead, **não** está); os dois lados do erro se
identificam e um 401/403 **para** a coleta em vez de repetir o mesmo aviso 50 vezes;
`deslogado()` reconhece o casco de redirecionamento e trata como fatal.

Na Central, a aba Coleta ganhou o endereço do atalho em campo copiável (arrastar falha
calado: o favorito vira link comum) e um botão **Testar**, que confirma se a sessão de
coleta responde antes de o operador ir até a Extecamp descobrir que não.

### 5.2 Primeira coleta real bem-sucedida (30/08/2026 18:34)

58 páginas, **130 cards novos e 21 leads que já existiam reencontrados e movidos** de
coluna — o casamento por telefone fazendo o trabalho que motivou ler os relatórios antes
das listagens. Os nove oferecimentos bateram com a varredura manual, um a um. Recoletar
em seguida: **0 criados, 0 movidos, 0 atualizados** (AC2 verificado em produção).

## 6. O número de inscritos no Painel

O Painel mostra, ao lado da captabilidade de cada curso, **inscritos + matriculados da
turma corrente**, com o mínimo de alunos ao lado (`57 insc. / 46`).

A soma não é enfeite: a aba "Inscritos" do ADM Cursos **esvazia** conforme a turma se
matricula. Mostrar só ela faria o número despencar exatamente quando o curso deu certo —
o FEQ-1000/018 leria "14" tendo 57 pessoas dentro. É a mesma regra cumulativa que
`estado.alcancou` impõe ao funil.

O número vem dos rótulos *"Total de ..."* da própria Extecamp (guardados em
`config['coleta_totais:<curso>']` pelo `_guardar_totais`), **não** da contagem de cards:
card só existe para quem a reconciliação alcançou, e o Painel precisa de um número que
feche com a secretaria. Sem coleta ainda, a tela mostra `—`, nunca `0` — "ainda não
olhamos" é uma afirmação diferente de "ninguém se inscreveu".

## 7. Runbook: o que fazer quando o catálogo muda

A coleta se ajusta sozinha na maior parte dos casos, porque lê a listagem em vez de uma
lista fixa. O que exige ação:

| Aconteceu | O que a coleta faz | O que você faz |
|---|---|---|
| **Nova turma** do mesmo curso | passa a coletar a turma maior e o Painel troca de número sozinho | nada |
| Curso sai de **inscrição** e entra em **andamento** | continua sendo coletado pela outra listagem; se não houver turma nova, é ele mesmo | nada |
| Curso ganha turma nova **e** mantém a anterior rodando | coleta só a nova (trava `coleta_alvos`) | nada |
| **Curso novo** no catálogo | é ignorado: a coleta só varre o que está em `cursos_metas` | cadastrar o curso (Injetor / `set_oferecimento`) — sem isso ele não aparece |
| Curso **sem oferecimento** (FEQ-0333, FEQ-0655) | nada a coletar; Painel mostra `—` | confirmar com a coordenação se sai do catálogo |
| Turma do `cursos_metas` **diverge** da Extecamp (FEQ-0499) | a coleta usa a da Extecamp, que é a verdade | corrigir o cadastro para o Growth não pautar a turma errada |
| Curso **adiado** — mesma turma, datas novas (FEQ-0580) | o Painel acusa a divergência de datas | `POST /api/injetor/datas` (prorrogação): mesma turma, mesma URL, só as datas. Ao aprovar, a LP é republicada |
| Turma existe no ADM Cursos mas o **link público ainda não abriu** (FEQ-0499/006) | acusa "turma aberta e sem link de inscrição cadastrado" | esperar o link; quando sair, `POST /api/injetor/oferecimento` (turma + URL), que também atualiza `cursos_metas` e republica a LP |
| A Extecamp **muda o HTML** | a página cai no `except` e as demais seguem (AC5) | rodar `atena/tests/test_coleta_extecamp.py`; a divergência aparece como total declarado ≠ lido |

**Conferência de cada coleta** (o retorno já mostra os dois lado a lado): para cada
oferecimento, `vistos` tem de bater com `totais_extecamp`, e o relatório de contatos tem
de trazer o mesmo número de pessoas da listagem. Divergiu, o parser errou — não a Extecamp.

## 8. Pendências

- Homônimo no mesmo oferecimento **sem telefone no relatório** ainda colapsa em um card
  só; com telefone, não.
- Coleta é **manual** (o operador dispara). Automatizar depende de a sessão da Extecamp
  durar, o que não controlamos.
- A coleta não enxerga turmas **encerradas** (só "em inscrição" e "em andamento"). Histórico
  de coortes antigas, se um dia interessar, sai de `cursos-concluidos.asp`.

## 9. Critérios de aceitação

- AC1 Nenhuma credencial da Extecamp trafega ou é guardada pela Atena.
- AC2 Coletar duas vezes seguidas não cria card duplicado nem move ninguém.
- AC3 Lead que já existia com telefone é reaproveitado, não duplicado, e mantém o telefone.
- AC4 As 3 abas de um oferecimento concordam nos totais (checagem cruzada da coleta).
- AC5 Falha ao ler uma página não derruba a coleta das demais.
- AC6 A fila de trabalho ordena por urgência (termo pendente e taxa vencida primeiro).
- AC7 Contato da secretaria (rodapé de toda página) nunca vira telefone de lead.
- AC8 Telefone/nome já existentes no card não são sobrescritos pela Extecamp.
