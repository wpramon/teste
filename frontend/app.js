const CONFIG = {
  endpoint: "https://gam-lineitem-copier.wagner-ramon.workers.dev"
};

let sessionUser = sessionStorage.getItem("gam_user") || "";
let sessionToken = sessionStorage.getItem("gam_session_token") || "";

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
  const invalid = [...origins, ...destinations].filter(v => !/^\d{5,6}$/.test(v));
  let error = "";

  if (invalid.length) error = `Use somente códigos de 5 a 6 dígitos. Inválidos: ${[...new Set(invalid)].join(", ")}`;
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

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const user = userName.value.trim().toUpperCase();
  const key = accessKey.value;
  if (!user || !key) return showLoginMessage("Informe usuário e chave de acesso.");

  const button = loginForm.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = "Validando...";
  loginMessage.hidden = true;

  try {
    const response = await fetch(`${CONFIG.endpoint}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, key })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.token) throw new Error(data.error || "Não foi possível autenticar.");

    sessionUser = data.user || user;
    sessionToken = data.token;
    sessionStorage.setItem("gam_user", sessionUser);
    sessionStorage.setItem("gam_session_token", sessionToken);
    accessKey.value = "";
    showCopier();
  } catch (error) {
    showLoginMessage(error.message || "Não foi possível autenticar.");
  } finally {
    button.disabled = false;
    button.textContent = "Entrar";
  }
});

logoutButton.addEventListener("click", logout);

function showCopier() {
  loginMessage.hidden = true;
  loginCard.hidden = true;
  copierCard.hidden = false;
  logoutButton.hidden = false;
  currentUser.textContent = sessionUser;
  appStatus.textContent = "Autenticado";
}

function logout() {
  sessionUser = "";
  sessionToken = "";
  sessionStorage.removeItem("gam_user");
  sessionStorage.removeItem("gam_session_token");
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
  if (!sessionUser || !sessionToken) return logout();

  setBusy(true);
  showMessage("Execução iniciando...", "success");
  appStatus.textContent = "Enviando…";

  try {
    const response = await fetch(CONFIG.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${sessionToken}`
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
      return;
    }

    if (!response.ok) {
      throw new Error(data.error || `Falha no envio: HTTP ${response.status}`);
    }

    if (!data.run_id) {
      throw new Error("A execução foi iniciada, mas o GitHub não retornou o Run ID para acompanhamento.");
    }

    appStatus.textContent = "Em execução";
    showMessage("Configurações sendo copiadas. Aguardando conclusão do GitHub Actions...", "success");

    const result = await waitForRun(data.run_id);

    if (result.conclusion === "success") {
      appStatus.textContent = "Concluído";
      showMessage(
        `✓ Cópia concluída com sucesso! ${state.origins.length} Line Item(s) processado(s).`,
        "success"
      );
    } else {
      appStatus.textContent = "Falhou";
      throw new Error(`A execução terminou com status: ${result.conclusion || "falha"}.`);
    }
  } catch (error) {
    if (sessionUser) {
      showMessage(error.message || "Não foi possível concluir a execução.", "error");
      appStatus.textContent = "Erro";
    }
  } finally {
    setBusy(false);
  }
});

async function waitForRun(runId) {
  const maxAttempts = 120;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const response = await fetch(
      `${CONFIG.endpoint}/status?run_id=${encodeURIComponent(runId)}`,
      {
        method: "GET",
        headers: {
          "Authorization": `Bearer ${sessionToken}`
        }
      }
    );

    const data = await response.json().catch(() => ({}));

    if (response.status === 401) {
      throw new Error("A autenticação expirou ou a chave de acesso não é mais válida.");
    }

    if (!response.ok) {
      throw new Error(data.error || `Erro ao consultar execução: HTTP ${response.status}`);
    }

    if (data.status === "completed") {
      return data;
    }

    appStatus.textContent = data.status === "queued" ? "Na fila" : "Em execução";
    await sleep(2000);
  }

  throw new Error("A execução continua em andamento. Consulte o GitHub Actions para acompanhar.");
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function showMessage(text, type) {
  messageEl.hidden = false;
  messageEl.className = `message ${type}`;
  messageEl.textContent = text;
}

function setBusy(busy) {
  copyButton.disabled = busy;
  clearButton.disabled = busy;
  sourceCodes.disabled = busy;
  targetCodes.disabled = busy;

  if (!busy) {
    const state = validateStateOnly();
    copyButton.disabled = Boolean(state.error) || !state.origins.length;
  }
}

function validateStateOnly() {
  const origins = parseCodes(sourceCodes.value);
  const destinations = parseCodes(targetCodes.value);
  const invalid = [...origins, ...destinations].filter(v => !/^\d{5}$/.test(v));
  let error = "";
  if (invalid.length) error = "invalid";
  else if (origins.length !== destinations.length && (origins.length || destinations.length)) error = "count";
  else if (new Set(origins).size !== origins.length) error = "source-duplicate";
  else if (new Set(destinations).size !== destinations.length) error = "target-duplicate";
  else if (origins.some((code, i) => code === destinations[i])) error = "same-pair";
  return { origins, destinations, error };
}

if (sessionUser && sessionToken) {
  showCopier();
}

validate();
