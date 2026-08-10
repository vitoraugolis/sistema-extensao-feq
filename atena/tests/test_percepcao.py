#!/usr/bin/env python3
"""
Percepção de mídia: áudio -> transcrição (Whisper), imagem -> descrição (Claude).

O requisito que mais importa aqui NÃO é transcrever bem — é nunca quebrar a conversa
quando a transcrição falha. Lead que manda áudio já era mal atendido com o rótulo cego;
ficaria pior se uma API fora do ar deixasse a mensagem sem resposta nenhuma.

E o requisito de honestidade: o que a máquina entendeu jamais pode se passar por texto
digitado pelo lead — nem no banco, nem no prompt do cérebro.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

os.environ["ATENA_DEV"] = "1"

from atena.agentes import percepcao
from atena.core.estado import Estado
from atena.core.orquestrador import Orquestrador
from atena.core import contratos as C

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


# ------------------------------------------------- 1) degradação: nunca explode
falso = pathlib.Path("/tmp/atena_percepcao_teste.bin")
falso.write_bytes(b"nao e um audio de verdade")

sem_chave = dict(os.environ)
os.environ.pop("OPENAI_API_KEY", None)
check(percepcao.transcrever_audio(falso, "audio/ogg") is None,
      "sem OPENAI_API_KEY devolve None (não estoura exceção)")
os.environ.pop("ANTHROPIC_API_KEY", None)
check(percepcao.descrever_imagem(falso, "image/jpeg") is None,
      "sem ANTHROPIC_API_KEY devolve None (não estoura exceção)")
os.environ.update(sem_chave)

check(percepcao.transcrever_audio("/caminho/que/nao/existe", "audio/ogg") is None,
      "arquivo inexistente devolve None")
check(percepcao.transcrever_audio(falso, "audio/amr") is None,
      "formato que o Whisper não aceita (amr) é recusado ANTES de gastar chamada de API")
check(percepcao.descrever_imagem(falso, "image/tiff") is None,
      "mime de imagem não suportado pela API de visão é recusado antes da chamada")
check(percepcao.perceber("document", falso, "application/pdf") is None
      and percepcao.perceber("video", falso, "video/mp4") is None,
      "tipo sem percepção (documento/vídeo) devolve None de propósito")

# extensão: as mídias são gravadas SEM extensão (data/media/<id>) e o Whisper deduz o
# formato pelo NOME do arquivo — sem o mapa abaixo a API recusaria todo áudio do WhatsApp
check(percepcao._EXT_AUDIO["audio/ogg"] == "ogg", "áudio de voz do WhatsApp (ogg) mapeado")
check("audio/amr" not in percepcao._EXT_AUDIO, "amr fora do mapa: o Whisper não aceita")

vazio = pathlib.Path("/tmp/atena_percepcao_vazio.bin")
vazio.write_bytes(b"")
check(percepcao.transcrever_audio(vazio, "audio/ogg") is None, "arquivo vazio devolve None")

# ------------------------------------------------- 2) transcrição chega ao cérebro
class BrainEspiao:
    def __init__(self):
        self.recebido = None

    def __call__(self, lead, mensagem, historico):
        self.recebido = mensagem
        return C.DecisaoAgente(C.CONTINUAR, "ok", mensagem_ao_lead=None)


def _novo():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="Ana", telefone="5519999990001", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    espiao = BrainEspiao()
    return est, Orquestrador(estado=est, brain=espiao), espiao


est, orq, espiao = _novo()
orq.processar_mensagem("c1", "", tipo="audio", media_id="m1",
                       transcricao="Oi, queria saber o valor do curso de inteligência artificial")
check("valor do curso" in espiao.recebido,
      "o que o lead FALOU chega ao cérebro (antes ele só via '[o lead enviou um áudio]')")
check("transcrito automaticamente" in espiao.recebido,
      "vai MARCADO como automático: Whisper erra, e o agente não pode repetir número/valor "
      "transcrito com a confiança de quem leu texto digitado")

est2, orq2, espiao2 = _novo()
orq2.processar_mensagem("c1", "", tipo="audio", media_id="m1", transcricao=None)
check(espiao2.recebido == "[o lead enviou um áudio]",
      "transcrição falhou -> cai no rótulo antigo e a conversa SEGUE (nunca fica sem resposta)")

est3, orq3, espiao3 = _novo()
orq3.processar_mensagem("c1", "olha isso aqui", tipo="image", media_id="m2",
                        transcricao="Print da tela de pagamento da Extecamp com erro 500.")
check("erro 500" in espiao3.recebido and "olha isso aqui" in espiao3.recebido,
      "imagem: descrição E legenda escrita pelo lead chegam juntas")
check("não do lead" in espiao3.recebido,
      "a descrição é atribuída ao leitor automático, não ao lead")

# ------------------------------------------------- 3) banco separa fala de palpite
est4, orq4, _ = _novo()
orq4.processar_mensagem("c1", "", tipo="audio", media_id="m3", transcricao="quero me inscrever")
linha = est4.historico("c1")[-1]
check(linha["transcricao"] == "quero me inscrever", "transcrição persistida em coluna própria")
check(not linha["texto"],
      "e NÃO no campo texto: texto é o que o lead escreveu, transcrição é o que a máquina "
      "entendeu — juntar os dois faria o operador ler palpite de IA como fala literal")
check(linha["tipo"] == "audio" and linha["media_id"] == "m3",
      "o áudio original continua na thread (o operador pode sempre ouvir e conferir)")

est4.set_transcricao(linha["id"], "corrigido depois")
check(est4.get_conversa(linha["id"])["transcricao"] == "corrigido depois",
      "set_transcricao permite ler mídia antiga (passivo de áudios anteriores à função)")

falso.unlink(missing_ok=True)
vazio.unlink(missing_ok=True)
print(f"\n{ok}/{ok} passaram.")
