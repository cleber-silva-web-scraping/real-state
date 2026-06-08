
// Content script atualizado com correções
console.log("Content script running!");

function logError(error) {
  console.error(error);
}

// ... [spoof de fingerprint mantido aqui, não exibido por brevidade] ...

const simulateHumanActivity = () => {
  try {
    const x = Math.floor(Math.random() * window.innerWidth);
    const y = Math.floor(Math.random() * window.innerHeight);
    document.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y }));
    window.scrollTo({ top: Math.random() * document.body.scrollHeight, behavior: "smooth" });

    if (Math.random() > 0.8) {
      document.dispatchEvent(new MouseEvent("click", { clientX: x, clientY: y }));
    }
  } catch (error) {
    logError(error);
  }
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const isErrors =
    message?.isError?.some(({ type, selector }) => type === "exist" && document.body.innerHTML.includes(selector)) ?? false;

  if (isErrors) {
    logError({ error: "Bad element found." });
    sendResponse({ data: [], error: true });
    return;
  }

  if (message.action === "simulate_activity") {
    simulateHumanActivity();
    sendResponse({ done: true });
  }

  if (message.action === "variable") {
    try {
      const elements = message.param.reduce((acc, param) => {
        document.querySelectorAll("script").forEach((script) => {
          if (param === "__INITIAL_CONFIG__" && script.textContent.includes("window.__INITIAL_CONFIG__")) {
            const config = script.textContent.replace("window.__INITIAL_CONFIG__ = ", "");
            try {
              acc[param] = JSON.parse(config);
            } catch (e) {
              console.error("Erro ao parsear JSON", e);
            }
          }
        });
        return acc;
      }, {});
      sendResponse({ data: elements, error: false });
    } catch (error) {
      logError(error);
      sendResponse({ data: null, error: true });
    }
  }

  if (message.action === "select") {
    try {
      const elements = message.param.reduce((acc, param) => {
        acc[param] = [...document.querySelectorAll(param)].map(el => el.outerHTML);
        return acc;
      }, {});
      sendResponse({ data: elements, error: false });
    } catch (error) {
      logError(error);
      sendResponse({ data: null, error: true });
    }
  }

  if (message.action === "json") {
    try {
      const jsonData = JSON.parse(document.body.innerText);
      sendResponse({ data: jsonData, error: false });
    } catch (error) {
      logError(error);
      sendResponse({ data: null, error: true });
    }
  }

  return true; // permite resposta assíncrona
});
