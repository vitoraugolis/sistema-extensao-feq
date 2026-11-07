#!/usr/bin/env python3
"""
Atena · Robô de inscrição na Extecamp (spec 21).

Leva a ficha da etapa 2 da LP para o formulário PÚBLICO de inscrição do curso
(ins.extecamp.unicamp.br/curso/inscricao/<SIGLA>/<TURMA>) — o mesmo que o lead
preencheria sozinho. São 4 passos HTTP, todos formulários PHP simples:

  1. GET da URL do curso      -> página que se auto-submete (procura_aluno.php)
  2. POST procura_aluno.php   -> "Já fiz curso" (busca por documento) | "Nunca fiz"
  3a. ex-aluno: documento achado -> pede a data de nascimento -> ficha pré-preenchida
  3b. novo: "Fazer primeiro cadastro" -> ficha em branco
  4. POST da ficha            -> redireciona para ok-inscricao*.php?id1=… (o link
                                 individual que o lead usa para seguir: taxa, boleto)

Fail-closed: campo obrigatório que a ficha não cobre, documento achado mas página
de nascimento desconhecida, resposta sem o link ok-inscricao — tudo vira
`pendencias` e NADA é enviado. Um cadastro duplicado ou com dado inventado na
Extecamp custa mais que um operador terminar a inscrição à mão.

`valer=False` (padrão) faz os passos de LEITURA (busca do documento, carga da
ficha) e para antes do POST final: é o dry-run, que não cria nada na Extecamp.

Nunca loga nem devolve valores da ficha em texto de erro — só nomes de campo.
"""
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin

BASE = "https://ins.extecamp.unicamp.br/inscricao/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
_NAO_ACHOU = re.compile(r"n[aã]o\s+localizado", re.I)
_OK_LINK = re.compile(r"""(?:https?://ins\.extecamp\.unicamp\.br/inscricao/)?(ok-inscricao[\w-]*\.php\?[^"'\s<>]+)""")

# Regras da coordenação para o bloco "Acadêmico e Profissional" (não pedimos isso
# ao lead): texto "Não informado", número "00", localidade = cidade onde mora.
NAO_INFORMADO = "Não informado"


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _dig(s):
    return re.sub(r"\D", "", str(s or ""))


