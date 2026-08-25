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
(FEQ) → oferecimentos → as 3 abas de cada um.

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

**Identidade — o ponto frágil, declarado.** Essas telas mostram o **nome e mais nada**,
e o sistema inteiro dedupe por telefone (CLAUDE.md §7). Então:
1. tenta casar com um card **existente** por nome normalizado *dentro do mesmo curso* —
   é o lead que veio do WhatsApp/LP e agora apareceu na Extecamp;
2. só se não achar, cria card com id derivado de `(curso, turma, nome)`, estável entre
   as abas: a mesma pessoa migrando de Inscritos para Matriculados **muda de coluna em
   vez de virar dois cards**.

Card criado assim **nasce sem telefone** e a tela diz isso. É o correto: não temos canal
nem consentimento, só o fato de a pessoa existir lá — e sem telefone nenhuma cadência a
alcança. A Extecamp nunca apaga um telefone que já tínhamos.

Idempotência sem tabela de controle: o próprio id determinístico + comparação do estado
guardado (`leads.extecamp_json`) decidem se houve mudança. Recoletar sem novidade
devolve tudo zerado — é assim que o operador sabe que não veio nada novo.

## 5. Pendências

- **Telefone/e-mail dos inscritos**: o ADM Cursos tem o relatório `reltel.asp`
  ("Relatório de telefones e e-mails") por oferecimento. Com ele o casamento passa a ser
  por telefone — a regra do resto do sistema — e o card nasce contactável. Falta uma
  amostra do HTML dessa página para escrever o parser. **É a maior melhoria pendente.**
- Homônimo no mesmo oferecimento hoje colapsa em um card só (consequência de casar por
  nome); resolve-se junto com o item acima.
- Coleta é **manual** (o operador dispara). Automatizar depende de a sessão da Extecamp
  durar, o que não controlamos.

## 6. Critérios de aceitação

- AC1 Nenhuma credencial da Extecamp trafega ou é guardada pela Atena.
- AC2 Coletar duas vezes seguidas não cria card duplicado nem move ninguém.
- AC3 Lead que já existia com telefone é reaproveitado, não duplicado, e mantém o telefone.
- AC4 As 3 abas de um oferecimento concordam nos totais (checagem cruzada da coleta).
- AC5 Falha ao ler uma página não derruba a coleta das demais.
- AC6 A fila de trabalho ordena por urgência (termo pendente e taxa vencida primeiro).
