#!/usr/bin/env python3
"""
Atena · Transporte WhatsApp Business Cloud.
Webhook de entrada -> orquestrador; envio guardado. Sem WHATSAPP_TOKEN o envio é
DRY-RUN (não manda nada). Fora do horário comercial, enfileira (tarefa) em vez de
enviar. Opt-out respeitado. Decisão da mensagem é do orquestrador; aqui só entrega.
ponytail: janela de 24h não rastreada por timestamp ainda -> envia texto livre em
dev; em produção, fora de 24h exige template (TODO marcado).
"""
import os
import re
import sys
import json
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core import guardrails as G
from atena.core.contatos import CONTATOS_OFICIAIS, payload_contato
from atena.agentes.ingestao import curso_de_texto


def _agora_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")

_GRAPH = "https://graph.facebook.com/v19.0"
_MEDIA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "media"
_TIPOS_MIDIA = ("image", "audio", "video", "document", "sticker")
_STATUS_PT = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}


def fmt_whatsapp(texto):
    """Negrito markdown (**x**) -> negrito WhatsApp (*x*). Belt para o LLM que escapar
    a instrução do prompt; o WhatsApp renderiza ** literal e polui a leitura."""
    return re.sub(r"\*\*+", "*", texto or "")


def _wamid(j):
    return ((j or {}).get("messages") or [{}])[0].get("id")


# Limites de tamanho da Meta por tipo (MB) e os mimes que ela aceita em cada um.
# O que não estiver nas listas vai como 'document': sempre entrega, sem surpresa
# (ex.: .webp, .webm — a Meta recusaria como image/audio).
_LIMITE_MB = {"image": 5, "audio": 16, "video": 16, "document": 100, "sticker": 0.5}
_MIME_IMAGEM = {"image/jpeg", "image/jpg", "image/png"}
_MIME_AUDIO = {"audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg", "audio/opus"}
_MIME_VIDEO = {"video/mp4", "video/3gpp"}


def tipo_de_mime(mime, nome_arquivo=""):
    """mime -> tipo de mensagem do WhatsApp (image/audio/video/document)."""
    m = (mime or "").split(";")[0].strip().lower()
    if m in _MIME_IMAGEM:
        return "image"
    if m in _MIME_AUDIO:
        return "audio"
    if m in _MIME_VIDEO:
        return "video"
    return "document"


# Rol de templates aprovados (pt_BR) — nomes e schema de variáveis ancorados no código
# que JÁ os envia em produção (nutricao.py, app.py). O status real (APPROVED/PENDING/…)
# vem da Meta em templates_status(); a UI só oferece os que a Meta confirma aprovados.
TEMPLATES_ROL = [
    {"nome": "feq_abertura_curso", "idioma": "pt_BR", "titulo": "Abertura de curso",
     "descricao": "Primeiro contato apresentando o curso ao lead.",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "curso", "rotulo": "Curso"}]},
    {"nome": "feq_lead", "idioma": "pt_BR", "titulo": "Abertura com botões",
     "descricao": "Abertura com botões de ação (link de inscrição / tirar dúvidas).",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "curso", "rotulo": "Curso"}]},
    {"nome": "feq_reengajamento", "idioma": "pt_BR", "titulo": "Reengajamento",
     "descricao": "Retoma um lead frio fora da janela de 24h.",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "curso", "rotulo": "Curso"},
                {"k": "prazo", "rotulo": "Prazo de inscrição"}]},
    {"nome": "feq_lembrete_inscricao", "idioma": "pt_BR", "titulo": "Lembrete de inscrição",
     "descricao": "Lembra o prazo de inscrição do curso.",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "curso", "rotulo": "Curso"},
                {"k": "prazo", "rotulo": "Prazo de inscrição"}]},
    {"nome": "retomar_atendimento", "idioma": "pt_BR", "titulo": "Retomar atendimento",
     "descricao": "Convida o lead a reabrir a conversa (sem variáveis).", "params": []},
    {"nome": "feq_promo_0480", "idioma": "pt_BR", "titulo": "Campanha FEQ-0480",
     "descricao": "Recuperação com o preço da campanha do FEQ-0480 (só enquanto vigente).",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "prazo", "rotulo": "Prazo de inscrição"}]},
    {"nome": "feq_promo_0331", "idioma": "pt_BR", "titulo": "Campanha FEQ-0331",
     "descricao": "Recuperação com o preço da campanha do FEQ-0331 (só enquanto vigente).",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "prazo", "rotulo": "Prazo de inscrição"}]},
    {"nome": "feq_link_corrigido", "idioma": "pt_BR", "titulo": "Correção de link",
     "descricao": "Reenvia o link CORRETO de inscrição a quem recebeu um link errado.",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "curso", "rotulo": "Curso"},
                {"k": "url_inscricao", "rotulo": "Link de inscrição"},
                {"k": "prazo", "rotulo": "Prazo de inscrição"}]},
    {"nome": "feq_inscricao_garantida", "idioma": "pt_BR", "titulo": "Inscrição garantida",
     "descricao": "Inscrição garantida; o link para prosseguir sai quando a Extecamp abrir o curso.",
     "params": [{"k": "nome", "rotulo": "Nome"}, {"k": "curso", "rotulo": "Curso"}]},
]


