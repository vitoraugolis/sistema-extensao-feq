#!/usr/bin/env python3
"""Growth Engine (spec 15): pacing, CPL, realocação, CAPI, recuperação 24h, scoring, opt-in."""
import os
import re
import sys
import base64
import pathlib
import hashlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# wp_deploy_bridge.publicar() lê WP_URL/WP_USER/WP_APP_PASSWORD direto do ambiente —
# se alguém rodar os testes com essas chaves reais no shell (ex.: `source .env`), o
# teste abaixo deixaria de ser dry-run e publicaria de verdade nas páginas em campanha.
# O teste garante seu próprio pressuposto ("sem credenciais = dry-run"), independente
# do ambiente de quem o invoca. (Incidente real: 09/08/2026, corrigido às pressas.)
for _k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
    os.environ.pop(_k, None)

from atena.core.estado import Estado
from atena.agentes.ingestao import Ingestao
from atena.growth.db import Growth
from atena.growth.meta_ads import MetaAds, tel_e164
from atena.growth import pacing, funil, ponte, ferramentas
from atena.growth.matriculas import processar_matricula

ok = 0
def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


class FakeOnt:
    def captavel(self, curso, hoje=None): return {"captavel": True}
    def promocao(self, curso, hoje=None): return None   # sem campanha nos testes
    def curso(self, curso):
        return {"codigo": curso, "nome": "Curso X", "inscricoes_fim": "2026-08-16"}


class _OntCaptabilidade(FakeOnt):
    """Igual ao FakeOnt, mas com o FEQ-0499 FECHADO — para exercitar o modo lista
    de espera e a priorização do cross-sell."""
    def captavel(self, curso, hoje=None):
        return {"captavel": curso != "FEQ-0499", "status":
                "encerrada" if curso == "FEQ-0499" else "aberta"}


class FakeWA:
    def __init__(self): self.enviados = []
    def enviar_template(self, tel, nome, params=None):
        self.enviados.append((tel, nome, params))
        return {"ok": True, "wamid": f"w.{len(self.enviados)}"}


AGORA = datetime.datetime(2026, 7, 13, 10, 0)
HOJE = AGORA.date()

# --- pacing.calcular (matemática pura) -----------------------------------------
r = pacing.calcular(65, 30, datetime.date(2026, 8, 16), 0.5, HOJE)   # faltam 35 em 34d
check(r["vel_necessaria"] == round(35 / 34, 2), "vel_necessaria = faltam/dias")
check(r["status"] == "atrasado", "0,5/dia contra ~1,03/dia -> atrasado")
r = pacing.calcular(39, 35, datetime.date(2026, 8, 16), 2.0, HOJE)
check(r["status"] == "adiantado", "2/dia contra ~0,12/dia -> adiantado")
check(pacing.calcular(33, 33, datetime.date(2026, 8, 16), 0, HOJE)["status"] == "concluido",
      "meta batida -> concluido")
check(pacing.calcular(91, 10, None, 1, HOJE)["status"] == "sem_prazo", "sem prazo -> sem_prazo")

# --- seed das turmas + inscritos/velocidade -------------------------------------
# LP para TODO o portfólio (20/07/2026), incluindo curso sem turma aberta (0499,
# em modo lista de espera). Os 4 primeiros seguem sendo as turmas-alvo da campanha.
g = Growth(":memory:")
check({c["curso"] for c in g.cursos()} == {"FEQ-0480", "FEQ-0580", "FEQ-0333", "FEQ-1000",
                                           "FEQ-0360", "FEQ-0331", "FEQ-0299", "FEQ-0670",
                                           "FEQ-0499"},
      "seed cobre os 9 cursos com LP (4 turmas-alvo + demais do portfólio)")
check([c for c in g.cursos() if c["curso"] == "FEQ-1000"][0]["meta_seg"] == 91,
      "meta de segurança = meta real +30% (FEQ-1000: 91)")

# --- CAPI dry-run: hash do telefone + evento de funil ---------------------------
est = Estado(":memory:")
est.upsert_lead("c1", nome="Ana", telefone="+55 (19) 99999-0001", curso="FEQ-0480")
ads = MetaAds()   # sem env -> dry-run
r = funil.registrar(g, ads, est, "c1", "inicio_inscricao", agora=AGORA)
check(r["ok"] and r["capi"] == "inicio_inscricao-c1", "inicio_inscricao registrado + CAPI")
r = ads.capi("Purchase", "+55 (19) 99999-0001", "e1")
esperado = hashlib.sha256(tel_e164("+55 (19) 99999-0001").encode()).hexdigest()
check(r["payload"]["data"][0]["user_data"]["ph"] == [esperado], "telefone só sai como SHA-256")
check(g.inscritos("FEQ-0480") == 0, "checkout iniciado ainda não conta como inscrito")
funil.registrar(g, ads, est, "c1", "inscricao_confirmada", agora=AGORA)
check(g.inscritos("FEQ-0480") == 1, "inscricao_confirmada conta como inscrito")
funil.registrar(g, ads, est, "c1", "inscricao_confirmada", agora=AGORA)
check(g.inscritos("FEQ-0480") == 1, "inscrito por card é DISTINCT (reenvio não duplica)")

# --- regra de CPL (sugere sem AUTO_APPLY; não repete em 3d) ----------------------
dia = HOJE.isoformat()
g.add_desempenho({"dia": dia, "objeto_id": "as1", "nome": "Conj A", "campanha": "FEQ-0480 v1",
                  "curso": "FEQ-0480", "gasto": 300.0, "leads": 2})       # CPL 150 > 60
g.add_desempenho({"dia": dia, "objeto_id": "as2", "nome": "Conj B", "campanha": "FEQ-0580 v1",
                  "curso": "FEQ-0580", "gasto": 100.0, "leads": 10})      # CPL 10, ok
acoes = pacing.regra_cpl(g, ads, agora=AGORA)
check([a["objeto_id"] for a in acoes] == ["as1"], "só o adset com CPL estourado é pausado")
check(not acoes[0]["aplicado"], "sem GROWTH_AUTO_APPLY=1 a pausa é só sugestão")
check(pacing.regra_cpl(g, ads, agora=AGORA) == [], "mesma sugestão não se repete em <3 dias")

# --- realocação: adiantado -> atrasado ------------------------------------------
ritmos = {"FEQ-0480": {"status": "atrasado", "razao": 0.3, "vel_necessaria": 1.0, "vel_7d": 0.3},
          "FEQ-0580": {"status": "adiantado", "razao": 2.0, "vel_necessaria": 0.1, "vel_7d": 0.2},
          "FEQ-0333": {"status": "no_ritmo", "razao": 1.0, "vel_necessaria": 0.5, "vel_7d": 0.5}}
