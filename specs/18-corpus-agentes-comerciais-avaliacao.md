# Spec 18 — Corpus, Agentes Comerciais e Avaliação do Sistema

> Status: **item 1 implementado**; itens 2 e 3 especificados com bloqueios e
> pré-condições explícitas. Escrita em 31/07/2026.

## 0. Restrições reais (medidas, não supostas)

Qualquer desenho aqui é limitado por três fatos verificados no ambiente:

| Fato | Medição | Consequência de projeto |
|---|---|---|
| VPS pequena | 414 MB RAM total, **177 MB livres**; sem `numpy`, `sklearn`, `bs4` | Nada de Chroma/FAISS/Qdrant nem modelo de embedding local (torch ≈ 2 GB). Vetores vivem no SQLite; similaridade em Python puro. |
| Sinal de conversão quase nulo | 253 leads → **1 Inscrito**, 0 Matriculados; 99 `inicio_inscricao` × 91 `verificacao_negativa` | **RL/algoritmo genético é prematuro.** Com 1 rótulo positivo, otimizador ajusta ruído. Primeiro medir e rotular, depois otimizar. |
| Verdade de conversão é manual | Estado "Inscrito" hoje vem da aba Verificação, preenchida à mão | Sem o item 2a (Extecamp) não existe rótulo confiável — e sem rótulo, nem avaliação nem aprendizado fecham o ciclo. |

### Sequência correta (difere da ordem do pedido)

O pedido lista corpus → agentes → avaliação. A dependência real é:

```
2a (verdade da Extecamp)  ──►  rótulos confiáveis
        │                            │
        ▼                            ▼
1 (corpus) ──► 2c (estudo) ──► 3 (avaliação: métrica → otimização)
        │
        ▼
2b (follow-up comercial)  [precisa dos estados vindos de 2a]
```

O item 1 é o único **sem pré-condição** — por isso é o que já está implementado.
O item 3 é dividido em **3.1 medição** (implementável agora) e **3.2 otimização**
(RL/GA), que só liga quando houver volume de rótulos (critério em §3.4).

---

## 1. Corpus — memória vetorial do sistema  ✅ implementado

### 1.1 Por que não um banco vetorial dedicado

Com 177 MB livres, subir Chroma/Qdrant ao lado do Flask derruba o serviço. E o
volume real é pequeno: ~1.300 conversas, ~250 leads. Busca exaustiva sobre alguns
milhares de vetores de 256 dimensões custa menos que a latência de rede de um
banco externo. **A escolha certa aqui é o SQLite que já existe.**

### 1.2 Esquema

```sql
CREATE TABLE corpus(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo TEXT,        -- qa | duvida | curso | episodio
  chave TEXT,       -- id natural p/ idempotência (ex.: "qa:c123:456")
  texto TEXT,       -- conteúdo indexado (já anonimizado)
  meta TEXT,        -- JSON: curso, resultado, origem…
  emb BLOB,         -- array('f') normalizado, little-endian
  dim INTEGER, modelo TEXT, ts TEXT,
  UNIQUE(chave)
);
```

`chave` única = reingestão é idempotente (o ciclo de estudo roda todo dia sem duplicar).

### 1.3 Embeddings

- **Online**: OpenAI `text-embedding-3-small` com `dimensions=256`. O modelo é
  Matryoshka — truncar para 256 preserva quase toda a qualidade e reduz o vetor
  de 6 KB para 1 KB. 10 mil documentos = 10 MB. Cabe.
- **Offline (sem `OPENAI_API_KEY`)**: embedding determinístico por *hashing* de
  n-gramas em 256 dimensões. Qualidade menor, mas o sistema **funciona e é
  testável sem chave** — mesma filosofia do dry-run do WhatsApp/Meta.
- Vetores são **normalizados na escrita**, então similaridade de cosseno vira
  produto escalar simples (sem divisão em tempo de busca).

### 1.4 Busca

Varredura por streaming do SQLite (nunca carrega tudo na memória) + heap de
top-k. Custo medido: ver §1.7. Filtro opcional por `tipo` e por `curso` no
`meta` empurra a seleção para o SQL, reduzindo o que chega ao Python.