# ------------------------------------------------------------------ parser
class _Forms(HTMLParser):
    """Todos os <form> da página: action e campos (valor atual, opções, required).
    Tolerante: o HTML da Extecamp tem comentário cobrindo botões, inputs duplicados
    e <option> sem value (o navegador manda o texto — fazemos igual)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self._sel, self._opt, self._ta = [], None, None, None

    def _f(self):
        if not self.forms:
            self.forms.append({"action": "", "campos": {}})
        return self.forms[-1]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append({"action": a.get("action") or "", "campos": {}})
        elif tag == "input" and a.get("name"):
            tipo = (a.get("type") or "text").lower()
            if tipo in ("submit", "button", "image", "reset"):
                return
            if tipo in ("checkbox", "radio") and "checked" not in a:
                self._f()["campos"].setdefault(a["name"], {"valor": "", "req": False, "opcoes": None})
                return
            c = self._f()["campos"]
            # primeiro wins: a ficha repete `comprovante_de_pagamento` (botão comentado +
            # hidden) — o navegador manda os dois, mas o valor útil é o primeiro não vazio
            if a["name"] in c and c[a["name"]]["valor"]:
                return
            c[a["name"]] = {"valor": a.get("value") or "", "req": "required" in a, "opcoes": None,
                            "readonly": "readonly" in a}
        elif tag == "textarea" and a.get("name"):
            self._ta = a["name"]
            self._f()["campos"][a["name"]] = {"valor": "", "req": "required" in a, "opcoes": None}
        elif tag == "select" and a.get("name"):
            self._sel = {"valor": None, "req": "required" in a, "opcoes": []}
            self._f()["campos"][a["name"]] = self._sel
        elif tag == "option" and self._sel is not None:
            self._opt = [a.get("value"), "", "selected" in a]

    def handle_data(self, d):
        if self._opt is not None:
            self._opt[1] += d
        elif self._ta:
            self._f()["campos"][self._ta]["valor"] += d

    def handle_endtag(self, tag):
        if tag == "option" and self._opt is not None:
            self._fecha_opt()
        elif tag == "select" and self._sel is not None:
            if self._opt is not None:
                self._fecha_opt()
            if self._sel["valor"] is None:   # sem selected: o navegador manda a 1ª opção
                self._sel["valor"] = self._sel["opcoes"][0][0] if self._sel["opcoes"] else ""
            self._sel = None
        elif tag == "textarea":
            self._ta = None

    def _fecha_opt(self):
        v, t, sel = self._opt
        t = t.strip()
        v = t if v is None else v
        self._sel["opcoes"].append((v, t))
        if sel:
            self._sel["valor"] = v
        self._opt = None


def formularios(html):
    p = _Forms()
    p.feed(html or "")
    return p.forms


def _form_com(html, campo):
    return next((f for f in formularios(html) if campo in f["campos"]), None)


def _escolher(opcoes, desejado):
    """Valor da opção cujo VALOR ou TEXTO bate com `desejado` (sem acento/caixa)."""
    alvo = _norm(desejado)
    if not alvo:
        return None
    for v, t in opcoes or []:
        if v and (_norm(v) == alvo or _norm(t) == alvo):
            return v
    return None


# ------------------------------------------------------------------ documento
def variantes_documento(numero, emissor="", cpf=""):
    """Formas em que o documento pode ter sido cadastrado na Extecamp. A busca é por
    texto exato e o cadastro antigo foi digitado à mão: com/sem hífen do dígito,
    com/sem pontos, e em MG o prefixo 'MG'/'MG-' que muita gente usa como parte do
    número. CIN usa o número do CPF. Ordem = mais provável primeiro."""
    bruto = (numero or "").strip().upper()
    uf = (emissor or "").strip().upper()
    nucleo = re.sub(r"^(?:[A-Z]{2})[\s.-]*", "", bruto) if re.match(r"^[A-Z]{2}\b|^[A-Z]{2}[\s.-]*\d", bruto) else bruto
    alnum = re.sub(r"[^0-9X]", "", nucleo)
    out = [bruto, nucleo, alnum]
    if len(alnum) >= 2:
        corpo, dv = alnum[:-1], alnum[-1]
        out += [f"{corpo}-{dv}"]
        if len(corpo) == 8:   # SP: 12.345.678-9
            out += [f"{corpo[:2]}.{corpo[2:5]}.{corpo[5:]}-{dv}"]
    if len(alnum) == 8:       # MG: 12.345.678 (sem dígito)
        out += [f"{alnum[:2]}.{alnum[2:5]}.{alnum[5:]}"]
    if uf == "MG" or bruto.startswith("MG"):
        base = [alnum] + ([f"{alnum[:2]}.{alnum[2:5]}.{alnum[5:]}"] if len(alnum) == 8 else [])
        out += [f"MG{b}" for b in base] + [f"MG-{b}" for b in base] + [f"MG {b}" for b in base]
    c = _dig(cpf)
    if len(c) == 11:
        out += [c, f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"]
    return [v for v in dict.fromkeys(o.strip() for o in out) if len(_dig(v)) >= 5]


# ------------------------------------------------------------------ mapeamento
_DOC_TIPO = {"rg": "RG", "cin": "CIN", "rne": "RNE", "rnm": "RNM", "passaporte": "PP", "pp": "PP"}
_SEXO = {"feminino": "F", "f": "F", "masculino": "M", "m": "M"}


def _nascimento(s):
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s) or None
    if m:
        return m.group(3), m.group(2), m.group(1)
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _celular(tel):
    d = _dig(tel)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) not in (10, 11):
        return None, None
    n = d[2:]
    return d[:2], (f"{n[:5]}-{n[5:]}" if len(n) == 9 else f"{n[:4]}-{n[4:]}")


def _parcelas(forma):
    """'19 PARCELA(S) DE R$ 866,09 …' -> 19. Pela QUANTIDADE e não pelo valor: a
    LP mostra o preço com desconto da campanha e a Extecamp, o de tabela."""
    m = re.match(r"\s*(\d{1,2})\s*PARCELA", (forma or "").upper())
    return int(m.group(1)) if m else None


def valores_desejados(ficha, lead):
    """Ficha (etapa 2) + lead (etapa 1) -> {campo_extecamp: valor}. Puro.
    Campos de select vêm como TEXTO desejado; `preencher` resolve contra as opções."""
    f, ld = ficha or {}, lead or {}
    v = {}
    v["pf_nome"] = (ld.get("nome") or "").strip()
    v["pf_email"] = (ld.get("email") or "").strip()
    v["pf_email_alternativo"] = f.get("email_alt", "")
    ddd, cel = _celular(ld.get("telefone"))
    v["DDD_CEL"], v["celular"] = ddd or "", cel or ""
    if f.get("telefone_residencial"):
        v["DDD_TELRES"] = _dig(f.get("ddd_residencial"))
        r = _dig(f["telefone_residencial"])
        v["TELEFONE"] = f"{r[:-4]}-{r[-4:]}" if len(r) >= 8 else r
    v["pf_sexo"] = _SEXO.get(_norm(f.get("sexo")), "")
    v["genero"] = f.get("genero", "")
    tipo = _DOC_TIPO.get(_norm(f.get("doc_tipo")), "")
    cpf = _dig(f.get("cpf"))
    v["do_ap_reg"] = tipo
    # CIN: a própria Extecamp copia o CPF formatado para o número do documento
    v["pf_rg"] = (f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if tipo == "CIN" and len(cpf) == 11
                  else (f.get("doc_numero") or "").strip())
    v["pf_emissor"] = (f.get("doc_emissor") or "").strip().upper()
    if len(cpf) == 11:
        v["CPF1"], v["CPF2"], v["CPF3"], v["CPF4"] = cpf[:3], cpf[3:6], cpf[6:9], cpf[9:]
    dma = _nascimento(f.get("nascimento"))
    if dma:
        v["DIA"], v["MES"], v["ANO"] = dma
    v["pf_estcivil"] = f.get("estado_civil", "")
    v["pf_nacion"] = f.get("pais_nascimento") or "Brasil"
    v["pf_naturae"] = (f.get("estado_nascimento") or "").strip().upper()
    v["pf_naturac"] = (f.get("cidade_nascimento") or "").strip()
    defi = _norm(f.get("deficiencia") or f.get("necessidades"))
    v["pf_deficiencia"] = "SIM" if defi in ("sim", "s") or (defi and defi not in ("nao", "n")) else "NÃO"
    if v["pf_deficiencia"] == "SIM":
        v["pf_def_qual"] = f.get("deficiencia") if defi not in ("sim", "s") else ""
        v["pf_deficiencia_aj"] = f.get("ajuda", "")
    cep = _dig(f.get("cep"))
    v["pf_cep"] = f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else ""
    v["pf_ender"] = (f.get("rua") or "").strip()
    num = _dig(f.get("numero"))
    v["pf_numero"] = num or "0"     # campo type=number: "S/N" não passa
    comp = (f.get("complemento") or "").strip()
    if not num and (f.get("numero") or "").strip():
        comp = " ".join(x for x in (f["numero"].strip(), comp) if x)
    v["pf_complemento"] = comp
    v["pf_bai"] = (f.get("bairro") or "").strip()
    uf = (f.get("estado") or "").strip().upper()
    cidade = (f.get("cidade") or "").strip()
    v["pf_est"], v["pf_cid"] = uf, cidade
    v["pais_endereco"] = f.get("pais") or "Brasil"
    # Acadêmico e profissional — regra da coordenação (ver NAO_INFORMADO)
    v["escolaridade"] = f.get("escolaridade") or "Superior"
    v["instituicao"] = f.get("instituicao") or NAO_INFORMADO
    v["cidade_instituicao"] = f.get("instituicao_cidade") or cidade or NAO_INFORMADO
    v["curso_aluno"] = f.get("curso_concluido") or NAO_INFORMADO
    v["ano_conclusao"] = _dig(f.get("ano_conclusao")) or "00"
    v["situacao_aluno"] = f.get("situacao") or "Empregado"
    v["pj_razsoc"] = f.get("empresa") or NAO_INFORMADO
    v["pj_est"], v["pj_cid"] = (f.get("empresa_estado") or uf), cidade
    v["cargo_aluno"] = f.get("cargo") or NAO_INFORMADO
    v["departamento"] = f.get("departamento") or NAO_INFORMADO
    v["tempo_experiencia"] = _dig(f.get("experiencia_anos")) or "00"
    v["nome_superior"] = f.get("superior") or NAO_INFORMADO
    # Financeiro: em nome do aluno, sem pagamento misto
    v["forma_de_pagamento"] = _parcelas(f.get("forma_pagamento"))
    v["misto"] = "NÃO"
    # Extras
    v["pf_fontinf"] = "RS"            # Redes sociais
    v["pf_tipo_contato"] = "Celular"
    return v


# Selects cujo valor desejado é texto a casar com as opções.
_SELECTS_TEXTO = ("genero", "pf_estcivil", "pf_nacion", "pais_endereco", "escolaridade",
                  "situacao_aluno", "pf_def_qual", "pf_deficiencia", "misto", "pf_emissor",
                  "pf_naturae", "pf_est", "pj_est", "do_ap_reg", "pf_sexo", "DIA", "MES", "ANO",
                  "DDD_CEL", "DDD_TELRES", "pf_fontinf", "pf_tipo_contato")
# Campos da escolha DO CURSO (não do cadastro do aluno): mesmo o ex-aluno com a ficha
# pré-preenchida recebe o nosso valor.
_SEMPRE = ("forma_de_pagamento", "misto", "pf_fontinf", "pf_tipo_contato")


def preencher(form, desejado, cidades=None, ex_aluno=False):
    """Sobrepõe `desejado` aos campos do form. Devolve (payload, pendencias).
    Ex-aluno: o que a Extecamp já trouxe preenchido FICA (é o cadastro oficial
    dele); só completamos o que veio vazio + as escolhas do curso (_SEMPRE)."""
    campos = form["campos"]
    payload = {k: (c["valor"] or "") for k, c in campos.items()}
    pend = []
    cidades = cidades or {}
    for nome, valor in desejado.items():
        if nome not in campos:
            continue
        c = campos[nome]
        if ex_aluno and payload.get(nome) and nome not in _SEMPRE:
            continue
        if c.get("readonly") and payload.get(nome):
            continue
        if valor in (None, ""):
            continue
        if nome == "forma_de_pagamento":
            op = next((ov for ov, _ in c["opcoes"] or [] if re.match(rf"0?{valor}-", ov or "")), None)
            if not op:
                pend.append(f"forma_de_pagamento: {valor} parcela(s) não existe no formulário")
                continue
            payload[nome] = op
        elif nome in ("pf_cid", "pj_cid"):
            lista = cidades.get(nome) or []
            payload[nome] = _escolher(lista, valor) or (valor if not lista else "")
            if not payload[nome]:
                pend.append(f"{nome}: cidade não encontrada na lista da UF")
        elif c["opcoes"] is not None and nome in _SELECTS_TEXTO:
            op = _escolher(c["opcoes"], valor)
            if op is None:
                pend.append(f"{nome}: opção não reconhecida")
                continue
            payload[nome] = op
        else:
            payload[nome] = str(valor)
    for nome, c in campos.items():
        if c.get("req") and not payload.get(nome) and not any(p.startswith(nome + ":") for p in pend):
            pend.append(f"{nome}: obrigatório e sem dado na ficha")
    payload.setdefault("cadastrar", "cadastrar")
    return payload, pend


# ------------------------------------------------------------------ fluxo HTTP
class _Http:
    """requests.Session com timeout e User-Agent; injetável nos testes."""

    def __init__(self, sessao=None):
        if sessao is None:
            import requests
            sessao = requests.Session()
            sessao.headers["User-Agent"] = _UA
        self.s = sessao

    def get(self, url, **kw):
        return self.s.get(url, timeout=30, **kw)

    def post(self, url, data, **kw):
        return self.s.post(url, data=data, timeout=30, **kw)


def _texto(r):
    r.encoding = r.encoding if (r.encoding or "").lower() not in ("iso-8859-1", "") else "utf-8"
    return r.text.lstrip("﻿")


def _cidades(http, uf, script="seleciona_cidade.php"):
    if not uf:
        return []
    f = _form_com("<form>" + _texto(http.get(urljoin(BASE, f"{script}?id_estado={uf}"))) + "</form>", "pf_cid")
    if not f:
        return []
    return [(v, t) for v, t in f["campos"]["pf_cid"]["opcoes"] if v]


def _completar_cep(http, desejado):
    """Ficha com CEP mas sem rua/bairro/cidade: o navegador da Extecamp completaria
    pelo ViaCEP no blur do campo — fazemos o mesmo."""
    if all(desejado.get(k) for k in ("pf_ender", "pf_bai", "pf_cid", "pf_est")) or not desejado.get("pf_cep"):
        return
    try:
        j = http.get(f"https://viacep.com.br/ws/{_dig(desejado['pf_cep'])}/json/").json()
    except Exception:
        return
    if j.get("erro"):
        return
    for k, jk in (("pf_ender", "logradouro"), ("pf_bai", "bairro"), ("pf_cid", "localidade"), ("pf_est", "uf")):
        if not desejado.get(k) and j.get(jk):
            desejado[k] = j[jk]
    if not desejado.get("pj_cid"):
        desejado["pj_cid"] = desejado["pf_cid"]
    if not desejado.get("pj_est"):
        desejado["pj_est"] = desejado["pf_est"]
    if desejado.get("cidade_instituicao") == NAO_INFORMADO:
        desejado["cidade_instituicao"] = desejado["pf_cid"]


def _link_ok(resp):
    for alvo in (resp.headers.get("Location") or "", getattr(resp, "url", "") or "", _texto(resp)):
        m = _OK_LINK.search(alvo)
        if m:
            return urljoin(BASE, m.group(1).replace("&amp;", "&"))
    return None


def _opcao_num(campo, x):
    """Valor da opção numérica igual a x ("05" casa com value="5"); sem opções, x."""
    return next((v for v, _ in campo.get("opcoes") or [] if v.isdigit() and int(v) == int(x)), x)


_JS_LOCATION = re.compile(r"""location\.href\s*=\s*['"]([^'"]+)['"]""")


def _data_nascimento(html, dma):
    """Ex-aluno: a Extecamp pede a data de nascimento antes de abrir a ficha
    (form `procura_aluno.php?id=2`, selects dia_t/mes_t/ano_t com valores sem zero à
    esquerda, vista em 06/11/2026). Tela desconhecida -> None (pendência, nunca chute)."""
    for f in formularios(html):
        c = f["campos"]
        dia = next((k for k in c if re.search(r"dia", k, re.I)), None)
        mes = next((k for k in c if re.search(r"mes", k, re.I)), None)
        ano = next((k for k in c if re.search(r"ano", k, re.I)), None)
        payload = {k: x["valor"] for k, x in c.items()}
        if dia and mes and ano:
            payload.update({k: _opcao_num(c[k], x) for k, x in ((dia, dma[0]), (mes, dma[1]), (ano, dma[2]))})
            return f["action"], payload
        unico = next((k for k in c if re.search(r"nasc|data", k, re.I)), None)
        if unico:
            payload[unico] = f"{dma[0]}/{dma[1]}/{dma[2]}"
            return f["action"], payload
    return None


_CONCLUIDA = re.compile(r"inscri[çc][ãa]o foi conclu[íi]da", re.I)


def _ja_inscrito(http, query):
    """Página de confirmação do aluno nesta turma (só leitura). Os ids do redirect em JS
    para a ficha do ex-aluno abrem `ok-inscricao_taxa.php`; se ela diz "inscrição foi
    concluída", a pessoa já está inscrita e este é o link de continuidade."""
    if not query:
        return None
    url = urljoin(BASE, "ok-inscricao_taxa.php?" + query)
    return url if _CONCLUIDA.search(_texto(http.get(url))) else None


