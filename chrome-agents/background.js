const apiKey = "30b7b231-be58-4d7e-859e-753c52d10173";
const backendBaseUrl = "http://localhost:8000";
const MESSAGE_RETRY_LIMIT = 25;
const MESSAGE_RETRY_DELAY_MS = 1500;
// Caminho quente = fetch graphql puro (~0.4s, footprint baixo), NAO render de
// pagina -> nao precisa do delay humano de 3-7s. Dispara quase back-to-back.
const NEXT_URL_DELAY_MIN_MS = 80;
const NEXT_URL_DELAY_MAX_MS = 250;
// Reload da pagina (render) so de vez em quando, p/ refrescar o token PX.
const RELOAD_EVERY = 50;
// Pausa longa humana: a cada 20-30min, para 5min.
const PAUSE_INTERVAL_MIN_MS = 20 * 60 * 1000;
const PAUSE_INTERVAL_MAX_MS = 30 * 60 * 1000;
const PAUSE_DURATION_MS = 5 * 60 * 1000;
let extensionStarted = false;
let apiQueryCount = 0;
let lastPageMetrics = null; // metricas do ultimo load de pagina (atribuidas 1x)
// MV3: o service worker morre quando ocioso. NAO usar setTimeout longo (mata o
// loop). Pausa = gate por timestamp em chrome.storage; watchdog via chrome.alarms
// ressuscita o loop. activeTabId/lastActivity guiam o watchdog.
let activeTabId = null;
let lastActivity = Date.now();
const WATCHDOG_STALL_MS = 90 * 1000;

function randomIntMs(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function markActivity() {
  lastActivity = Date.now();
}

// Retorna true se deve PAUSAR agora (chamador nao busca task).
async function maybeLongPause() {
  const now = Date.now();
  const r = await chrome.storage.local.get(["pausedUntil", "nextPauseAt"]);
  let pausedUntil = r.pausedUntil || 0;
  let nextPauseAt = r.nextPauseAt || now + randomIntMs(PAUSE_INTERVAL_MIN_MS, PAUSE_INTERVAL_MAX_MS);
  if (now < pausedUntil) return true; // ainda dentro da pausa
  if (now >= nextPauseAt) {
    pausedUntil = now + PAUSE_DURATION_MS;
    nextPauseAt = now + randomIntMs(PAUSE_INTERVAL_MIN_MS, PAUSE_INTERVAL_MAX_MS);
    await chrome.storage.local.set({ pausedUntil, nextPauseAt });
    console.log("[pause] pausa humana de 5min");
    return true;
  }
  await chrome.storage.local.set({ nextPauseAt });
  return false;
}

function isZillowUrl(u) {
  return /^https?:\/\/[^/]*zillow\.com\//i.test(String(u || ""));
}

function payloadIsApiFetch(payload) {
  return (
    Array.isArray(payload?.actions) &&
    payload.actions.some((a) => a?.type === "api_fetch")
  );
}

function generateBrowserID() {
  const browserType = navigator.userAgent;
  const uniqueID = crypto.randomUUID();

  const browserData = {
    browser_id: uniqueID,
    browser_type: browserType,
  };

  return new Promise((resolve) => {
    chrome.storage.local.set(browserData, () => resolve(browserData));
  });
}

async function ensureBrowserID() {
  let result = await chrome.storage.local.get(["browser_id", "browser_type"]);
  if (!result.browser_id || !result.browser_type) {
    await generateBrowserID();
    result = await chrome.storage.local.get(["browser_id", "browser_type"]);
  }
  return result;
}

function logError(error) {
  console.error(error);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomInt(min, max) {
  const low = Math.ceil(min);
  const high = Math.floor(max);
  return Math.floor(Math.random() * (high - low + 1)) + low;
}

function scheduleNextUrl(tabId) {
  markActivity();
  const delay = randomInt(NEXT_URL_DELAY_MIN_MS, NEXT_URL_DELAY_MAX_MS);
  console.log(`Esperando ${delay}ms antes do próximo getNextUrl...`);
  setTimeout(() => {
    getNextUrl(tabId);
  }, delay);
}

function shouldRetrySendMessage(errorMessage) {
  const text = String(errorMessage || "").toLowerCase();
  return (
    text.includes("receiving end does not exist") ||
    text.includes("could not establish connection") ||
    text.includes("message port closed") ||
    text.includes("message channel closed") ||
    text.includes("before a response was received")
  );
}

function getTabById(tabId) {
  return new Promise((resolve) => {
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError) {
        resolve(null);
        return;
      }
      resolve(tab || null);
    });
  });
}

