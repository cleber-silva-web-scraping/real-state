console.log("Zillow content script running!");
try { new Image().src = "http://localhost:8000/health?diag=top-level&url=" + encodeURIComponent(location.href); } catch (_e) {}

const HUMAN_SCROLL_STEPS_MIN = 2;
const HUMAN_SCROLL_STEPS_MAX = 4;
const HUMAN_SCROLL_PX_MIN = 60;
const HUMAN_SCROLL_PX_MAX = 240;
const HUMAN_SCROLL_VIEWPORT_MIN_RATIO = 0.08;
const HUMAN_SCROLL_VIEWPORT_MAX_RATIO = 0.34;
const HUMAN_SCROLL_WAIT_MIN_MS = 550;
const HUMAN_SCROLL_WAIT_MAX_MS = 1700;
const CLICK_BEFORE_MIN_MS = 140;
const CLICK_BEFORE_MAX_MS = 380;
const CLICK_AFTER_MIN_MS = 80;
const CLICK_AFTER_MAX_MS = 260;

try {
  chrome.runtime.sendMessage({ type: "cba_boot" }, () => {
    void chrome.runtime.lastError;
  });
} catch (_error) {}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomInt(min, max) {
  const low = Math.ceil(min);
  const high = Math.floor(max);
  return Math.floor(Math.random() * (high - low + 1)) + low;
}

async function sleepRandom(minMs, maxMs) {
  const low = Math.max(0, Number(minMs) || 0);
  const high = Math.max(low, Number(maxMs) || low);
  await sleep(randomInt(low, high));
}

function getScrollDistanceBounds() {
  const viewportHeight = Math.max(Number(window.innerHeight) || 0, 600);
  const dynamicMin = Math.round(viewportHeight * HUMAN_SCROLL_VIEWPORT_MIN_RATIO);
  const dynamicMax = Math.round(viewportHeight * HUMAN_SCROLL_VIEWPORT_MAX_RATIO);
  const minDistance = Math.max(HUMAN_SCROLL_PX_MIN, Math.min(dynamicMin, dynamicMax));
  const maxDistance = Math.max(minDistance, Math.max(HUMAN_SCROLL_PX_MAX, dynamicMax));
  return { minDistance, maxDistance };
}

async function performHumanLikeScroll() {
  const doc = document.documentElement;
  const body = document.body;
  const totalHeight = Math.max(Number(doc?.scrollHeight || 0), Number(body?.scrollHeight || 0));
  if (totalHeight <= window.innerHeight + 20) return;
  const steps = randomInt(HUMAN_SCROLL_STEPS_MIN, HUMAN_SCROLL_STEPS_MAX);
  const { minDistance, maxDistance } = getScrollDistanceBounds();
  for (let index = 0; index < steps; index += 1) {
    const direction = Math.random() < 0.8 ? 1 : -1;
    const distance = randomInt(minDistance, maxDistance) * direction;
    try { window.scrollBy({ top: distance, behavior: "smooth" }); }
    catch (_error) { window.scrollBy(0, distance); }
    await sleepRandom(HUMAN_SCROLL_WAIT_MIN_MS, HUMAN_SCROLL_WAIT_MAX_MS);
  }
}