### 1.5 O que entra no corpus

| tipo | conteúdo | por que importa |
|---|---|---|
| `qa` | pergunta real do lead → resposta do agente que foi **aprovada e enviada** | precedente de como já respondemos bem |
| `duvida` | pergunta → resposta da **coordenação** | fonte humana autorizada |
| `curso` | ficha do curso vinda da ontologia | âncora factual |
| `episodio` | conversa inteira + desfecho (`Inscrito`/`Perdido`/silêncio) | matéria-prima do item 3 |

### 1.6 LGPD — o corpus não pode virar um vazamento

Texto de conversa contém PII. Antes de indexar, `anonimizar()` remove telefone,
e-mail, CPF e URLs com token. **Nome do lead não é indexado** (o campo `meta`
guarda só `card_id`, que já é interno). O corpus é um índice de *como
conversamos*, não um cadastro de pessoas.

### 1.7 O corpus NÃO relaxa o anti-alucinação

Trecho recuperado entra no prompt rotulado como **precedente**, nunca como fato.
Preço, data, carga e requisito continuam vindo só da ontologia, e o Revisor
continua fail-closed. Um precedente desatualizado (ex.: preço da turma passada)
que escapasse para a mensagem é bloqueado pelo Revisor exatamente como hoje.

---

## 2. Agentes comerciais

### 2a. Coletor Extecamp — **bloqueado por credenciais**

Objetivo: substituir a verificação manual pela verdade do sistema de inscrição —
quem se inscreveu, quem pagou a taxa, quem subiu documento, quem matriculou.

**Bloqueio real**: a área com dados de inscritos exige login institucional. Não
tenho (nem devo ter) essa credencial. O que é preciso decidir antes de codar:

1. **Credencial de serviço** — usuário próprio para o robô, com escopo de leitura.
   Não usar a conta pessoal de um coordenador.
2. **Existe API?** Sempre preferir a API à raspagem. Se a Extecamp expuser
   qualquer endpoint (mesmo CSV de relatório), o item cai de "scraping frágil"
   para "integração estável". **Verificar antes de escrever qualquer parser.**
3. **LGPD** — trazer nome/CPF/e-mail de aluno para o nosso banco muda a base
   legal do tratamento. Precisa de decisão consciente da FEQ, não minha.
4. **Termos de uso** — raspagem de sistema da própria instituição, com conta
   autorizada, é uso legítimo; ainda assim registrar autorização.

Desenho quando desbloqueado: sessão autenticada com `requests`, parser sem `bs4`
(indisponível na VPS — usar `html.parser` da stdlib), *rate limit* respeitoso,
diff contra o estado local, e escrita **idempotente** em `leads.fase`. Toda
mudança de fase vira evento no funil (que já alimenta CAPI e métricas).

### 2b. Follow-up comercial — parcialmente existente

Já existe: cadência fria por template (`nutricao.py`), recuperação de checkout
24h (`growth/motor.py recuperar`), 5 toques (spec 17).

O que **falta** e depende de 2a — os estados intermediários que hoje não
enxergamos:

| Estado (vem de 2a) | Ação comercial |
|---|---|
| clicou no link, não iniciou inscrição | lembrete com o link + oferta de ajuda no preenchimento |
| iniciou, não pagou a taxa | lembrete do boleto/prazo (é aqui que os 91 negativos provavelmente estão) |
| pagou, não subiu documento | instrução do que falta, por documento |
| inscrito, turma perto de fechar | urgência real (vagas/prazo da ontologia) |

Sem 2a, hoje só conseguimos inferir "clicou e não confirmou" — que é exatamente
o `verificacao_negativa` preenchido à mão.

### 2c. Ciclo de estudo — ✅ base implementada

Job periódico que: (1) ingere o que é novo desde a última rodada; (2) consolida
episódios com desfecho; (3) reporta o que aprendeu (quantos documentos, quais
perguntas novas apareceram). Roda no agendador interno (`_operacoes`), não em cron
externo — a VPS não tem crontab configurado (verificado).

---

## 3. Avaliação do sistema

### 3.1 Medição (implementável agora)