async function waitForTabComplete(tabId, maxWaitMs = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < maxWaitMs) {
    const tab = await getTabById(tabId);
    if (!tab) {
      return { ready: false, tab: null };
    }
    const currentUrl = String(tab.url || "");
    if (currentUrl.startsWith("chrome-error://") || tab.status === "complete") {
      return { ready: true, tab };
    }
    await sleep(500);
  }

  const tab = await getTabById(tabId);
  return { ready: false, tab };
}

function isInjectableUrl(url) {
  const normalized = String(url || "").toLowerCase();
  return normalized.startsWith("http://") || normalized.startsWith("https://");
}

async function injectContentScript(tabId) {
  return new Promise((resolve) => {
    chrome.scripting.executeScript(
      {
        target: { tabId },
        files: ["content.js"],
      },
      () => {
        if (chrome.runtime.lastError) {
          resolve({
            ok: false,
            error: chrome.runtime.lastError.message || String(chrome.runtime.lastError),
          });
          return;
        }
        resolve({ ok: true, error: "" });
      },
    );
  });
}

async function runActionWithScripting(tabId, actionType, actionParams, isErrorRules) {
  return new Promise((resolve) => {
    chrome.scripting.executeScript(
      {
        target: { tabId },
        func: (type, params, rules) => {
          const hasBlocking = Array.isArray(rules)
            ? rules.some(
                (rule) =>
                  rule &&
                  rule.type === "exist" &&
                  typeof rule.selector === "string" &&
                  document.body?.innerHTML?.includes(rule.selector),
              )
            : false;
          if (hasBlocking) {
            return { data: [], error: true };
          }

          const safeParams = Array.isArray(params) ? params : [];
          if (type === "select") {
            try {
              const data = safeParams.reduce((acc, selector) => {
                acc[selector] = [...document.querySelectorAll(selector)].map(
                  (element) => element.outerHTML,
                );
                return acc;
              }, {});
              return { data, error: false };
            } catch (_error) {
              return { data: null, error: true };
            }
          }

          if (type === "variable") {
            const data = {};
            for (const param of safeParams) {
              if (param !== "__INITIAL_CONFIG__") {
                continue;
              }
              const scripts = document.querySelectorAll("script");
              for (const script of scripts) {
                const text = script.textContent || "";
                if (!text.includes("window.__INITIAL_CONFIG__")) {
                  continue;
                }
                try {
                  data[param] = JSON.parse(
                    text.replace("window.__INITIAL_CONFIG__ = ", ""),
                  );
                } catch (_error) {
                  data[param] = null;
                }
                break;
              }
            }
            return { data, error: false };
          }

          if (type === "json") {
            try {
              return { data: JSON.parse(document.body.innerText), error: false };
            } catch (_error) {
              return { data: null, error: true };
            }
          }

          return { data: null, error: true };
        },
        args: [actionType, actionParams, isErrorRules],
      },
      (results) => {
        if (chrome.runtime.lastError) {
          resolve({
            ok: false,
            response: null,
            runtimeError: chrome.runtime.lastError.message || String(chrome.runtime.lastError),
          });
          return;
        }

        const response = Array.isArray(results) ? results[0]?.result : null;
        resolve({
          ok: true,
          response: response || { data: null, error: true },
          runtimeError: "",
        });
      },
    );
  });
}

async function sendMessageWithRetry(tabId, message, maxRetries = MESSAGE_RETRY_LIMIT) {
  for (let attempt = 1; attempt <= maxRetries; attempt += 1) {
    const result = await new Promise((resolve) => {
      chrome.tabs.sendMessage(tabId, message, (response) => {
        const runtimeError = chrome.runtime.lastError;
        if (runtimeError) {
          resolve({
            ok: false,
            response: null,
            runtimeError: runtimeError.message || String(runtimeError),
          });
          return;
        }
        resolve({ ok: true, response, runtimeError: "" });
      });
    });

    if (result.ok) {
      return result;
    }

    if (attempt >= maxRetries || !shouldRetrySendMessage(result.runtimeError)) {
      return result;
    }

    const tab = await getTabById(tabId);
    if (tab && isInjectableUrl(tab.url)) {
      const injectResult = await injectContentScript(tabId);
      if (!injectResult.ok) {
        console.log(
          `content.js injection failed on retry ${attempt}/${maxRetries}: ${injectResult.error}`,
        );
      }
    }

    console.log(
      `sendMessage retry ${attempt}/${maxRetries} after error: ${result.runtimeError}`,
    );
    await sleep(MESSAGE_RETRY_DELAY_MS);
  }

  return { ok: false, response: null, runtimeError: "sendMessage retry limit reached" };
}