r = pacing.realocar(g, ads, ritmos, agora=AGORA)
check(r and r["doador"] == "FEQ-0580" and r["receptor"] == "FEQ-0480",
      "verba sai do adiantado e vai para o atrasado")
check(not r["aplicado"], "realocação sem AUTO_APPLY = sugestão registrada")
check(pacing.realocar(g, ads, ritmos, agora=AGORA) is None, "máx. 1 realocação/dia por par")
check(pacing.realocar(g, ads, {"FEQ-0480": ritmos["FEQ-0480"]}, agora=AGORA) is None,
      "sem curso adiantado não há doador")

# --- recuperação de checkout abandonado (espera por tipo, escalonamento) ---------
g2, est2, wa = Growth(":memory:"), Estado(":memory:"), FakeWA()
est2.upsert_lead("c2", nome="Bia", telefone="+5519988880002", curso="FEQ-1000")
g2.add_evento("c2", "FEQ-1000", "boleto_gerado", ts=(AGORA - datetime.timedelta(hours=25)).isoformat())
r = funil.recuperar(g2, est2, FakeOnt(), wa, agora=AGORA)
check(r["enviados"] == ["c2"] and wa.enviados[0][1] == "feq_lead",
      "boleto há 25h sem conversão -> 1º toque é feq_lead (ajuda)")
r = funil.recuperar(g2, est2, FakeOnt(), wa, agora=AGORA + datetime.timedelta(hours=1))
check(r["enviados"] == [], "não repete toque antes de 48h")
r = funil.recuperar(g2, est2, FakeOnt(), wa, agora=AGORA + datetime.timedelta(hours=50))
check(r["enviados"] == ["c2"] and wa.enviados[-1][1] == "feq_lembrete_inscricao",
      "2º toque escala p/ feq_lembrete_inscricao (urgência)")
check(wa.enviados[-1][2][2] == "16/08/2026", "lembrete leva o prazo real (urgência legítima)")
g2.add_evento("c2", "FEQ-1000", "inscricao_confirmada", ts=AGORA.isoformat())
r = funil.recuperar(g2, est2, FakeOnt(), wa, agora=AGORA + datetime.timedelta(days=6))
check(r["enviados"] == [], "convertido sai da régua")
est2.upsert_lead("c3", nome="Cid", telefone="+5519988880003", curso="FEQ-1000", optout=1)
g2.add_evento("c3", "FEQ-1000", "boleto_gerado", ts=(AGORA - datetime.timedelta(hours=30)).isoformat())
r = funil.recuperar(g2, est2, FakeOnt(), wa, agora=AGORA)
check(r["enviados"] == [], "opt-out jamais recebe recuperação")

# --- lead scoring ----------------------------------------------------------------
est2.upsert_lead("c4", nome="Du", telefone="+5519988880004", curso="FEQ-0480")
check(funil.score(est2, g2, "c4") == 0 and funil.classificar(0) == "frio", "lead mudo = frio")
est2.add_conversa("c4", "lead", "quero saber o cronograma e o valor")
check(funil.score(est2, g2, "c4") == 50 and funil.classificar(50) == "morno",
      "respondeu (+30) e perguntou preço/cronograma (+20) = morno")
g2.add_evento("c4", "FEQ-0480", "boleto_gerado", ts=AGORA.isoformat())
check(funil.classificar(funil.score(est2, g2, "c4")) == "quente", "boleto gerado = quente")
check(funil.score(est2, g2, "c3") == 0, "opt-out zera o score")

# --- opt-in LGPD na ingestão ------------------------------------------------------
est3 = Estado(":memory:")
r = Ingestao(est3, FakeOnt()).ingest({"nome": "Eva", "telefone": "+5519988880005",
                                      "curso": "FEQ-0480", "consentimento": "true"})
lead = est3.get_lead(r["card_id"])
check(bool(lead["optin_ts"]) and lead["optin_fonte"] == "meta_lead_ad",
      "opt-in explícito registrado na ingestão (LGPD)")

# ================= spec 16: ponte, offline conversions, ferramentas IA ===========

# --- CAPI: desduplicação (event_id do navegador) + sinais web + hash de e-mail ---
r = ads.capi("InitiateCheckout", "+5519988880009", "web-abc-123",
             action_source="website", contexto={"fbp": "fb.1.x", "ip": "1.2.3.4",
                                                "ua": "UA", "url": "https://feq/ponte"})
d = r["payload"]["data"][0]
check(d["event_id"] == "web-abc-123" and d["action_source"] == "website",
      "event_id do navegador + action_source=website (dedup Pixel×CAPI)")
check(d["user_data"]["fbp"] == "fb.1.x" and d["event_source_url"] == "https://feq/ponte",
      "fbp/ip/ua/url viajam como sinais de match")
r = ads.capi("Purchase", None, "e2", email="  Ana@X.com ")
check(r["payload"]["data"][0]["user_data"]["em"] ==
      [hashlib.sha256(b"ana@x.com").hexdigest()],
      "e-mail normalizado (trim+lower) e só sai como SHA-256")

# --- ponte: valida, ingere com opt-in fonte=ponte e vincula o event_id -----------
est4, g4 = Estado(":memory:"), Growth(":memory:")
ing4 = Ingestao(est4, FakeOnt())
dados = {"nome": "Gil", "telefone": "(19) 98888-0010", "curso": "feq-0480",
         "event_id": "11111111-2222-3333-4444-555555555555", "fbp": "fb.1.y",
         "ip": "10.0.0.1", "ua": "UA", "url": "https://feq/ponte?curso=FEQ-0480"}
r = ponte.processar(dados, est4, g4, ads, ing4)
check(r["ok"] and r["redirect"].endswith("/FEQ-0480/009"), "ponte devolve o redirect da turma")
ev = g4.eventos(card_id=r["card_id"])[0]
check(ev["tipo"] == "inicio_inscricao" and ev["capi"] == dados["event_id"],
      "evento gravado com o MESMO event_id do Pixel")
check(est4.get_lead(r["card_id"])["optin_fonte"] == "ponte", "opt-in LGPD com fonte=ponte")
check(funil.status_funil(g4, est4, r["card_id"]) == "initiated_checkout",
      "status derivado: initiated_checkout")
check(not ponte.processar({"telefone": "123", "curso": "FEQ-0480"}, est4, g4, ads, ing4)["ok"],
      "telefone inválido é recusado")
