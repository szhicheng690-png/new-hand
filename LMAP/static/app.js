const state = {
  userId: "",
  providers: [],
  prompts: [],
  conversations: [],
  messages: [],
  activeConversationId: null,
};

const $ = (selector) => document.querySelector(selector);

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (response.status === 401) {
    window.location.href = "/login?next=/";
    throw new Error(data.error || "请先登录");
  }
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
};

const titles = {
  chat: "对话界面",
  models: "模型切换 / 配置",
  prompts: "Prompt 模板管理",
  billing: "用户中心 / 计费看板",
  history: "对话 & 历史",
};

async function bootstrap() {
  const data = await api("/api/bootstrap");
  state.userId = data.user.id;
  state.providers = data.providers;
  state.prompts = data.prompt_templates;
  state.conversations = data.conversations;
  renderAll(data.user);
}

function renderAll(user) {
  $("#userName").textContent = user.name || user.id;
  $("#userId").textContent = user.id;
  renderProviderOptions();
  renderPromptOptions();
  renderProviders();
  renderPrompts();
  renderUsage(user);
  renderHistory();
  renderMessages();
}

function renderProviderOptions() {
  const providerSelect = $("#providerSelect");
  providerSelect.innerHTML = state.providers
    .map((provider) => `<option value="${provider.id}">${provider.name}</option>`)
    .join("");
  providerSelect.value = state.providers[0]?.id || "";
  renderModelOptions();
  updateProviderStatus();
}

function renderModelOptions() {
  const provider = state.providers.find((item) => item.id === $("#providerSelect").value) || state.providers[0];
  const modelSelect = $("#modelSelect");
  modelSelect.innerHTML = (provider?.models || [])
    .map((model) => `<option value="${model}">${model}</option>`)
    .join("");
}

function renderPromptOptions() {
  const promptSelect = $("#promptSelect");
  promptSelect.innerHTML =
    '<option value="">不使用模板</option>' +
    state.prompts.map((prompt) => `<option value="${prompt.id}">${escapeHtml(prompt.title)}</option>`).join("");
}

function renderProviders() {
  $("#providerCards").innerHTML = state.providers
    .map(
      (provider) => `
      <article class="provider-card">
        <header>
          <div>
            <h2>${provider.name}</h2>
            <p>${provider.base_url}</p>
          </div>
          <span class="badge ${provider.enabled ? "enabled" : ""}">${provider.enabled ? "已启用" : "未配置"}</span>
        </header>
        <div class="model-tags">
          ${provider.models.map((model) => `<span>${model}</span>`).join("")}
        </div>
        <form data-provider="${provider.id}">
          <label>Base URL</label>
          <input name="base_url" value="${escapeAttr(provider.base_url || provider.default_base_url)}" />
          <label>API Key ${provider.key_mask ? `(${escapeHtml(provider.key_mask)})` : ""}</label>
          <input name="api_key" type="password" placeholder="sk-..." autocomplete="off" />
          <button class="primary" type="submit">保存并启用</button>
          <button type="button" data-delete-provider="${provider.id}">删除配置</button>
        </form>
      </article>
    `,
    )
    .join("");

  document.querySelectorAll(".provider-card form").forEach((form) => {
    form.addEventListener("submit", saveProvider);
  });
  document.querySelectorAll("[data-delete-provider]").forEach((button) => {
    button.addEventListener("click", deleteProvider);
  });
}

function renderPrompts() {
  $("#promptList").innerHTML =
    state.prompts
      .map(
        (prompt) => `
        <article class="template-item">
          <strong>${escapeHtml(prompt.title)}</strong>
          <p>${escapeHtml(prompt.content)}</p>
          <footer>
            <button data-edit-prompt="${prompt.id}">编辑</button>
            <button data-delete-prompt="${prompt.id}">删除</button>
          </footer>
        </article>
      `,
      )
      .join("") || '<p class="muted">还没有模板。</p>';

  document.querySelectorAll("[data-edit-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      const prompt = state.prompts.find((item) => item.id === button.dataset.editPrompt);
      $("#promptId").value = prompt.id;
      $("#promptTitle").value = prompt.title;
      $("#promptContent").value = prompt.content;
    });
  });
  document.querySelectorAll("[data-delete-prompt]").forEach((button) => {
    button.addEventListener("click", deletePrompt);
  });
}

