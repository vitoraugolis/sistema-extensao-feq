#!/usr/bin/env python3
"""
Atena · Percepção da mídia do lead — áudio vira texto, imagem vira descrição.

Muita gente manda áudio em vez de digitar. Até aqui o cérebro recebia apenas o rótulo
"[o lead enviou um áudio]" (ver Orquestrador._processar_mensagem) e respondia no vácuo
— na prática, quem prefere áudio era mal atendido.

  áudio  -> OpenAI Whisper  (transcrição do que o lead falou)
  imagem -> Claude (visão)  (descrição objetiva + transcrição do texto visível)

Imagem fica no Claude porque é o provedor que já processa TODO o conteúdo das conversas
— não faz sentido abrir um segundo processador de dados pessoais só para isso. Áudio vai
ao Whisper porque o Claude não transcreve áudio.

DEGRADA EM SILÊNCIO, sempre: sem chave, formato não suportado, arquivo grande demais,
timeout ou erro da API -> devolve None e o orquestrador volta ao rótulo antigo. Perceber
mídia melhora a conversa; nunca pode ser um ponto novo de falha dela.

O resultado é marcado como AUTOMÁTICO em toda a cadeia (coluna própria no banco, selo na
Central, marcador no prompt): transcrição erra, e nem o operador nem o cérebro podem
tratá-la como se fosse texto digitado pelo lead.

LGPD: áudio e imagem do lead saem para processador externo (OpenAI/Anthropic) — mesma
natureza do texto que já vai ao cérebro hoje. Ver specs/07.
"""
import os
import sys
import base64
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Whisper deduz o formato pela EXTENSÃO do nome do arquivo, e as mídias são gravadas
# como data/media/<media_id>, sem extensão nenhuma — mandar assim a API recusa.
# Áudio de voz do WhatsApp é audio/ogg (opus). amr fica de fora: o Whisper não aceita.
_EXT_AUDIO = {
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/oga": "oga",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm",
}
# tipos que a API de visão do Claude aceita
_MIME_IMAGEM = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_LIMITE_AUDIO_MB = 24     # limite do Whisper é 25 MB (o do WhatsApp já é 16)
_LIMITE_IMAGEM_MB = 5     # limite prático da API de visão

# Vocabulário do domínio: o Whisper aceita um prompt de viés e sem ele erra justamente
# os termos que mais importam aqui (Extecamp vira "extercampo", FEQ vira "feck").
_VIES_WHISPER = ("Conversa sobre cursos de extensão da FEQ Unicamp. Termos frequentes: "
                 "Extecamp, FEQ, Unicamp, inscrição, matrícula, mensalidade, boleto, "
                 "engenharia química, especialização, carga horária.")

_PROMPT_IMAGEM = (
    "Descreva em português, de forma objetiva e curta (no máximo 3 frases), esta imagem "
    "enviada por um lead que conversa com a secretaria de um curso de extensão "
    "universitária. TRANSCREVA literalmente qualquer texto legível — documento, "
    "comprovante, boleto, print de tela, mensagem de erro: é quase sempre o que importa. "
    "NÃO especule sobre o que não está visível, não tente identificar quem aparece na "
    "foto e não invente nenhum dado. Se estiver ilegível ou sem conteúdo útil, diga isso "
    "em uma frase só. Responda em texto corrido, SEM formatação markdown (sem asteriscos, "
    "sem listas): a saída vai direto para a tela do operador e para o prompt do agente.")

_MAX_CARACTERES = 4000    # transcrição gigante não pode estourar o prompt do cérebro


def _ler(caminho, limite_mb):
    """Bytes do arquivo, ou None se não existe/está vazio/passa do limite."""
    p = pathlib.Path(caminho)
    if not p.exists():
        return None
    dados = p.read_bytes()
    if not dados or len(dados) > limite_mb * 1024 * 1024:
        return None
    return dados


def transcrever_audio(caminho, mime):
    """Áudio -> texto, via Whisper. None em qualquer falha (o chamador degrada)."""
    chave = os.environ.get("OPENAI_API_KEY")
    ext = _EXT_AUDIO.get((mime or "").split(";")[0].strip().lower())
    if not (chave and ext):
        return None
    dados = _ler(caminho, _LIMITE_AUDIO_MB)
    if not dados:
        return None
    try:
        import requests
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions", timeout=120,
            headers={"Authorization": f"Bearer {chave}"},
            files={"file": (f"audio.{ext}", dados, mime)},
            data={"model": os.environ.get("WHISPER_MODELO", "whisper-1"),
                  # sem language ele às vezes "detecta" espanhol num áudio em português
                  # com ruído e devolve a transcrição traduzida
                  "language": "pt", "prompt": _VIES_WHISPER, "response_format": "text"})
        if r.status_code != 200:
            print(f"[percepcao] whisper {r.status_code}: {r.text[:140]}", flush=True)
            return None
        return (r.text or "").strip()[:_MAX_CARACTERES] or None
    except Exception as e:
        print(f"[percepcao] whisper falhou: {type(e).__name__}: {e}", flush=True)
        return None


def descrever_imagem(caminho, mime):
    """Imagem -> descrição objetiva + texto visível, via Claude. None em qualquer falha."""
    from atena.agentes.cerebro import _claude, chave_do
    chave = chave_do("claude")
    m = (mime or "").split(";")[0].strip().lower()
    if not (chave and m in _MIME_IMAGEM):
        return None
    dados = _ler(caminho, _LIMITE_IMAGEM_MB)
    if not dados:
        return None
    try:
        # _claude repassa `messages` direto no corpo, então o conteúdo em blocos
        # (imagem + texto) do formato de visão passa sem precisar de cliente novo.
        saida = _claude(
            chave, os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "Você descreve imagens para um atendente humano, com precisão e sem especular.",
            [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": m,
                                             "data": base64.b64encode(dados).decode()}},
                {"type": "text", "text": _PROMPT_IMAGEM}]}])
        return (saida or "").strip()[:_MAX_CARACTERES] or None
    except Exception as e:
        print(f"[percepcao] visão falhou: {type(e).__name__}: {e}", flush=True)
        return None


def perceber(tipo, caminho, mime):
    """Despacha pelo tipo já normalizado pelo transporte. Tipo sem percepção
    (documento, vídeo) devolve None de propósito — segue com o rótulo genérico."""
    if tipo == "audio":
        return transcrever_audio(caminho, mime)
    if tipo in ("image", "sticker"):
        return descrever_imagem(caminho, mime)
    return None


if __name__ == "__main__":   # uso: python3 -m atena.agentes.percepcao <arquivo> <mime>
    print(perceber("audio" if "audio" in sys.argv[2] else "image", sys.argv[1], sys.argv[2]))