check(not ponte.processar({"telefone": "19988880011", "curso": "FEQ-9999"},
                          est4, g4, ads, ing4)["ok"], "curso desconhecido é recusado")
r2 = ponte.processar({**dados, "event_id": "<script>"}, est4, g4, ads, ing4)
check(r2["ok"] and r2["event_id"] != "<script>", "event_id sujo é substituído, não confiado")

# --- conversões offline: telefone casa lead -> Matriculado + Purchase com valor --
r = processar_matricula(g4, ads, est4, "+55 (19) 98888-0010", valor=3900, agora=AGORA)
check(r["ok"] and r["casou_lead"], "telefone (com 9º dígito) casa o lead da ponte")
check(est4.get_lead(r["card_id"])["fase"] == "Matriculado", "lead promovido a Matriculado")
mat = [e for e in g4.eventos(card_id=r["card_id"]) if e["tipo"] == "matricula_confirmada"][0]
check(mat["valor"] == 3900 and mat["capi"], "matrícula com valor + Purchase enviado")
check(funil.status_funil(g4, est4, r["card_id"]) == "enrolled", "status derivado: enrolled")
r = processar_matricula(g4, ads, est4, "aluno@email.com", curso="FEQ-1000", valor=4200)
check(r["ok"] and not r["casou_lead"], "e-mail sem lead local ainda envia a conversão")
check(not processar_matricula(g4, ads, est4, "sem@lead.com")["ok"],
      "e-mail sem lead e sem --curso é recusado")

# --- ferramentas p/ agentes: leitura livre, escrita atrás do gate ----------------
r = ferramentas.executar("listar_campanhas", {}, g4, ads)
check(r["ok"] and r["campanhas"] == [], "listar_campanhas em dry-run devolve []")
r = ferramentas.executar("aumentar_orcamento", {"objeto_id": "cmp1", "percentual": 200}, g4, ads)
check(r["ok"] and not r["aplicado"] and r["percentual"] == 50,
      "aumento clampado a +50% e vira sugestão sem AUTO_APPLY")
check(g4.listar_acoes(1)[0]["tipo"] == "orcamento_agente", "sugestão auditada em acoes_orcamento")
check(not ferramentas.executar("pausar_ad", {"ad_id": "ad1", "motivo": "  "}, g4, ads)["ok"],
      "pausar_ad sem motivo é recusado")
check(not ferramentas.executar("hackear_tudo", {}, g4, ads)["ok"], "ferramenta desconhecida recusada")
g4.add_desempenho({"dia": HOJE.isoformat(), "objeto_id": "as9", "nome": "X",
                   "campanha": "FEQ-0480 v1", "curso": "FEQ-0480", "gasto": 780.0, "leads": 10})
r = ferramentas.executar("custo_por_matricula", {"dias": 7}, g4, ads, agora=AGORA)
check(r["cursos"]["FEQ-0480"]["custo_por_matricula"] == 780.0
      and r["cursos"]["FEQ-0480"]["roas"] == 5.0,
      "custo por matrícula (780/1) e ROAS (3900/780) corretos")

# ================= bridge pages (LP two-step, spec 16 §2/§6/§7) ===================
import tempfile
from atena.growth import wp_deploy_bridge as wpd

# --- régua da ponte: 2h, feq_lead, e lead que respondeu sai ----------------------
g5, est5, wa5 = Growth(":memory:"), Estado(":memory:"), FakeWA()
est5.upsert_lead("c6", nome="Hugo", telefone="+5519988880006", curso="FEQ-0480")
g5.add_evento("c6", "FEQ-0480", "inicio_inscricao",
              ts=(AGORA - datetime.timedelta(hours=1)).isoformat())
r = funil.recuperar(g5, est5, FakeOnt(), wa5, agora=AGORA)
check(r["enviados"] == [], "ponte: 1h de silêncio ainda não dispara (espera = 2h)")
r = funil.recuperar(g5, est5, FakeOnt(), wa5, agora=AGORA + datetime.timedelta(hours=2))
check(r["enviados"] == ["c6"] and wa5.enviados[0][1] == "feq_lead",
      "ponte: 2h sem conversão -> feq_lead ('teve dificuldade? posso ajudar')")
est5.upsert_lead("c7", nome="Iva", telefone="+5519988880007", curso="FEQ-0480")
g5.add_evento("c7", "FEQ-0480", "inicio_inscricao",
              ts=(AGORA - datetime.timedelta(hours=5)).isoformat())
est5.add_conversa("c7", "lead", "tive um problema no site")   # respondeu APÓS o checkout
r = funil.recuperar(g5, est5, FakeOnt(), wa5, agora=AGORA + datetime.timedelta(days=30))
check("c7" not in r["enviados"], "lead que respondeu após o checkout sai da régua (cérebro cuida)")

# --- aba Verificação: fila + veredito do operador fecha o loop (spec 17 §8b) ------
g6, est6, wa6 = Growth(":memory:"), Estado(":memory:"), FakeWA()
est6.upsert_lead("v1", nome="Vera", telefone="+5519977770001", curso="FEQ-0480")
g6.add_evento("v1", "FEQ-0480", "inicio_inscricao",
              ts=(AGORA - datetime.timedelta(minutes=10)).isoformat())
check([p["card_id"] for p in g6.verificacoes_pendentes()] == ["v1"],
      "lead da ponte sem desfecho entra na fila de verificação")
r = funil.ativar_um(g6, est6, FakeOnt(), wa6, "v1", agora=AGORA)
check(r["ok"] and r["toque"] == 1 and wa6.enviados[0][1] == "feq_lead",
      "'não inscreveu' -> 1º toque AGORA (10min após o checkout, sem esperar as 2h)")
g6.add_evento("v1", "FEQ-0480", "verificacao_negativa")
check(g6.verificacoes_pendentes() == [] and
      [p["card_id"] for p in g6.checkouts_pendentes()] == ["v1"],
      "verificacao_negativa: sai da fila de verificação mas SEGUE na régua")
r = funil.recuperar(g6, est6, FakeOnt(), wa6, agora=AGORA + datetime.timedelta(hours=3))
check(r["enviados"] == [], "régua respeita a espera do 2º toque (24h) após a ativação imediata")
r = funil.recuperar(g6, est6, FakeOnt(), wa6, agora=AGORA + datetime.timedelta(hours=25))
check(r["enviados"] == ["v1"] and wa6.enviados[-1][1] == "feq_lembrete_inscricao",
      "24h depois a régua continua do toque 2 (cadência não recomeça)")