# Templates de RECUPERAÇÃO dedicados a uma campanha. O preço vai literal no corpo
# (é a identidade da oferta), então eles só podem ser usados enquanto a promoção do
# curso estiver vigente — `template_promocional` faz esse gate contra a ontologia.
# Sem isso, um template aprovado sobreviveria à campanha e anunciaria preço morto.
TEMPLATES_PROMO = {"FEQ-0480": "feq_promo_0480", "FEQ-0331": "feq_promo_0331"}

# Correção de link errado. Existe porque 6 leads do FEQ-0480 receberam a turma /001 em
# vez de /009 (o LLM converteu a URL à mão). Mandar o link certo por template é a única
# forma de alcançá-los fora da janela de 24h.
CORPO_LINK_CORRIGIDO = (
    "Oi, {{1}}! Aqui é da Extensão FEQ Unicamp. Passamos para você um link de inscrição com "
    "um erro de digitação — desculpe pelo transtorno. O link correto do curso {{2}} é: "
    "{{3}} — as inscrições vão até {{4}}. "
    "Se tiver qualquer dificuldade para concluir, me chama por aqui que eu te ajudo.")

CORPOS_PROMO = {
    "feq_promo_0480": (
        "Oi, {{1}}! Aqui é da Extensão FEQ Unicamp. As inscrições do curso Técnicas de "
        "Inteligência Artificial Aplicadas às Engenharias de Processos (FEQ-0480) vão até "
        "{{2}} e, nesta campanha, o investimento está em 19x de R$ 799,00. "
        "Pagando à vista ou fechando em grupo de 3 ou mais pessoas, são 15% de desconto: "
        "19x de R$ 736,18 ou R$ 13.987,39 à vista. "
        "Quer que eu te envie o programa completo e o link de inscrição?"),
    "feq_promo_0331": (
        "Oi, {{1}}! Aqui é da Extensão FEQ Unicamp. As inscrições do curso Análise "
        "Quantitativa de Riscos combinada à Fluidodinâmica Computacional e Aprendizagem de "
        "Máquina (FEQ-0331) vão até {{2}} e, nesta campanha, há 10% de desconto com "
        "parcelamento em 6x: 6x de R$ 872,39 ou R$ 5.234,36 à vista. "
        "Quer que eu te envie o programa completo e o link de inscrição?"),
}


# Forma dos parâmetros de cada template, em UM lugar só: antes cada caminho de envio
# (nutrição, reenvio) montava a sua e bastava um deles divergir do WABA para o envio
# falhar silenciosamente. Templates de campanha usam nome+prazo (o curso já está no corpo).
PARAMS_TEMPLATE = {
    "feq_abertura_curso": ("nome", "curso"),
    "feq_lead": ("nome", "curso"),
    "feq_reengajamento": ("nome", "curso", "prazo"),
    "feq_lembrete_inscricao": ("nome", "curso", "prazo"),
    "retomar_atendimento": (),
    "feq_promo_0480": ("nome", "prazo"),
    "feq_promo_0331": ("nome", "prazo"),
    "feq_link_corrigido": ("nome", "curso", "url_inscricao", "prazo"),
    "feq_inscricao_garantida": ("nome", "curso"),
}


