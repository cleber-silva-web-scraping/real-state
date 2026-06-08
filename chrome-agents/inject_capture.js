// MAIN-world interceptor: captura as chamadas graphql que a propria pagina de
// imovel do Zillow dispara, p/ extrair o sha256Hash + operationName atuais do
// persistedQuery (o hash muda com deploy do Zillow -> auto-captura por run).
// Roda em document_start, antes da pagina fazer os requests. Expoe os candidatos
// num atributo do <html> (DOM e compartilhado com o content script isolado).
(function () {
  // sentinel (no doc topo p/ o content.js do frame de cima ler).
  function topDoc() {
    try {
      const td = window.top && window.top.document;
      td.documentElement; // testa acesso (lanca se cross-origin)
      return td;
    } catch (_e) {
      return document;
    }
  }
  try { topDoc().documentElement.setAttribute("data-zgql-active", "1"); } catch (_e) {}
  const candidates = [];

  // publica no doc TOPO mesclando com o que outros frames ja escreveram (o /zg-graph
  // do detalhe sai do iframe/sub-app for-rent).
  function publish() {
    try {
      const td = topDoc();
      let existing = [];
      try { existing = JSON.parse(td.documentElement.getAttribute("data-zgql") || "[]"); } catch (_e) {}
      const key = (c) => `${c.method}|${c.op || ""}|${c.hash || ""}|${c.urlSnippet || ""}`;
      const seen = new Set(existing.map(key));
      const merged = existing.slice();
      for (const c of candidates) {
        if (!seen.has(key(c))) { seen.add(key(c)); merged.push(c); }
      }
      td.documentElement.setAttribute("data-zgql", JSON.stringify(merged.slice(-50)));
    } catch (_e) {}
  }

  function record(method, url, body) {
    try {
      const urlStr = String(url || "");
      const isGql = /graphql|zg-graph/i.test(urlStr);
      const bodyStr = body && typeof body === "string" ? body : "";
      const looksHash = /persistedQuery|sha256Hash/.test(urlStr + bodyStr);
      // so registra graphql/zg-graph / requests com persistedQuery
      if (!isGql && !looksHash) return;
      let hash = null;
      let op = null;
      let variables = null;
      try {
        const u = new URL(urlStr, location.href);
        op = u.searchParams.get("operationName") || op;
        const ext = u.searchParams.get("extensions");
        if (ext) hash = JSON.parse(ext)?.persistedQuery?.sha256Hash || hash;
        const vars = u.searchParams.get("variables");
        if (vars) variables = JSON.parse(vars);
      } catch (_e) {}
      if (bodyStr) {
        try {
          const b = JSON.parse(bodyStr);
          op = b.operationName || op;
          hash = b?.extensions?.persistedQuery?.sha256Hash || hash;
          variables = b.variables || variables;
        } catch (_e) {}
      }
      const vk = variables ? Object.keys(variables) : [];
      candidates.push({
        method: String(method || "GET").toUpperCase(),
        op,
        hash,
        endpoint: /zg-graph/i.test(urlStr) ? "zg-graph" : "graphql",
        urlSnippet: urlStr.slice(0, 120),
        hasZpid: vk.includes("zpid"),
        hasBuildingKey: vk.includes("buildingKey"),
        hasDeviceType: vk.includes("deviceType"),
        variablesKeys: vk,
      });
      publish();
    } catch (_e) {}
  }

  const origFetch = window.fetch;
  if (typeof origFetch === "function") {
    window.fetch = function (input, init) {
      try {
        const url = typeof input === "string" ? input : input && input.url;
        record((init && init.method) || (input && input.method) || "GET", url, init && init.body);
      } catch (_e) {}
      return origFetch.apply(this, arguments);
    };
  }

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__zg_method = method;
    this.__zg_url = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      record(this.__zg_method, this.__zg_url, body);
    } catch (_e) {}
    return origSend.apply(this, arguments);
  };
})();
