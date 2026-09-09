#!/usr/bin/env python3
"""
Coleta do ADM Cursos da Extecamp (spec 20): parsers + reconciliação com o funil.

As fixtures são HTML REAL do sistema, gravado em iso-8859-1 como ele serve. Isso
não é preciosismo: o ADM Cursos declara charset=iso-8859-1 e ler como UTF-8
corromperia todo nome acentuado — que é a maioria. Fixture em UTF-8 seria um
teste passando enquanto a produção quebra.

O que estes testes protegem:
  1. a COR da linha é a única fonte do estado da taxa — se o mapa quebrar, o
     operador cobra documento de quem nem aceitou o termo;
  2. os tokens sigla/of/id são opacos e têm de viajar intactos — reencodá-los
     muda o valor e a URL seguinte cai em outro curso;
  3. a mesma pessoa NUNCA vira dois cards, nem entre abas nem entre coletas.
"""
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado                        # noqa: E402
from atena.coleta import extecamp as E                      # noqa: E402
from atena.coleta import sincronizacao as S                 # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures" / "extecamp"
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


def _fix(nome):
    return FIX.joinpath(nome).read_bytes()


# --------------------------------------------------------------- 1) inscritos
d = E.parse_inscritos(_fix("inscritos.html"))
check(d["curso"] == "FEQ-1000" and d["turma"] == "018", "cabeçalho: sigla e oferecimento")
check(d["nome"] == "ENGENHARIA DE PROCESSOS QUÍMICOS", f"nome do curso com acento: {d['nome']}")
check(d["totais"] == {"inscritos": 14, "matriculados": 38, "cancelados": 25},
      f"totais lidos da própria página: {d['totais']}")
check(d["vagas"] == {"min": 46, "max": 90}, f"vagas mín/máx: {d['vagas']}")
check(d["paginas"] == 1, "paginação detectada (a lista pode ter várias páginas)")
check(len(d["itens"]) == 5, f"5 inscritos na fixture (veio {len(d['itens'])})")

por_nome = {i["nome"]: i for i in d["itens"]}
check("BÁRBARA ABADE MENDES DÜREN" in por_nome,
      "iso-8859-1 decodificado certo (Á e Ü preservados)")

# a COR é o estado da taxa — cada uma tem consequência operacional diferente
check(por_nome["BÁRBARA ABADE MENDES DÜREN"]["taxa"] == "termo_pendente",
      "cinza = ainda não aceitou o termo da taxa")
check(por_nome["DENIS EDUARDO SILVA"]["taxa"] == "taxa_a_pagar",
      "amarelo = aceitou o termo, não pagou")
check(por_nome["MAYARA CRIS RIBEIRO DA SILVA LESSA"]["taxa"] == "taxa_vencida",
      "vermelho = venceu, precisa de novo boleto")
check(por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["taxa"] == "taxa_paga",
      "verde = taxa paga, pode enviar documentos")

check(por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["documentacao"] == "APROVADO",
      "status da documentação extraído do <b> dentro da célula do nome")
check(por_nome["DENIS EDUARDO SILVA"]["documentacao"] == "EM ANÁLISE",
      "EM ANÁLISE (com acento) reconhecido")
check("Status documenta" not in por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["nome"],
      "o status não vaza para dentro do nome")
check(por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["numero"] == 5,
      "número da inscrição separado do nome")

# a combinação taxa × documentação é o que vira trabalho para o operador
check("novo boleto" in por_nome["MAYARA CRIS RIBEIRO DA SILVA LESSA"]["acao"],
      "taxa vencida -> ação é pedir novo boleto")
check("documentos" in por_nome["BÁRBARA ABADE MENDES DÜREN"]["acao"].lower()
      or "termo" in por_nome["BÁRBARA ABADE MENDES DÜREN"]["acao"].lower(),
      "termo pendente -> ação é cobrar o aceite")
check(por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["acao"].startswith("Aguardar"),
      "documentação aprovada -> nada a fazer com o lead, aguardar a secretaria")
check(E.situacao("taxa_paga", "APROVADO") != E.situacao("taxa_paga", "PENDENTE"),
      "mesma cor com documentação diferente gera ação diferente")