est6.upsert_lead("v2", nome="Otto", telefone="+5519977770002", curso="FEQ-0480", optout=1)
g6.add_evento("v2", "FEQ-0480", "inicio_inscricao", ts=AGORA.isoformat())
check(not funil.ativar_um(g6, est6, FakeOnt(), wa6, "v2", agora=AGORA)["ok"],
      "ativação imediata respeita opt-out (guarda de conteúdo vale sempre)")
check(not funil.ativar_um(g6, est6, FakeOnt(), wa6, "v-nao-existe", agora=AGORA)["ok"],
      "card sem checkout pendente -> ativação recusada com motivo")
g6.add_evento("v1", "FEQ-0480", "inscricao_confirmada")
check("v1" not in [p["card_id"] for p in g6.checkouts_pendentes()],
      "'inscreveu' (inscricao_confirmada) tira o lead da régua e da fila")

# --- render da LP: tokens preenchidos, modal, CTAs e destino ---------------------
lp = ponte.render(g5, FakeOnt(), "FEQ-0480")
check(lp and "{{" not in lp, "LP renderiza sem tokens {{...}} sobrando")
check('src="https://cdn.tailwindcss.com' not in lp and "<style>" in lp
      and "data-cta" in lp and "bridge-form" in lp,
      "LP tem CSS estático (não CDN — plugins como WindPress quebram scripts vizinhos), "
      "CTAs que abrem modal e o formulário do modal")
check("noindex" in lp, "noindex injetado")
check("&&" not in lp, "sem '&&' cru no HTML — WordPress normaliza '&' p/ '&#038;' e quebraria o JS"
      " (o operador lógico só existe DENTRO do payload base64, imune a esse filtro)")
b64 = re.search(r'atob\("([^"]+)"\)', lp).group(1)
decodificado = base64.b64decode(b64).decode()
check("/webhook/ponte" in decodificado and "FEQ-0480/009" in decodificado and "&&" in decodificado,
      "webhook e destino Extecamp injetados no payload base64 (decodificado corretamente)")
check(ponte.render(g5, FakeOnt(), "FEQ-9999") is None, "curso fora do cursos_metas não renderiza")

# --- payload do bridge-logic ({whatsapp, curso_id}) é aceito ---------------------
r = ponte.processar({"nome": "Alias", "whatsapp": "19966660001", "curso_id": "feq-0580",
                     "event_id": "e2e2e2e2-aaaa-bbbb-cccc-dddddddddddd"},
                    est5, g5, ads, Ingestao(est5, FakeOnt()))
check(r["ok"] and r["redirect"].endswith("FEQ-0580/001"),
      "payload {whatsapp, curso_id} do bridge-logic é aceito")

# --- deploy WP: renderiza todas e, sem credenciais, não publica ------------------
pgs = wpd.renderizar(g5, FakeOnt(), g5.cursos(), tempfile.mkdtemp())
check(len(pgs) == 9 and all(pathlib.Path(p["arquivo"]).exists() for p in pgs),
      "deploy renderiza as 9 LPs do portfólio em disco")
check(all(x["status"] == "dry_run" for x in wpd.publicar(pgs)),
      "sem credenciais WP o deploy é dry-run (não publica nada)")

# --- /thank-you-page-lp/: interstício pro pixel de Lead do Google Ads/GTM --------
# Sufixo, não prefixo: "lp-thank-you-page" não disparou o Lead no GTM (usuário
# confirmou) — o gatilho exige o path COMEÇANDO com /thank-you-page, não só contendo
# a substring em qualquer posição. Sufixo preserva esse prefixo sem colidir com a
# página Elementor pré-existente no slug exato "thank-you-page" (id 18818, outra campanha).
check("window.location.href = \"/" + wpd.OBRIGADO_SLUG + "/?to=\" + encodeURIComponent(CFG.destino)"
      in (pathlib.Path(__file__).resolve().parents[1] / "web" / "bridge-logic.js").read_text(),
      "após o modal, o fluxo passa por /thank-you-page-lp/ antes do destino real")
check(wpd.OBRIGADO_SLUG.startswith("thank-you-page"),
      "slug COMEÇA com thank-you-page (não só contém) — gatilho do GTM exige prefixo de path")
obrigado = ponte.pagina_obrigado_html()
check('ns.html?id=GTM-WPXLCWJ' in obrigado, "noscript do GTM (body code) presente na página de obrigado")
b64_obrigado = re.search(r'atob\("([^"]+)"\)', obrigado).group(1)
dec_obrigado = base64.b64decode(b64_obrigado).decode()
check('URLSearchParams' in dec_obrigado and 'p.get("to")' in dec_obrigado and "&&" not in obrigado,
      "bootstrap lê ?to= e nunca tem '&' cru fora do base64 (imune ao convert_chars do WP)")
check(".page-header, .pre-content { display: none !important; }" in obrigado
      and "#site-header, #header-container" in obrigado,
      "página de obrigado também esconde cabeçalho/faixa de título do tema (mesmos seletores das LPs)")
check('class="lp-obrigado"' in obrigado and "<img" in obrigado,
      "página de obrigado usa o mesmo canvas full-bleed + logo das LPs, não fica emoldurada pelo tema")
check(wpd.publicar_obrigado()["status"] == "dry_run",
      "sem credenciais WP, publicar /lp-thank-you-page/ também é dry-run")

# ================= fluxo consolidado (spec 17): inscricao, telemetria, cadência ===
# --- destino é o formulário /curso/inscricao/ ------------------------------------
check(all("/curso/inscricao/" in c["url"] for c in g5.cursos() if c["url"]),
      "seed aponta para o formulário /curso/inscricao/")
check([c["url"] for c in g5.cursos() if c["curso"] == "FEQ-0499"] == [""],
      "curso sem turma aberta (0499) fica SEM url de inscrição — LP vira lista de espera")
meta = {"curso": "FEQ-0480", "turma": "009",
        "url": "https://ins.extecamp.unicamp.br/curso/dados/FEQ-0480/009"}
check(ponte.url_inscricao(meta, None).endswith("/curso/inscricao/FEQ-0480/009"),
      "url antiga /dados/ é normalizada para /inscricao/")
check(ponte.url_inscricao(meta, {"url_extecamp": "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/010"})
      .endswith("/010"), "url_extecamp da ontologia (governada) tem preferência")