Não existe "uma métrica". O funil tem estágios com taxas próprias, e é a queda
entre estágios que diz onde o sistema está ruim:

```
lead → respondeu → qualificado → clicou inscrição → inscrito → matriculado
```

Métricas por etapa: taxa de resposta, taxa de qualificação, CTR para Extecamp,
conversão, e **tempo até cada transição**. Mais: taxa de queda no determinístico
(saúde do cérebro), taxa de bloqueio do Revisor (saúde do grounding), taxa de
escalonamento (o que o agente não resolve sozinho).

### 3.2 Recompensa (quando houver rótulo)

`r = w1·matriculado + w2·inscrito + w3·clicou − w4·optout − w5·escalonamento_evitável`

Pesos ancorados em valor real: matrícula vale o `aVista` do curso; opt-out é
perda permanente. **Não inventar pesos**: derivar do plano de pagamento da
ontologia.

### 3.3 Otimização — o que é honesto fazer

O espaço de decisão do agente é pequeno e discreto: horário do toque, intervalo
entre toques, qual template, quando ofertar o link, tom. Isso **não** pede deep
RL. O que cabe:

- **Bandit contextual (Thompson sampling)** por variante de mensagem/cadência.
  É o método correto para "qual variante converte mais" com pouco dado, porque
  equilibra exploração e exploração e já dá resultado com dezenas de amostras.
- **Algoritmo genético** sobre o vetor de parâmetros da cadência (dias entre
  toques, teto de toques, janela horária), avaliado por *replay* contrafactual
  sobre o histórico — evita testar no lead real.
- Deep RL (PPO etc.) **não se justifica**: espaço de ação minúsculo, episódios
  longos, dado escasso. Seria complexidade sem retorno.

### 3.4 Critério de liberação (gate honesto)

O otimizador de 3.3 **só liga** quando:

- ≥ 100 episódios com desfecho conhecido (hoje: **1**), e
- ≥ 30 conversões positivas (hoje: **1**), e
- rótulo vindo de 2a (automático), não de digitação manual.

Antes disso, o sistema só **mede e reporta** (3.1). Ligar otimização antes é
produzir número bonito sem significado — e pior, mudar a operação com base nele.

---

## 4. Definition of Done por item

1. **Corpus**: ingere sem duplicar, busca com e sem chave de API, anonimiza PII,
   e não altera o comportamento do Revisor. ✅
2. **Agentes**: 2a só começa após as 4 decisões de §2a; 2b só depois de 2a;
   2c roda no agendador e é observável na Central.
3. **Avaliação**: 3.1 visível na Central com série temporal; 3.2/3.3 atrás do
   gate de §3.4, com replay contrafactual antes de qualquer aplicação em lead real.

---

# Revisão de 04/08/2026 — o fluxo de RL do sistema multiagentes

> Escrita depois de instrumentar a autoria das mensagens (`conversas.autor`) e
> medir o que realmente existe de sinal. Substitui a intenção de §3.2/3.3.

## R1. O erro que quase cometemos: otimizar para conversão

A recompensa "natural" seria a matrícula. Medido: **1 conversão em 265 leads**.
Um episódio positivo. Qualquer bandit, GA ou PPO treinado nisso ajusta ruído —
e, pior, produz um número convincente que muda a operação sem base.

## R2. O sinal que EXISTE hoje: autonomia

Com a autoria instrumentada, aparece um rótulo denso e barato:

| Medida | Valor (04/08/2026) |
|---|---|
| Conversas engajadas (lead falou) | 113 |
| Conversas que precisaram de humano | **32 (28%)** |
| Falas digitadas por humano | 105 |
| Mensagens geradas pelo LLM | 418 |
| Escalonamentos abertos agora | 6 |
| Dúvidas à coordenação | 21 |

**Taxa de autonomia = 72%** — e cada conversa é um episódio rotulado, não um a
cada 265. É esta a função-objetivo tratável hoje, e ela coincide com o que o
usuário pediu: *"até resolver todas as dúvidas sem nem mesmo me consultar"*.

Cuidado que impede a métrica de virar perversa: **autonomia sozinha é gamificável**
— o agente "não precisa de humano" se simplesmente responder qualquer coisa. Por
isso a recompensa é composta e o Revisor entra com sinal negativo:

