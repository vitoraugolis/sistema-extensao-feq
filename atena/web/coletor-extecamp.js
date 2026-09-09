/* Atena · coletor do ADM Cursos da Extecamp (spec 20 §2).
 *
 * Roda DENTRO da aba já autenticada da Extecamp — é a única forma de enxergar a
 * sessão criada pelo login humano + verificação anti-robô. De fora (nossa
 * origem) o navegador não deixaria ler aquelas páginas, e com razão.
 *
 * É de propósito uma BOMBA BURRA: busca a página, manda o HTML para a Central e
 * pergunta "e agora?". Quem conhece a estrutura do ADM Cursos é o backend, em
 * Python testado contra fixtures reais — HTML de terceiro muda sem avisar, e
 * essa lógica não pode viver como JavaScript sem teste no navegador de ninguém.
 */
(function () {
  var BASE = "{{BASE}}", TOKEN = "{{TOKEN}}";
  if (window.__atenaColeta) { window.__atenaColeta.mostrar(); return; }

  var PAUSA = 700;          // respiro entre requisições: o ADM Cursos é um ASP antigo
  var TETO = 400;           // trava de segurança contra laço infinito de links
  var fila = [], vistos = {}, log = [], soma = {criados: 0, movidos: 0, atualizados: 0, paginas: 0};
  var parado = false, painel;

  function cria(tag, css, texto) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (texto) e.textContent = texto;
    return e;
  }

  function ui() {
    painel = cria("div", "position:fixed;right:14px;bottom:14px;z-index:2147483647;width:330px;" +
      "background:#fff;border:2px solid #141414;border-radius:10px;box-shadow:4px 4px 0 #141414;" +
      "font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#141414;overflow:hidden");
    var topo = cria("div", "background:#F96A10;color:#fff;font-weight:700;padding:8px 10px;" +
      "display:flex;align-items:center;gap:8px", "Atena · coleta Extecamp");
    var x = cria("button", "margin-left:auto;background:none;border:0;color:#fff;font-size:16px;" +
      "cursor:pointer;line-height:1", "✕");
    x.onclick = function () { parado = true; painel.remove(); window.__atenaColeta = null; };
    topo.appendChild(x);
    var corpo = cria("div", "padding:10px;max-height:260px;overflow:auto");
    painel.appendChild(topo); painel.appendChild(corpo);
    document.body.appendChild(painel);
    return corpo;
  }

  function pinta() {
    if (!painel) return;
    var c = painel.lastChild;
    c.innerHTML = "";
    var st = cria("div", "font-weight:700;margin-bottom:6px",
      parado ? "Coleta encerrada." : "Coletando… " + soma.paginas + " página(s), " +
        fila.length + " na fila");
    c.appendChild(st);
    c.appendChild(cria("div", "margin-bottom:8px",
      "Cards novos: " + soma.criados + " · movidos: " + soma.movidos +
      " · atualizados: " + soma.atualizados));
    log.slice(-8).forEach(function (l) {
      c.appendChild(cria("div", "font-size:11.5px;color:#5b5b5b;border-top:1px solid #eee;padding:4px 0", l));
    });
  }

  function anota(t) { log.push(t); pinta(); }

  /* A fila guarda {url, ctx}: algumas páginas (os relatórios de contato) não
   * dizem a que oferecimento pertencem, então o backend manda o contexto junto e
   * o coletor devolve intacto. Assim o backend segue sem estado entre requisições. */
  function enfileira(item) {
    var u = typeof item === "string" ? item : (item && item.url);
    if (!u || vistos[u] || fila.length + soma.paginas > TETO) return;
    vistos[u] = 1;
    fila.push({url: u, ctx: (typeof item === "object" && item.ctx) || null});
  }

  /* O ADM Cursos serve iso-8859-1. Deixar o navegador adivinhar o charset
   * corromperia todo nome acentuado — que aqui é a maioria. Decodifica explícito. */
  function baixar(url) {
    return fetch(url, {credentials: "include"})
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) { return new TextDecoder("iso-8859-1").decode(buf); });
  }

  function entregar(item, corpo) {
    return fetch(BASE + "/api/coleta/pagina", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: TOKEN, url: item.url, ctx: item.ctx, html: corpo})
    }).then(function (r) {
      /* Um 401/403 aqui não é "essa página falhou": é a coleta inteira que não vai
       * andar (sessão expirada, ou a rota exigindo cookie que esta origem não tem).
       * Sem ler o status, o `.json()` estoura num erro genérico e o operador vê
       * dezenas de avisos idênticos sem saber que só precisa reabrir a sessão. */
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          var e = new Error(j.erro || ("a Central respondeu " + r.status));
          e.fatal = (r.status === 401 || r.status === 403);
          throw e;
        });
      }
      return r.json();
    });
  }

  function passo() {
    if (parado || !fila.length) {
      parado = true;
      anota("Pronto. Volte à Central para ver a fila de trabalho.");
      pinta();
      return;
    }
    var item = fila.shift(), url = item.url;
    /* Os dois lados falham por motivos completamente diferentes — a Extecamp por
     * sessão vencida ou página que mudou, a Central por token vencido ou rota
     * fechada. Um `catch` só, rotulado com o nome do arquivo .asp, mandava o
     * operador procurar no sistema errado. Agora cada lado diz quem é. */
    baixar(url).catch(function (e) {
      throw new Error("não consegui LER esta página da Extecamp — " + e.message +
                      " (a sessão do ADM Cursos ainda está aberta nesta aba?)");
    }).then(function (corpo) {
      return entregar(item, corpo).catch(function (e) {
        var novo = new Error("li a página, mas não consegui ENTREGAR à Central — " + e.message);
        novo.fatal = e.fatal;
        throw novo;
      });
    }).then(function (r) {
      soma.paginas++;
      (r.proximas || []).forEach(enfileira);
      if (r.erro) {
        anota("⚠ " + r.erro);
        if (r.fatal) {        // sessão da Extecamp caiu: o resto da fila viria vazio
          parado = true;
          pinta();
          return;
        }
      } else if (r.resumo && r.resumo.ok) {
        var s = r.resumo;
        soma.criados += (s.criados || []).length;
        soma.movidos += (s.movidos || []).length;
        soma.atualizados += (s.atualizados || []).length;
        anota(s.curso + "/" + s.turma + " · " + s.aba + ": " + s.vistos + " lido(s)");
      } else {
        anota(r.tipo + ": +" + (r.proximas || []).length + " página(s)");
      }
      setTimeout(passo, PAUSA);
    }).catch(function (e) {
      anota("⚠ " + url.split("/").pop().split("?")[0] + ": " + e.message);
      if (e.fatal) {          // insistir 50 vezes no mesmo 403 não ajuda ninguém
        parado = true;
        anota("Coleta interrompida: reabra a sessão na aba Coleta de Dados da Central.");
        pinta();
        return;
      }
      setTimeout(passo, PAUSA);
    });
  }

  window.__atenaColeta = {mostrar: function () { if (painel) painel.style.display = "block"; }};
  ui();
  anota("Sessão autenticada detectada nesta aba.");
  /* Entra pelas duas listas de unidades: cursos em inscrição e em andamento. */
  ["cursos-em-inscricao.asp", "admcursosand_ext_pre.asp"].forEach(function (p) {
    enfileira("https://www.extecamp.unicamp.br/admcursos/" + p);
  });
  pinta();
  passo();
})();
