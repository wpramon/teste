const CONFIG = {
  // Não coloque PAT, senha ou qualquer credencial neste arquivo.
  // Configure aqui somente a URL HTTPS de uma camada autenticada que aceite POST.
  endpoint: ""
};

const sourceCodes = document.querySelector("#sourceCodes");
const targetCodes = document.querySelector("#targetCodes");
const copyButton = document.querySelector("#copyButton");
const clearButton = document.querySelector("#clearButton");
const pairsEl = document.querySelector("#pairs");
const sourceCount = document.querySelector("#sourceCount");
const targetCount = document.querySelector("#targetCount");
const pairCount = document.querySelector("#pairCount");
const messageEl = document.querySelector("#message");
const dialog = document.querySelector("#confirmDialog");
const confirmPairs = document.querySelector("#confirmPairs");
const appStatus = document.querySelector("#appStatus");

function parseCodes(value) {
  return value.split(/[,;\n]+/).map(v => v.trim()).filter(Boolean);
}

function validate() {
  const origins = parseCodes(sourceCodes.value);
  const destinations = parseCodes(targetCodes.value);
  const invalid = [...origins, ...destinations].filter(v => !/^\d{5}$/.test(v));
  let error = "";

  if (invalid.length) error = `Use somente códigos de 5 dígitos. Inválidos: ${[...new Set(invalid)].join(", ")}`;
  else if (origins.length !== destinations.length && (origins.length || destinations.length)) error = `Quantidades diferentes: ${origins.length} origem(ns) e ${destinations.length} destino(s).`;
  else if (new Set(origins).size !== origins.length) error = "Existe código repetido na lista de origem.";
  else if (new Set(destinations).size !== destinations.length) error = "Existe código repetido na lista de destino.";
  else if (origins.some((code, i) => code === destinations[i])) error = "Há um par com o mesmo código na origem e no destino.";

  sourceCount.textContent = origins.length;
  targetCount.textContent = destinations.length;
  pairCount.textContent = !error && origins.length === destinations.length ? origins.length : 0;

  if (!origins.length && !destinations.length) {
    pairsEl.className = "pairs empty";
    pairsEl.textContent = "Preencha as listas para visualizar o pareamento.";
  } else if (!error && origins.length === destinations.length) {
    pairsEl.className = "pairs";
    pairsEl.innerHTML = origins.map((code, i) => `<div class="pair"><b>${code}</b><span class="arrow">→</span><b>${destinations[i]}</b></div>`).join("");
  }

  messageEl.hidden = !error;
  messageEl.className = "message error";
  messageEl.textContent = error;
  copyButton.disabled = Boolean(error) || !origins.length;
  return { origins, destinations, error };
}

[sourceCodes, targetCodes].forEach(el => el.addEventListener("input", validate));

clearButton.addEventListener("click", () => {
  sourceCodes.value = "";
  targetCodes.value = "";
  messageEl.hidden = true;
  appStatus.textContent = "Pronto";
  validate();
  sourceCodes.focus();
});

copyButton.addEventListener("click", () => {
  const state = validate();
  if (state.error) return;
  confirmPairs.innerHTML = state.origins.map((code, i) => `<div class="pair"><b>${code}</b><span class="arrow">→</span><b>${state.destinations[i]}</b></div>`).join("");
  dialog.showModal();
});

dialog.addEventListener("close", async () => {
  if (dialog.returnValue !== "confirm") return;
  const state = validate();
  if (state.error) return;

  if (!CONFIG.endpoint) {
    showMessage("A interface está pronta, mas o endpoint autenticado ainda não foi configurado. Nenhuma credencial deve ser colocada no app.js.", "error");
    return;
  }

  setBusy(true);
  try {
    const response = await fetch(CONFIG.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_codes: state.origins.join(","),
        target_codes: state.destinations.join(",")
      })
    });
    if (!response.ok) throw new Error(`Falha no envio: HTTP ${response.status}`);
    const data = await response.json().catch(() => ({}));
    showMessage(data.message || "Solicitação enviada para execução.", "success");
    appStatus.textContent = "Enviado";
  } catch (error) {
    showMessage(error.message || "Não foi possível iniciar a execução.", "error");
    appStatus.textContent = "Erro";
  } finally {
    setBusy(false);
  }
});

function showMessage(text, type) {
  messageEl.hidden = false;
  messageEl.className = `message ${type}`;
  messageEl.textContent = text;
}

function setBusy(busy) {
  copyButton.disabled = busy;
  clearButton.disabled = busy;
  appStatus.textContent = busy ? "Enviando…" : appStatus.textContent;
}

validate();
