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

| Aba da Extecamp | Coluna do funil |
|---|---|
| Inscritos | Inscrito |
| Matriculados | Matriculado |
| Cancelados | Perdido |

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

## 5. Pendências

- **Layout real dos relatórios não conferido**: o parser é cego ao layout e foi
  exercitado contra duas fixturas com estruturas diferentes + a trava do rodapé sobre
  HTML real, mas ninguém viu a página verdadeira. Na primeira coleta de verdade, conferir
  se o número de contatos bate com o total de inscritos do oferecimento — o retorno da
  coleta já mostra os dois lado a lado.
- Homônimo no mesmo oferecimento **sem telefone no relatório** ainda colapsa em um card
  só; com telefone, não.
- Coleta é **manual** (o operador dispara). Automatizar depende de a sessão da Extecamp
  durar, o que não controlamos.

## 6. Critérios de aceitação

- AC1 Nenhuma credencial da Extecamp trafega ou é guardada pela Atena.
- AC2 Coletar duas vezes seguidas não cria card duplicado nem move ninguém.
- AC3 Lead que já existia com telefone é reaproveitado, não duplicado, e mantém o telefone.
- AC4 As 3 abas de um oferecimento concordam nos totais (checagem cruzada da coleta).
- AC5 Falha ao ler uma página não derruba a coleta das demais.
- AC6 A fila de trabalho ordena por urgência (termo pendente e taxa vencida primeiro).
- AC7 Contato da secretaria (rodapé de toda página) nunca vira telefone de lead.
- AC8 Telefone/nome já existentes no card não são sobrescritos pela Extecamp.