function isEmptySelectResult(data) {
  if (!data || typeof data !== "object") return true;
  const values = Object.values(data);
  if (values.length === 0) return true;
  return values.every((value) => Array.isArray(value) && value.length === 0);
}

async function sendDataToBackend(data) {
  const { browser_id, browser_type } = await ensureBrowserID();
  try {
    const response = await fetch(`${backendBaseUrl}/v1/process`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
      },
      body: JSON.stringify({
        browser_id: browser_id,
        browser_type: browser_type,
        ...data,
      }),
    });
    await response.json();
  } catch (error) {
    logError({ error: "Error on try send data to backend", response: error });
  }
}

async function getNextUrl(tabId) {
  activeTabId = tabId;
  markActivity();
  if (await maybeLongPause()) {
    // em pausa: nao busca; re-checa em 30s (e o watchdog cobre se o SW morrer).
    setTimeout(() => getNextUrl(tabId), 30000);
    return;
  }
  const { browser_id } = await ensureBrowserID();
  try {
    const response = await fetch(
      `${backendBaseUrl}/v1/next/${browser_id}`,
      {
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
        },
      },
    );
    if (response.status !== 200) {
      const retryDelay = randomInt(NEXT_URL_DELAY_MIN_MS, NEXT_URL_DELAY_MAX_MS);
      console.log(`No url available, retry in ${retryDelay}ms`);
      setTimeout(() => {
        getNextUrl(tabId);
      }, retryDelay);
    } else {
      if (response.ok) {
        const payload = await response.json();
        navigateAndExecuteActions(tabId, payload);
      } else {
        logError({ error: "Error on try next url from backend", response });
      }
    }
  } catch (error) {
    logError({ error: "Error on try next url from backend", response: error });
    const retryDelay = randomInt(NEXT_URL_DELAY_MIN_MS, NEXT_URL_DELAY_MAX_MS);
    console.log(`Try again in ${retryDelay}ms`);
    await new Promise((resolve) => setTimeout(resolve, retryDelay));
    getNextUrl(tabId);
  }
}