def _localizar(http, url_curso, ficha):
    """Passos 1-3: entrada do curso e busca do cadastro pelas variantes do documento.
    Retorno: {erro} | {ex_aluno, ficha_html (None = primeiro cadastro), novo, query, n}."""
    r = http.get(url_curso)
    f0 = _form_com(_texto(r), "sigla")
    if not f0:
        return {"erro": "página do curso sem o formulário de entrada (link fechado?)"}
    pag = _texto(http.post(urljoin(url_curso, f0["action"]), {k: c["valor"] for k, c in f0["campos"].items()}))
    busca = _form_com(pag, "rg")
    novo = next((f for f in formularios(pag) if "inscricao.php" in f["action"]), None)
    if not busca or not novo:
        return {"erro": "tela de busca de cadastro mudou (faltam os forms rg/primeiro cadastro)"}
    variantes = variantes_documento((ficha or {}).get("doc_numero"), (ficha or {}).get("doc_emissor"),
                                    (ficha or {}).get("cpf"))
    out = {"ex_aluno": False, "ficha_html": None, "novo": novo, "query": None, "n": len(variantes)}
    for doc in variantes:
        base = {k: c["valor"] for k, c in busca["campos"].items()}
        resp = _texto(http.post(urljoin(BASE, busca["action"]), {**base, "rg": doc}))
        if _NAO_ACHOU.search(resp):
            continue
        out["ex_aluno"] = True
        if _form_com(resp, "pf_nome"):          # foi direto para a ficha
            out["ficha_html"] = resp
            return out
        dma = _nascimento((ficha or {}).get("nascimento"))
        passo = _data_nascimento(resp, dma) if dma else None
        if not passo:
            campos = sorted({k for f in formularios(resp) for k in f["campos"]})
            return {**out, "erro": "ex-aluno: tela de data de nascimento não reconhecida "
                                   f"(campos: {', '.join(campos) or 'nenhum'})"}
        acao, dados = passo
        html = _texto(http.post(urljoin(BASE, acao), dados))
        m = _JS_LOCATION.search(html)
        if m and not _form_com(html, "pf_nome"):
            out["query"] = m.group(1).replace("&amp;", "&").split("?", 1)[-1]
            html = _texto(http.get(urljoin(BASE, m.group(1).replace("&amp;", "&"))))
        if not _form_com(html, "pf_nome"):
            return {**out, "erro": "ex-aluno: data de nascimento não aceita pela Extecamp"}
        out["ficha_html"] = html
        return out
    return out


