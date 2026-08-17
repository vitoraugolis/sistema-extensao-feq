/* Atena · Bridge Page (specs 16 §2 e 17 §5) — two-step opt-in + telemetria de sessão.
 * Lê window.BRIDGE = {curso, aberto, webhook, webhook_inscricao, pixel} (do template).
 * Regras: CTA abre a etapa 1 (contato, sem Pixel); a etapa 2 é a ficha completa e é
 * ela que dispara o Pixel InitiateCheckout com o MESMO event_id que vai ao backend
 * (dedup CAPI) e leva à página de obrigado — sucesso, falha ou timeout, o lead nunca
 * fica preso esperando o backend.
 * Telemetria: alcance/tempo por seção (data-secao), scroll máx., CTA, modal;
 * resumo via sendBeacon na saída. Sem PII — a sessão só se liga ao lead se o
 * modal for enviado (sessao_id acompanha o POST). */
(function () {
  "use strict";
  var CFG = window.BRIDGE || {};

  // Tipografia da marca: o manual da FEQ usa Gotham (licença comercial) — o
  // substituto livre de mesma lógica geométrica é a Montserrat. O <link> é criado
  // AQUI, e não com @import no bridge.css, porque o convert_chars() do WordPress
  // troca "&" por "&#038;" em todo o post_content (inclusive dentro de <style>) e
  // quebraria "...&display=swap". Este bloco viaja em base64, imune a isso.
  // Sem a fonte (rede fora/CSP), o stack de fallback do CSS assume — nunca some texto.
  (function () {
    var l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = "https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap";
    document.head.appendChild(l);
  })();
  var modal = document.getElementById("modal");
  var form = document.getElementById("bridge-form");
  var tel = document.getElementById("whatsapp");
  var erroTel = document.getElementById("erro-tel");
  var erroOptin = document.getElementById("erro-optin");
  var TIMEOUT_MS = 1500;

  // Contagem regressiva da campanha. A página é publicada ESTÁTICA no WordPress,
  // então "faltam X dias" calculado no servidor congelaria no dia da publicação —
  // e passaria a mentir. Calcular no cliente mantém a urgência sempre verdadeira,
  // e o texto vira o prazo puro quando a campanha acaba.
  function prazoDias() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-prazo-dias]"), function (el) {
      var fim = new Date(el.getAttribute("data-fim") + "T23:59:59");
      if (isNaN(fim)) return;
      var dias = Math.ceil((fim - new Date()) / 86400000);
      if (dias > 1) el.textContent = "termina em " + dias + " dias";
      else if (dias === 1) el.textContent = "último dia amanhã";
      else if (dias === 0) el.textContent = "ÚLTIMO DIA";
      else {
        var barra = el.closest(".barra-promo");
        if (barra) barra.style.display = "none";       // campanha encerrada: some
      }
    });
  }
  prazoDias();

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

  // Guardado entre as duas etapas: a etapa 2 reencontra o lead pelo TELEFONE, e o
  // event_id é criado aqui para ser o MESMO no Pixel e na CAPI quando a etapa 2 for
  // concluída (a dedup exige o par idêntico).
  var LEAD = { nome: "", email: "", telefone: "", event_id: "" };
  var INSTAGRAM_URL = "https://www.instagram.com/extensaofequnicamp/";

  // ---- ETAPA 1: valida -> POST -> abre a etapa 2. SEM Pixel. -------------------
  // O lead que só deixou o contato ainda não é conversão: a etapa 1 alimenta a régua
  // de recuperação de 2h no backend, mas nada é reportado ao Meta aqui (spec 17 §7).
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var digitos = tel.value.replace(/\D/g, "");
    if (digitos.length < 10 || digitos.length > 11) {
      erroTel.classList.remove("hidden");
      tel.focus();
      return;
    }
    erroTel.classList.add("hidden");
    var email = (document.getElementById("email") || {}).value || "";
    var erroEmail = document.getElementById("erro-email");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      if (erroEmail) erroEmail.classList.remove("hidden");
      return;
    }
    if (erroEmail) erroEmail.classList.add("hidden");
    if (!document.getElementById("optin").checked) {   // LGPD: opt-in explícito, com feedback
      erroOptin.classList.remove("hidden");
      return;
    }
    erroOptin.classList.add("hidden");
    document.getElementById("continuar").disabled = true;
    tele.modal_enviado = 1;

    LEAD.nome = document.getElementById("nome").value.trim();
    LEAD.email = email.trim();
    LEAD.telefone = digitos;
    LEAD.event_id = uuid();

    var seguiu = false;
    function seguir() {
      if (seguiu) return;
      seguiu = true;
      enviarTele();
      // Curso SEM turma aberta: para aqui — não há inscrição a completar.
      if (!CFG.aberto) { confirmarEspera(); return; }
      abrirEtapa2();
    }
    function confirmarEspera() {
      var caixa = document.querySelector("#modal .modal-caixa") || form.parentNode;
      caixa.innerHTML =
        '<div class="espera-ok">' +
        '<p class="espera-check">✓</p>' +
        '<p class="espera-tit">Pronto, você está na lista!</p>' +
        '<p class="espera-sub">Este curso está entre turmas. Assim que a próxima for ' +
        'confirmada, a gente te avisa no WhatsApp. Enquanto isso, acompanhe as novidades ' +
        'no nosso Instagram <a href="' + INSTAGRAM_URL + '" target="_blank" rel="noopener">' +
        '@extensaofequnicamp</a>.</p></div>';
    }
    fetch(CFG.webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        nome: LEAD.nome, email: LEAD.email,
        whatsapp: digitos, curso_id: CFG.curso,
        event_id: LEAD.event_id, sessao_id: tele.sessao_id,
        fbp: cookie("_fbp"), fbc: cookie("_fbc"),
        url: window.location.href,
        utm: utmSalvos()          // origem da campanha, capturada em silêncio
      })
    }).then(seguir, seguir);
    setTimeout(seguir, TIMEOUT_MS);   // backend lento/fora: o lead não fica preso
  });

  function abrirEtapa2() {
    var m = document.getElementById("modal");
    var e2 = document.getElementById("etapa2");
    if (!e2) return;
    if (m) { m.classList.add("hidden"); m.classList.remove("flex"); }
    var alvo = document.getElementById("e2-nome");
    if (alvo) alvo.textContent = (LEAD.nome.split(" ")[0] || "");
    e2.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    window.scrollTo(0, 0);
  }

  // ---- ETAPA 2: valida -> Pixel (AQUI) -> POST -> obrigado --------------------
  var formE2 = document.getElementById("etapa2-form");
  if (formE2) formE2.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var erro = document.getElementById("erro-e2");
    var faltando = [];
    ["cpf", "nascimento", "escolaridade", "forma_pagamento"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el && !String(el.value).trim()) faltando.push(id);
    });
    if (faltando.length) {
      erro.textContent = "Preencha os campos obrigatórios antes de concluir.";
      erro.classList.remove("hidden");
      var p = document.getElementById(faltando[0]); if (p) p.focus();
      return;
    }
    erro.classList.add("hidden");
    document.getElementById("e2-enviar").disabled = true;

    // É AQUI que a conversão conta. Mesmo event_id da etapa 1 -> a CAPI (que recebe
    // este mesmo id no POST abaixo) desduplica contra este disparo do Pixel.
    if (CFG.pixel) {
      fbq("track", "InitiateCheckout",
          { content_ids: [CFG.curso], content_name: CFG.curso }, { eventID: LEAD.event_id });
    }
    var corpo = { whatsapp: LEAD.telefone, nome: LEAD.nome, email: LEAD.email,
                  curso_id: CFG.curso, event_id: LEAD.event_id,
                  fbp: cookie("_fbp"), fbc: cookie("_fbc"), url: window.location.href };
    new FormData(formE2).forEach(function (v, k) { if (v) corpo[k] = v; });

    var foi = false;
    function concluir() {
      if (foi) return;
      foi = true;
      // Página de obrigado e PARA AQUI — sem repassar o lead para o formulário da
      // Extecamp. Ele acabou de preencher a ficha completa AQUI; mandá-lo preencher
      // tudo de novo lá seria pedir a mesma coisa duas vezes.
      // A transcrição na Extecamp é trabalho do time (aba Verificação da Central).
      // O path continua começando com /thank-you-page porque é o que o gatilho de
      // conversão do Google Ads/GTM observa.
      window.location.href = "/thank-you-page-lp/";
    }
    fetch(CFG.webhook_inscricao, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify(corpo)
    }).then(concluir, concluir);
    setTimeout(concluir, 4000);   // ficha é maior que o modal: dá mais folga ao POST
  });
})();