# --- telemetria de sessão + analytics ---------------------------------------------
s1 = {"sessao_id": "sess-1", "curso": "FEQ-0480", "duracao": 40, "max_scroll": 90,
      "ultima_secao": "investimento", "secoes": {"hero": 8, "beneficios": 5, "objetivos": 4,
                                                 "valor": 6, "habilidades": 3, "conteudo": 12,
                                                 "quem": 4, "professor": 3, "depoimentos": 3,
                                                 "investimento": 7},
      "cta_cliques": 1, "modal_aberto": 1, "modal_enviado": 1}
s2 = {"sessao_id": "sess-2", "curso": "FEQ-0480", "duracao": 9, "max_scroll": 30,
      "ultima_secao": "valor", "secoes": {"hero": 5, "beneficios": 3, "objetivos": 2,
                                          "valor": 4, "habilidades": 2}}
g5.upsert_lp_sessao(s1); g5.upsert_lp_sessao(s2)
g5.upsert_lp_sessao({**s1, "modal_aberto": 0, "modal_enviado": 0, "duracao": 55})
a = g5.lp_analytics(curso="FEQ-0480")
check(a["sessoes"] == 2, "upsert por sessao_id não duplica sessão")
check(a["taxas"]["modal_enviado_pct"] == 50, "flag de modal enviado sobrevive a beacon posterior")
check(a["funil_secoes"]["hero"]["alcance_pct"] == 100
      and a["funil_secoes"]["investimento"]["alcance_pct"] == 50,
      "funil por seção: hero 100%, investimento 50%")
check(a["maior_abandono"]["antes_de"] == "conteudo" and a["maior_abandono"]["queda_pct"] == 50,
      "maior abandono detectado antes de 'conteudo' (onde o lead para)")
check(a["saiu_em"].get("valor") == 1, "registro de onde o lead saiu")
r = ponte.processar({"nome": "Tel", "whatsapp": "19955550003", "curso_id": "FEQ-0480",
                     "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", "sessao_id": "sess-2"},
                    est5, g5, ads, Ingestao(est5, FakeOnt()))
lig = g5.conn.execute("SELECT card_id FROM lp_sessoes WHERE sessao_id='sess-2'").fetchone()[0]
check(lig == r["card_id"], "modal enviado vincula a sessão de telemetria ao lead")
r = ferramentas.executar("analise_lp", {"curso": "FEQ-0480"}, g5, ads)
check(r["ok"] and r["sessoes"] == 2, "agentes leem o funil da LP via ferramenta analise_lp")

# --- cadência de 5 toques ----------------------------------------------------------
g6, est6, wa6 = Growth(":memory:"), Estado(":memory:"), FakeWA()
est6.upsert_lead("c9", nome="Kim", telefone="+5519988880009", curso="FEQ-0480")
g6.add_evento("c9", "FEQ-0480", "inicio_inscricao",
              ts=(AGORA - datetime.timedelta(hours=3)).isoformat())
t = AGORA
esperados = ["feq_lead", "feq_lembrete_inscricao", "feq_reengajamento",
             "feq_lembrete_inscricao", "feq_lead"]
for i, (tpl, espera) in enumerate(funil.CADENCIA):
    t = t + datetime.timedelta(hours=(espera or 0) + 1)
    r = funil.recuperar(g6, est6, FakeOnt(), wa6, agora=t)
    check(r["enviados"] == ["c9"] and wa6.enviados[-1][1] == esperados[i],
          f"toque {i + 1}/5 = {esperados[i]}")
r = funil.recuperar(g6, est6, FakeOnt(), wa6, agora=t + datetime.timedelta(days=30))
check(r["enviados"] == [], "após 5 toques a régua para (não vira spam)")

# --- valor da conversão offline ----------------------------------------------------
check(funil.valor_curso({"plano": {"a_vista": "16455.75", "n": 19, "valor_parcela": "866.09"}})
      == 16455.75, "valor_curso prefere o valor à vista")
check(funil.valor_curso({"plano": {"n": 10, "valor_parcela": "100.50"}}) == 1005.0,
      "sem à vista, valor = parcelas × valor_parcela")
check(funil.valor_curso(None) == 0, "sem plano, valor 0 (conversão conta mesmo assim)")

# --- fragmento p/ WordPress + conteúdo programático --------------------------------
frag = ponte.fragmento(lp)
check("<!doctype" not in frag and "bridge-form" in frag and "<style>" in frag
      and 'src="https://cdn.tailwindcss.com' not in frag,
      "fragmento WP: miolo com scripts e CSS estático, sem doctype/head/CDN")
c_html = ponte._conteudo_html("(1) Módulo A; (2) Módulo B; (3) Módulo C")
check(c_html.count("<li") == 3 and "Módulo B" in c_html,
      "conteúdo programático vira lista numerada ancorada")
check("wa.me/551931672313" in lp and "wa-flutuante" in lp,
      "botão WhatsApp flutuante presente com o número do WABA")

# --- fotos reais (site institucional da FEQ, não fabricadas) -----------------------
check(ponte._foto_professor_html("Flavio Vasconcelos da Silva").startswith('<img src="https://www.feq.unicamp.br/')
      and "foto-professor" in ponte._foto_professor_html("Flavio Vasconcelos da Silva"),
      "professor reconhecido -> <img> com a foto oficial do diretório de docentes")
check(ponte._foto_professor_html("Alguém Não Cadastrado") == "",
      "professor não mapeado -> sem <img> (nunca inventa foto)")
check("feq.unicamp.br/wp-content/uploads/2024/10/Group-103.png" in ponte.HERO_BG_URL_DEFAULT,
      "hero usa a imagem institucional (laboratório/planta), não mais o stock genérico")

# --- jornada nível MBA: nav-âncora, credencial real do professor, cross-sell -------
cred = ponte._professor_credencial_html("Osvaldir Pereira Taranto")
check("lattes.cnpq.br/3804707640110777" in cred and "DEPro" in cred,
      "professor reconhecido -> departamento real + link do Lattes oficial")
check(ponte._professor_credencial_html("Alguém Não Cadastrado") == "",
      "professor sem credencial mapeada -> sem bloco (nunca inventa departamento/Lattes)")

rel = ponte._cursos_relacionados_html(g5, FakeOnt(), "FEQ-0480")
check(rel.count('<a href="/inscricao-') == 3 and "FEQ-0480" not in rel,
      "cursos relacionados: no máx. 3 cards, nunca o próprio curso da página")
# com o portfólio inteiro no ar, o cross-sell prioriza quem está com inscrição ABERTA
_ont_cap = _OntCaptabilidade()
rel_ab = ponte._cursos_relacionados_html(g5, _ont_cap, "FEQ-0480")
check("inscricao-feq-0499-001" not in rel_ab,
      "cross-sell não sugere curso FECHADO havendo abertos suficientes")
