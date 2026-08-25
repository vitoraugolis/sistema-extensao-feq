#!/usr/bin/env python3
"""
Atena · Coleta — parsers do ADM Cursos da Extecamp (spec 20).

Só LEITURA e só funções PURAS: recebem HTML, devolvem dados. Nada aqui abre
conexão, tem credencial ou escreve no banco — o transporte mora no coletor que
roda no navegador do operador (ver spec 20 §2) e a escrita, em sincronizacao.py.
Essa separação é o que torna o pedaço frágil (HTML de terceiro, que muda sem
avisar) testável contra fixtures reais.

O HTML do ADM Cursos é gerado por ASP clássico e é MALFORMADO de propósito
(<tr>/<td> sem fechar, <form> dentro de <table>). Por isso o parser é um
HTMLParser tolerante — regex sobre essas tabelas quebraria no primeiro
oferecimento com um espaço a mais.

Encoding: as páginas declaram iso-8859-1. `decodificar` honra isso; ler como
UTF-8 corromperia todo nome com acento, que é a maioria deles.
"""
import re
import html as _html
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs

# Cor da linha = estado da TAXA DE INSCRIÇÃO (regra da Extecamp, informada pela
# coordenação). É a única fonte desse estado: não existe coluna de texto com ele.
COR_TAXA = {
    "#a9a9a9": "termo_pendente",   # cinza    — não aceitou o termo da taxa
    "#ffff00": "taxa_a_pagar",     # amarelo  — aceitou o termo, não pagou
    "#f78181": "taxa_vencida",     # vermelho — aceitou, venceu: precisa de novo boleto
    "#58fa58": "taxa_paga",        # verde    — pagou; pode enviar documentos
}

# O que fazer com cada combinação (taxa × documentação). É o que transforma a
# raspagem em fila de trabalho — sem isto o operador olharia uma tabela colorida.
_ACOES = {
    ("termo_pendente", None): ("Não aceitou o termo da taxa de inscrição",
                               "Cobrar o aceite do termo — sem ele a inscrição não anda."),
    ("taxa_a_pagar", None): ("Aceitou o termo; taxa de inscrição ainda não paga",
                             "Lembrar do boleto da taxa antes de vencer."),
    ("taxa_vencida", None): ("Taxa de inscrição venceu sem pagamento",
                             "Pedir à secretaria um novo boleto/link da taxa."),
    ("taxa_paga", "PENDENTE"): ("Taxa paga; documentos ainda não enviados",
                                "Orientar o envio dos documentos no portal do aluno."),
    ("taxa_paga", "EM ANÁLISE"): ("Documentos enviados, em análise pela secretaria",
                                  "Nada com o lead: aguardar a análise da secretaria."),
    ("taxa_paga", "APROVADO"): ("Documentação aprovada",
                                "Aguardar a secretaria enviar o 1º boleto do curso."),
}


def decodificar(bruto):
    """bytes -> str. Página declara iso-8859-1; ler como UTF-8 quebraria os acentos."""
    if isinstance(bruto, str):
        return bruto
    m = re.search(rb"charset=([\w-]+)", bruto[:2000], re.I)
    cs = (m.group(1).decode("ascii", "ignore") if m else "iso-8859-1")
    try:
        return bruto.decode(cs, "replace")
    except LookupError:
        return bruto.decode("iso-8859-1", "replace")