def montar_params(nome_template, lead, ont):
    """Parâmetros do template na ordem que o WABA espera. Devolve None quando algum
    campo sairia VAZIO — a Meta recusa variável em branco, e é melhor não enviar do
    que gastar a tentativa num 400."""
    c = (ont.curso(lead.get("curso")) or {}) if ont else {}
    fonte = {
        "nome": (lead.get("nome") or "").strip() or "olá",
        "curso": f"{c.get('nome', lead.get('curso') or '')} ({c.get('codigo', '')})".strip(),
        "prazo": "/".join(reversed((c.get("inscricoes_fim") or "").split("-"))),
        # SEMPRE da ontologia: é justamente a montagem manual do link que causou o erro
        "url_inscricao": c.get("url_inscricao") or "",
    }
    vals = [fonte.get(k, "") for k in PARAMS_TEMPLATE.get(nome_template, ("nome", "curso"))]
    return None if any(not v.strip() for v in vals) else vals


def template_promocional(curso, ont, wa=None):
    """Nome do template de campanha do curso, ou None.

    Dois gates, os dois necessários:
      • promoção VIGENTE na ontologia — senão anunciaríamos preço morto;
      • template APROVADO na Meta — recém-criado ele fica PENDING, e mandar um
        template não aprovado só queima a tentativa de recuperação do lead.
    Sem conseguir consultar a Meta (dry-run/dev), não bloqueia por aprovação.
    """
    nome = TEMPLATES_PROMO.get((curso or "").upper())
    if not nome or not ont or not ont.promocao(curso):
        return None
    st = wa.templates_status() if wa is not None else None
    return nome if (st is None or st.get(nome) == "APPROVED") else None


CATEGORIAS_TEMPLATE = [
    ("UTILITY", "Utilidade — atualização de algo que o lead já pediu/contratou"),
    ("MARKETING", "Marketing — divulgação, oferta, convite, reengajamento"),
    ("AUTHENTICATION", "Autenticação — código de verificação"),
]
_NOME_TEMPLATE_RE = re.compile(r"^[a-z0-9_]{1,512}$")
_VAR_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def _n_variaveis(texto):
    return len({int(v) for v in _VAR_RE.findall(texto or "")})


def validar_template(nome, categoria, corpo, exemplos=None):
    """Checa o que a Meta rejeitaria, ANTES de gastar a chamada. Devolve o erro (str) ou None."""
    if not _NOME_TEMPLATE_RE.match(nome or ""):
        return "nome inválido: use só letras minúsculas, números e _ (ex.: feq_convite_turma)"
    if categoria not in dict(CATEGORIAS_TEMPLATE):
        return "categoria inválida"
    corpo = (corpo or "").strip()
    if not corpo:
        return "o corpo da mensagem é obrigatório"
    if len(corpo) > 1024:
        return f"corpo tem {len(corpo)} caracteres — o limite da Meta é 1024"
    nums = sorted({int(v) for v in _VAR_RE.findall(corpo)})
    if nums and nums != list(range(1, len(nums) + 1)):
        return f"as variáveis devem ser sequenciais a partir de {{{{1}}}} — encontrei {nums}"
    if nums:
        # a Meta recusa corpo que começa ou termina em variável
        if _VAR_RE.match(corpo) or corpo.endswith("}}"):
            return "o corpo não pode começar nem terminar com uma variável — escreva algum texto em volta"
        exemplos = [e for e in (exemplos or []) if str(e).strip()]
        if len(exemplos) < len(nums):
            return f"informe um exemplo para cada variável ({len(nums)} no corpo, {len(exemplos)} preenchido(s))"
    return None


def extrair_mensagem(m):
    """Normaliza um message do webhook -> (tipo, texto/legenda, media_id, mime).
    Cobre texto, cliques de botão (quick-reply de template) e mídias."""
    t = m.get("type", "text")
    if t == "text":
        return "texto", (m.get("text") or {}).get("body", ""), None, None
    if t == "button":                      # resposta de botão de template (feq_lead)
        return "texto", (m.get("button") or {}).get("text", ""), None, None
    if t == "interactive":
        i = m.get("interactive") or {}
        r = i.get("button_reply") or i.get("list_reply") or {}
        return "texto", r.get("title", ""), None, None
    if t in _TIPOS_MIDIA:
        md = m.get(t) or {}
        return t, md.get("caption", ""), md.get("id"), md.get("mime_type")
    if t == "contacts":
        # antes virava o rótulo genérico "[contacts]" — nome e telefone eram perdidos.
        # Serializa em texto porque não existe outro carreador estruturado em conversas;
        # bolhaConteudo() (Central) faz o parse de volta para desenhar o cartão.
        cs = []
        for c in (m.get("contacts") or []):
            nome = (c.get("name") or {}).get("formatted_name") or "(sem nome)"
            tel = ((c.get("phones") or [{}])[0]).get("phone", "")
            cs.append({"nome": nome, "telefone": tel})
        return "contatos", json.dumps(cs, ensure_ascii=False), None, None
    return t, f"[{t}]", None, None          # location/etc.: rótulo


