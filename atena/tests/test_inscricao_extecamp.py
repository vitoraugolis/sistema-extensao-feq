#!/usr/bin/env python3
"""
Robô de inscrição na Extecamp (spec 21) contra uma Extecamp FALSA que imita os
formulários reais (lidos em 06/11/2026 em ins.extecamp.unicamp.br): página do curso
que se auto-submete, busca por documento ("não localizado"), ficha com selects,
cidades por UF via seleciona_cidade.php e o redirect ok-inscricao_taxa.php.
Nada aqui toca a rede.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.coleta import inscricao as I

ok = 0


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  ok:", msg)


URL = "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/009"
LINK = "https://ins.extecamp.unicamp.br/inscricao/ok-inscricao_taxa.php?id1=%F2%16&id2=%81g&id3=%F7"

ENTRADA = """<form method="POST" action="https://ins.extecamp.unicamp.br/inscricao/procura_aluno.php" id="payment-form">
<input type="hidden" name="sigla" value="FEQ-0480"><input type="hidden" name="of" value="009">
<input type="hidden" name="primeira" value="PRIMEIRA"><input type="hidden" name="cont" value="0"></form>"""
BUSCA = """<form name="form1" method="post" action="procura_aluno.php?id=1">
<input type="text" name="rg" value=""><input type="hidden" name="sigla" value="FEQ-0480">
<input type="hidden" name="primeira" value="SEGUNDA"><input type="hidden" name="cont" value="1">
<button type="submit">Localizar meu cadastro</button></form>
<form name="form2" method="post" action="inscricao.php?id1=&id2=%81g"><button type="submit">Fazer primeiro cadastro</button></form>"""
NAO_ACHOU = BUSCA + "<p>Atenção: Documento não localizado ou incorreto.</p>"
NASC = """<form method="post" action="procura_aluno.php?id=2"><input type="hidden" name="rg" value="X">
<select name="DIA"><option value=""></option><option value="17">17</option></select>
<select name="MES"><option value=""></option><option value="05">05</option></select>
<select name="ANO"><option value=""></option><option value="1990">1990</option></select></form>"""


def ficha_html(pref=None):
    p = pref or {}
    v = lambda k: p.get(k, "")
    return f"""<form action="inscricao.php?id=1&id2=%81g" method="post" name="form1">