async function navigateAndExecuteActions(tabId, payload) {
  const { url } = payload;
  if (!url) return;

  // api_fetch: o dado vem do fetch same-origin, nao do load da pagina. Evita
  // recarregar a MESMA pagina toda query -> so recarrega a cada RELOAD_EVERY (ou
  // se a aba saiu da zillow.com). No reload faz humanizacao (scroll+click) e mede
  // a banda do load (page_metrics).
  if (payloadIsApiFetch(payload)) {
    apiQueryCount += 1;
    const cur = (await getTabById(tabId))?.url || "";
    const shouldReload = !isZillowUrl(cur) || apiQueryCount % RELOAD_EVERY === 0;
    if (!shouldReload) {
      executeActions(tabId, payload);
      return;
    }
    console.log(`[reload] query #${apiQueryCount} -> reload + humanize`);
    await chrome.tabs.update(tabId, { url });
    await waitForTabComplete(tabId, 30000);
    await sleep(Math.floor(Math.random() * 1000) + 1000);
    try {
      const pm = await sendMessageWithRetry(tabId, { action: "page_metrics" });
      lastPageMetrics = pm.ok ? pm.response?.data || null : null;
    } catch (_e) {
      lastPageMetrics = null;
    }
    try {
      await sendMessageWithRetry(tabId, { action: "humanize" });
    } catch (_e) {}
    // humanize pode ter clicado num anuncio e voltado; garante aba na zillow.com
    const cur2 = (await getTabById(tabId))?.url || "";
    if (!isZillowUrl(cur2)) {
      await chrome.tabs.update(tabId, { url });
      await waitForTabComplete(tabId, 30000);
    }
    executeActions(tabId, payload);
    return;
  }

  console.log("Actions: url: ", url.split("/")[4]);
  await chrome.tabs.update(tabId, { url });
  const readyResult = await waitForTabComplete(tabId, 30000);
  if (!readyResult.ready) {
    console.log("Tab not complete after 30s. Continuing with best effort.");
  }

  // User-requested pacing: wait 1-2s after page is ready/best-effort ready.
  const readyDelay = Math.floor(Math.random() * 1000) + 1000;
  await sleep(readyDelay);

  const tab = await getTabById(tabId);
  if (!tab) {
    await sendDataToBackend({
      slug: url,
      step: payload?.step,
      content: {
        ...payload,
        nav_error: "tab not found after navigation",
      },
      id: payload?.id,
      queue_name: payload?.queue_name,
      start_str: payload?.start_str,
      profile: "is-error",
      process_id: payload?.process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  const currentUrl = tab?.url ?? "";
  if (currentUrl.startsWith("chrome-error://")) {
    await sendDataToBackend({
      slug: url,
      step: payload?.step,
      content: payload,
      id: payload?.id,
      queue_name: payload?.queue_name,
      start_str: payload?.start_str,
      profile: "is-error",
      process_id: payload?.process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  executeActions(tabId, payload);
}

async function handleApiFetchResult(tabId, payload, sendResult) {
  const { url, step, id, queue_name, start_str, profile, process_id } = payload;
  const resp = sendResult.ok ? sendResult.response : null;

  // Bloqueio (PX) ou falha de envio: recarrega a pagina de busca p/ forcar o
  // "Press & Hold" a renderizar (o captcha solver, processo separado, resolve na
  // tela) e reporta is-error -> backend re-emite a MESMA query apos RETRY_DELAY.
  if (!sendResult.ok || !resp || resp.blocked) {
    console.log("api_fetch blocked/failed -> reload search page to render captcha");
    try {
      await chrome.tabs.update(tabId, { url: payload.url });
      await waitForTabComplete(tabId, 30000);
    } catch (_e) {}
    await sendDataToBackend({
      slug: url,
      step,
      content: {
        reason: "px_block",
        blocked: true,
        send_error: sendResult.runtimeError || "",
        http_status: resp?.http_status,
        snippet: resp?.snippet,
      },
      id,
      queue_name,
      start_str,
      profile: "is-error",
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  // Hash expirou (PERSISTED_QUERY_NOT_IN_LIST) -> nao e PX. Avisa o backend p/
  // recapturar (profile normal, sem reload).
  if (resp.expired) {
    await sendDataToBackend({
      slug: url,
      step,
      content: { expired: true, http_status: resp.http_status },
      id,
      queue_name,
      start_str,
      profile,
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  // Resposta veio mas nao parseou como JSON esperado.
  if (resp.error) {
    await sendDataToBackend({
      slug: url,
      step,
      content: { reason: "api_parse_error", fetch_error: resp.fetch_error || "" },
      id,
      queue_name,
      start_str,
      profile: "is-error",
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  // Sucesso: manda o JSON do searchResults pro backend extrair os detailUrl.
  const pageMetrics = lastPageMetrics;
  lastPageMetrics = null; // atribui a banda do load uma unica vez
  await sendDataToBackend({
    slug: url,
    step,
    content: resp.data,
    metrics: resp.metrics || null,
    page_metrics: pageMetrics,
    id,
    queue_name,
    start_str,
    profile,
    process_id,
    screenshot: "",
  });
  scheduleNextUrl(tabId);
}

async function executeActions(tabId, payload) {
  const { url, actions, step, id, queue_name, start_str, profile, process_id } =
    payload;

  const serverAction = Array.isArray(actions)
    ? actions.find((action) =>
        [
          "select",
          "json",
          "variable",
          "click_collect",
          "api_fetch",
          "capture_query",
          "extract_detail",
        ].includes(action?.type),
      )
    : null;

  if (!serverAction) {
    await sendDataToBackend({
      slug: url,
      step,
      content: payload,
      id,
      queue_name,
      start_str,
      profile: "is-error",
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  const message = {
    action: serverAction.type,
    param:
      serverAction.type === "api_fetch"
        ? {
            method: serverAction.method,
            endpoint: serverAction.endpoint,
            body: serverAction.body,
            client_id: serverAction.client_id,
          }
        : serverAction.selector,
    isError: payload?.isError,
  };

  const clickCollectProductSelector =
    serverAction.type === "click_collect" &&
    serverAction?.selector &&
    typeof serverAction.selector === "object"
      ? String(serverAction.selector.productSelector || "")
      : "";

  async function sendClickCollectFallbackLinks() {
    if (!clickCollectProductSelector) {
      return false;
    }

    const fallbackResult = await runActionWithScripting(
      tabId,
      "select",
      [clickCollectProductSelector],
      payload?.isError,
    );

    if (
      !fallbackResult.ok ||
      fallbackResult.response?.error ||
      isEmptySelectResult(fallbackResult.response?.data)
    ) {
      return false;
    }

    await sendDataToBackend({
      slug: url,
      step,
      content: {
        ...(fallbackResult.response?.data || {}),
        processed_products: [],
        fallback_to_products_mode: true,
      },
      id,
      queue_name,
      start_str,
      profile,
      process_id,
      screenshot: "",
    });
    return true;
  }

  const sendResult = await sendMessageWithRetry(tabId, message);

  if (serverAction.type === "api_fetch") {
    await handleApiFetchResult(tabId, payload, sendResult);
    return;
  }

  if (serverAction.type === "extract_detail") {
    // sempre avanca (profile normal): o content espera 9s -> da tempo do captcha
    // solver resolver Press&Hold visivel; null apos isso = pula esse imovel.
    await sendDataToBackend({
      slug: url,
      step,
      content: sendResult.ok ? sendResult.response?.data || { property: null } : { property: null },
      id,
      queue_name,
      start_str,
      profile,
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  if (serverAction.type === "capture_query") {
    const capData = sendResult.ok
      ? sendResult.response?.data || { candidates: [], no_data: true }
      : { candidates: [], send_failed: true, send_error: sendResult.runtimeError || "" };
    // a aba ficou na pagina de detalhe (homedetails) usada p/ captar o hash. volta
    // pra busca leve -> contexto same-origin limpo p/ os POSTs de detalhe.
    try {
      await chrome.tabs.update(tabId, { url: "https://www.zillow.com/homes/for_rent/" });
      await waitForTabComplete(tabId, 20000);
    } catch (_e) {}
    await sendDataToBackend({
      slug: url,
      step,
      content: capData,
      id,
      queue_name,
      start_str,
      profile,
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  if (!sendResult.ok) {
    if (serverAction.type === "click_collect") {
      const fallbackSent = await sendClickCollectFallbackLinks();
      if (fallbackSent) {
        scheduleNextUrl(tabId);
        return;
      }

      await sendDataToBackend({
        slug: url,
        step,
        content: {
          ...payload,
          send_message_error: sendResult.runtimeError,
        },
        id,
        queue_name,
        start_str,
        profile: "is-error",
        process_id,
        screenshot: "",
      });
      scheduleNextUrl(tabId);
      return;
    }

    const fallbackResult = await runActionWithScripting(
      tabId,
      serverAction.type,
      serverAction.selector,
      payload?.isError,
    );

    if (
      fallbackResult.ok &&
      !fallbackResult.response?.error &&
      !(serverAction.type === "select" && isEmptySelectResult(fallbackResult.response?.data))
    ) {
      await sendDataToBackend({
        slug: url,
        step,
        content: fallbackResult.response?.data ?? [],
        id,
        queue_name,
        start_str,
        profile,
        process_id,
        screenshot: "",
      });
      scheduleNextUrl(tabId);
      return;
    }

    await sendDataToBackend({
      slug: url,
      step,
      content: {
        ...payload,
        send_message_error: sendResult.runtimeError,
        fallback_error: fallbackResult.runtimeError || "",
        fallback_response_error: fallbackResult.response?.error || false,
      },
      id,
      queue_name,
      start_str,
      profile: "is-error",
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  if (
    response?.error ||
    (serverAction.type === "select" && isEmptySelectResult(response?.data))
  ) {
    if (serverAction.type === "click_collect") {
      const fallbackSent = await sendClickCollectFallbackLinks();
      if (fallbackSent) {
        scheduleNextUrl(tabId);
        return;
      }

      await sendDataToBackend({
        slug: url,
        step,
        content: {
          response: response ?? payload,
        },
        id,
        queue_name,
        start_str,
        profile: "is-error",
        process_id,
        screenshot: "",
      });
      scheduleNextUrl(tabId);
      return;
    }

    const fallbackResult = await runActionWithScripting(
      tabId,
      serverAction.type,
      serverAction.selector,
      payload?.isError,
    );

    if (
      fallbackResult.ok &&
      !fallbackResult.response?.error &&
      !(serverAction.type === "select" && isEmptySelectResult(fallbackResult.response?.data))
    ) {
      await sendDataToBackend({
        slug: url,
        step,
        content: fallbackResult.response?.data ?? [],
        id,
        queue_name,
        start_str,
        profile,
        process_id,
        screenshot: "",
      });
      scheduleNextUrl(tabId);
      return;
    }

    await sendDataToBackend({
      slug: url,
      step,
      content: {
        response: response ?? payload,
        fallback_error: fallbackResult.runtimeError || "",
        fallback_response_error: fallbackResult.response?.error || false,
      },
      id,
      queue_name,
      start_str,
      profile: "is-error",
      process_id,
      screenshot: "",
    });
    scheduleNextUrl(tabId);
    return;
  }

  await sendDataToBackend({
    slug: url,
    step,
    content: response?.data ?? [],
    id,
    queue_name,
    start_str,
    profile,
    process_id,
    screenshot: "",
  });
  scheduleNextUrl(tabId);
}

function simulateActivity(tabId) {
  chrome.tabs.sendMessage(tabId, { action: "simulate_activity" }, () => {
    if (chrome.runtime.lastError) {
      logError({
        error: "Error sending message to content.js:",
        lastError: chrome.runtime.lastError,
      });
    }
  });
}

function run() {
  if (extensionStarted) {
    return;
  }
  extensionStarted = true;
  console.log("Extension started!");
  ensureBrowserID();

  chrome.tabs.query({}, (tabs) => {
    if (chrome.runtime.lastError) {
      logError({
        error: "Error querying tabs",
        lastError: chrome.runtime.lastError,
      });
    }

    const availableTabs = Array.isArray(tabs) ? tabs : [];
    const tab = availableTabs.find(
      (current) =>
        current &&
        typeof current.id === "number" &&
        !String(current.url || "").startsWith("chrome://") &&
        !String(current.url || "").startsWith("chrome-extension://"),
    );

    if (tab && typeof tab.id === "number") {
      setTimeout(() => getNextUrl(tab.id), 1000);
      return;
    }

    chrome.tabs.create({ url: "about:blank" }, (newTab) => {
      if (chrome.runtime.lastError) {
        chrome.windows.create({ url: "about:blank" }, (newWindow) => {
          const fallbackTabId = newWindow?.tabs?.[0]?.id;
          if (typeof fallbackTabId !== "number") {
            logError({
              error: "Error creating fallback window/tab.",
              lastError: chrome.runtime.lastError,
            });
            return;
          }
          setTimeout(() => getNextUrl(fallbackTabId), 1000);
        });
        return;
      }

      if (!newTab || typeof newTab.id !== "number") {
        logError({ error: "Error creating new tab." });
        return;
      }
      setTimeout(() => getNextUrl(newTab.id), 1000);
    });
  });
}

// Watchdog: chrome.alarms sobrevive a suspensao do SW. A cada 1min acorda o SW;
// se o loop estiver parado (sem atividade > 90s), ressuscita o getNextUrl.
chrome.alarms.create("watchdog", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "watchdog") return;
  if (activeTabId == null) {
    run();
    return;
  }
  if (Date.now() - lastActivity > WATCHDOG_STALL_MS) {
    console.log("[watchdog] loop parado -> ressuscitando getNextUrl");
    getNextUrl(activeTabId);
  }
});

chrome.runtime.onStartup.addListener(() => {
  run();
});

chrome.runtime.onInstalled.addListener(() => {
  generateBrowserID();
  run();
});

chrome.tabs.onCreated.addListener(() => {
  run();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "cba_boot") {
    run();
    sendResponse({ ok: true });
    return true;
  }
  if (message?.type === "queue_clicks") {
    // repassa as coords dos cards pro backend (sem mixed-content); o hash_clicker
    // pega a fila e da o clique REAL no SO.
    const coords = Array.isArray(message.coords) ? message.coords : [];
    (async () => {
      for (const [x, y] of coords) {
        try {
          await fetch(`${backendBaseUrl}/v1/click?x=${x}&y=${y}`, { headers: { "x-api-key": apiKey } });
        } catch (_e) {}
      }
    })();
    sendResponse({ ok: true, queued: coords.length });
    return true;
  }
  return true;
});

run();