check("inscricao-feq-0580-001" in rel, "link usa o mesmo padrão de slug do deploy WP")

lp2 = ponte.render(g5, FakeOnt(), "FEQ-0480")
check('id="beneficios"' in lp2 and 'id="programa"' in lp2 and 'id="professor"' in lp2
      and 'id="investimento"' in lp2 and 'id="faq"' in lp2,
      "âncoras da nav de atalhos existem nas seções correspondentes")
check('href="#beneficios"' in lp2 and 'href="#faq"' in lp2, "nav-âncora aponta pros ids certos")
check('id="cta-fixo"' in lp2, "barra de CTA fixa presente")

# --- demais professores (LinkedIn/Lattes reais, achados por busca) -----------------
op = ponte._outros_professores_html(["Flavio Vasconcelos da Silva", "Alguém Não Cadastrado"])
check('lattes.cnpq.br/1108208002886617' in op and '<a href' in op,
      "professor com link verificado -> vira <a>")
check('Alguém Não Cadastrado' in op and 'href' not in op.split('Alguém Não Cadastrado')[0].split('<span>')[-1],
      "professor sem link verificado -> nome puro, nunca inventa link")
check(ponte._outros_professores_html([]) == "" and ponte._outros_professores_html(None) == "",
      "curso sem professores adicionais -> seção some (não deixa bloco vazio)")

# --- catálogo real (não FakeOnt): campos novos do redesign dos cards chegam até a LP
from atena.ontologia.servico import Ontologia as _OntReal
_ont_real = _OntReal()
_c0480 = _ont_real.curso("FEQ-0480")
check(_c0480["formato_aulas_resumo"] == "Aulas online ao vivo + gravadas"
      and "gravadas liberadas às segundas" in _c0480["formato_aulas_detalhe"],
      "FEQ-0480: card de formato de aulas tem o texto real dado pelo usuário")
check(_c0480["professores_adicionais"][0] == "Claudio Vieira Neto"
      and "Vitor Augusto Oliveira da Silva" in _c0480["professores_adicionais"]
      and len(_c0480["professores_adicionais"]) == 8,
      "FEQ-0480: lista de professores adicionais completa e na ordem informada")
check("Professores:" not in (_c0480["conteudo_programatico"] or ""),
      "ementa não tem mais o texto travado de professores desatualizado")
lp_real = ponte.render(g5, _ont_real, "FEQ-0480")
check("Aulas online ao vivo + gravadas" in lp_real
      and "19x de R$ 866,09" in lp_real          # tabela: fica como referência do desconto
      and "R$ 69,00" in lp_real,
      "LP renderizada com ontologia real: cards 2 e 3 no formato novo")
# com a campanha de agosto no ar, o destaque passa a ser o preço promocional
check("19x de R$ 799,00" in lp_real and "R$ 13.987,39" in lp_real,
      "LP destaca a promoção vigente sem esconder o valor de tabela")
check("Mais professores do curso" in lp_real and "lattes.cnpq.br/0419561926689242" in lp_real,
      "LP renderizada com ontologia real: seção de outros professores presente")

# --- período de oferecimento (datas de início/fim das aulas, fonte Extecamp) -------
check('data-secao="periodo"' in lp_real and "14/09/2026" in lp_real and "15/03/2028" in lp_real
      and "Período do curso" in lp_real,
      "0480 (aberto): faixa com o período de oferecimento (início e fim) da Extecamp")
_lp_espera = ponte.render(g5, _ont_real, "FEQ-0499")
check("Período da última turma" in _lp_espera,
      "curso em lista de espera: período rotulado como 'última turma' (não engana o lead)")
_lp_0670 = ponte.render(g5, _ont_real, "FEQ-0670")
check("01/02/2027" in _lp_0670 and "01/06/2027" in _lp_0670,
      "0670 (datas buscadas na Extecamp nesta sessão) aparecem na LP")
# curso sem as datas na ontologia -> a faixa some (nunca inventa)
check(ponte._periodo_html({"aulas_inicio": None, "aulas_fim": None}) == "",
      "curso sem período no grafo -> faixa de datas some")

# --- conteúdo rico importado das páginas oficiais preexistentes (spec 17) ----------
check('data-secao="objetivos"' in lp_real and "Tornar os profissionais diferenciados" in lp_real,
      "0480: objetivos oficiais na LP")
check('class="hab-chip"' in lp_real and "Machine Learning e Deep Learning" in lp_real,
      "0480: habilidades oficiais em chips")
check('class="prog-mod"' in lp_real and "Processo de Decisão de Markov (MDP)" in lp_real,
      "0480: programa em accordion com subtópicos oficiais")
check('id="depoimentos"' in lp_real and "Luiz Octavio" in lp_real and "Juliana Panizza" in lp_real,
      "0480: depoimentos REAIS transcritos da página oficial")
check('href="#depoimentos"' in lp_real, "0480: nav-âncora ganha o atalho Depoimentos")
check("Nossos alunos atuam em empresas como" in lp_real and "Braskem" in lp_real,
      "0480: prova social oficial de empresas")
check('id="inscricao"' in lp_real and 'class="passo-n"' in lp_real and "termo de compromisso" in lp_real,
      "0480: passo a passo oficial de inscrição")
check("lato-sensu" in lp_real and "reconhecida pelo MEC" in lp_real,
      "0480: FAQ de certificação/MEC (texto oficial)")
check("R$ 14.000,00" in lp_real and "Perspectiva profissional" in lp_real,
      "0480: salário de referência publicado (com a ressalva original)")
check("Livre-Docente em Controle de Processos" in lp_real,
      "0480: bio oficial do professor responsável no card")
# --- fotos dos docentes em destaque (fonte: páginas oficiais) + rótulo -------------
check("vitor_machado-e1721925782819.jpg" in lp_real,
      "0480: Vitor Machado em destaque com foto da página oficial")
check("douglas-alves-goulart.jpg" in lp_real and "Cientista de Dados" in lp_real
      and "data-driven" in lp_real,
      "0480: Douglas em destaque com foto + bio readequada do LinkedIn")
check("Mais professores do curso" in lp_real and "Demais professores" not in lp_real,
      "rótulo trocado para 'Mais professores do curso'")
check('class="depo-aspa"' in lp_real and "&ldquo;" not in lp_real,
      "depoimentos com aspa unicode (sem entidade que o WP mangla)")