check(por_nome["JEFFERSON JEREMIAS SILVA"]["forma_taxa"] == "cartao"
      and por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["forma_taxa"] == "boleto",
      "forma de pagamento da taxa vem da imagem (boleto x cartão)")
check(por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["documento_entregue"] is True
      and por_nome["DENIS EDUARDO SILVA"]["documento_entregue"] is False,
      "coluna Doc/Ficha lida (SIM/SIM x NÃO/NÃO)")

# tokens opacos: são binário url-encoded, NÃO podem ser normalizados
ch = por_nome["ALCIDES HENRIQUE LEITE WROBLEWSKI"]["chaves"]
# em aluno.asp os mesmos tokens do curso viajam como id1/id2 (e não sigla/of)
check(ch["id1"] == "%81g%C8%C2%5F%E2%DD%9E" and ch["id2"] == "%F7%13%A1",
      f"tokens do curso preservados EXATAMENTE como vieram (opacos do ASP): {ch}")
check(ch.get("id3") and ch.get("id4"), "ids do aluno preservados para abrir a ficha")
check("%" in ch["id1"], "não foram url-decodificados: decodificar muda o valor e perde a chave")

# --------------------------------------------------------------- 2) demais abas
m = E.parse_matriculados(_fix("matriculados.html"))
check(m["aba"] == "matriculados" and len(m["itens"]) == 3, "matriculados parseados")
check(m["itens"][0]["nome"] == "ALANA LIBERATO DE MATTOS SOUSA", "nome do matriculado")
check(m["itens"][0]["matriculado_em"] == "13/08/2026", "data da matrícula")
check("ex206488@unicamp.br" in m["itens"][1]["sise"], "campo SISE preservado")

c = E.parse_cancelados(_fix("cancelados.html"))
check(c["aba"] == "cancelados" and len(c["itens"]) == 2, "cancelados parseados")
check(c["itens"][0]["nome"] == "BRUNA LAIS DE SOUZA SILVA" and c["itens"][0]["numero"] == 11,
      "em cancelados o número e o nome vêm em linhas separadas — e mesmo assim casam")

# as três abas concordam nos mesmos totais: é a checagem cruzada de que a
# coleta pegou a página certa do curso certo
for aba in (d, m, c):
    check(aba["totais"].get("inscritos") == 14 and aba["totais"].get("matriculados") == 38
          and aba["totais"].get("cancelados") == 25,
          f"{aba['aba']}: totais batem com as outras abas")

lst = E.parse_lista_cursos(_fix("lista_cursos.html"))
check(len(lst) == 1 and lst[0]["curso"] == "FEQ-1000" and lst[0]["turma"] == "018",
      "lista de cursos: sigla e oferecimento")
check(lst[0]["insc_fim"] == "03/09/2026" and lst[0]["ofer_fim"] == "24/06/2028",
      "datas de inscrição e oferecimento na ordem certa")
check(lst[0]["chaves"]["sigla"] == "%81g%C8%C2%5F%E2%DD%9E",
      "é daqui que saem os tokens para montar as URLs das abas")

# --------------------------------------------------------------- 3) reconciliação
def _est():
    return Estado(":memory:")


est = _est()
r1 = S.sincronizar(est, d)
check(r1["ok"] and len(r1["criados"]) == 5, f"1ª coleta cria os 5 cards: {len(r1['criados'])}")
# Cada degrau da Extecamp vira uma COLUNA — é o que mostra onde o funil sangra
_fase = {est.get_lead(x["card_id"])["nome"]: est.get_lead(x["card_id"])["fase"]
         for x in r1["criados"]}
check(_fase["BÁRBARA ABADE MENDES DÜREN"] == "Inscrito",
      "cinza (não aceitou o termo) -> Inscrito, o degrau zero")
check(_fase["DENIS EDUARDO SILVA"] == "Taxa aceita",
      "amarelo (aceitou, não pagou) -> Taxa aceita")
check(_fase["MAYARA CRIS RIBEIRO DA SILVA LESSA"] == "Taxa aceita",
      "vermelho (venceu) também é Taxa aceita: ela ACEITOU; o que venceu foi o boleto")
