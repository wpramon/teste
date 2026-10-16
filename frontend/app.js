const CONFIG = {
  endpoint: "https://gam-lineitem-copier.wagner-ramon.workers.dev"
};

let sessionUser = "";
let sessionKey = "";

const $ = (s) => document.querySelector(s);
const loginCard = $("#loginCard");
const copierCard = $("#copierCard");
const loginForm = $("#loginForm");
const userName = $("#userName");
const accessKey = $("#accessKey");
const loginMessage = $("#loginMessage");
const currentUser = $("#currentUser");
const logoutButton = $("#logoutButton");
const appStatus = $("#appStatus");
const sourceCodes = $("#sourceCodes");
const targetCodes = $("#targetCodes");
const copyButton = $("#copyButton");
const clearButton = $("#clearButton");
const pairsEl = $("#pairs");
const sourceCount = $("#sourceCount");
const targetCount = $("#targetCount");
const pairCount = $("#pairCount");
const messageEl = $("#message");
const dialog = $("#confirmDialog");
const confirmPairs = $("#confirmPairs");

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

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const user = userName.value.trim().toUpperCase();
  const key = accessKey.value;
  if (!user || !key) return showLoginMessage("Informe usuário e chave de acesso.");

  // O segredo fica apenas em memória nesta aba. A validação real ocorre no POST do Worker.
  sessionUser = user;
  sessionKey = key;
  accessKey.value = "";
  loginMessage.hidden = true;
  loginCard.hidden = true;
  copierCard.hidden = false;
  logoutButton.hidden = false;
  currentUser.textContent = user;
  appStatus.textContent = "Autenticado";
  sourceCodes.focus();
});

logoutButton.addEventListener("click", logout);

function logout() {
  sessionUser = "";
  sessionKey = "";
  userName.value = "";
  accessKey.value = "";
  sourceCodes.value = "";
  targetCodes.value = "";
  copierCard.hidden = true;
  loginCard.hidden = false;
  logoutButton.hidden = true;
  appStatus.textContent = "Bloqueado";
  validate();
  userName.focus();
}

function showLoginMessage(text) {
  loginMessage.hidden = false;
  loginMessage.className = "message error";
  loginMessage.textContent = text;
}

[sourceCodes, targetCodes].forEach(el => el.addEventListener("input", validate));

clearButton.addEventListener("click", () => {
  sourceCodes.value = "";
  targetCodes.value = "";
  messageEl.hidden = true;
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
  if (!sessionUser || !sessionKey) return logout();

  setBusy(true);
  try {
    const response = await fetch(CONFIG.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-GAM-User": sessionUser,
        "X-GAM-Key": sessionKey
      },
      body: JSON.stringify({
        source_codes: state.origins.join(","),
        target_codes: state.destinations.join(",")
      })
    });

    const data = await response.json().catch(() => ({}));

    if (response.status === 401) {
      logout();
      showLoginMessage(data.error || "Usuário ou chave de acesso inválidos.");
      throw new Error("Credencial inválida.");
    }

    if (!response.ok) throw new Error(data.error || `Falha no envio: HTTP ${response.status}`);

    showMessage(data.message || "Solicitação enviada para execução.", "success");
    appStatus.textContent = "Enviado";
  } catch (error) {
    if (sessionUser) {
      showMessage(error.message || "Não foi possível iniciar a execução.", "error");
      appStatus.textContent = "Erro";
    }
  } finally {
    if (sessionUser) setBusy(false);
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
  if (busy) appStatus.textContent = "Enviando…";
  else validate();
}

validate();