class WhatsApp:
    def __init__(self, estado, orquestrador):
        self.est = estado
        self.orq = orquestrador
        self.token = os.environ.get("WHATSAPP_TOKEN")
        self.phone_id = os.environ.get("PHONE_NUMBER_ID", "")
        self.waba_id = os.environ.get("WHATSAPP_WABA_ID", "")
        self._tpl_cache = None

    def handshake(self, args):
        if args.get("hub.verify_token") == os.environ.get("META_VERIFY_TOKEN", "troque-este-token"):
            return args.get("hub.challenge", ""), 200
        return "forbidden", 403

    def _baixar_midia(self, media_id, mime):
        """Baixa os bytes da mídia (URL da Meta expira em ~5 min + exige token) e
        guarda em data/media/<media_id>. Idempotente. Sem token -> não baixa."""
        if not media_id or not self.token:
            return
        dest = _MEDIA_DIR / media_id
        if dest.exists():
            self.est.add_midia(media_id, mime)   # garante o mime registrado
            return
        try:
            import requests
            meta = requests.get(f"{_GRAPH}/{media_id}", timeout=20,
                                headers={"Authorization": f"Bearer {self.token}"})
            if meta.status_code != 200:
                print(f"[wa-midia] meta {media_id}: {meta.status_code}", flush=True); return
            j = meta.json(); url = j.get("url")
            if not url:
                return
            blob = requests.get(url, timeout=40, headers={"Authorization": f"Bearer {self.token}"})
            if blob.status_code == 200:
                _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob.content)
                self.est.add_midia(media_id, mime or j.get("mime_type", ""))
        except Exception as e:
            print(f"[wa-midia] {media_id} falhou: {e}", flush=True)

    def _perceber(self, tipo, media_id, mime):
        """Áudio -> transcrição, imagem -> descrição (agentes/percepcao.py). Roda ANTES
        do cérebro, para que a fala do lead que veio em áudio chegue lá como texto em vez
        do rótulo cego "[o lead enviou um áudio]".

        Best-effort por definição: qualquer falha devolve None e a conversa segue com o
        rótulo antigo — nenhum lead fica sem resposta porque uma API de transcrição caiu."""
        caminho = _MEDIA_DIR / media_id
        if not caminho.exists():
            return None
        try:
            from atena.agentes.percepcao import perceber
            return perceber(tipo, caminho, mime)
        except Exception as e:
            print(f"[wa-percepcao] {media_id}: {type(e).__name__}: {e}", flush=True)
            return None

    def receber(self, payload):
        """Extrai telefone + conteúdo (texto/botão/mídia) e encaminha ao orquestrador."""
        out = []
        for entry in payload.get("entry", []):
            for ch in entry.get("changes", []):
                v = ch.get("value", {})
                # Nome que a PESSOA colocou no próprio perfil do WhatsApp — a Meta manda
                # isto junto do lote de mensagens (array `contacts`, paralelo a `messages`,
                # casado por wa_id). É a única fonte de nome para quem chega sozinho pelo
                # WhatsApp, sem passar por formulário nenhum — sem isso o card nasce como
                # "wa-554199515077" e fica assim para sempre.
                perfis = {c.get("wa_id"): ((c.get("profile") or {}).get("name") or "").strip()
                         for c in (v.get("contacts") or [])}
                for m in v.get("messages", []):
                    if self.est.msg_ja_vista(m.get("id")):
                        continue   # reentrega da Meta — já processada, não duplicar resposta
                    if m.get("type") == "reaction":
                        # Reação NÃO é uma mensagem para o cérebro responder — é uma marca
                        # sobre uma mensagem existente. Intercepta ANTES de extrair_mensagem/
                        # processar_mensagem: se caísse no fluxo normal, o lead reagindo com
                        # 👍 a uma pergunta antiga viraria "nova fala" e confundiria o agente.
                        r = m.get("reaction") or {}
                        cid = self.est.marcar_reacao(r.get("message_id"), r.get("emoji"))
                        if cid:
                            out.append({"card_id": cid, "acao": "reacao", "emoji": r.get("emoji")})
                        continue
                    tel = m.get("from", "")
                    nome_perfil = perfis.get(tel, "")
                    tipo, texto, media_id, mime = extrair_mensagem(m)
                    if m.get("type") in ("button", "interactive"):
                        # Clique em botão de campanha: só existe sinal da Meta para
                        # quick-reply (context.id = wamid da mensagem respondida) — botão
                        # de LINK nunca gera webhook nenhum, limitação da plataforma, não
                        # do código. Silencioso se não achar campanha (resposta normal a
                        # um template avulso, fora de campanha).
                        ctx_id = (m.get("context") or {}).get("id")
                        if ctx_id:
                            cid_clique = self.est.registrar_clique(ctx_id)
                            if cid_clique:
                                out.append({"card_id": cid_clique, "acao": "campanha_clique"})
                    transcricao = None
                    if media_id:
                        self._baixar_midia(media_id, mime)
                        transcricao = self._perceber(tipo, media_id, mime)
                    card_id = self.est.lead_por_telefone(tel)
                    if not card_id:
                        # Número desconhecido = quase sempre o botão de WhatsApp das LPs.
                        # O agente ATENDE (antes o card ficava parado esperando humano):
                        # o curso sai do texto quando a mensagem o cita — o link do botão
                        # manda "Vim da página do curso FEQ-XXXX" — e, sem isso, o cérebro
                        # entra em modo descoberta e pergunta. Nunca inventamos o curso.
                        card_id = f"wa-{tel}"
                        curso = curso_de_texto(texto)
                        self.est.upsert_lead(
                            card_id, nome=nome_perfil, telefone=tel, curso=curso,
                            cursos_interesse=curso, fase="Lead",
                            estado_conversa="qualificacao",
                            # o lead nos escreveu primeiro: é opt-in e abre a janela de 24h
                            optin_ts=_agora_iso(), optin_fonte="whatsapp_lp")
                        self.est.log_acao("whatsapp", f"lead novo pelo WhatsApp (curso: {curso or 'a descobrir'})",
                                          card_id)
                    elif nome_perfil:
                        # Card JÁ existia (veio de "op-..." criado na mão, de outra
                        # integração, ou de uma mensagem anterior sem o array `contacts`).
                        # Preenche o nome só se AINDA estiver vazio: nunca sobrescreve um
                        # nome que o operador já leu na conversa e corrigiu. Essa guarda —
                        # "só quando vazio" — é o que torna a operação idempotente: a
                        # segunda mensagem da mesma pessoa não faz nada, porque a primeira
                        # já preencheu.
                        lead = self.est.get_lead(card_id) or {}
                        if not (lead.get("nome") or "").strip():
                            self.est.upsert_lead(card_id, nome=nome_perfil)
                            self.est.log_acao("whatsapp",
                                              f"nome preenchido pelo perfil do WhatsApp: {nome_perfil}",
                                              card_id)
                    out.append({"card_id": card_id,
                                **self.orq.processar_mensagem(card_id, texto, tipo=tipo,
                                                              media_id=media_id,
                                                              transcricao=transcricao)})
                for s in v.get("statuses", []):        # status das NOSSAS mensagens enviadas
                    self._processar_status(s, out)
        return out

    def _processar_status(self, s, out):
        """Webhook de status: atualiza a mensagem enviada; falha vira alerta na Central.
        Guarda o CÓDIGO do erro — é ele que diz se a retentativa deve mudar o número
        (131026) ou esperar e trocar de template (131049/130472). Ver transporte/reenvio."""
        st = _STATUS_PT.get(s.get("status"), s.get("status") or "")
        errs = s.get("errors") or [{}]
        codigo = errs[0].get("code") if st == "falhou" else None
        cid = self.est.atualizar_status_msg(s.get("id"), st, codigo)
        if st == "falhou" and cid:
            motivo = errs[0].get("title") or errs[0].get("message") or "erro desconhecido"
            self.est.add_alerta("envio_falhou", f"Mensagem ao lead {cid} NÃO foi entregue: {motivo}")
            out.append({"card_id": cid, "acao": "envio_falhou", "motivo": motivo, "codigo": codigo})
        elif st in ("entregue", "lido") and cid:
            self._corrigir_numero(cid, s.get("id"))

    def _corrigir_numero(self, card_id, wamid):
        """Entregou numa VARIANTE do número (retentativa do 9º dígito)? Então é essa a
        conta de WhatsApp do lead: corrige o cadastro para os próximos contatos irem
        direto ao número certo, em vez de falharem de novo."""
        env = self.est.envio_por_wamid(wamid) or {}
        destino = env.get("destino")
        lead = self.est.get_lead(card_id) or {}
        if destino and lead.get("telefone") and destino != lead["telefone"]:
            self.est.set_lead(card_id, telefone=destino)
            self.est.log_acao("reenvio", f"número corrigido para {destino} (entregou na variante)",
                              card_id)

    def _guarda_destinatario(self, telefone, forcar=False, agora=None):
        """Regra única de quem pode receber mensagem livre (texto OU mídia): opt-out e
        janela de 24h da Meta; horário comercial só barra cadência automática.
        Devolve o dict de recusa, ou None quando pode enviar."""
        card_id = self.est.lead_por_telefone(telefone) if self.est else None
        if card_id:
            lead = self.est.get_lead(card_id)
            if lead and lead.get("optout"):
                return {"ok": False, "status": "bloqueado", "motivo": "lead em opt-out"}
            ts = self.est.ultima_lead_ts(card_id)
            if not ts or (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).total_seconds() > 86400:
                return {"ok": False, "status": "fora_da_janela_24h",
                        "motivo": "sem mensagem do lead nas últimas 24h — use template aprovado"}
        if not forcar and not G.dentro_janela(agora):
            return {"ok": False, "status": "fora_do_horario",
                    "motivo": "fora do horário comercial (reenvie no horário)"}
        return None

    def enviar(self, telefone, texto, forcar=False, agora=None):
        if not texto:
            return {"ok": False, "motivo": "sem texto"}
        texto = fmt_whatsapp(texto)
        recusa = self._guarda_destinatario(telefone, forcar, agora)
        if recusa:
            return recusa
        if not self.token:
            return {"ok": True, "status": "dry_run", "para": telefone, "texto": texto}
        import requests
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone,
                                "type": "text", "text": {"body": texto}})
        j = r.json() if r.text else {}
        return {"ok": r.status_code == 200, "status": r.status_code,
                "wamid": _wamid(j) if r.status_code == 200 else None,
                "motivo": None if r.status_code == 200 else str(j.get("error", {}).get("message", ""))[:120]}

    def enviar_reacao(self, telefone, wamid_alvo, emoji, forcar=True):
        """Reage a UMA mensagem existente (nossa ou do lead). Mesmo guard de enviar():
        é uma mensagem de sessão como outra qualquer para a Meta, não some sozinha.
        Não cria linha na thread — anexa direto via marcar_reacao (mesmo mecanismo do
        lado inbound), então o operador vê o mesmo badge nos dois sentidos.
        `forcar=True` por padrão: só existe caminho humano/operador hoje (como
        conversa_enviar/conversa_enviar_midia) — o gate de horário comercial é para a
        cadência AUTOMÁTICA, não para um humano acordado clicando reagir."""
        recusa = self._guarda_destinatario(telefone, forcar=forcar)
        if recusa:
            return recusa
        if not self.token:
            self.est.marcar_reacao(wamid_alvo, emoji)
            return {"ok": True, "status": "dry_run", "para": telefone, "emoji": emoji}
        import requests
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone, "type": "reaction",
                                "reaction": {"message_id": wamid_alvo, "emoji": emoji or ""}})
        j = r.json() if r.text else {}
        ok = r.status_code == 200
        if ok:
            self.est.marcar_reacao(wamid_alvo, emoji)
        return {"ok": ok, "status": r.status_code,
                "motivo": None if ok else str(j.get("error", {}).get("message", ""))[:120]}

    def enviar_contato(self, telefone, chave, forcar=True):
        """Compartilha um dos CONTATOS_OFICIAIS (secretaria/whatsapp) como cartão —
        mais profissional que ditar um telefone em texto corrido. `forcar=True` por
        padrão pelo mesmo motivo de enviar_reacao: é sempre ação humana explícita."""
        contato = payload_contato(chave)
        if not contato:
            return {"ok": False, "motivo": f"contato '{chave}' desconhecido"}
        recusa = self._guarda_destinatario(telefone, forcar=forcar)
        if recusa:
            return recusa
        if not self.token:
            return {"ok": True, "status": "dry_run", "para": telefone, "contato": chave}
        import requests
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone,
                                "type": "contacts", "contacts": [contato]})
        j = r.json() if r.text else {}
        return {"ok": r.status_code == 200, "status": r.status_code,
                "wamid": _wamid(j) if r.status_code == 200 else None,
                "motivo": None if r.status_code == 200 else str(j.get("error", {}).get("message", ""))[:120]}

    def enviar_midia(self, telefone, conteudo, nome_arquivo, mime, legenda=None,
                     forcar=False, agora=None):
        """Envia arquivo/áudio/foto ao lead: sobe o binário para a Meta, manda a mensagem
        e guarda uma cópia local para a Central renderizar a mídia na thread.
        Mesmos guardrails do texto livre (opt-out + janela de 24h)."""
        if not conteudo:
            return {"ok": False, "motivo": "arquivo vazio"}
        tipo = tipo_de_mime(mime, nome_arquivo)
        limite = _LIMITE_MB.get(tipo, 16) * 1024 * 1024
        if len(conteudo) > limite:
            return {"ok": False, "status": "grande_demais",
                    "motivo": f"{tipo}: limite da Meta é {_LIMITE_MB.get(tipo, 16)} MB"}
        recusa = self._guarda_destinatario(telefone, forcar, agora)
        if recusa:
            return recusa
        legenda = fmt_whatsapp(legenda) if legenda else None
        # legenda só acompanha a mídia em image/video/document — áudio a Meta não aceita
        com_legenda = bool(legenda) and tipo in ("image", "video", "document")
        if not self.token:                      # dry-run: guarda local e não chama a Meta
            mid = f"local-{int(datetime.datetime.now().timestamp() * 1000)}"
            self._guardar_local(mid, conteudo, mime)
            return {"ok": True, "status": "dry_run", "para": telefone, "tipo": tipo,
                    "media_id": mid, "arquivo": nome_arquivo, "legenda_enviada": com_legenda}
        import requests
        up = requests.post(f"{_GRAPH}/{self.phone_id}/media", timeout=120,
                           headers={"Authorization": f"Bearer {self.token}"},
                           data={"messaging_product": "whatsapp", "type": mime},
                           files={"file": (nome_arquivo, conteudo, mime)})
        if up.status_code != 200 or not (up.json() or {}).get("id"):
            j = up.json() if up.text else {}
            return {"ok": False, "status": up.status_code,
                    "motivo": "upload falhou: " + str(j.get("error", {}).get("message", ""))[:120]}
        mid = up.json()["id"]
        corpo = {"id": mid}
        if com_legenda:
            corpo["caption"] = legenda
        if tipo == "document":
            corpo["filename"] = nome_arquivo
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=60,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone,
                                "type": tipo, tipo: corpo})
        j = r.json() if r.text else {}
        if r.status_code == 200:
            self._guardar_local(mid, conteudo, mime)
        return {"ok": r.status_code == 200, "status": r.status_code, "tipo": tipo,
                "media_id": mid if r.status_code == 200 else None,
                "wamid": _wamid(j) if r.status_code == 200 else None,
                "legenda_enviada": bool(corpo.get("caption")),
                "motivo": None if r.status_code == 200 else str(j.get("error", {}).get("message", ""))[:120]}

    def _guardar_local(self, media_id, conteudo, mime):
        """Cópia local (mesma pasta das mídias recebidas) para a thread da Central
        exibir o que foi enviado — a URL da Meta expira e exige token."""
        try:
            _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            (_MEDIA_DIR / media_id).write_bytes(conteudo)
            self.est.add_midia(media_id, mime)
        except Exception as e:
            print(f"[wa-midia] cópia local de {media_id} falhou: {e}", flush=True)

    def templates_listar(self, forcar=False):
        """Lista os templates da WABA na Graph API (nome, status, categoria, idioma,
        componentes). None quando não dá para consultar (sem token/WABA).
        Cache em memória (templates mudam raro); `forcar` refaz a busca."""
        if not self.token or not self.waba_id:
            return None
        if self._tpl_cache is not None and not forcar:
            return self._tpl_cache
        try:
            import requests
            r = requests.get(f"{_GRAPH}/{self.waba_id}/message_templates",
                             params={"fields": "name,status,category,language,components,rejected_reason",
                                     "limit": 200},
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=20)
            if r.status_code != 200:
                print(f"[wa-templates] meta {r.status_code}: {r.text[:120]}", flush=True)
                return None
            self._tpl_cache = r.json().get("data", [])
            return self._tpl_cache
        except Exception as e:
            print(f"[wa-templates] falhou: {e}", flush=True)
            return None

    def templates_status(self, forcar=False):
        """{nome: status} — derivado da listagem, para o seletor de envio filtrar aprovados."""
        lst = self.templates_listar(forcar)
        return None if lst is None else {t["name"]: t.get("status") for t in lst}

    def criar_template(self, nome, categoria, corpo, exemplos=None, cabecalho=None,
                       rodape=None, botoes=None, idioma="pt_BR"):
        """Cria um template na Meta e o submete para aprovação (fica PENDING).
        Valida o que a Meta rejeitaria antes de gastar a chamada. Sem token/WABA: dry-run."""
        erro = validar_template(nome, categoria, corpo, exemplos)
        if erro:
            return {"ok": False, "erro": erro}
        comps = []
        if cabecalho:
            comps.append({"type": "HEADER", "format": "TEXT", "text": cabecalho})
        corpo_comp = {"type": "BODY", "text": corpo}
        n = _n_variaveis(corpo)
        if n:
            corpo_comp["example"] = {"body_text": [[str(e) for e in (exemplos or [])[:n]]]}
        comps.append(corpo_comp)
        if rodape:
            comps.append({"type": "FOOTER", "text": rodape})
        botoes = [b for b in (botoes or []) if (b or "").strip()]
        if botoes:
            comps.append({"type": "BUTTONS",
                          "buttons": [{"type": "QUICK_REPLY", "text": b.strip()} for b in botoes[:3]]})
        payload = {"name": nome, "language": idioma, "category": categoria, "components": comps}
        if not self.token or not self.waba_id:
            return {"ok": True, "status": "dry_run", "enviado": payload,
                    "aviso": "sem WHATSAPP_TOKEN/WHATSAPP_WABA_ID — nada foi criado na Meta"}
        import requests
        r = requests.post(f"{_GRAPH}/{self.waba_id}/message_templates", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"}, json=payload)
        j = r.json() if r.text else {}
        if r.status_code == 200:
            self._tpl_cache = None            # invalida: a lista mudou
            return {"ok": True, "id": j.get("id"), "status": j.get("status", "PENDING"),
                    "categoria": j.get("category", categoria), "nome": nome}
        return {"ok": False, "status": r.status_code,
                "erro": str(j.get("error", {}).get("error_user_msg")
                            or j.get("error", {}).get("message", ""))[:300]}

    def enviar_template(self, telefone, nome, idioma="pt_BR", params=None):
        """1º contato / fora da janela de 24h: exige template aprovado.
        ponytail: sem gate de horário aqui (template é operador/sistema-iniciado, pré-aprovado)."""
        card_id = self.est.lead_por_telefone(telefone) if self.est else None
        if card_id:
            lead = self.est.get_lead(card_id)
            if lead and lead.get("optout"):
                return {"ok": False, "status": "bloqueado", "motivo": "lead em opt-out"}
        if not self.token:
            return {"ok": True, "status": "dry_run", "template": nome, "para": telefone}
        tpl = {"name": nome, "language": {"code": idioma}}
        if params:
            tpl["components"] = [{"type": "body",
                                  "parameters": [{"type": "text", "text": str(p)} for p in params]}]
        import requests
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone,
                                "type": "template", "template": tpl})
        j = r.json() if r.text else {}
        return {"ok": r.status_code == 200, "status": r.status_code,
                "wamid": _wamid(j) if r.status_code == 200 else None, "resp": j}
