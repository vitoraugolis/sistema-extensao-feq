/* Atena · Bridge Page (specs 16 §2 e 17 §5) — two-step opt-in + telemetria de sessão.
 * Lê window.BRIDGE = {curso, destino, webhook, pixel} (injetado pelo template).
 * Regras: CTA abre modal; submit valida, dispara Pixel InitiateCheckout com o
 * MESMO event_id que vai ao backend (dedup CAPI) e redireciona à Extecamp em
 * <=1,5s — sucesso, falha ou timeout: a venda nunca espera o backend.
 * Telemetria: alcance/tempo por seção (data-secao), scroll máx., CTA, modal;
 * resumo via sendBeacon na saída. Sem PII — a sessão só se liga ao lead se o
 * modal for enviado (sessao_id acompanha o POST). */
(function () {
  "use strict";
  var CFG = window.BRIDGE || {};
  var modal = document.getElementById("modal");
  var form = document.getElementById("bridge-form");
  var tel = document.getElementById("whatsapp");
  var erroTel = document.getElementById("erro-tel");
  var erroOptin = document.getElementById("erro-optin");
  var TIMEOUT_MS = 1500;

  function uuid() {
    return (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
      : "s-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }
  // UTMs da campanha: lidos da URL de ENTRADA e guardados na sessão. Se o visitante
  // navegar para outra página do site antes de converter, a querystring se perde —
  // sem isto a atribuição do anúncio some justamente em quem leu mais e converteu.
  var UTM_KEYS = ["utm_source","utm_medium","utm_campaign","utm_content","utm_term",
                  "gclid","fbclid"];
  function utmSalvos() {
    var achados = {}, q;
    try { q = new URLSearchParams(window.location.search); } catch (e) { q = null; }
    if (q) UTM_KEYS.forEach(function (k) { if (q.get(k)) achados[k] = q.get(k).slice(0, 200); });
    try {
      if (Object.keys(achados).length) {
        sessionStorage.setItem("atena_utm", JSON.stringify(achados));
        return achados;
      }
      return JSON.parse(sessionStorage.getItem("atena_utm") || "{}");
    } catch (e) { return achados; }   // navegador sem storage: usa só o que veio na URL
  }
  utmSalvos();                        // grava logo no carregamento, não só no envio

  function cookie(n) {
    var m = document.cookie.match("(^|;)\\s*" + n + "=([^;]*)");
    return m ? m[2] : "";
  }

  // ---- Pixel: ViewContent no load; InitiateCheckout só no submit válido ----
  if (CFG.pixel) {
    !function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
    n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
    n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,
    document,'script','https://connect.facebook.net/en_US/fbevents.js');
    fbq("init", CFG.pixel);
    fbq("track", "PageView");
    fbq("track", "ViewContent", { content_ids: [CFG.curso], content_name: CFG.curso });
  }

  // ---- Telemetria de sessão (spec 17 §5) -----------------------------------
  var LP_URL = (CFG.webhook || "").replace("/webhook/ponte", "/webhook/lp");
  var tele = { sessao_id: uuid(), curso: CFG.curso, inicio: Date.now(),
               secoes: {}, ultima_secao: "", max_scroll: 0,
               cta_cliques: 0, modal_aberto: 0, modal_enviado: 0 };
  var visiveis = {};   // secao -> timestamp de entrada

  if (window.IntersectionObserver) {
    var obs = new IntersectionObserver(function (entries) {
      var agora = Date.now();
      entries.forEach(function (e) {
        var s = e.target.getAttribute("data-secao");
        if (e.isIntersecting) {
          visiveis[s] = agora;
          tele.ultima_secao = s;
          if (!(s in tele.secoes)) tele.secoes[s] = 0;
        } else if (visiveis[s]) {
          tele.secoes[s] = (tele.secoes[s] || 0) + Math.round((agora - visiveis[s]) / 1000);
          delete visiveis[s];
        }
      });
    }, { threshold: 0.4 });
    Array.prototype.forEach.call(document.querySelectorAll("[data-secao]"), function (el) {
      obs.observe(el);
    });
  }
  // ---- Barra de CTA fixa: some no hero (que já tem CTA grande), aparece ao
  // rolar além dele — padrão das duas referências de LP de curso. ----------
  var ctaFixo = document.getElementById("cta-fixo");
  var waBotao = document.getElementById("wa-flutuante");
  var hero = document.querySelector('[data-secao="hero"]');
  window.addEventListener("scroll", function () {
    var doc = document.documentElement;
    var alt = doc.scrollHeight - window.innerHeight;
    if (alt > 0) {
      var pct = Math.round(100 * (window.scrollY || doc.scrollTop) / alt);
      if (pct > tele.max_scroll) tele.max_scroll = Math.min(100, pct);
    }
    if (ctaFixo && hero) {
      var passouHero = hero.getBoundingClientRect().bottom < 0;
      ctaFixo.classList.toggle("hidden", !passouHero);
      if (waBotao) waBotao.classList.toggle("wa-subiu", passouHero);
    }
  }, { passive: true });

  function resumo() {
    var agora = Date.now();
    Object.keys(visiveis).forEach(function (s) {   // fecha seções ainda na tela
      tele.secoes[s] = (tele.secoes[s] || 0) + Math.round((agora - visiveis[s]) / 1000);
      visiveis[s] = agora;
    });
    return JSON.stringify({ sessao_id: tele.sessao_id, curso: tele.curso,
      duracao: Math.round((agora - tele.inicio) / 1000), max_scroll: tele.max_scroll,
      ultima_secao: tele.ultima_secao, secoes: tele.secoes,
      cta_cliques: tele.cta_cliques, modal_aberto: tele.modal_aberto,
      modal_enviado: tele.modal_enviado });
  }
  function enviarTele() {
    if (!LP_URL) return;
    try {
      if (navigator.sendBeacon) navigator.sendBeacon(LP_URL, resumo());
      else fetch(LP_URL, { method: "POST", body: resumo(), keepalive: true });
    } catch (e) { /* telemetria jamais quebra a página */ }
  }
  enviarTele();                                       // registra a visita mesmo sem saída limpa
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") enviarTele();
  });

  // ---- Modal: todo [data-cta] abre; overlay/X/ESC fecham -------------------
  function abrir() {
    tele.cta_cliques += 1;
    tele.modal_aberto = 1;
    modal.classList.remove("hidden");
    modal.classList.add("flex");
    document.getElementById("nome").focus();
  }
  function fechar() {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-cta]"), function (b) {
    b.addEventListener("click", abrir);
  });
  document.getElementById("overlay").addEventListener("click", fechar);
  document.getElementById("fechar").addEventListener("click", fechar);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") fechar(); });
  window.addEventListener("pageshow", function () {   // voltar da Extecamp (bfcache)
    document.getElementById("continuar").disabled = false;
  });

  // ---- Máscara de WhatsApp: (##) #####-#### enquanto digita ----------------
  function mascarar(v) {
    var d = v.replace(/\D/g, "");
    if (d.slice(0, 2) === "55" && d.length > 11) d = d.slice(2);   // colou com +55
    d = d.slice(0, 11);
    if (d.length <= 2) return d;
    if (d.length <= 6) return "(" + d.slice(0, 2) + ") " + d.slice(2);
    if (d.length <= 10) return "(" + d.slice(0, 2) + ") " + d.slice(2, 6) + "-" + d.slice(6);
    return "(" + d.slice(0, 2) + ") " + d.slice(2, 7) + "-" + d.slice(7);
  }
  tel.addEventListener("input", function () { tel.value = mascarar(tel.value); });

  // ---- Submit: valida -> Pixel -> POST (timeout 1,5s) -> Extecamp SEMPRE ---
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var digitos = tel.value.replace(/\D/g, "");
    if (digitos.length < 10 || digitos.length > 11) {
      erroTel.classList.remove("hidden");
      tel.focus();
      return;
    }
    erroTel.classList.add("hidden");
    if (!document.getElementById("optin").checked) {   // LGPD: opt-in explícito, com feedback
      erroOptin.classList.remove("hidden");
      return;
    }
    erroOptin.classList.add("hidden");
    document.getElementById("continuar").disabled = true;
    tele.modal_enviado = 1;

    var eid = uuid();   // regra de ouro: MESMO id no Pixel e na CAPI (dedup)
    if (CFG.pixel) {
      fbq("track", "InitiateCheckout",
          { content_ids: [CFG.curso], content_name: CFG.curso }, { eventID: eid });
    }
    var redirecionou = false;
    function irParaExtecamp() {
      if (redirecionou) return;
      redirecionou = true;
      enviarTele();
      // Curso SEM turma aberta (lista de espera): não há inscrição pra onde mandar.
      // Confirma na própria página — o lead já foi gravado pelo POST acima.
      if (!CFG.aberto || !CFG.destino) { confirmarEspera(); return; }
      // Passa por /thank-you-page-lp/ (interstício com o noscript do GTM) antes do
      // destino real: é a URL que a conversão de Lead do Google Ads/GTM observa
      // (o gatilho exige o path começando com /thank-you-page — um prefixo como
      // "lp-thank-you-page" não disparou o Lead, confirmado pelo usuário).
      window.location.href = "/thank-you-page-lp/?to=" + encodeURIComponent(CFG.destino);
    }
    function confirmarEspera() {
      var caixa = document.querySelector("#modal .modal-caixa") || form.parentNode;
      caixa.innerHTML =
        '<div class="espera-ok">' +
        '<p class="espera-check">✓</p>' +
        '<p class="espera-tit">Pronto, você está na lista!</p>' +
        '<p class="espera-sub">Assim que a próxima turma deste curso for confirmada, ' +
        'a gente te avisa no WhatsApp. Se quiser falar com a equipe agora, é só usar ' +
        'o botão do WhatsApp aqui na página.</p></div>';
    }
    // keepalive: o POST sobrevive à navegação se o redirect vier antes da resposta
    fetch(CFG.webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        nome: document.getElementById("nome").value.trim(),
        whatsapp: digitos, curso_id: CFG.curso,
        event_id: eid, sessao_id: tele.sessao_id,
        fbp: cookie("_fbp"), fbc: cookie("_fbc"),
        url: window.location.href,
        utm: utmSalvos()          // origem da campanha, capturada em silêncio
      })
    }).then(irParaExtecamp, irParaExtecamp);
    setTimeout(irParaExtecamp, TIMEOUT_MS);   // backend lento/fora: a venda não espera
  });
})();