function renderUsage(user) {
  const used = user.used_tokens || 0;
  const quota = user.quota_tokens || 1;
  const percent = Math.min(100, Math.round((used / quota) * 100));
  $("#quotaBar").style.width = `${percent}%`;
  $("#quotaText").textContent = `${used.toLocaleString()} / ${quota.toLocaleString()} tokens`;
  $("#requestCount").textContent = (user.requests || 0).toLocaleString();
  $("#tokenCount").textContent = used.toLocaleString();
  $("#costCount").textContent = `${Number(user.cost_cents || 0).toFixed(4)} 分`;
  $("#billTokens").textContent = used.toLocaleString();
  $("#billRemaining").textContent = (user.remaining_tokens || 0).toLocaleString();
  $("#billCost").textContent = `${Number(user.cost_cents || 0).toFixed(4)} 分`;

  const max = Math.max(...(user.provider_usage || []).map((item) => item.total_tokens), 1);
  $("#providerUsage").innerHTML =
    (user.provider_usage || [])
      .map(
        (item) => `
        <div class="usage-row">
          <strong>${escapeHtml(item.provider_id)}</strong>
          <div class="usage-track"><span style="width:${Math.round((item.total_tokens / max) * 100)}%"></span></div>
          <span>${Number(item.total_tokens).toLocaleString()}</span>
        </div>
      `,
      )
      .join("") || '<p class="muted">暂无消耗数据。</p>';
}

function renderHistory() {
  $("#historyList").innerHTML =
    state.conversations
      .map(
        (item) => `
        <article class="history-item">
          <strong>${escapeHtml(item.title)}</strong>
          <span class="muted">${escapeHtml(item.provider_id)} / ${escapeHtml(item.model)} / ${item.message_count} 条消息</span>
          <footer><button data-load-conv="${item.id}">打开</button></footer>
        </article>
      `,
      )
      .join("") || '<p class="muted">暂无对话历史。</p>';

  document.querySelectorAll("[data-load-conv]").forEach((button) => {
    button.addEventListener("click", () => loadConversation(button.dataset.loadConv));
  });
}

function renderMessages() {
  const messages = $("#messages");
  if (!state.messages.length) {
    messages.innerHTML = `
      <div class="message assistant">
        你好，我是聚合平台的演示助手。你可以先直接发送问题，也可以到“模型配置”里填入供应商 API Key 后调用真实模型。
      </div>
    `;
    return;
  }
  messages.innerHTML = state.messages
    .map((message) => `<div class="message ${message.role}">${escapeHtml(message.content)}</div>`)
    .join("");
  messages.scrollTop = messages.scrollHeight;
}

function updateProviderStatus() {
  const provider = state.providers.find((item) => item.id === $("#providerSelect").value);
  $("#providerStatus").textContent = `供应商：${provider?.name || "-"}`;
}

async function saveProvider(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const providerId = form.dataset.provider;
  const formData = new FormData(form);
  setStatus("保存模型配置中...");
  try {
    await api("/api/provider-keys", {
      method: "POST",
      body: JSON.stringify({
        provider_id: providerId,
        base_url: formData.get("base_url"),
        api_key: formData.get("api_key"),
        enabled: true,
      }),
    });
    await bootstrap();
    setStatus("配置已保存");
  } catch (error) {
    setStatus(error.message);
  }
}