class _Linhas(HTMLParser):
    """Todas as <tr> da página como células de texto + links + imagens.

    Fecha <tr>/<td> implicitamente na abertura da próxima: o ASP não fecha as
    tags e um parser estrito devolveria a página inteira numa célula só.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.linhas, self._linha, self._cel = [], None, None

    def _fecha_cel(self):
        if self._cel is not None and self._linha is not None:
            self._linha["celulas"].append(self._cel)
        self._cel = None

    def _fecha_linha(self):
        self._fecha_cel()
        if self._linha is not None and self._linha["celulas"]:
            self.linhas.append(self._linha)
        self._linha = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "tr":
            self._fecha_linha()
            self._linha = {"attrs": a, "celulas": []}
        elif tag in ("td", "th"):
            self._fecha_cel()
            self._cel = {"texto": "", "links": [], "imgs": []}
        elif self._cel is not None:
            if tag == "a" and a.get("href"):
                self._cel["links"].append(a["href"])
            elif tag == "img":
                self._cel["imgs"].append(a)
            elif tag == "br":
                self._cel["texto"] += "\n"

    def handle_endtag(self, tag):
        if tag == "tr":
            self._fecha_linha()
        elif tag in ("td", "th"):
            self._fecha_cel()
        elif tag == "table":
            self._fecha_linha()

    def handle_data(self, dado):
        if self._cel is not None:
            self._cel["texto"] += dado

    def close(self):
        super().close()
        self._fecha_linha()


def _linhas(pagina):
    p = _Linhas()
    p.feed(pagina)
    p.close()
    return p.linhas


def _txt(s):
    """Normaliza espaço/NBSP — o ASP indenta o conteúdo das células com tabs."""
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def chaves(href):
    """sigla/of/id1..id4 do link, SEM decodificar: são tokens opacos do ASP e
    reencodá-los muda o valor. Viajam como vieram e voltam iguais na próxima URL."""
    q = parse_qs(urlsplit(href or "").query, keep_blank_values=True)
    bruto = dict(re.findall(r"[?&]([^=&]+)=([^&]*)", href or ""))
    return {k: bruto.get(k, (q.get(k) or [""])[0]) for k in
            ("sigla", "of", "id1", "id2", "id3", "id4") if k in bruto or k in q}


def _cabecalho(pagina):
    """'FEQ-1000 - NOME - Oferecimento: 018' do <h1>."""
    m = re.search(r"([A-Z]{2,4}-\d{3,4})\s*-\s*(.*?)\s*-\s*Oferecimento:\s*(\d+)",
                  _txt(re.sub(r"<[^>]+>", " ", pagina)), re.S)
    if not m:
        return {"curso": None, "nome": None, "turma": None}
    return {"curso": m.group(1), "nome": _txt(m.group(2)), "turma": m.group(3)}


def _totais(linhas):
    """'Total de inscritos: 14' etc. O rótulo de cancelados vem com typo no
    sistema ('Tota de cancelados') — casar por prefixo, não por igualdade."""
    achados = {}
    for ln in linhas:
        cels = [_txt(c["texto"]) for c in ln["celulas"]]
        rot = " ".join(cels).lower()
        num = next((c for c in reversed(cels) if c.isdigit()), None)
        if num is None:
            continue
        # RADICAL, não a frase inteira: o mesmo total aparece com rótulos diferentes
        # em cada aba ("Total de cancelados" na de inscritos, "Total de alunos
        # cancelados" na de cancelados) e um deles ainda vem com typo do sistema
        # ("Tota de cancelados"). Casar por frase perdia o número em silêncio.
        for chave, marca in (("inscritos", "inscritos"), ("matriculados", "matriculados"),
                             ("cancelados", "cancelad"), ("desistentes", "desistente"),
                             ("matriculados_lista", "total de alunos:")):
            if marca in rot and chave not in achados:
                achados[chave] = int(num)
    if "matriculados" not in achados and "matriculados_lista" in achados:
        achados["matriculados"] = achados["matriculados_lista"]
    achados.pop("matriculados_lista", None)
    return achados


def _paginas(pagina):
    m = re.search(r"de\s+(\d+)\s+p[áa]ginas", pagina, re.I)
    return max(1, int(m.group(1))) if m else 1


def situacao(taxa, doc):
    """(rótulo legível, ação sugerida) para a combinação taxa × documentação."""
    return _ACOES.get((taxa, doc)) or _ACOES.get((taxa, None)) or ("Situação desconhecida", "")


def _nome_e_numero(texto):
    """'5 - ALCIDES ...' -> (5, 'ALCIDES ...'). Em cancelados o número e o nome
    vêm quebrados em linhas diferentes, então normaliza antes de casar."""
    t = _txt(texto)
    m = re.match(r"^(\d+)\s*-\s*(.+)$", t)
    return (int(m.group(1)), _txt(m.group(2))) if m else (None, t)


def parse_inscritos(pagina):
    """Aba 'Inscritos' de um oferecimento — o coração da coleta."""
    pagina = decodificar(pagina)
    linhas = _linhas(pagina)
    out = []
    for ln in linhas:
        cor = (ln["attrs"].get("bgcolor") or "").strip().lower()
        taxa = COR_TAXA.get(cor)
        cels = ln["celulas"]
        if not taxa or not cels or not any("aluno.asp" in h for h in cels[0]["links"]):
            continue
        bruto = cels[0]["texto"]
        doc = None
        md = re.search(r"Status\s+documenta[çc][ãa]o:\s*\(([^)]*)\)", _txt(bruto), re.I)
        if md:
            doc = _txt(md.group(1)).upper()
            bruto = re.sub(r"Status\s+documenta[çc][ãa]o:.*", "", bruto, flags=re.I | re.S)
        numero, nome = _nome_e_numero(bruto)
        doc_ficha = _txt(cels[2]["texto"]) if len(cels) > 2 else ""
        rotulo, acao = situacao(taxa, doc)
        href = next(h for h in cels[0]["links"] if "aluno.asp" in h)
        out.append({
            "numero": numero, "nome": nome,
            "taxa": taxa, "cor": cor, "documentacao": doc,
            "situacao": rotulo, "acao": acao,
            "inscrito_em": _txt(cels[1]["texto"]) if len(cels) > 1 else "",
            "doc_ficha": doc_ficha,
            "documento_entregue": doc_ficha.upper().startswith("SIM"),
            "contato": _txt(cels[3]["texto"]) if len(cels) > 3 else "",
            "forma_taxa": ("cartao" if any("cartao" in (i.get("src") or "")
                                           for i in (cels[4]["imgs"] if len(cels) > 4 else []))
                           else "boleto" if len(cels) > 4 and cels[4]["imgs"] else None),
            "chaves": chaves(href),
        })
    vagas = {}
    for rot, chave in (("mínimo", "min"), ("máximo", "max")):
        mv = re.search(rf"N[ºo°]\s*{rot}\s+de\s+alunos:\s*(\d+)", pagina, re.I)
        if mv:
            vagas[chave] = int(mv.group(1))
    return {**_cabecalho(pagina), "aba": "inscritos", "itens": out, "vagas": vagas,
            "totais": _totais(linhas), "paginas": _paginas(pagina)}


def parse_matriculados(pagina):
    """Aba 'Matriculados'. Sem número de inscrição e sem cor: o nome é a chave."""
    pagina = decodificar(pagina)
    linhas = _linhas(pagina)
    out = []
    for ln in linhas:
        cels = ln["celulas"]
        if not cels or not any("prop_aluno_ins.asp" in h for h in cels[0]["links"]):
            continue
        href = next(h for h in cels[0]["links"] if "prop_aluno_ins.asp" in h)
        _, nome = _nome_e_numero(cels[0]["texto"])
        out.append({
            "nome": nome,
            "status": _txt(cels[1]["texto"]) if len(cels) > 1 else "",
            "sise": _txt(cels[2]["texto"]) if len(cels) > 2 else "",
            "matriculado_em": _txt(cels[3]["texto"]) if len(cels) > 3 else "",
            "chaves": chaves(href),
        })
    return {**_cabecalho(pagina), "aba": "matriculados", "itens": out,
            "totais": _totais(linhas), "paginas": _paginas(pagina)}


def parse_cancelados(pagina):
    """Aba 'Cancelados' — quem desistiu em algum ponto do processo."""
    pagina = decodificar(pagina)
    linhas = _linhas(pagina)
    out = []
    for ln in linhas:
        cels = ln["celulas"]
        if not cels or not any("aluno_canc.asp" in h for h in cels[0]["links"]):
            continue
        href = next(h for h in cels[0]["links"] if "aluno_canc.asp" in h)
        numero, nome = _nome_e_numero(cels[0]["texto"])
        out.append({
            "numero": numero, "nome": nome,
            "cancelado_em": _txt(cels[1]["texto"]) if len(cels) > 1 else "",
            "doc_ficha": _txt(cels[2]["texto"]) if len(cels) > 2 else "",
            "contato": _txt(cels[3]["texto"]) if len(cels) > 3 else "",
            "chaves": chaves(href),
        })
    return {**_cabecalho(pagina), "aba": "cancelados", "itens": out,
            "totais": _totais(linhas), "paginas": _paginas(pagina)}


def parse_lista_cursos(pagina):
    """Lista de oferecimentos (em inscrição ou em andamento) -> alvos da coleta.

    É daqui que saem os tokens `sigla`/`of` opacos: sem esta página não há como
    montar as URLs das abas, porque eles não derivam da sigla legível.
    """
    pagina = decodificar(pagina)
    out, vistos = [], set()
    for ln in _linhas(pagina):
        cels = ln["celulas"]
        hrefs = [h for c in cels for h in c["links"] if "curso.asp" in h]
        if not hrefs:
            continue
        k = chaves(hrefs[0])
        if not (k.get("sigla") and k.get("of")):
            continue
        textos = [_txt(c["texto"]) for c in cels]
        sigla = next((t for t in textos if re.fullmatch(r"[A-Z]{2,4}-\d{3,4}", t)), None)
        if not sigla:
            continue
        datas = [t for t in textos if re.fullmatch(r"\d{2}/\d{2}/\d{4}", t)]
        turma = next((t for t in reversed(textos) if re.fullmatch(r"\d{3}", t)), None)
        ident = (k["sigla"], k["of"])
        if ident in vistos:
            continue
        vistos.add(ident)
        out.append({
            "curso": sigla,
            "nome": next((t for t in textos if len(t) > 8 and not re.match(r"^[\d/]+$", t)
                          and t != sigla), ""),
            "turma": turma,
            "insc_inicio": datas[0] if len(datas) > 0 else None,
            "insc_fim": datas[1] if len(datas) > 1 else None,
            "ofer_inicio": datas[2] if len(datas) > 2 else None,
            "ofer_fim": datas[3] if len(datas) > 3 else None,
            "chaves": k,
        })
    return out


# ---------------------------------------------------------------- roteiro da coleta
BASE = "https://www.extecamp.unicamp.br/admcursos/"
# Portas de entrada: a lista de unidades de cada modalidade. O usuário entra por
# elas no menu "Cursos > Em inscrição / Em andamento".
ENTRADAS = {"inscricao": BASE + "cursos-em-inscricao.asp",
            "andamento": BASE + "admcursosand_ext_pre.asp"}
ABAS = ("inscritos", "matriculados", "cancelados")


def classificar(url):
    """Que página é esta? O backend decide o próximo passo a partir disto —
    o coletor no navegador não sabe nada sobre a estrutura do ADM Cursos."""
    caminho = urlsplit(url or "").path.rsplit("/", 1)[-1].lower()
    if caminho in ("cursos-em-inscricao.asp", "admcursosand_ext_pre.asp"):
        return "unidades"
    if caminho.startswith("admcursosand_ext") or caminho.startswith("admcursosand_sigla"):
        return "lista"
    for aba in ABAS:
        if caminho == f"{aba}.asp":
            return aba
    return "desconhecido"


def links_unidades(pagina):
    """Links de unidade (admcursosand_ext*.asp?id=...) da página de entrada.
    A FEQ é uma linha dessa tabela; o id da unidade também é token opaco."""
    pagina = decodificar(pagina)
    achados, vistos = [], set()
    for m in re.finditer(r'href="(admcursosand_ext1?\.asp\?id=[^"]+)"', pagina, re.I):
        u = _html.unescape(m.group(1))
        if u not in vistos:
            vistos.add(u)
            achados.append(BASE + u)
    return achados


def urls_das_abas(chave, pagina=None):
    """URLs das 3 abas de um oferecimento, a partir dos tokens dele.
    `pagina`: número da página da listagem (as abas paginam de 20 em 20)."""
    sig, of = chave.get("sigla") or chave.get("id1"), chave.get("of") or chave.get("id2")
    if not (sig and of):
        return {}
    sufixo = f"&pagina_form={pagina}" if pagina and pagina > 1 else ""
    return {aba: f"{BASE}{aba}.asp?sigla={sig}&of={of}{sufixo}" for aba in ABAS}