```
r(episódio) = + 1.0 · sem_intervenção_humana
              + 0.3 · lead_respondeu_depois        (não matou a conversa)
              + 2.0 · iniciou_inscrição
              + 8.0 · inscrito                      (raro hoje; peso alto p/ quando vier)
              − 1.0 · bloqueio_do_Revisor           (autonomia com alucinação não conta)
              − 2.0 · opt-out                       (dano permanente)
```

Pesos são iniciais e devem ser recalibrados quando houver ≥30 conversões (§3.4).

## R3. Onde está a "política" num sistema multiagentes

Não há uma política única. Há quatro pontos de decisão, e é neles que se atribui
crédito quando um episódio precisa de humano:

| Agente | Decide | Falha típica que gera intervenção |
|---|---|---|
| Cérebro (LLM) | o que responder, escalar ou consultar | escalou o que já sabia; respondeu vago |
| Revisor | deixa sair ou bloqueia | bloqueou resposta correta (falso positivo) |
| Bibliotecário/Corpus | que contexto entrega | não achou o precedente que existia |
| Nutrição/Funil | quando tocar, com qual template | tocou fora de hora; template errado |

**Atribuição de crédito prática** (sem gradiente): ao registrar uma intervenção,
gravar a causa observável — `escalou`, `revisor_bloqueou`, `sem_precedente`,
`lead_insatisfeito`. Isso já é coletável: `decisoes.acao`, `aprovado_revisor` e o
motivo do escalonamento. Sem essa etiqueta, "melhorar o sistema" é adivinhação.

## R4. O laço de aprendizado que JÁ roda (e por que é o certo agora)

```
lead pergunta → agente não resolve → HUMANO responde (intervenção = rótulo negativo)
      ↓                                        ↓
  corpus busca precedente          curador destila a resolução em LIÇÃO
      ↑                                        ↓
      └──────────── lição entra no corpus ─────┘
                (próxima pergunta parecida: agente resolve sozinho)
```

Isto é *policy improvement* por memória, não por gradiente. É o método correto
para este regime: dado escasso, ação discreta, e cada exemplo custa caro (o tempo
de uma pessoa). Um método de gradiente precisaria de milhares de episódios para
aprender o que uma lição bem escrita ensina de primeira.

**O que falta instrumentar para fechar o laço** (próximo passo natural):
a cada intervenção, registrar o TEMA; depois medir *reincidência por tema*.
Se "prazo de reconhecimento de pagamento" gerou 6 intervenções antes da lição e
0 depois, a lição funcionou. Essa curva por tema é a evidência de aprendizado —
mais informativa que uma taxa global.

## R5. Quando (e só então) ligar otimização estatística

| Método | Para quê | Pré-condição |
|---|---|---|
| **Bandit contextual** (Thompson) | escolher variante de mensagem/cadência | ≥ 30 episódios por variante |
| **Algoritmo genético** | ajustar o vetor da cadência (dias entre toques, teto, janela) | replay contrafactual sobre histórico ≥ 100 episódios |
| **RL por gradiente** | — | **não se justifica**: espaço de ação minúsculo, episódios longos, dado caro |

O GA aqui não experimenta no lead real: avalia sobre o histórico (dado o que
aconteceu, a política candidata teria tocado antes/depois?). Só a candidata
vencedora vai a teste real, e mesmo assim atrás do gate de §3.4.

## R6. Painel de aprendizado (o que a Central deve mostrar)

1. **Taxa de autonomia** — conversas sem intervenção / conversas engajadas, por semana.
2. **Intervenções por tema** — onde o sistema ainda depende de você.
3. **Reincidência pós-lição** — o tema voltou depois de virar lição?
4. **Cobertura do corpus** — % de perguntas que acharam precedente acima do limiar.
5. **Saúde dos guardrails** — bloqueios do Revisor e quedas no determinístico (já em §3.1).

Ordem de implementação sugerida: (1) etiquetar causa/tema da intervenção →
(2) painel de autonomia → (3) curva de reincidência → só depois (4) bandit.
