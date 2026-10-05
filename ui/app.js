"use strict";

const elements = {
  brandRoot: document.getElementById("brandRoot"),
  brandMark: document.getElementById("brandMark"),
  brandTag: document.getElementById("brandTag"),
  assistantName: document.getElementById("assistantName"),
  welcomeName: document.getElementById("welcomeName"),
  welcomeText: document.getElementById("welcomeText"),
  backendSelect: document.getElementById("backendSelect"),
  newConversation: document.getElementById("newConversation"),
  modelStart: document.getElementById("modelStart"),
  modelStop: document.getElementById("modelStop"),
  statusToggle: document.getElementById("statusToggle"),
  statusPanel: document.getElementById("statusPanel"),
  statusClose: document.getElementById("statusClose"),
  deviceName: document.getElementById("deviceName"),
  deviceFootnote: document.getElementById("deviceFootnote"),
  modelName: document.getElementById("modelName"),
  capabilitySummary: document.getElementById("capabilitySummary"),
  llmStatus: document.getElementById("llmStatus"),
  asrStatus: document.getElementById("asrStatus"),
  latencyStatus: document.getElementById("latencyStatus"),
  connectionText: document.getElementById("connectionText"),
  conversation: document.getElementById("conversation"),
  messageLabel: document.getElementById("messageLabel"),
  chatLog: document.getElementById("chatLog"),
  emptyState: document.getElementById("emptyState"),
  emptyDetail: document.getElementById("emptyDetail"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
  sendButton: document.getElementById("sendButton"),
  voiceButton: document.getElementById("voiceButton"),
  voiceButtonLabel: document.getElementById("voiceButtonLabel"),
  voiceAvailability: document.getElementById("voiceAvailability"),
  imageButton: document.getElementById("imageButton"),
  videoButton: document.getElementById("videoButton"),
  voiceSlot: document.getElementById("voiceSlot"),
  voiceFeedback: document.getElementById("voiceFeedback"),
  imageSlot: document.getElementById("imageSlot"),
  videoSlot: document.getElementById("videoSlot"),
};

const state = {
  assistant: null,
  backends: new Map(),
  selectedId: null,
  messages: new Map(),
  latency: new Map(),
  busy: false,
  voice: { phase: "idle", asr_connected: false, backend_busy: false, request_id: null },
  voiceRevision: -1,
  voiceEpoch: null,
  voiceControlPending: false,
  media: null,
  uploads: false,
  requestUncertain: false,
  selectionPending: false,
  resetPending: false,
  resetFence: new Map(),
  modelLifecycle: false,
  model: null,
  modelRevision: -1,
  modelPending: false,
  voiceEventSource: null,
  eventStreamToken: null,
};

const inputNames = { text: "文本", voice: "语音", image: "图像", video: "视频" };
const inputControls = {
  voice: [elements.voiceButton, elements.voiceSlot],
  image: [elements.imageButton, elements.imageSlot],
  video: [elements.videoButton, elements.videoSlot],
};

function selectedBackend() {
  return state.backends.get(state.selectedId);
}

function modelTransitioning() {
  return state.modelPending || Boolean(state.model?.transitioning)
    || ["starting", "stopping", "switching"].includes(state.model?.state);
}

function modelReady() {
  return !state.modelLifecycle || (state.model?.selected_backend_id === state.selectedId
    && state.model.state === "ready" && !modelTransitioning());
}

function applyModelState(snapshot) {
  if (!snapshot || !snapshot.state) return;
  if (snapshot.epoch && state.voiceEpoch && snapshot.epoch !== state.voiceEpoch) return;
  if (Number.isInteger(snapshot.revision) && snapshot.revision < state.modelRevision) return;
  if (state.model && (state.model.epoch !== snapshot.epoch || state.model.revision !== snapshot.revision)) state.media?.clear();
  state.modelRevision = snapshot.revision ?? state.modelRevision;
  state.model = snapshot;
  if (!snapshot.transitioning && snapshot.selected_backend_id && state.backends.has(snapshot.selected_backend_id)
      && state.selectedId !== snapshot.selected_backend_id) {
    state.selectedId = snapshot.selected_backend_id;
    elements.backendSelect.value = state.selectedId;
    renderBackend();
  }
  const target = state.backends.get(snapshot.target_backend_id) || selectedBackend();
  const labels = { offline: "模型未连接", starting: `正在启动 ${target?.name || "模型"}…`,
    switching: "正在切换模型…", stopping: "正在停止模型…", ready: "模型已连接", error: "模型启动或切换失败，请重试" };
  setConnection(labels[snapshot.state] || "模型未连接", ["offline", "error"].includes(snapshot.state));
  elements.llmStatus.textContent = labels[snapshot.state] || "未连接";
  updateSendState();
}

async function refreshModelState() {
  if (!state.modelLifecycle || state.modelPending) return;
  try { applyModelState(await getJson("/api/model/state")); }
  catch { setConnection("模型状态暂不可用", true); }
}

async function requestModel(action, backendId) {
  state.modelPending = true;
  updateSendState();
  try {
    const result = await getJson(`/api/model/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action === "stop" ? {} : { backend_id: backendId }),
    });
    applyModelState(result);
  } catch (error) {
    elements.backendSelect.value = state.selectedId;
    state.messages.get(state.selectedId).push({ role: "notice", text: error.message });
    renderMessages();
  } finally {
    state.modelPending = false;
    updateSendState();
    refreshModelState();
  }
}

function setConnection(label, offline) {
  elements.connectionText.textContent = label;
  elements.connectionText.classList.toggle("offline", offline);
  elements.statusToggle.classList.toggle("offline", offline);
}

function inputState(backend, input) {
  if (!backend.capabilities[input]) return "当前模型暂不支持";
  return backend.available_inputs[input] ? "已接入" : "即将接入";
}

function renderCapabilities(backend) {
  elements.capabilitySummary.textContent = Object.keys(inputNames)
    .map((input) => `${inputNames[input]}${backend.available_inputs[input] ? "可用" : backend.capabilities[input] ? "待接入" : "不支持"}`)
    .join(" · ");
  for (const [input, [button, slot]] of Object.entries(inputControls)) {
    if (input !== "voice") button.disabled = !canUseMedia(backend,input);
    slot.title = backend.capabilities[input] && !backend.available_inputs[input]
      ? `${inputNames[input]}输入即将接入`
      : inputState(backend, input);
  }
  renderVoiceControls();
}

function renderVoiceControls() {
  const backend = selectedBackend();
  if (!backend) return;
  const phase = state.voice.phase;
  const listening = phase === "listening";
  const canStart = Boolean(backend.capabilities.voice && backend.available_inputs.voice
    && state.voice.asr_connected && !state.voice.backend_busy && !state.busy
    && !state.resetPending && !state.selectionPending
    && !state.media?.hasFile && !state.requestUncertain
    && modelReady() && !modelTransitioning()
    && phase !== "thinking" && phase !== "recognizing" && !state.voice.request_id);
  elements.voiceButton.disabled = modelTransitioning() || state.voiceControlPending || !(listening || canStart);
  elements.voiceButton.classList.toggle("is-listening", listening);
  elements.voiceButton.setAttribute("aria-label", listening ? "取消语音输入" : "语音输入");
  elements.voiceButton.setAttribute("aria-pressed", String(listening));
  elements.voiceButtonLabel.textContent = listening ? "取消录音" : "语音输入";
  elements.voiceAvailability.textContent = listening ? "再次点击取消本轮采集"
    : !backend.capabilities.voice ? "当前模型不支持语音输入"
    : !backend.available_inputs.voice ? "语音输入未接入"
    : !state.voice.asr_connected ? "语音服务未连接"
    : !modelReady() || modelTransitioning() ? "模型准备中，请稍候"
    : !canStart ? "请等待当前交互完成" : "点击语音输入，说一句话即可";
  elements.voiceSlot.title = listening ? "再次点击取消" : !backend.capabilities.voice
    ? "当前模型不支持语音输入" : !backend.available_inputs.voice ? "语音输入未接入"
      : !state.voice.asr_connected ? "语音服务未连接" : canStart ? "点击开始语音输入" : "请等待当前回答完成";
  elements.asrStatus.textContent = !backend.capabilities.voice ? "当前模型不支持"
    : !backend.available_inputs.voice ? "未接入" : state.voice.asr_connected ? "已连接" : "未连接";
  const labels = { listening: "正在聆听…", recognizing: "正在识别…", thinking: `${state.assistant?.name || "助手"}正在思考…` };
  elements.voiceFeedback.hidden = !labels[phase];
  elements.voiceFeedback.textContent = labels[phase] || "";
}

function renderMessages() {
  const backend = selectedBackend();
  if (!backend) return;
  const messages = state.messages.get(backend.id) || [];
  elements.conversation.classList.toggle("is-empty", messages.length === 0);
  elements.chatLog.replaceChildren();
  if (messages.length === 0) {
    elements.chatLog.append(elements.emptyState);
    return;
  }
  for (const message of messages) {
    const item = document.createElement("div");
    item.className = `message ${message.role}`;
    if (message.pending) item.classList.add("pending");
    const meta = document.createElement("span");
    meta.className = "message-meta";
    meta.textContent = message.role === "user" ? "你" : message.role === "assistant" ? state.assistant.name : "提示";
    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = message.text;
    item.append(meta, body);
    elements.chatLog.append(item);
  }
  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
}

function updateSendState() {
  const backend = selectedBackend();
  const acceptsText = Boolean(backend?.available_inputs.text);
  const occupied = state.busy || state.voice.backend_busy || state.resetPending || state.voice.phase === "thinking" || modelTransitioning() || state.media?.uploading || state.requestUncertain;
  elements.messageInput.disabled = !acceptsText || occupied || !modelReady();
  elements.sendButton.disabled = (!acceptsText && !state.media?.pending) || occupied || !modelReady() || (!elements.messageInput.value.trim() && !state.media?.pending);
  elements.backendSelect.disabled = occupied || state.selectionPending || state.backends.size === 0;
  elements.newConversation.disabled = !backend || occupied || !modelReady() || state.selectionPending
    || ["listening", "recognizing"].includes(state.voice.phase);
  elements.modelStart.hidden = !state.modelLifecycle || !backend?.managed || modelReady();
  elements.modelStart.disabled = occupied || state.selectionPending;
  elements.modelStop.disabled = !state.modelLifecycle || !state.model?.owned || occupied || state.selectionPending;
  renderVoiceControls();
  for (const [kind,button] of [["image",elements.imageButton],["video",elements.videoButton]]) button.disabled = !canUseMedia(backend,kind);
}

function canUseMedia(backend,kind) {
  return Boolean(state.uploads && state.media && backend?.capabilities[kind] && backend.available_inputs[kind]
    && modelReady() && !modelTransitioning() && !state.busy && !state.voice.backend_busy && !state.resetPending
    && !state.selectionPending && !state.requestUncertain && !state.media.uploading
    && !["listening","recognizing","thinking"].includes(state.voice.phase));
}

function renderBackend() {
  const backend = selectedBackend();
  if (!backend) return;
  elements.deviceName.textContent = backend.device;
  elements.deviceFootnote.textContent = `Local · ${backend.device.replace(/^NVIDIA\s+/, "")}`;
  elements.modelName.textContent = backend.name;
  const lastLatency = state.latency.get(backend.id);
  elements.latencyStatus.textContent = lastLatency == null ? "—" : `${(lastLatency / 1000).toFixed(1)} 秒`;
  elements.llmStatus.textContent = "检查中";
  setConnection("正在检查连接", false);
  renderCapabilities(backend);
  renderMessages();
  updateSendState();
}

async function getJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { cache: "no-store", ...options });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    console.warn("UI transport request failed", error);
    throw new Error(`${state.assistant?.name || "助手"}暂时无法连接到服务，请稍后重试。`);
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("界面服务返回了无法读取的数据。");
  }
  if (!response.ok) throw new Error(data.error?.message || `请求失败 (${response.status})`);
  return data;
}

async function checkStatus() {
  if (state.modelLifecycle) { await refreshModelState(); return; }
  if (!state.selectedId || state.busy) return;
  const backendId = state.selectedId;
  try {
    const result = await getJson(`/api/status?backend_id=${encodeURIComponent(backendId)}`);
    if (state.selectedId !== backendId) return;
    const connected = result.transport === "reachable";
    elements.llmStatus.textContent = connected ? "可连接" : "未连接";
    setConnection(connected ? "模型已连接" : "模型未连接", !connected);
  } catch {
    if (state.selectedId !== backendId) return;
    elements.llmStatus.textContent = "检查失败";
    setConnection("连接状态暂不可用", true);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const backend = selectedBackend();
  const text = elements.messageInput.value.trim();
  const attachment = state.media?.pending;
  if (!backend || (!backend.available_inputs.text && !attachment) || (!text && !attachment) || state.media?.uploading || state.requestUncertain || state.busy || state.resetPending
      || state.voice.backend_busy || state.voice.phase === "thinking" || !modelReady()) return;
  const backendId = backend.id;
  const messages = state.messages.get(backendId);
  const requestId = `${Date.now()}-${Math.random()}`;
  const userText = attachment ? `${text}${text ? "\n" : ""}[${attachment.kind === "image" ? "图片" : "视频"}：${state.media.name}]` : text;
  messages.push({ role: "user", text: userText }, { role: "assistant", text: "正在生成回答…", pending: true, requestId });
  const removePending = () => { const index = messages.findIndex(item => item.pending && item.requestId === requestId); if (index >= 0) messages.splice(index,1); };
  state.busy = true;
  renderMessages();
  updateSendState();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 130_000);
  let completed = false;
  try {
    const result = await getJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend_id: backendId, text, ...(attachment ? {attachment_id: attachment.attachment_id} : {}) }),
      signal: controller.signal,
    });
    completed = true;
    removePending();
    messages.push({ role: "assistant", text: result.text || "暂时没有收到回答。" });
    state.latency.set(backendId, result.latency_ms);
    elements.latencyStatus.textContent = `${(result.latency_ms / 1000).toFixed(1)} 秒`;
    elements.messageInput.value = "";
  } catch (error) {
    removePending();
    if (error.name === "AbortError") state.requestUncertain = true;
    const message = error.name === "AbortError"
      ? `${state.assistant.name}暂时没有回应；本次请求可能仍在处理，请稍后查看。`
      : error.message;
    messages.push({ role: "notice", text: message });
    elements.messageInput.value = "";
  } finally {
    clearTimeout(timer);
    state.busy = false;
    // Failed admission can leave an idle upload. DELETE cannot remove an
    // active inference lease, so uncertain requests remain safe as well.
    state.media?.clear({discard: !completed});
    renderMessages();
    updateSendState();
    elements.messageInput.focus();
    checkStatus();
    if (state.requestUncertain) refreshVoiceState();
  }
}

function applyVoiceState(snapshot, allowEpochReset = false) {
  if (snapshot.epoch && snapshot.epoch !== state.voiceEpoch) {
    if (state.voiceEpoch !== null && !allowEpochReset) return;
    state.voiceEpoch = snapshot.epoch;
    state.media?.clear();
    state.voiceRevision = -1;
    state.modelRevision = -1;
  }
  const revision = snapshot.event_id ?? snapshot.id;
  if (Number.isInteger(revision)) {
    if (revision < state.voiceRevision) return;
    state.voiceRevision = revision;
  }
  state.voice = { ...state.voice, ...snapshot };
  if (snapshot.backend_busy === false) state.requestUncertain = false;
  if (snapshot.model_state) applyModelState(snapshot.model_state);
  if (snapshot.selected_backend_id && state.backends.has(snapshot.selected_backend_id)
      && state.selectedId !== snapshot.selected_backend_id) {
    state.selectedId = snapshot.selected_backend_id;
    state.media?.clear();
    elements.backendSelect.value = state.selectedId;
    renderBackend();
    checkStatus();
  }
  renderVoiceControls();
  updateSendState();
}

function onVoiceEvent(event) {
  const type = event.type;
  if (type === "state") {
    applyVoiceState(event, true);
    return;
  }
  if (event.epoch && state.voiceEpoch && event.epoch !== state.voiceEpoch) return;
  if (type === "model_state") { applyModelState(event.model_state); return; }
  const fence = state.resetFence.get(event.backend_id);
  if (["transcript_ready", "thinking", "answered", "error"].includes(type)
      && fence && fence.epoch === event.epoch && Number.isInteger(event.id) && event.id <= fence.id) return;
  if (type === "selection") {
    applyVoiceState({ event_id: event.id, epoch: event.epoch, selected_backend_id: event.backend_id,
                      backend_busy: event.backend_busy });
    return;
  }
  if (type === "backend_busy") {
    if (event.backend_id === state.selectedId) applyVoiceState({ event_id: event.id, epoch: event.epoch, backend_busy: event.busy });
    return;
  }
  if (type === "asr_status") {
    applyVoiceState({ event_id: event.id, epoch: event.epoch, asr_connected: event.asr_connected, phase: event.phase,
                      request_id: event.asr_connected ? state.voice.request_id : null });
    return;
  }
  if (["listening", "recognizing", "thinking", "idle", "answered", "error"].includes(type)) {
    applyVoiceState({ event_id: event.id, epoch: event.epoch, phase: type === "answered" || type === "error" ? "idle" : type,
                      request_id: type === "idle" || type === "answered" || type === "error" ? null : event.request_id });
  }
  if (type === "transcript_ready") {
    const messages = state.messages.get(event.backend_id);
    if (!messages || messages.some((item) => item.voiceRequestId === event.request_id && item.role === "user")) return;
    messages.push({ role: "user", text: event.text, voiceRequestId: event.request_id });
    if (state.selectedId === event.backend_id) renderMessages();
  } else if (type === "thinking") {
    const messages = state.messages.get(event.backend_id);
    if (messages && !messages.some((item) => item.voiceRequestId === event.request_id && item.pending)) {
      messages.push({ role: "assistant", text: "正在生成回答…", pending: true,
                      voiceRequestId: event.request_id });
      if (state.selectedId === event.backend_id) renderMessages();
    }
  } else if (type === "answered" || type === "error") {
    const backendId = event.backend_id || state.selectedId;
    const messages = state.messages.get(backendId);
    if (messages) {
      const index = messages.findIndex((item) => item.voiceRequestId === event.request_id && item.pending);
      if (index >= 0) messages.splice(index, 1);
      if (!messages.some((item) => item.voiceRequestId === event.request_id && item.role === (type === "answered" ? "assistant" : "notice"))) {
        messages.push({ role: type === "answered" ? "assistant" : "notice",
                        text: type === "answered" ? event.text : event.message,
                        voiceRequestId: event.request_id });
      }
      if (type === "answered") state.latency.set(backendId, event.latency_ms);
      if (state.selectedId === backendId) renderMessages();
    }
  }
}

async function newConversation() {
  if (elements.newConversation.disabled) return;
  const backendId = state.selectedId;
  state.resetPending = true;
  updateSendState();
  try {
    const result = await getJson("/api/session/clear", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend_id: backendId }),
    });
    if (result.ok !== true || result.backend_id !== backendId) throw new Error("暂时无法开始新对话。");
    state.media?.clear();
    state.messages.set(backendId, []);
    state.latency.delete(backendId);
    state.resetFence.set(backendId, { id: result.event_id, epoch: result.epoch });
    elements.latencyStatus.textContent = "—";
  } catch (error) {
    state.messages.get(backendId).push({ role: "notice", text: error.message });
  } finally {
    state.resetPending = false;
    renderMessages();
    updateSendState();
  }
}

async function toggleVoice() {
  if (elements.voiceButton.disabled) return;
  const action = state.voice.phase === "listening" ? "stop" : "start";
  state.voiceControlPending = true;
  renderVoiceControls();
  try {
    const result = await getJson("/api/voice/control", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    applyVoiceState(result);
  } catch (error) {
    const messages = state.messages.get(state.selectedId);
    messages.push({ role: "notice", text: error.message });
    renderMessages();
    refreshVoiceState();
  } finally {
    state.voiceControlPending = false;
    renderVoiceControls();
  }
}

async function refreshVoiceState() {
  const revisionAtStart = state.voiceRevision;
  try {
    const snapshot = await getJson("/api/voice/state");
    if (snapshot.event_stream_token && snapshot.event_stream_token !== state.eventStreamToken) {
      state.eventStreamToken = snapshot.event_stream_token;
      connectVoiceEvents();
    }
    applyVoiceState(snapshot);
  }
  catch {
    if (state.voiceRevision === revisionAtStart) applyVoiceState({ asr_connected: false });
  }
}

function connectVoiceEvents() {
  if (typeof EventSource === "undefined") return;
  state.voiceEventSource?.close();
  const suffix = state.eventStreamToken ? `?token=${encodeURIComponent(state.eventStreamToken)}` : "";
  state.voiceEventSource = new EventSource(`/api/voice/events${suffix}`);
  state.voiceEventSource.addEventListener("voice", (message) => {
    try { onVoiceEvent(JSON.parse(message.data)); } catch { refreshVoiceState(); }
  });
  state.voiceEventSource.addEventListener("error", () => refreshVoiceState());
}

function setStatusPanel(open) {
  elements.statusPanel.hidden = !open;
  elements.statusToggle.setAttribute("aria-expanded", String(open));
}

async function initialize() {
  try {
    const [assistant, result] = await Promise.all([
      getJson("/api/assistant"), getJson("/api/backends"),
    ]);
    state.assistant = assistant;
    state.eventStreamToken = result.event_stream_token || null;
    state.modelLifecycle = Boolean(result.api_features?.model_lifecycle);
    state.uploads = Boolean(result.api_features?.uploads);
    if (typeof MediaComposer !== "function") throw new Error("附件组件加载失败，请刷新页面。");
    state.media = new MediaComposer({limits: result.attachment_limits || {image_bytes:10485760,video_bytes:52428800},
      onChange: updateSendState, onError: message => { state.messages.get(state.selectedId)?.push({role:"notice",text:message}); renderMessages(); }});
    elements.brandRoot.setAttribute("aria-label", `${assistant.name}，${assistant.subtitle}`);
    elements.brandMark.textContent = assistant.name.match(/[A-Za-z0-9]/)?.[0]?.toUpperCase() || assistant.name[0];
    elements.brandTag.textContent = assistant.subtitle;
    elements.assistantName.textContent = assistant.name;
    elements.welcomeName.textContent = assistant.name;
    elements.welcomeText.textContent = assistant.welcome.replace("。", "。\n");
    elements.messageInput.placeholder = `给${assistant.name}发消息…`;
    elements.messageLabel.textContent = `给${assistant.name}发消息`;
    elements.conversation.setAttribute("aria-label", `与${assistant.name}对话`);
    document.title = `${assistant.name} · Local AI`;
    elements.backendSelect.replaceChildren();
    for (const backend of result.backends) {
      state.backends.set(backend.id, backend);
      state.messages.set(backend.id, []);
      const option = document.createElement("option");
      option.value = backend.id;
      option.textContent = backend.name;
      option.title = `${backend.name} · ${backend.device}`;
      elements.backendSelect.append(option);
    }
    state.selectedId = result.default_backend_id;
    elements.backendSelect.value = state.selectedId;
    renderBackend();
    checkStatus();
    refreshVoiceState();
    connectVoiceEvents();
  } catch (error) {
    elements.llmStatus.textContent = "配置不可用";
    setConnection("配置暂不可用", true);
    elements.emptyDetail.hidden = false;
    elements.emptyDetail.textContent = error.message;
  }
}

elements.chatForm.addEventListener("submit", sendMessage);
elements.messageInput.addEventListener("input", updateSendState);
elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});
elements.backendSelect.addEventListener("change", async () => {
  if (state.media?.uploading) { elements.backendSelect.value=state.selectedId; return; }
  state.media?.clear();
  if (state.selectionPending) return;
  const previous = state.selectedId;
  const target = elements.backendSelect.value;
  if (state.modelLifecycle) {
    await requestModel("activate", target);
    return;
  }
  state.selectionPending = true;
  updateSendState();
  try {
    applyVoiceState(await getJson("/api/voice/selection", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend_id: target }),
    }));
  } catch (error) {
    elements.backendSelect.value = previous;
    state.messages.get(previous).push({ role: "notice", text: error.message });
    renderMessages();
    refreshVoiceState();
  } finally {
    state.selectionPending = false;
  }
  setStatusPanel(false);
  updateSendState();
});
elements.voiceButton.addEventListener("click", toggleVoice);
elements.imageButton.addEventListener("click", () => { if (!elements.imageButton.disabled) state.media.choose("image",state.selectedId); });
elements.videoButton.addEventListener("click", () => { if (!elements.videoButton.disabled) state.media.choose("video",state.selectedId); });
elements.newConversation.addEventListener("click", newConversation);
elements.modelStart.addEventListener("click", () => requestModel("activate", state.selectedId));
elements.modelStop.addEventListener("click", () => requestModel("stop"));
window.addEventListener("pagehide", () => {
  state.media?.dispose();
  if (state.voice.request_id && ["listening", "recognizing"].includes(state.voice.phase)) {
    navigator.sendBeacon?.("/api/voice/control", new Blob([JSON.stringify({ action: "stop" })],
                                                       { type: "application/json" }));
  }
  state.voiceEventSource?.close();
});
elements.statusToggle.addEventListener("click", () => setStatusPanel(elements.statusPanel.hidden));
elements.statusClose.addEventListener("click", () => setStatusPanel(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setStatusPanel(false);
});
document.addEventListener("click", (event) => {
  if (!elements.statusPanel.hidden && !elements.statusPanel.contains(event.target)
      && !elements.statusToggle.contains(event.target)) setStatusPanel(false);
});

initialize();
setInterval(checkStatus, 10_000);
setInterval(() => { if (modelTransitioning()) refreshModelState(); }, 1_000);