check(_fase["ALCIDES HENRIQUE LEITE WROBLEWSKI"] == "Documento entregue",
      "verde + APROVADO -> Documento entregue")
check(S.fase_de("inscritos", "taxa_paga", "PENDENTE") == "Taxa paga",
      "verde + documentação PENDENTE para em Taxa paga")
check(S.fase_de("inscritos", "taxa_paga", "EM ANÁLISE") == "Documento entregue",
      "EM ANÁLISE já é documento entregue (a bola está com a secretaria)")

from atena.core.estado import ESTEIRA, alcancou                 # noqa: E402
check(all(f in ESTEIRA for f in _fase.values()), "toda fase usada existe na esteira")
check(all(alcancou(f, "Inscrito") for f in _fase.values()),
      "todo inscrito da Extecamp conta como inscrito, em qualquer degrau")
# a razão de existir o ordinal: contagem por lista de strings cairia aqui
_ff = est.stats_funil()
check(sum(n for fa, n in _ff.items() if alcancou(fa, "Inscrito")) == 5,
      "contagem de inscritos é CUMULATIVA (não some quando o lead avança de degrau)")
r2 = S.sincronizar(est, d)
check(not r2["criados"] and not r2["movidos"] and r2["inalterados"] == 5,
      "2ª coleta idêntica não cria nem move nada (idempotente)")
check(len(est.listar_leads()) == 5, "e não sobrou card duplicado")

# lead que já existia (veio do WhatsApp, COM telefone) não pode virar 2º card
est2 = _est()
est2.upsert_lead("wa-5519999990001", nome="Mayara Cris Ribeiro da Silva Lessa",
                 telefone="5519999990001", curso="FEQ-1000", fase="Prospect")
r3 = S.sincronizar(est2, d)
check(len(est2.listar_leads()) == 5, "casou com o card existente em vez de duplicar")
lead = est2.get_lead("wa-5519999990001")
check(lead["fase"] == "Taxa aceita",
      f"o card existente foi movido para o degrau REAL dela, não para 'Inscrito' "
      f"genérico (veio {lead['fase']})")
check(lead["telefone"] == "5519999990001",
      "e manteve o telefone — a Extecamp não tem telefone e não pode apagar o nosso")
check(any(x["de"] == "Prospect" and x["para"] == "Taxa aceita" for x in r3["movidos"]),
      f"a movimentação é reportada com o degrau exato: {r3['movidos']}")

# a MESMA pessoa migrando de aba reusa o card — é o caminho normal do funil
est3 = _est()
S.sincronizar(est3, {"aba": "inscritos", "curso": "FEQ-1000", "turma": "018", "totais": {},
                     "itens": [{"nome": "ALANA LIBERATO DE MATTOS SOUSA", "numero": 9,
                                "taxa": "taxa_paga", "documentacao": "APROVADO",
                                "situacao": "x", "acao": "y"}]})
antes = len(est3.listar_leads())
r4 = S.sincronizar(est3, m)   # a Alana aparece agora como matriculada
check(len(est3.listar_leads()) == antes + 2,
      "só os 2 matriculados NOVOS viram card; a Alana reusa o dela")
check(any("ALANA" in x["nome"] and x["para"] == "Matriculado" for x in r4["movidos"]),
      "Alana foi movida Inscrito -> Matriculado, não recriada")

# cancelado cai em Perdido
est4 = _est()
r5 = S.sincronizar(est4, c)
check(all(est4.get_lead(x["card_id"])["fase"] == "Cancelado" for x in r5["criados"]),
      "aba Cancelados -> coluna Cancelado (não 'Perdido': quem cancelou tinha se "
      "inscrito, é outro problema e outro dono)")

# mudança de situação DENTRO da mesma fase é detectada (é o que muda a ação)
est5 = _est()
base = {"aba": "inscritos", "curso": "FEQ-1000", "turma": "018", "totais": {},
        "itens": [{"nome": "TESTE SITUACAO", "numero": 1, "taxa": "taxa_vencida",
                   "documentacao": "PENDENTE", "situacao": "venceu", "acao": "novo boleto"}]}