# Foto real de quem deu o depoimento quando existe nas páginas oficiais; iniciais quando não.
check('class="depo-foto"' in lp_real and "2024/02/luiz.png" in lp_real
      and "2024/02/juliana.png" in lp_real and 'class="depo-avatar"' not in lp_real,
      "0480: depoentes com foto oficial (Luiz e Juliana), sem cair nas iniciais")
check('class="depo-texto"' in lp_real and "<blockquote" not in lp_real,
      "depoimento usa .depo-texto, não <blockquote> (o tema WP pinta blockquote de branco)")
check("914,20" not in lp_real and "1.097,05" not in lp_real,
      "0480: plano de pagamento DESATUALIZADO da página antiga NÃO é importado (Extecamp vence)")
check("Garantia de 7 dias" not in lp_real, "0480: sem garantia inventada (só o FEQ-1000 a publica)")
lp_1000 = ponte.render(g5, _ont_real, "FEQ-1000")
check("Garantia de 7 dias" in lp_1000 and "Edson Papassídero" in lp_1000,
      "1000: garantia oficial de 7 dias + depoimento próprio")
check("linkedin.com/in/edneu-jatkoski" in lp_1000 and "Refinaria de Paulínia" in lp_1000,
      "1000: bio e LinkedIn do Jatkoski (fonte: página oficial do curso)")
check("guilherme-300x300-1.png" in lp_1000 and "edneu.png" in lp_1000,
      "1000: Castilho e Jatkoski em destaque com foto oficial")
check(lp_1000.count("2025/09/D-Flavio-Vasconcelos") == 1,
      "1000: Flavio em destaque com foto (só 1 card; responsável é o Osvaldir)")
lp_0580b = ponte.render(g5, _ont_real, "FEQ-0580")
check('id="depoimentos"' not in lp_0580b and 'href="#depoimentos"' not in lp_0580b,
      "0580: sem depoimentos publicados -> seção e âncora somem (nunca inventa)")
check('class="prog-mod"' not in lp_0580b and "<li" in lp_0580b,
      "0580: sem detalhe oficial de programa -> lista numerada de sempre (sem accordion vazio)")
check('data-secao="objetivos"' in lp_0580b, "0580: objetivos oficiais presentes")
lp_0333b = ponte.render(g5, _ont_real, "FEQ-0333")
check("lattes.cnpq.br/6149769815435543" in lp_0333b,
      "0333: Lattes do Doubek corrigido pro publicado na página oficial do curso")
check('class="prog-mod"' in lp_0333b and "Diferença entre CCS e CCUS" in lp_0333b,
      "0333: programa detalhado oficial em accordion")

# --- LP de TODO o portfólio + modo LISTA DE ESPERA (spec 17 §2b) -------------------
for _cod in ("FEQ-0360", "FEQ-0331", "FEQ-0299", "FEQ-0670", "FEQ-0499"):
    _lp = ponte.render(g5, _ont_real, _cod)
    check(_lp and "{{" not in _lp, f"{_cod}: LP renderiza sem token sobrando")
_lp0299 = ponte.render(g5, _ont_real, "FEQ-0299")
check("Ronierik Pioli Vieira" in _lp0299 and "CrystalWalk" in _lp0299,
      "0299 (novo): professor e programa oficiais na LP")
check("ensino médio" in _lp0299.lower(),
      "0299: requisito de ensino MÉDIO (não superior) vem da ontologia")
_lp0670 = ponte.render(g5, _ont_real, "FEQ-0670")
check("GROMACS" in _lp0670 and "Luiz Guilherme Lomônaco" in _lp0670,
      "0670 (novo): programa e corpo docente oficiais")
_lp0360 = ponte.render(g5, _ont_real, "FEQ-0360")
check("Fabio Marcelo Brogna" in _lp0360 and "HAZOP" in _lp0360
      and "15.883" in _lp0360,
      "0360: docentes, programa e salário de referência oficiais")

# curso SEM turma aberta -> lista de espera (nada de inscrição prometida)
_lp0499 = ponte.render(g5, _ont_real, "FEQ-0499")
check("Quero ser avisado" in _lp0499 and "Inscreva-se" not in _lp0499,
      "0499 (fechado): CTA vira 'Quero ser avisado', sem prometer inscrição")
check("lista de espera" in _lp0499.lower() and 'id="inscricao"' not in _lp0499,
      "0499: some a seção 'Como se inscrever' (não há inscrição aberta)")
check('href="#inscricao"' not in _lp0499, "0499: nav-âncora sem atalho de Inscrição")
# fallback: os depoentes do 0499 não têm foto publicada -> iniciais, nunca rosto inventado
check('class="depo-avatar"' in _lp0499 and 'class="depo-foto"' not in _lp0499,
      "0499: depoentes sem foto oficial ficam com as iniciais")
# prova social (professor + depoimentos) vem ANTES do conteúdo programático
check(lp_real.index('data-secao="professor"') < lp_real.index('data-secao="depoimentos"')
      < lp_real.index('data-secao="conteudo"') < lp_real.index('data-secao="investimento"'),
      "ordem: professor -> depoimentos -> conteúdo -> investimento")
_b64 = re.search(r'atob\("([^"]+)"\)', _lp0499).group(1)
_cfg = base64.b64decode(_b64).decode()
check('"aberto": false' in _cfg and '"destino": ""' in _cfg,
      "0499: window.BRIDGE marca aberto=false e destino vazio (JS não redireciona)")
check("confirmarEspera" in _cfg and "espera-ok" in _cfg,
      "0499: JS confirma a lista de espera na própria página")
check("Valores da última turma" in _lp0499,
      "0499: investimento fica informativo, sem prometer preço da próxima turma")
# ...e o curso ABERTO segue com o fluxo de inscrição intacto
_b64a = re.search(r'atob\("([^"]+)"\)', lp_real).group(1)
_cfga = base64.b64decode(_b64a).decode()
check('"aberto": true' in _cfga and "/curso/inscricao/FEQ-0480/009" in _cfga,
      "0480 (aberto): destino da Extecamp preservado, aberto=true")
check('id="inscricao"' in lp_real and "Inscreva-se →" in lp_real,
      "0480 (aberto): seção de inscrição e CTA normais")

# lead de lista de espera NÃO pode virar checkout: senão cairia na aba Verificação
# (pedindo pro operador conferir uma inscrição inexistente) e na régua de recuperação.
g8, est8, wa8 = Growth(":memory:"), Estado(":memory:"), FakeWA()
r = ponte.processar({"nome": "Espera", "whatsapp": "19955559999", "curso_id": "FEQ-0499",
                     "event_id": "espera-aaaa-bbbb-cccc-dddddddddddd"},
                    est8, g8, ads, Ingestao(est8, _ont_real), ont=_ont_real)
