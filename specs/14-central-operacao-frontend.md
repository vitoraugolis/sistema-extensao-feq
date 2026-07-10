# 14 — Central de Operação (Frontend)

**Módulos-alvo:** `atena/api/app.py` (REST + SSE) + `atena/web/` (SPA).
**Status:** protótipo navegável implementado (auth/RBAC/auditoria; captabilidade e Revisor reais). **Estética:** **iOS-like, glassmorphism** (vidro fosco,
vibrância, cantos arredondados, tipografia tipo SF). Mockup validado com o operador.

## Propósito
Central de comando do Sistema Extensão FEQ: **visibilidade total** das conversas, **intervenção
humana** quando necessário, **estatísticas** do funil e a porta do **Injetor** (`13`). Materializa o
"painel de transparência" do SPEC original.

## Seções
1. **Dashboard / Estatísticas**
   - Contadores do funil: Lead · Prospect · Inscrito · Matriculado (+ Em espera, Perdido).
   - Taxas de conversão por etapa e **por curso**; leads no tempo (série); SLA/`watch-late`.
   - Saúde dos agentes: % mensagens bloqueadas pelo Revisor, opt-out rate, escalonamentos abertos.
2. **Conversas ao vivo**
   - Lista de conversas (filtro por curso/estado/atribuição) + thread completa por lead.
   - **Intervir:** assumir a conversa (pausa o agente), enviar mensagem manual, ou
     **aprovar/editar/rejeitar** a mensagem que o agente propôs.
3. **Fila de aprovação humana** — escalonamentos, gates de captabilidade, propostas do Injetor/Curador.
4. **Injetor** (`13`) — submeter update → ver diff + relatório de impacto → aprovar/rejeitar.
5. **Transparência** (`/inspecao`) — para qualquer mensagem: o trace de decisão + **âncoras** que a
   fundamentaram (preço/data/requisito → tripla do grafo).

## Tempo real
SSE (ou WebSocket) para novas mensagens, mudança de contadores e novos itens na fila. Servido pelo
próprio monólito (sem segundo serviço nesta fase). **Implementado por polling (5s)**: a aba Conversas
separa a "moldura" (cabeçalho + campo de escrita, montada 1× ao abrir) das "mensagens" (atualizadas
no polling) — o `<textarea>` nunca é recriado, então foco/cursor do operador não se perdem.

## Múltiplos cursos de interesse (✅ implementado)
Um lead tem **um curso ativo** (`leads.curso` — o que o agente conversa / gate / template) e um
**conjunto de cursos de interesse** (`leads.cursos_interesse`, CSV; o ativo sempre incluído). Acumula
por: 2º formulário do mesmo telefone (ingest não troca o ativo), troca de interesse na conversa
(orquestrador), e adição manual (`POST /api/leads/<id>/interesse {curso, ativar?}`). Na conversa,
chips por curso (ativo com ★, clique em outro = tornar ativo). No **Funil**, filtro por curso
(`#kbFiltro`) casa contra o conjunto de interesse; o card mostra o ativo + `+N`.
`estado.cursos_interesse(lead)` devolve a lista.

## Stack recomendada
SPA leve servida pela API Flask (HTML/CSS/JS com glassmorphism; charts via SVG/lib leve). Mantém o
monólito coeso; evita um build pesado de frontend no estágio local. React/Vite fica como evolução se
a UI crescer.

## Segurança, LGPD e auditoria (não-negociável — a central expõe PII)
- **Autenticação + RBAC**: a central mostra telefone e conteúdo de conversa (PII) → login obrigatório
  e papéis (operador, supervisor).
- **Auditoria das ações humanas**: quem leu, enviou, interveio, aprovou — registrado (a trilha não é
  só dos agentes).
- **Mensagem manual passa pelo Revisor** por padrão (com override explícito e auditado do supervisor).
- **Opt-out e janela de horário** valem também para envio manual.

## Critérios de aceitação
- AC1 Operador vê a thread completa de qualquer lead em tempo (quase) real.
- AC2 Operador consegue pausar o agente e assumir/editar a próxima mensagem.
- AC3 Contadores do funil batem com o FARO/estado (fonte única).
- AC4 `/inspecao` mostra as âncoras de uma afirmação factual enviada.
- AC5 Acesso exige autenticação; ações humanas ficam auditadas.
- AC6 Envio manual respeita Revisor (salvo override auditado), opt-out e horário.