S.sincronizar(est5, base)
depois = json.loads(json.dumps(base))
depois["itens"][0].update(taxa="taxa_paga", situacao="pago", acao="pedir documentos")
r6 = S.sincronizar(est5, depois)
check(len(r6["movidos"]) == 1 and r6["movidos"][0]["de"] == "Taxa aceita"
      and r6["movidos"][0]["para"] == "Taxa paga",
      f"pagar a taxa SOBE um degrau no funil: {r6['movidos']}")
guardado = json.loads(est5.get_lead(r6["movidos"][0]["card_id"])["extecamp_json"])
check(guardado["taxa"] == "taxa_paga", "o estado da Extecamp fica gravado no card")

check(S.card_id_de("FEQ-1000", "018", "José da Silva")
      == S.card_id_de("FEQ-1000", "018", "JOSE  DA SILVA"),
      "acento e espaço duplo não geram card diferente para a mesma pessoa")
check(S.card_id_de("FEQ-1000", "018", "X") != S.card_id_de("FEQ-1000", "019", "X"),
      "mesma pessoa em OUTRO oferecimento é outro card (é outra inscrição)")

# --------------------------------------------------- 4) relatório de contatos
# É ele que traz o TELEFONE — as listagens não têm essa coluna. Sem telefone o
# card nasce inalcançável e o lead que já falava conosco vira duplicata.
#
# As duas fixturas descrevem o MESMO conteúdo em layouts diferentes de propósito:
# não vimos a página real, então o parser casa pelo FORMATO do dado (e-mail e
# telefone brasileiro têm forma inequívoca) em vez de posição de coluna. Se o
# layout real for um terceiro, ele continua valendo.
ra = E.parse_relatorio_contatos(_fix("relatorio_a.html"))
rb = E.parse_relatorio_contatos(_fix("relatorio_b.html"))
check(len(ra) == 2 and len(rb) == 2, "os dois layouts devolvem 2 contatos")
check({c["nome"] for c in ra} == {c["nome"] for c in rb},
      "mesmos nomes nos dois layouts (parser cego ao layout)")
check(rb[0]["nome"] == "ALCIDES HENRIQUE LEITE WROBLEWSKI",
      f"prefixo '5 - ' removido do nome no relatório: {rb[0]['nome']}")
check("5541998123456" in ra[0]["telefones"] and "alcides.w@gmail.com" in ra[0]["emails"],
      "telefone normalizado e e-mail extraídos")

# A ARMADILHA: o rodapé com os telefones da própria Extecamp está em TODA página
# do sistema. Sem a trava, todo inscrito herdaria o número da secretaria.
for c in ra + rb:
    check(not any(t in E._INSTITUCIONAIS for t in c["telefones"]),
          f"{c['nome'][:18]}: não pegou telefone da secretaria")
    check(not any("extecamp.unicamp.br" in m for m in c["emails"]),
          f"{c['nome'][:18]}: não pegou e-mail institucional")
check(E.parse_relatorio_contatos(_fix("inscritos.html")) == [],
      "rodar o extrator na LISTAGEM real não inventa contato nenhum")
check(E._telefones("09/03/2026 14:07:54 CPF 123.456.789-09") == [],
      "data e CPF não viram telefone")

check(E.classificar("https://x/admcursos/reltel.asp?sigla=A&of=B") == "contatos"
      and E.classificar("reltel_mat.asp?a=1") == "contatos"
      and E.classificar("reltel_canc.asp?a=1") == "contatos",
      "os três relatórios (inscritos/matriculados/cancelados) são reconhecidos")
check(set(E.urls_relatorios({"sigla": "S", "of": "O"})) == {"inscritos", "matriculados", "cancelados"},
      "um relatório por aba")

# celular na frente do fixo: o canal é WhatsApp
idx = S.indexar_contatos([{"nome": "X", "telefones": ["(41) 3333-1010", "(41) 99812-3456"],
                           "emails": []}])
check(idx["X"]["telefone"] == "5541998123456",
      f"escolhe o CELULAR, não o fixo (veio {idx['X']['telefone']})")