async function deleteProvider(event) {
  const providerId = event.currentTarget.dataset.deleteProvider;
  setStatus("删除模型配置中...");
  try {
    await api(`/api/provider-keys/${providerId}`, { method: "DELETE" });
    await bootstrap();
    setStatus("配置已删除");
  } catch (error) {
    setStatus(error.message);
  }
}

async function savePrompt(event) {
  event.preventDefault();
  setStatus("保存模板中...");
  try {
    await api("/api/prompt-templates", {
      method: "POST",
      body: JSON.stringify({
        id: $("#promptId").value,
        title: $("#promptTitle").value,
        content: $("#promptContent").value,
      }),
    });
    $("#promptForm").reset();
    $("#promptId").value = "";
    await bootstrap();
    setStatus("模板已保存");
  } catch (error) {
    setStatus(error.message);
  }
}

async function deletePrompt(event) {
  const promptId = event.currentTarget.dataset.deletePrompt;
  setStatus("删除模板中...");
  try {
    await api(`/api/prompt-templates/${promptId}`, { method: "DELETE" });
    await bootstrap();
    setStatus("模板已删除");
  } catch (error) {
    setStatus(error.message);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $("#messageInput");
  const content = input.value.trim();
  if (!content) return;

  const prompt = state.prompts.find((item) => item.id === $("#promptSelect").value);
  const outgoing = [];
  if (prompt) {
    outgoing.push({ role: "system", content: prompt.content });
  }
  outgoing.push(...state.messages.filter((item) => item.role !== "system"));
  outgoing.push({ role: "user", content });

  state.messages.push({ role: "user", content });
  state.messages.push({ role: "assistant", content: "" });
  input.value = "";
  renderMessages();
  setStatus("模型响应中...");

  const assistantIndex = state.messages.length - 1;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        provider_id: $("#providerSelect").value,
        model: $("#modelSelect").value,
        conversation_id: state.activeConversationId,
        messages: outgoing,
      }),
    });
    if (response.status === 401) {
      window.location.href = "/login?next=/";
      return;
    }
    if (!response.ok || !response.body) {
      const data = await response.json();
      throw new Error(data.error || "模型响应失败");
    }
    await readSse(response.body, (eventData) => {
      if (eventData.type === "delta") {
        state.messages[assistantIndex].content += eventData.content;
        renderMessages();
      }
      if (eventData.type === "done") {
        state.activeConversationId = eventData.conversation_id;
      }
      if (eventData.type === "error") {
        throw new Error(eventData.message);
      }
    });
    await bootstrap();
    setStatus("响应完成");
  } catch (error) {
    state.messages[assistantIndex].content = `请求失败：${error.message}`;
    renderMessages();
    setStatus(error.message);
  }
}

async function readSse(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const event of events) {
      const line = event.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const data = line.replace("data: ", "");
      if (data === "[DONE]") continue;
      onEvent(JSON.parse(data));
    }
  }
}

async function loadConversation(conversationId) {
  try {
    const data = await api(`/api/conversations/${conversationId}`);
    state.activeConversationId = conversationId;
    state.messages = data.messages.map((message) => ({ role: message.role, content: message.content }));
    switchView("chat");
    renderMessages();
  } catch (error) {
    setStatus(error.message);
  }
}

function switchView(view) {
  document.querySelectorAll(".nav-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `${view}View`);
  });
  $("#viewTitle").textContent = titles[view] || "大模型聚合平台";
}

function setStatus(message) {
  $("#requestStatus").textContent = message;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

document.querySelectorAll(".nav-tabs button").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

$("#providerSelect").addEventListener("change", () => {
  renderModelOptions();
  updateProviderStatus();
});

$("#chatForm").addEventListener("submit", sendMessage);
$("#promptForm").addEventListener("submit", savePrompt);
$("#clearChat").addEventListener("click", () => {
  state.messages = [];
  state.activeConversationId = null;
  renderMessages();
});
$("#logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
  } finally {
    window.location.href = "/login";
  }
});

bootstrap().catch((error) => setStatus(error.message));