async function waitFor(checkFn, options = {}) {
  const timeoutMs = options.timeoutMs ?? 15000;
  const intervalMinMs = options.intervalMinMs ?? options.intervalMs ?? 200;
  const intervalMaxMs = options.intervalMaxMs ?? options.intervalMs ?? intervalMinMs;
  const errorMessage = options.errorMessage ?? "wait timeout";
  const start = Date.now();
  while (Date.now() - start <= timeoutMs) {
    const value = checkFn();
    if (value) return value;
    await sleepRandom(intervalMinMs, intervalMaxMs);
  }
  throw new Error(errorMessage);
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function parseUrlOrNull(value) {
  try { return new URL(String(value || ""), window.location.href); }
  catch (_error) { return null; }
}

function pageFromUrl(value) {
  const parsed = parseUrlOrNull(value);
  if (!parsed) return 1;
  const rawPage = Number.parseInt(parsed.searchParams.get("page") || "1", 10);
  if (!Number.isFinite(rawPage) || rawPage <= 0) return 1;
  return rawPage;
}

function isElementVisible(element) {
  if (!(element instanceof Element)) return false;
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden") return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

async function triggerClick(element) {
  if (!element) return;
  try { element.scrollIntoView({ block: "center", inline: "center", behavior: "auto" }); }
  catch (_error) {}
  await sleepRandom(CLICK_BEFORE_MIN_MS, CLICK_BEFORE_MAX_MS);
  const rect = element.getBoundingClientRect();
  const clientX = Math.round(rect.left + rect.width / 2);
  const clientY = Math.round(rect.top + rect.height / 2);
  const mouseOptions = {
    bubbles: true, cancelable: true, composed: true, view: window,
    clientX, clientY, button: 0,
  };
  if (typeof PointerEvent === "function") {
    element.dispatchEvent(new PointerEvent("pointerdown", { ...mouseOptions, pointerId: 1, pointerType: "mouse" }));
    element.dispatchEvent(new PointerEvent("pointerup", { ...mouseOptions, pointerId: 1, pointerType: "mouse" }));
  }
  element.dispatchEvent(new MouseEvent("mousedown", mouseOptions));
  element.dispatchEvent(new MouseEvent("mouseup", mouseOptions));
  element.dispatchEvent(new MouseEvent("click", mouseOptions));
  element.click();
  await sleepRandom(CLICK_AFTER_MIN_MS, CLICK_AFTER_MAX_MS);
}

function normalizePropertyPath(href) {
  let raw = String(href || "").trim();
  if (!raw || raw.startsWith("#") || raw.startsWith("javascript:")) return null;
  raw = raw.replace(/&amp;/gi, "&");
  if (raw.startsWith("//")) raw = `https:${raw}`;
  let path = "";
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    const parsed = parseUrlOrNull(raw);
    if (!parsed || !String(parsed.hostname || "").toLowerCase().includes("zillow.com")) return null;
    path = parsed.pathname || "";
  } else {
    path = raw.split("?")[0].split("#")[0];
  }
  if (!path.startsWith("/")) path = `/${path}`;
  path = path.split("?")[0].split("#")[0].trim();
  if (!path || path === "/") return null;
  return path;
}

function collectPropertyPathsFromListing(selector) {
  const elements = [...document.querySelectorAll(selector)];
  const seen = new Set();
  const paths = [];
  for (const element of elements) {
    const anchor = element instanceof HTMLAnchorElement ? element : element.querySelector("a");
    if (!anchor) continue;
    const path = normalizePropertyPath(anchor.getAttribute("href") || anchor.href || "");
    if (!path || seen.has(path)) continue;
    seen.add(path);
    paths.push(path);
  }
  return paths;
}

function findNextPageAnchor(currentPage) {
  const links = [...document.querySelectorAll("a[href*='_p'], a[href*='page=']")];
  const visible = links.filter((a) => isElementVisible(a));
  const pool = visible.length > 0 ? visible : links;
  const byPage = pool.find((a) => {
    const h = a.getAttribute("href") || a.href || "";
    const match = h.match(/(\d+)_p\/?$/);
    return match && Number(match[1]) === currentPage + 1;
  });
  if (byPage) return byPage;
  const byText = pool.find((a) => {
    const t = normalizeText(a.textContent);
    return t === "next" || t.includes("next");
  });
  return byText || null;
}

function parseZillowNextData() {
  const scripts = document.querySelectorAll("script");
  for (const script of scripts) {
    const text = script.textContent || "";
    if (!text.includes("__NEXT_DATA__")) continue;
    const jsonText = text.replace("window.__INITIAL_STATE__", "")
      .replace("__NEXT_DATA__", "")
      .replace(/^\s*=?\s*/, "")
      .trim();
    if (!jsonText.endsWith("}")) continue;
    try { return JSON.parse(jsonText); }
    catch (_error) { continue; }
  }
  return null;
}

function parseZillowData() {
  const nextData = parseZillowNextData();
  if (nextData?.props?.pageProps) return nextData.props.pageProps;
  for (const key of Object.keys(window)) {
    if (key.startsWith("__")) continue;
    const val = window[key];
    if (val && typeof val === "object" && (val.cat1 || val.cat2 || val.searchResults || val.property)) return val;
  }
  return null;
}

async function processListingsByClick(params) {
  const productSelector = typeof params?.productSelector === "string" && params.productSelector.trim() !== ""
    ? params.productSelector
    : "a[href*='/homedetails/'], article[data-test='property-card'] a";

  const listingUrl = window.location.href;
  const listingParsed = parseUrlOrNull(listingUrl);
  const listingPath = listingParsed?.pathname || window.location.pathname;
  const requestedPage = Number.parseInt(String(params?.currentPage || ""), 10);
  const listingPage = Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage : pageFromUrl(listingUrl);

  await waitFor(() => (document.readyState === "interactive" || document.readyState === "complete") && Boolean(document.querySelector(productSelector)), {
    timeoutMs: 26000, intervalMinMs: 180, intervalMaxMs: 420, errorMessage: "listing page not ready",
  });

  await performHumanLikeScroll();
  const processedPathsRaw = Array.isArray(params?.processedPaths) ? params.processedPaths : [];
  const processedSet = new Set(processedPathsRaw.filter((item) => Boolean(item)));
  const propertyPaths = collectPropertyPathsFromListing(productSelector);
  const processedProperties = [];

  for (const path of propertyPaths) {
    if (processedSet.has(path)) {
      processedProperties.push({ path, skipped: true, error: "" });
      continue;
    }

    const itemResult = { path, skipped: false, error: "", initial_config: null };

    try {
      const anchor = await waitFor(() => {
        const all = [...document.querySelectorAll(productSelector)];
        for (const el of all) {
          const a = el instanceof HTMLAnchorElement ? el : el.querySelector("a");
          if (!a) continue;
          const p = normalizePropertyPath(a.getAttribute("href") || a.href || "");
          if (p === path && isElementVisible(a)) return a;
        }
        return null;
      }, { timeoutMs: 10000, intervalMinMs: 140, intervalMaxMs: 360, errorMessage: `anchor not found: ${path}` });

      if (anchor instanceof HTMLAnchorElement) anchor.target = "_self";
      await performHumanLikeScroll();
      await triggerClick(anchor);
      await sleepRandom(850, 1900);

      await waitFor(() => {
        const notListing = !(parseUrlOrNull(window.location.href)?.pathname === listingPath);
        return notListing && Boolean(normalizePropertyPath(window.location.href));
      }, { timeoutMs: 26000, intervalMinMs: 180, intervalMaxMs: 440, errorMessage: `navigation timeout: ${path}` });

      await waitFor(() => document.readyState === "interactive" || document.readyState === "complete", {
        timeoutMs: 26000, intervalMinMs: 160, intervalMaxMs: 360, errorMessage: "document not ready",
      });

      await performHumanLikeScroll();

      const zillowData = await waitFor(() => parseZillowData(), {
        timeoutMs: 22000, intervalMinMs: 180, intervalMaxMs: 420, errorMessage: `missing data: ${path}`,
      }).catch(() => null);

      if (zillowData && typeof zillowData === "object") {
        itemResult.initial_config = zillowData;
        processedSet.add(path);
      } else {
        itemResult.error = "missing property data";
      }
    } catch (error) {
      itemResult.error = String(error?.message || error || "unknown click error");
    } finally {
      window.history.back();
      await waitFor(() => parseUrlOrNull(window.location.href)?.pathname === listingPath && Boolean(document.querySelector(productSelector)), {
        timeoutMs: 26000, intervalMinMs: 180, intervalMaxMs: 440, errorMessage: "listing not restored",
      }).catch(() => {
        window.location.href = listingUrl;
      });
      await sleepRandom(240, 720);
      processedProperties.push(itemResult);
    }
  }

  return { product_paths: propertyPaths, processed_products: processedProperties };
}

async function handleMessage(message) {
  if (message?.action === "simulate_activity") return { done: true };

  if (message?.action === "api_fetch") {
    // Request same-origin na pagina ja autenticada (herda cookies httpOnly do
    // PerimeterX, TLS do Chrome, UA e sensor). Sem fingerprint mismatch.
    try {
      const p = message?.param || {};
      const method = String(p.method || "PUT").toUpperCase();
      const headers = { accept: "*/*", "content-type": "application/json" };
      if (p.client_id) headers["client-id"] = p.client_id; // ex vertical-living
      const opts = { method, credentials: "include", headers };
      if (method !== "GET" && method !== "HEAD") {
        opts.body = JSON.stringify(p.body || {});
      }
      const t0 = performance.now();
      const r = await fetch(p.endpoint, opts);
      const text = await r.text();
      const metrics = {
        fetch_ms: Math.round(performance.now() - t0),
        json_bytes: new Blob([text]).size,
      };
      // hash expirado/safelist (diferente de bloqueio PX) -> recapturar
      const expired = /PERSISTED_QUERY_NOT_IN_LIST|QUERY_NOT_IN_SAFELIST/i.test(text);
      const blocked =
        r.status !== 200 ||
        /has been denied|Request blocked|px-captcha|perimeterx/i.test(text);
      if (expired)
        return { data: null, error: false, expired: true, metrics, http_status: r.status,
                 snippet: String(text).slice(0, 200) };
      if (blocked)
        return { data: null, error: false, blocked: true, metrics, http_status: r.status,
                 snippet: String(text).slice(0, 200) };
      try {
        return { data: JSON.parse(text), error: false, metrics };
      } catch (_e) {
        return { data: null, error: true, metrics };
      }
    } catch (error) {
      return { data: null, error: true, fetch_error: String(error?.message || error) };
    }
  }

  if (message?.action === "extract_detail") {
    // Detalhe do imovel vem embutido na pagina (SSR). Pega o script
    // <script id="__NEXT_DATA__"> por ID (o conteudo e JSON puro, nao contem a
    // string "__NEXT_DATA__"), com fallback p/ parseZillowData.
    function extractEmbedded() {
      const nd = document.getElementById("__NEXT_DATA__");
      if (nd && nd.textContent) {
        try {
          const j = JSON.parse(nd.textContent);
          return j?.props?.pageProps || j;
        } catch (_e) {}
      }
      return parseZillowData();
    }
    let data = null;
    const t0 = Date.now();
    while (Date.now() - t0 < 9000) {
      data = extractEmbedded();
      if (data && typeof data === "object") break;
      await sleep(400);
    }
    return { data: { property: data }, error: !data };
  }

  if (message?.action === "capture_query") {
    // O GET /graphql full-property (751a1453, zpid+deviceType) so dispara via CLIQUE
    // REAL num card (SPA), NAO no load da homedetails (que e SSR). content.js acha
    // cards de casa, manda coords -> background -> /v1/click -> hash_clicker clica de
    // verdade -> modal/SPA abre -> GET dispara -> interceptor publica em data-zgql.
    function dismissOverlays() {
      try {
        for (const b of document.querySelectorAll("button,[role='button']")) {
          const t = (b.textContent || "").trim().toLowerCase();
          const al = (b.getAttribute("aria-label") || "").toLowerCase();
          if (t === "got it" || t === "no thanks" || al.includes("close") || al.includes("dismiss")) {
            try { b.click(); } catch (_e) {}
          }
        }
        document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true }));
      } catch (_e) {}
    }
    try {
      // SO cards de CASA (/homedetails/). A lista do Zillow e VIRTUALIZADA e rola num
      // container proprio (nao a window); os apes-comunidade ficam no topo e as casas
      // abaixo. Rola o CONTAINER da lista ate casas entrarem no DOM, ai clica.
      const sel = "a[href*='/homedetails/']";
      function scrollList(dy) {
        const card = document.querySelector("[data-test='property-card'], article, [id*='grid-search']");
        let el = card;
        while (el && el !== document.body) {
          const oy = getComputedStyle(el).overflowY;
          if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight + 40) {
            el.scrollTop += dy;
            return true;
          }
          el = el.parentElement;
        }
        window.scrollBy(0, dy);
        return false;
      }
      let all = [];
      const ct0 = Date.now();
      while (Date.now() - ct0 < 16000) {
        dismissOverlays();
        all = [...document.querySelectorAll(sel)];
        if (all.length > 0) break;
        scrollList(700);
        await sleep(700);
      }
      dismissOverlays();
      if (all.length > 0) {
        try { all[0].scrollIntoView({ block: "center" }); } catch (_e) {}
        await sleep(700);
      }
      const yOffset = Math.max(0, (window.outerHeight || 0) - (window.innerHeight || 0));
      const ih = window.innerHeight || 800;
      const coords = [];
      for (const el of [...document.querySelectorAll(sel)]) {
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 20 || r.top < 60 || r.bottom > ih) continue; // visivel
        coords.push([
          Math.round((window.screenX || 0) + r.left + r.width * 0.5),
          Math.round((window.screenY || 0) + yOffset + r.top + r.height * 0.4),
        ]);
        if (coords.length >= 6) break;
      }
      try { chrome.runtime.sendMessage({ type: "queue_clicks", coords }, () => void chrome.runtime.lastError); } catch (_e) {}
    } catch (_e) {}
    let candidates = [];
    const t0 = Date.now();
    while (Date.now() - t0 < 24000) {
      try {
        candidates = JSON.parse(document.documentElement.getAttribute("data-zgql") || "[]");
      } catch (_e) {}
      if (candidates.some((c) => c && c.hash && c.hasZpid)) break; // hash da casa apareceu
      await sleep(500);
    }
    const interceptor_active =
      document.documentElement.getAttribute("data-zgql-active") === "1";
    return { data: { candidates, interceptor_active, page_url: location.href }, error: false };
  }

  if (message?.action === "page_metrics") {
    // Banda total do load da pagina (scripts/css/img) via Resource Timing.
    let page_bytes = 0;
    let page_load_ms = 0;
    let resource_count = 0;
    try {
      const nav = performance.getEntriesByType("navigation")[0];
      if (nav) {
        page_bytes += nav.transferSize || 0;
        page_load_ms = Math.round(nav.duration || 0);
      }
      for (const e of performance.getEntriesByType("resource")) {
        page_bytes += e.transferSize || 0;
        resource_count += 1;
      }
    } catch (_e) {}
    return { data: { page_bytes, page_load_ms, resource_count }, error: false };
  }

  if (message?.action === "humanize") {
    // Comportamento humano no refresh: scroll lento sobe/desce + clicar num anuncio.
    try {
      await performHumanLikeScroll();
      const sel = "a[href*='/homedetails/'], article[data-test='property-card'] a";
      const cards = [...document.querySelectorAll(sel)].filter(isElementVisible);
      if (cards.length > 0) {
        const el = cards[randomInt(0, cards.length - 1)];
        if (el instanceof HTMLAnchorElement) el.target = "_self";
        await triggerClick(el);
        await sleepRandom(1500, 3500);
        try { window.history.back(); } catch (_e) {}
        await sleepRandom(800, 1800);
      }
      return { data: { humanized: true }, error: false };
    } catch (error) {
      return { data: { humanized: false }, error: true };
    }
  }

  if (message?.action === "click_collect") {
    try {
      const data = await processListingsByClick(message?.param || {});
      return { data, error: false };
    } catch (error) {
      return { data: { product_paths: [], processed_products: [], error: String(error?.message || error || "click_collect failed") }, error: true };
    }
  }

  await performHumanLikeScroll();

  if (message?.action === "variable") {
    const params = Array.isArray(message.param) ? message.param : [];
    const data = {};
    for (const param of params) {
      if (param === "__INITIAL_CONFIG__") data[param] = parseZillowData();
    }
    return { data, error: false };
  }

  if (message?.action === "select") {
    const params = Array.isArray(message.param) ? message.param : [];
    try {
      const data = params.reduce((acc, selector) => {
        acc[selector] = [...document.querySelectorAll(selector)].map((e) => e.outerHTML);
        return acc;
      }, {});
      return { data, error: false };
    } catch (_error) {
      return { data: null, error: true };
    }
  }

  if (message?.action === "json") {
    try { return { data: JSON.parse(document.body.innerText), error: false }; }
    catch (_error) { return { data: null, error: true }; }
  }

  return { data: null, error: true };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message).then((response) => sendResponse(response)).catch((_error) => sendResponse({ data: null, error: true }));
  return true;
});