# --------------------------------------------------- 5) identidade por telefone
# O ganho real: o card do WhatsApp tem o nome grafado de outro jeito. Casar por
# nome criaria duplicata; o telefone reencontra a pessoa.
est6 = _est()
est6.upsert_lead("wa-5519998887766", nome="Barbara D.", telefone="5519998887766",
                 curso="FEQ-1000", fase="Prospect")
contatos = S.indexar_contatos(ra)
r7 = S.sincronizar(est6, d, contatos=contatos)
check(len(est6.listar_leads()) == 5,
      f"nome diferente, MESMO telefone -> não duplicou ({len(est6.listar_leads())} cards)")
bd = est6.get_lead("wa-5519998887766")
check(bd["fase"] == "Inscrito",
      "o card do WhatsApp foi para Inscrito — ela ainda não aceitou o termo da taxa")
check(bd["nome"] == "Barbara D.",
      "o nome que o operador já tinha NÃO é sobrescrito pelo cadastro da Extecamp")
alc = next(l for l in est6.listar_leads() if "ALCIDES" in (l["nome"] or ""))
check(alc["telefone"] == "5541998123456" and alc["email"] == "alcides.w@gmail.com",
      "card novo nasce contactável quando o relatório trouxe o contato")
sem = next(l for l in est6.listar_leads() if "MAYARA" in (l["nome"] or ""))
check(sem["telefone"] == "",
      "quem não está no relatório fica sem telefone — e não inventa um")

# telefone que o operador já usou vale mais que o cadastro da Extecamp
est7 = _est()
est7.upsert_lead("wa-5511111111111", nome="ALCIDES HENRIQUE LEITE WROBLEWSKI",
                 telefone="5511111111111", curso="FEQ-1000", fase="Lead")
S.sincronizar(est7, d, contatos=contatos)
check(est7.get_lead("wa-5511111111111")["telefone"] == "5511111111111",
      "telefone existente nunca é sobrescrito pelo do relatório")

# ------------------------------------------- 6) o que a coleta real de 30/08/2026 mostrou
# Tudo aqui foi conferido contra o ADM Cursos autenticado (pendência da spec 20 §5).

# A listagem "em andamento" liga por cursoand.asp, e não curso.asp. Aceitar só o
# segundo fazia a coleta devolver ZERO oferecimento para todo curso já iniciado —
# em silêncio, porque lista vazia é indistinguível de "nenhum curso nosso aqui".
LISTA_ANDAMENTO = """<table>
<tr><td><a href="cursoand.asp?sigla=%81g&of=%F7%12%AD">GESTAO DE PROCESSOS</a></td>
    <td><a href="cursoand.asp?sigla=%81g&of=%F7%12%AD">FEQ-0360</a></td>
    <td>23/08/2024</td><td>10/12/2024</td><td>18/01/2025</td><td>23/05/2026</td>
    <td>004</td></tr></table>"""
la = E.parse_lista_cursos(LISTA_ANDAMENTO)
check(len(la) == 1 and la[0]["curso"] == "FEQ-0360" and la[0]["turma"] == "004",
      "listagem 'em andamento' (cursoand.asp) devolve o oferecimento, não uma lista vazia")
check(la[0]["chaves"]["of"] == "%F7%12%AD",
      "o token opaco 'of' atravessa a listagem de andamento sem ser reencodado")

# O typo é do próprio sistema ("Tota de cancelados") e aparece na aba de inscritos
# real do FEQ-1000/018. Casar por frase perderia o número em silêncio.
TOTAIS_REAIS = """<table>
<tr><td>Total de inscritos:</td><td>14</td></tr>
<tr><td>Total de matriculados:</td><td>43</td></tr>
<tr><td>Tota de cancelados:</td><td>27</td></tr></table>"""
tt = E._totais(E._linhas(TOTAIS_REAIS))
check(tt == {"inscritos": 14, "matriculados": 43, "cancelados": 27},
      "os três totais do FEQ-1000/018 são lidos, typo do sistema incluído")