<input type="text" name="pf_nome" value="{v('pf_nome')}" required>
<input type="mail" name="pf_email" value="{v('pf_email')}" required>
<select name="DDD_CEL"><option value="">DDD</option><option value="19">19</option></select>
<input type="tel" name="celular" value="">
<select name="pf_sexo" required><option value="">Sexo</option><option value="F">Feminino</option><option value="M">Masculino</option></select>
<select name="genero" required><option value="">Selecione</option><option value="Mulher cisgênero">Mulher cisgênero</option><option value="Prefere não responder">Prefere não responder</option></select>
<select name="do_ap_reg" required><option value="">Tipo</option><option value="RG">RG</option><option value="CIN">CIN</option><option value="PP">PASSAPORTE</option></select>
<input type="text" name="pf_rg" value="{v('pf_rg')}">
<select name="pf_emissor" required><option value="">UF</option><option value="SP">SP</option><option value="MG">MG</option></select>
<input name="CPF1" required><input name="CPF2" required><input name="CPF3" required><input name="CPF4" required>
<select name="DIA"><option value=""></option><option value="17">17</option></select>
<select name="MES"><option value=""></option><option value="05">05</option></select>
<select name="ANO"><option value=""></option><option value="1990">1990</option></select>
<select name="pf_estcivil" required><option value="">EC</option><option value="União estável">União estável</option></select>
<select name="pf_nacion" required><option value="">País</option><option value="Brasil">Brasil</option></select>
<select name="pf_naturae" required><option value="">UF</option><option value="MG">MG</option></select>
<input type="text" name="pf_naturac" required>
<select name="pf_deficiencia" required><option value="">?</option><option value="SIM">SIM</option><option value="NÃO">NÃO</option></select>
<input type="text" name="pf_cep" required onblur="pesquisacep(this.value);">
<input type="text" name="pf_ender" required><input type="number" name="pf_numero" required>
<input type="text" name="pf_complemento"><input type="text" name="pf_bai" required>
<select name="pf_est" required onChange="MandaID(this.value)"><option value="">UF</option><option value="SP">SP</option></select>
<span id="exibe_cidade"><select name="pf_cid" required><option value="">Cidade onde reside</option></select></span>
<select name="pais_endereco" required><option value="">País</option><option value="Brasil">Brasil</option></select>
<select name="escolaridade" required><option value="">?</option><option value="Técnico">Técnico</option><option value="Superior">Superior</option></select>
<input name="instituicao" required><input name="cidade_instituicao" required><input name="curso_aluno" required>
<input type="number" name="ano_conclusao" required>
<select name="situacao_aluno" required><option value="">?</option><option>Autônomo</option><option>Empregado</option></select>
<input name="pj_razsoc"><select name="pj_est"><option value="">UF</option><option value="SP">SP</option></select>
<select name="pj_cid"><option value=""></option></select>
<input name="cargo_aluno"><input name="departamento"><input type="number" name="tempo_experiencia"><input name="nome_superior">
<select name="forma_de_pagamento" required><option value="">Qual?</option><option value="">PAGAMENTOS APENAS COM BOLETO</option>
<option value="01-16.455,75-B">01 PARCELA(S)</option><option value="19-866,09-B">19 PARCELA(S)</option></select>
<!--<button type="button" name="comprovante_de_pagamento" value="Aluno">Aluno</button>-->
<input name="pagamento_misto" type="hidden" value=""><input type="hidden" name="comprovante_de_pagamento" value="">
<select name="misto"><option value="NÃO">NÃO</option><option value="SIM">SIM</option></select>
<select name="pf_fontinf"><option value="IN">Site da Extecamp</option><option value="RS">Redes Sociais</option></select>
<select name="pf_tipo_contato"><option value="E_mail">Selecione</option><option value="Celular">Celular</option></select>
<input type="hidden" name="cadastrar" value="cadastrar">
<button type="submit" name="cadastrar">Finalizar inscrição</button></form>"""


CIDADES_SP = """<select class="form-control" id="pf_cid" name="pf_cid" required><option value="">Cidade onde reside</option>
<option value="Campinas">Campinas</option><option value="São Paulo">São Paulo</option></select>"""


class R:
    def __init__(self, text, status=200, headers=None, url="", j=None):
        self.text, self.status_code, self.headers, self.url = text, status, headers or {}, url
        self.encoding, self._j = "utf-8", j

    def json(self):
        return self._j


class FakeExtecamp:
    """docs_cadastrados: documentos que a busca encontra. final: resposta do POST da ficha."""

    def __init__(self, docs=(), final="ok", pref=None):
        self.docs, self.final, self.pref = set(docs), final, pref
        self.buscas, self.envio = [], None

    def get(self, url, **kw):
        if url == URL:
            return R(ENTRADA, url=url)
        if "seleciona_cidade.php" in url:
            return R(CIDADES_SP if url.endswith("=SP") else "<select name='pf_cid'></select>")
        if "viacep" in url:
            return R("", j={"logradouro": "Avenida Albert Einstein", "bairro": "Cidade Universitária",
                            "localidade": "Campinas", "uf": "SP"})
        raise AssertionError(f"GET inesperado {url}")

    def post(self, url, data=None, **kw):
        if url.endswith("procura_aluno.php"):
            return R(BUSCA)
        if url.endswith("procura_aluno.php?id=1"):
            self.buscas.append(data["rg"])
            return R(NASC if data["rg"] in self.docs else NAO_ACHOU)
        if url.endswith("procura_aluno.php?id=2"):
            return R(ficha_html(self.pref) if (data["DIA"], data["MES"], data["ANO"]) == ("17", "05", "1990")
                     else NASC)
        if "inscricao.php?id1=" in url:
            return R(ficha_html())
        if "inscricao.php?id=1" in url:
            self.envio = data
            if self.final == "ok":
                return R("", status=302, headers={"Location": LINK})
            return R("<p>ATENÇÃO: Favor verificar os campos em destaque.</p>" + ficha_html())
        raise AssertionError(f"POST inesperado {url}")


FICHA = {"cpf": "123.456.789-09", "nascimento": "1990-05-17", "doc_tipo": "RG",
         "doc_numero": "MG-12.345.678", "doc_emissor": "MG", "sexo": "Feminino",
         "genero": "Mulher cisgênero", "estado_civil": "União estável", "estado_nascimento": "MG",
         "cidade_nascimento": "Belo Horizonte", "cep": "13083-852", "numero": "100",
         "forma_pagamento": "19 PARCELA(S) DE R$ 799,00 ATRAVÉS DE BOLETO BANCÁRIO — Campanha −8%"}
LEAD = {"nome": "Maria Teste", "email": "maria@example.com", "telefone": "5519999990001"}

print("documento")
v = I.variantes_documento("MG-12.345.678", "MG", "12345678909")
check({"MG-12.345.678", "12345678", "1234567-8", "MG12345678", "MG-12345678"} <= set(v),
      "MG: com/sem prefixo MG e com/sem hífen do dígito")
check("123.456.789-09" in v and "12345678909" in v, "CPF entra (CIN usa o número do CPF)")
check(I.variantes_documento("12.345.678-X")[:1] == ["12.345.678-X"] and "12345678-X" in I.variantes_documento("12.345.678-X"),
      "SP com dígito X: como digitado primeiro, depois só com hífen")

print("primeiro cadastro (dry-run)")
fx = FakeExtecamp()
r = I.inscrever(FICHA, LEAD, URL, valer=False, sessao=fx)
check(r["ok"] and r["caminho"] == "novo" and not r["pendencias"], "ficha completa, caminho novo")
check(fx.envio is None and not r["enviado"], "dry-run NÃO envia a ficha")
check(fx.buscas == I.variantes_documento(FICHA["doc_numero"], FICHA["doc_emissor"], FICHA["cpf"]),
      "testou todas as variantes do documento antes de cadastrar")

print("primeiro cadastro (valendo)")
fx = FakeExtecamp()
r = I.inscrever(FICHA, LEAD, URL, valer=True, sessao=fx)
e = fx.envio
check(r["ok"] and r["link"] == LINK, "devolve o link individual ok-inscricao")
check(e["forma_de_pagamento"] == "19-866,09-B", "pagamento casado pelo nº de parcelas, não pelo preço da campanha")
check(e["pf_sexo"] == "F" and e["genero"] == "Mulher cisgênero", "sexo e gênero")
check((e["CPF1"], e["CPF4"]) == ("123", "09") and (e["DIA"], e["MES"], e["ANO"]) == ("17", "05", "1990"),
      "CPF em 4 partes e nascimento em dia/mês/ano")
check(e["DDD_CEL"] == "19" and e["celular"] == "99999-0001", "celular com DDD separado e máscara")
check(e["pf_cid"] == "Campinas" and e["pf_ender"] == "Avenida Albert Einstein" and e["pf_cep"] == "13083-852",
      "endereço completado pelo CEP e cidade casada com a lista da UF")
check(e["escolaridade"] == "Superior" and e["situacao_aluno"] == "Empregado"
      and e["instituicao"] == "Não informado" and e["ano_conclusao"] == "00"
      and e["cidade_instituicao"] == "Campinas" and e["pj_cid"] == "Campinas",
      "acadêmico/profissional pela regra da coordenação")
check(e["pf_fontinf"] == "RS" and e["pf_tipo_contato"] == "Celular" and e["misto"] == "NÃO",
      "redes sociais, contato por celular, sem pagamento misto")
check(e["pf_deficiencia"] == "NÃO" and e["cadastrar"] == "cadastrar", "deficiência NÃO por padrão; botão cadastrar")

print("ex-aluno")
fx = FakeExtecamp(docs={"MG-12345678"}, pref={"pf_nome": "MARIA DA SILVA TESTE", "pf_rg": "MG-12345678"})
r = I.inscrever(FICHA, LEAD, URL, valer=True, sessao=fx)
check(r["ok"] and r["caminho"] == "ex-aluno", "achou o cadastro numa variante do documento")
check(fx.envio["pf_nome"] == "MARIA DA SILVA TESTE" and fx.envio["pf_rg"] == "MG-12345678",
      "dado do cadastro oficial é mantido")
check(fx.envio["pf_email"] == "maria@example.com" and fx.envio["forma_de_pagamento"] == "19-866,09-B",
      "campo vazio é completado; escolha do curso é sempre a nossa")
check(fx.buscas[-1] == "MG-12345678", "parou de buscar ao encontrar")

print("fail-closed")
fx = FakeExtecamp()
r = I.inscrever({**FICHA, "genero": "", "forma_pagamento": "07 PARCELA(S) DE R$ 1"}, LEAD, URL, valer=True, sessao=fx)
check(not r["ok"] and fx.envio is None, "ficha incompleta não é enviada")
check(any(p.startswith("genero") for p in r["pendencias"])
      and any(p.startswith("forma_de_pagamento") for p in r["pendencias"]), "pendências nomeiam os campos")
check(not any("Maria" in p or "123" in p for p in r["pendencias"]), "pendência não carrega PII")
fx = FakeExtecamp(final="erro")
r = I.inscrever(FICHA, LEAD, URL, valer=True, sessao=fx)
check(not r["ok"] and r["enviado"] and "ATENÇÃO" in r["pendencias"][-1],
      "envio sem link = falha marcada como ENVIADA, com a mensagem da Extecamp")
r = I.inscrever({**FICHA, "nascimento": "1991-01-01"}, LEAD, URL, valer=True,
                sessao=FakeExtecamp(docs={"MG-12.345.678"}))
check(not r["ok"] and r["caminho"] == "ex-aluno" and "não aceita" in r["pendencias"][0],
      "ex-aluno com nascimento errado não cai para primeiro cadastro (evita duplicar)")

print(f"\n{ok} checks OK — inscrição Extecamp")