check(r["ok"], "lead de curso fechado é capturado (lista de espera)")
_tipos = [e["tipo"] for e in g8.eventos(card_id=r["card_id"])]
check(_tipos == ["lista_espera"],
      "curso fechado grava 'lista_espera', não 'inicio_inscricao'")
check(g8.checkouts_pendentes() == [] and g8.verificacoes_pendentes() == [],
      "lead de espera fica FORA da régua de recuperação e da fila de Verificação")
check(funil.recuperar(g8, est8, _ont_real, wa8, agora=AGORA + datetime.timedelta(days=3))["enviados"] == [],
      "régua não manda template de inscrição p/ quem está na lista de espera")
_c1000 = _ont_real.curso("FEQ-1000")
check("sábados pela manhã, das 9h às 12h" in _c1000["formato_aulas_detalhe"]
      and "cases e exercícios" in _c1000["formato_aulas_detalhe"]
      and "NÃO exigem presença síncrona" in _c1000["formato_aulas_detalhe"]
      and "rotina bastante corrida" in _c1000["formato_aulas_detalhe"],
      "FEQ-1000: teoria ao vivo 9h-12h sáb + tarde assíncrona p/ rotina corrida (texto do usuário)")
check(len(_c1000["professores_adicionais"]) == 11 and "Sérgio D Amore Filho" in _c1000["professores_adicionais"],
      "FEQ-1000: lista de professores adicionais completa")
_c0333 = _ont_real.curso("FEQ-0333")
check(_c0333["formato_aulas_resumo"] == "Presencial no 1º encontro + online ao vivo"
      and "demais encontros online ao vivo" in _c0333["formato_aulas_detalhe"],
      "FEQ-0333: 1º encontro presencial + demais online ao vivo (texto real dado pelo usuário)")

# --- FEQ-0580: formato de aulas (teórica gravada + lab ao vivo) + Claudio com LinkedIn
_c0580 = _ont_real.curso("FEQ-0580")
check(_c0580["formato_aulas_resumo"] == "Aulas gravadas + laboratório ao vivo"
      and "laboratórios práticos ao vivo aos sábados" in _c0580["formato_aulas_detalhe"],
      "FEQ-0580: card de formato de aulas com o texto real dado pelo usuário")
lp_0580 = ponte.render(g5, _ont_real, "FEQ-0580")
check("Aulas gravadas + laboratório ao vivo" in lp_0580,
      "LP do FEQ-0580 usa o card de formato de aulas novo, não mais o fallback")
check('href="https://www.linkedin.com/in/claudio-vieira-neto"' in lp_0580,
      "Claudio Vieira Neto agora tem LinkedIn verificado na LP do FEQ-0580")
op580 = ponte._outros_professores_html(_c0580["professores_adicionais"])
check('href="https://www.linkedin.com/in/claudio-vieira-neto"' in op580,
      "PROFESSOR_LINK vale pro nome em qualquer curso (mesma pessoa, mesma entrada)")

# --- auditoria sistêmica: toda classe usada no template tem que existir no CSS -----
# (achado real: bridge-template.html usava "pt-10 pb-14" no wrapper da hero, mas o CSS
# só definia "pt-14"/"pb-10" — invertidas. Sem regra nenhuma pega, sem erro, sem
# quebrar nada: o navegador ignora a classe desconhecida em silêncio. O resultado foi
# a hero sem respiro nenhum no topo — exatamente a queixa "logo no lugar errado" que
# voltou depois do fix anterior "resolver" só o sintoma visível.
# Varre o TEMPLATE ESTÁTICO + a LP já renderizada (lp2, lp_real) + fragmentos gerados
# à parte (op): funções como _outros_professores_html/_cursos_relacionados_html/
# _professor_credencial_html geram HTML com classes próprias que nunca aparecem no
# arquivo .html estático — só na saída de ponte.render() — e ficavam fora da
# auditoria antes desta linha.)
_html = ((pathlib.Path(__file__).resolve().parents[1] / "web" / "bridge-template.html").read_text()
         + lp2 + lp_real + op + lp_0580 + lp_1000 + lp_0333b)
_css = (pathlib.Path(__file__).resolve().parents[1] / "web" / "bridge.css").read_text()
_usadas = set()
for _m in re.finditer(r'class="([^"]*)"', _html):
    _usadas.update(_m.group(1).split())
_faltando = []
for _c in _usadas:
    _seletor = "." + re.sub(r"([:./])", r"\\\1", _c)
    if _seletor not in _css:
        _faltando.append(_c)
check(not _faltando, f"toda classe usada no template tem definição no CSS (faltando: {_faltando})")
check("input:-webkit-autofill" in _css and "-webkit-text-fill-color" in _css,
      "autofill do navegador recolorido (sem isso o texto digitado some: branco sobre branco)")

# --- Growth.conn é compartilhado entre threads (waitress) — leitura sem lock ------
# corrompe o cursor concorrente com escrita (mesmo bug de atena/core/estado.py,
# 18/07/2026: checkouts_pendentes/verificacoes_pendentes/add_evento são exatamente
# o caminho da aba Verificação). 6 leitoras + 3 escritoras por ~1s.
import threading as _threading
import time as _time
g7 = Growth(":memory:")
est7 = Estado(":memory:")
est7.upsert_lead("gc1", nome="Zeca", telefone="1199990000", curso="FEQ-0480")
g7.add_evento("gc1", "FEQ-0480", "inicio_inscricao")
_erros_g = []
_parar_g = _threading.Event()


def _leitor_g():
    while not _parar_g.is_set():
        try:
            g7.checkouts_pendentes()
            g7.verificacoes_pendentes()
            g7.cursos()
        except Exception as e:
            _erros_g.append(e)


def _escritor_g():
    n = 0
    while not _parar_g.is_set():
        try:
            g7.add_evento("gc1", "FEQ-0480", "recuperacao_enviada", ts=f"2026-07-18T10:00:{n%60:02d}")
            n += 1
        except Exception as e:
            _erros_g.append(e)


_threads_g = [_threading.Thread(target=_leitor_g) for _ in range(6)] + \
             [_threading.Thread(target=_escritor_g) for _ in range(3)]
for _t in _threads_g:
    _t.start()
_time.sleep(1)
_parar_g.set()
for _t in _threads_g:
    _t.join()
check(not _erros_g, f"Growth sob concorrência sem erro de SQLite (achado: {_erros_g[0] if _erros_g else None})")

print(f"\n{ok} checks OK — growth")