# A contagem do Painel é CUMULATIVA. Se fosse só a aba "Inscritos", o FEQ-1000/018
# mostraria 14 — e cairia mais ainda a cada matrícula, ou seja, pioraria justamente
# quando o curso desse certo. O número honesto é 14 + 43.
check(tt["inscritos"] + tt["matriculados"] == 57,
      "inscritos do Painel = lista de inscritos + matriculados (57 no FEQ-1000/018)")

# ------------------------------------------- 7) o coletor consegue mesmo escrever?
# O coletor roda na origem da Extecamp, onde o cookie da Central não existe. Se o
# guard de sessão barrar /api/coleta/pagina, o preflight CORS volta 401 e o fetch
# nem sai — a coleta inteira falha em silêncio, que foi o que aconteceu na primeira
# tentativa real. A fila de trabalho, essa sim, tem de continuar protegida.
from atena.core.auth import LIVRE                               # noqa: E402
check("/api/coleta/pagina" in LIVRE,
      "o receptor da coleta dispensa sessão — o coletor vem de outra origem")
check("/api/coleta" not in LIVRE,
      "a fila de trabalho (PII de lead) continua exigindo sessão")

# ------------------------------------------- 8) sessão vencida não vira "curso vazio"
# O ADM Cursos não responde 401 quando a sessão cai: responde 200 com ~190 bytes de
# <meta refresh> para o login. Entregar isso ao parser dá zero linha em toda página,
# e a coleta reportaria "0 inscritos" em todos os cursos — que é uma afirmação
# perigosamente diferente de "não estou vendo nada".
CASCO = (b'<html><head><title>Untitled Document</title>'
         b'<META http-equiv="refresh" content="0;URL=/admcursos/index_seg.htm">'
         b'</head><body bgcolor="#FFFFFF"></body></html>')
check(E.deslogado(CASCO), "o redirecionamento para o login é reconhecido como sessão vencida")
check(not E.deslogado(_fix("inscritos.html")),
      "página de conteúdo real não é confundida com sessão vencida")

# --------------------------------- 9) o atalho precisa apontar para https, sempre
# O coletor é injetado numa página https (a Extecamp). Script http em página https é
# conteúdo ativo misto: o navegador bloqueia SEM erro visível, e clicar o atalho não
# faz absolutamente nada — nem painel, nem aviso. Foi assim que a primeira coleta
# real "não funcionou". Loopback segue http porque em dev não há TLS.
import os as _os                                                # noqa: E402
from atena.api import app as _A                                 # noqa: E402


def _base(url):
    velho = _os.environ.pop("ATENA_PUBLIC_URL", None)
    try:
        with _A.app.test_request_context("/", base_url=url):
            return _A._base_coleta()
    finally:
        if velho is not None:
            _os.environ["ATENA_PUBLIC_URL"] = velho


check(_base("http://feq.vitoraugolis.com.br") == "https://feq.vitoraugolis.com.br",
      "domínio público em http é promovido a https (senão o atalho morre calado)")
check(_base("http://localhost:8095") == "http://localhost:8095",
      "loopback continua http — em dev não há TLS e o navegador aceita")

# A rota que ENTREGA o atalho, de ponta a ponta. Existe porque uma função nova
# inserida logo abaixo de um @app.route rouba o decorador da rota seguinte, e o
# endpoint passa a devolver outra coisa — silenciosamente, com status 200.
from urllib.parse import unquote                                # noqa: E402

_c = _A.app.test_client()
with _c.session_transaction() as _ss:
    _ss["usuario"], _ss["papel"] = "teste", "supervisor"
_r = _c.post("/api/coleta/sessao")
_j = _r.get_json() or {}
check(_r.status_code == 200 and _j.get("ok") and _j.get("token"),
      "/api/coleta/sessao devolve JSON com token (e não o retorno de outra função)")
check(unquote(_j.get("bookmarklet", "")).startswith("javascript:(function()"),
      "o atalho é um bookmarklet executável")
check("src='https://" in unquote(_j.get("bookmarklet", "")),
      "o atalho carrega o coletor por https")

print(f"\n{ok} checks OK — coleta Extecamp")
