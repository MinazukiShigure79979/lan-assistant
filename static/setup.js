(() => {
  const $ = (selector) => document.querySelector(selector);
  const els = {
    loading: $("#loadingPanel"),
    ollama: $("#ollamaPanel"),
    models: $("#modelsPanel"),
    done: $("#donePanel"),
    stepOllama: $("#stepOllama"),
    stepModels: $("#stepModels"),
    stepStart: $("#stepStart"),
    ollamaState: $("#ollamaState"),
    ollamaBar: $("#ollamaBar"),
    ollamaMessage: $("#ollamaMessage"),
    ollamaError: $("#ollamaError"),
    retryOllama: $("#retryOllama"),
    ollamaSource: $("#ollamaSource"),
    ollamaCustomUrl: $("#ollamaCustomUrl"),
    installOllama: $("#installOllama"),
    hardwareLine: $("#hardwareLine"),
    whisperList: $("#whisperList"),
    llmList: $("#llmList"),
    whisperCount: $("#whisperCount"),
    llmCount: $("#llmCount"),
    downloadBtn: $("#downloadBtn"),
    downloadSummary: $("#downloadSummary"),
    downloadHint: $("#downloadHint"),
    downloadProgress: $("#downloadProgress"),
    totalBar: $("#totalBar"),
    downloadMessage: $("#downloadMessage"),
    downloadList: $("#downloadList"),
    downloadError: $("#downloadError"),
  };
  const selectedWhisper = new Set();
  const selectedLlm = new Set();
  let current = null;
  let renderedOptionsKey = "";
  let pollTimer = null;
  const OLLAMA_SOURCES = {
    official: "https://ollama.com/download/ollama-windows-amd64.zip",
    github: "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `请求失败（${response.status}）`);
    }
    return data;
  }

  function setStep(active) {
    for (const [name, step] of [["ollama", els.stepOllama], ["models", els.stepModels], ["start", els.stepStart]]) {
      step.classList.toggle("active", name === active);
      step.classList.toggle("done", name === "ollama" ? active !== "ollama" : name === "models" ? active === "start" : false);
    }
  }

  function showPanel(name) {
    for (const panel of [els.loading, els.ollama, els.models, els.done]) {
      panel.classList.toggle("hidden", panel !== name);
    }
  }

  function card(item, kind) {
    const label = document.createElement("label");
    label.className = "model-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = item.id;
    input.checked = kind === "whisper" ? selectedWhisper.has(item.id) : selectedLlm.has(item.id);
    const copy = document.createElement("div");
    copy.className = "model-copy";
    const title = document.createElement("div");
    title.className = "model-title";
    title.appendChild(document.createTextNode(item.label));
    if (item.recommended) {
      const tag = document.createElement("span");
      tag.className = "tag recommend";
      tag.textContent = "推荐";
      title.appendChild(tag);
    }
    if (item.downloaded) {
      const tag = document.createElement("span");
      tag.className = "tag downloaded";
      tag.textContent = "已下载";
      title.appendChild(tag);
    }
    copy.appendChild(title);
    const desc = document.createElement("div");
    desc.className = "model-desc";
    desc.textContent = item.desc || "";
    copy.appendChild(desc);
    const meta = document.createElement("div");
    meta.className = "model-meta";
    meta.textContent = `${item.size || ""}${item.repo ? ` · ${item.repo}` : ""}`;
    copy.appendChild(meta);
    label.appendChild(input);
    label.appendChild(copy);
    label.classList.toggle("selected", input.checked);
    input.addEventListener("change", () => {
      const set = kind === "whisper" ? selectedWhisper : selectedLlm;
      if (input.checked) set.add(item.id);
      else set.delete(item.id);
      label.classList.toggle("selected", input.checked);
      updateAction();
    });
    return label;
  }

  function renderOptions(state) {
    const key = JSON.stringify({
      w: state.whisper.map((item) => [item.id, item.downloaded]),
      l: state.llm.map((item) => [item.id, item.downloaded]),
    });
    if (key === renderedOptionsKey) return;
    renderedOptionsKey = key;
    const hasWhisper = state.whisper.some((item) => selectedWhisper.has(item.id));
    const hasLlm = state.llm.some((item) => selectedLlm.has(item.id));
    if (!hasWhisper) {
      const pick = state.whisper.find((item) => item.recommended) ||
        state.whisper.find((item) => item.downloaded) || state.whisper[0];
      if (pick) selectedWhisper.add(pick.id);
    }
    if (!hasLlm) {
      const pick = state.llm.find((item) => item.recommended) ||
        state.llm.find((item) => item.downloaded) || state.llm[0];
      if (pick) selectedLlm.add(pick.id);
    }
    els.whisperList.replaceChildren(...state.whisper.map((item) => card(item, "whisper")));
    els.llmList.replaceChildren(...state.llm.map((item) => card(item, "llm")));
    els.whisperCount.textContent = `${state.whisper.length} 个选项`;
    els.llmCount.textContent = `${state.llm.length} 个选项`;
    updateAction();
  }

  function updateAction() {
    if (!current) return;
    const busy = !!current.busy;
    const whisperOk = current.whisper.some((item) => selectedWhisper.has(item.id));
    const llmOk = current.llm.some((item) => selectedLlm.has(item.id));
    const totalGb = (
      current.whisper.filter((item) => selectedWhisper.has(item.id))
        .concat(current.llm.filter((item) => selectedLlm.has(item.id)))
    ).reduce((sum, item) => sum + (Number(item.size_bytes) || 0), 0);
    els.downloadBtn.disabled = busy || !whisperOk || !llmOk;
    els.downloadBtn.textContent = busy ? "下载中…" : "下载并启动";
    els.downloadSummary.textContent = whisperOk && llmOk
      ? `已选择 ${selectedWhisper.size} 个语音模型 + ${selectedLlm.size} 个回答模型 · 约 ${(totalGb / 1024 ** 3).toFixed(1)} GB`
      : "请至少选择一个语音识别模型和一个回答模型";
    els.downloadHint.textContent = busy
      ? "下载完成后会自动启动主服务，请保持页面打开。"
      : "下载过程中可以停留在此页面；已完成文件会跳过。";
  }

  function hardwareLine(state) {
    const hw = state.hardware || {};
    const parts = [];
    if (hw.cpu) parts.push(hw.cpu);
    if (hw.ram_gb) parts.push(`${hw.ram_gb} GB RAM`);
    if (hw.gpu_label) parts.push(hw.gpu_label);
    if (hw.profile_label) parts.push(hw.profile_label);
    els.hardwareLine.textContent = parts.length
      ? `本机：${parts.join(" · ")}`
      : "未检测到详细硬件信息，将按默认配置推荐。";
  }

  function renderDownload(state) {
    const busy = !!state.busy || !!state.downloading;
    const show = busy || state.stage === "error";
    els.downloadProgress.classList.toggle("hidden", !show);
    if (!show) return;
    els.totalBar.style.width = `${Math.round((state.progress || 0) * 100)}%`;
    els.downloadMessage.textContent = state.message || "";
    els.downloadList.replaceChildren();
    for (const item of Object.values(state.downloads || {})) {
      const row = document.createElement("div");
      row.className = "download-item";
      const name = document.createElement("span");
      name.textContent = item.label || "";
      const status = document.createElement("b");
      status.textContent = item.status === "done"
        ? "完成"
        : item.status === "downloading"
          ? `${Math.round((item.progress || 0) * 100)}%`
          : "等待";
      row.appendChild(name);
      row.appendChild(status);
      els.downloadList.appendChild(row);
    }
  }

  function sync(state) {
    current = state;
    if (window.I18N) window.I18N.apply(state.ui_language || "zh");
    const versionLabel = document.getElementById("setupVersion");
    if (versionLabel && state.display_version) versionLabel.textContent = state.display_version;
    if (!state.required) {
      showPanel(els.done);
      setStep("start");
      window.setTimeout(() => window.location.replace("/"), 650);
      return;
    }
    const ollama = state.ollama || {};
    const installing = !!state.installing_ollama;
    const modelsReady = !!ollama.installed;
    if (!modelsReady) {
      setStep("ollama");
      showPanel(els.ollama);
      els.ollamaState.textContent = installing ? "正在安装…" : state.stage === "error" ? "安装失败" : "准备下载…";
      els.ollamaBar.style.width = `${Math.round((state.progress || 0) * 100)}%`;
      els.ollamaMessage.textContent = state.message || "下载约 1.6 GB，请保持网络连接。";
      els.ollamaError.textContent = state.error || "";
      els.ollamaError.classList.toggle("hidden", !state.error);
      els.retryOllama.classList.add("hidden");
      const custom = els.ollamaSource && els.ollamaSource.value === "custom";
      if (els.ollamaCustomUrl) els.ollamaCustomUrl.classList.toggle("hidden", !custom);
      if (els.ollamaSource) els.ollamaSource.disabled = installing;
      if (els.ollamaCustomUrl) els.ollamaCustomUrl.disabled = installing;
      if (els.installOllama) {
        els.installOllama.classList.toggle("hidden", installing);
        els.installOllama.disabled = installing;
        els.installOllama.textContent = state.error ? "重试安装" : "下载并安装 Ollama";
      }
      return;
    }
    setStep(state.stage === "done" ? "start" : "models");
    showPanel(els.models);
    hardwareLine(state);
    renderOptions(state);
    updateAction();
    renderDownload(state);
    els.downloadError.textContent = state.error || "";
    els.downloadError.classList.toggle("hidden", !state.error);
  }

  async function refresh() {
    try {
      const state = await api("/api/setup/state");
      sync(state);
    } catch (err) {
      els.loading.querySelector("p").textContent = `无法连接本地服务：${err.message}`;
    }
  }

  async function startDownload() {
    if (!current) return;
    els.downloadBtn.disabled = true;
    els.downloadError.classList.add("hidden");
    try {
      const result = await api("/api/setup/download", {
        method: "POST",
        body: JSON.stringify({
          whisper_ids: [...selectedWhisper],
          llm_ids: [...selectedLlm],
        }),
      });
      if (result.state) sync(result.state);
    } catch (err) {
      els.downloadBtn.disabled = false;
      els.downloadError.textContent = err.message;
      els.downloadError.classList.remove("hidden");
    }
  }

  async function startOllamaInstall() {
    const selected = els.ollamaSource ? els.ollamaSource.value : "official";
    const sourceUrl = selected === "custom"
      ? (els.ollamaCustomUrl ? els.ollamaCustomUrl.value.trim() : "")
      : OLLAMA_SOURCES[selected] || OLLAMA_SOURCES.official;
    if (!/^https?:\/\//i.test(sourceUrl)) {
      els.ollamaError.textContent = "请填写有效的 http/https Ollama ZIP 地址。";
      els.ollamaError.classList.remove("hidden");
      return;
    }
    if (els.installOllama) {
      els.installOllama.disabled = true;
      els.installOllama.textContent = "正在安装…";
    }
    els.ollamaError.classList.add("hidden");
    try {
      const result = await api("/api/setup/install-ollama", {
        method: "POST",
        body: JSON.stringify({ source_url: sourceUrl }),
      });
      if (result.error) throw new Error(result.error);
      refresh();
    } catch (err) {
      els.ollamaError.textContent = err.message;
      els.ollamaError.classList.remove("hidden");
      if (els.installOllama) {
        els.installOllama.disabled = false;
        els.installOllama.textContent = "重试安装";
      }
    }
  }

  els.downloadBtn.addEventListener("click", startDownload);
  if (els.installOllama) {
    els.installOllama.addEventListener("click", startOllamaInstall);
  }
  if (els.ollamaSource) {
    els.ollamaSource.addEventListener("change", () => {
      const custom = els.ollamaSource.value === "custom";
      if (els.ollamaCustomUrl) {
        els.ollamaCustomUrl.classList.toggle("hidden", !custom);
        if (custom) els.ollamaCustomUrl.focus();
      }
    });
  }
  els.retryOllama.addEventListener("click", startOllamaInstall);

  refresh();
  pollTimer = window.setInterval(refresh, 1000);
})();