def inscrever(ficha, lead, url_curso, valer=False, sessao=None):
    """Executa a inscrição. Retorno:
    {ok, caminho: 'ex-aluno'|'novo', link?, pendencias: [...], enviado: bool,
     documento_testado: n, ja_inscrito?}. `ok` só é True com o link ok-inscricao em mãos
    (valer, ou pessoa já inscrita nesta turma) ou, no dry-run, com a ficha pronta para enviar."""
    http = _Http(sessao)
    out = {"ok": False, "caminho": None, "link": None, "pendencias": [], "enviado": False}
    desejado = valores_desejados(ficha, lead)

    loc = _localizar(http, url_curso, ficha)
    out["documento_testado"] = loc.get("n")
    ex_aluno = loc.get("ex_aluno", False)
    if ex_aluno:
        out["caminho"] = "ex-aluno"
    if loc.get("erro"):
        out["pendencias"].append(loc["erro"])
        return out
    link = _ja_inscrito(http, loc["query"])
    if link:                                    # nunca inscrever duas vezes
        out.update(ok=True, link=link, ja_inscrito=True)
        return out
    ficha_html = loc["ficha_html"]
    if ficha_html is None:                      # 3b. primeiro cadastro
        ficha_html = _texto(http.post(urljoin(BASE, loc["novo"]["action"]), {}))
    out["caminho"] = "ex-aluno" if ex_aluno else "novo"
    form = _form_com(ficha_html, "pf_nome")
    if not form:
        out["pendencias"].append("ficha de inscrição não carregou")
        return out

    # 4. preenche e (se valer) envia
    _completar_cep(http, desejado)
    uf_res = desejado.get("pf_est") or form["campos"].get("pf_est", {}).get("valor")
    cidades = {"pf_cid": _cidades(http, uf_res)}
    cidades["pj_cid"] = cidades["pf_cid"] if desejado.get("pj_est") == uf_res else _cidades(http, desejado.get("pj_est"))
    payload, pend = preencher(form, desejado, cidades, ex_aluno=ex_aluno)
    out["pendencias"] += pend
    out["campos_preenchidos"] = sorted(k for k, v in payload.items() if v)
    if pend:
        return out
    if not valer:
        out["ok"] = True
        return out
    resp = http.post(urljoin(BASE, form["action"]), payload, allow_redirects=False)
    out["enviado"] = True
    link = _link_ok(resp)
    if not link and resp.status_code in (301, 302, 303) and resp.headers.get("Location"):
        link = _link_ok(http.get(urljoin(BASE, resp.headers["Location"])))
    if not link:
        # 06/11/2026: a Extecamp devolveu "ATENÇÃO: campos em destaque" e mesmo assim
        # inscreveu. A palavra final é a página de confirmação, buscada de novo.
        loc2 = _localizar(http, url_curso, ficha)
        link = None if loc2.get("erro") else _ja_inscrito(http, loc2.get("query"))
    if not link:
        txt = re.sub(r"<[^>]+>", " ", _texto(resp))
        m = re.search(r"ATEN[ÇC][ÃA]O[^.]{0,160}", txt)
        out["pendencias"].append("Extecamp não devolveu o link de continuidade"
                                 + (f": {m.group(0).strip()}" if m else ""))
        out["resposta_html"] = _texto(resp)   # quem chama guarda: mostra os campos em destaque
        return out
    out.update(ok=True, link=link)
    return out
