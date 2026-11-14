(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  let activeSpeechButton = null;
  let speechPollTimer = null;
  const TTS_ORIGIN = "http://127.0.0.1:8766";
  const state = {
    profile: {},
    settings: {},
    turns: [],
    suggestion: null,
    status: {
      asr: { status: "loading", message: "正在加载语音识别模型…" },
      llm: { status: "loading", message: "正在准备本地语言模型…" },
      capture: { running: false, started_at: null, error: "", devices: {} },
    },
    partial: { other: null, me: null },
    levels: { other: 0, me: 0 },
    devices: { loopback: [], microphones: [], default_loopback: null, default_mic: null },
    es: null,
    clock: null,
    startedAt: null,
    endedAt: null,
    alertKeys: new Set(),
    busy: false,
    demoTurns: null,
    hasOpened: false,
    mode: "call",
    overlayRunning: false,
    continuation: null,
    callSummary: null,
    meetingNotes: null,
    voiceInput: { status: "idle", id: 0 },
    voiceHistory: [],
    hardware: {},
    diarization: {},
    diarizationRun: {},
    hardwareRecommendation: null,
    models: { whisper: [], llm: [] },
    modelManagement: null,
    sessions: null,
    sessionOpen: null,
    version: "",
    displayVersion: "",
    profileDirty: false,
    profileSaving: false,
    profileSaveTimer: null,
    mediaSaveTimer: null,
  };

  const els = {
    alertWrap: $("#alerts"),
    appVersionLabel: $("#appVersionLabel"),
    profileName: $("#profileName"),
    profileGoal: $("#profileGoal"),
    profileAvatar: $("#profileAvatar"),
    pillAsr: $("#pillAsr"),
    pillLlm: $("#pillLlm"),
    btnStart: $("#btnStart"),
    btnStartLabel: $("#btnStartLabel"),
    btnExport: $("#btnExport"),
    captureState: $("#captureState"),
    timer: $("#timer"),
    turns: $("#turns"),
    emptyState: $("#emptyState"),
    partialWrap: $("#partialWrap"),
    partialSpeaker: $("#partialSpeaker"),
    partialText: $("#partialText"),
    partialZh: $("#partialZh"),
    transcriptScroll: $("#transcriptScroll"),
    asrLatency: $("#asrLatency"),
    meterOther: $("#meterOther"),
    meterMe: $("#meterMe"),
    channelOther: $("#channelOther"),
    channelMe: $("#channelMe"),
    suggestionBody: $("#suggestionBody"),
    suggestionMeta: $("#suggestionMeta"),
    coachScroll: $("#coachScroll"),
    toastWrap: $("#toastWrap"),
    profileDialog: $("#profileDialog"),
    profileForm: $("#profileForm"),
    profileSaveState: $("#profileSaveState"),
    mediaPresetState: $("#mediaPresetState"),
    settingsDialog: $("#settingsDialog"),
    settingsForm: $("#settingsForm"),
    settingsSaveStatus: $("#settingsSaveStatus"),
    btnRestartApp: $("#btnRestartApp"),
    setRunAsAdmin: $("#setRunAsAdmin"),
    sessionList: $("#sessionList"),
    sessionSummary: $("#sessionSummary"),
    sessionTypeFilter: $("#sessionTypeFilter"),
    sessionDeleteMode: $("#sessionDeleteMode"),
    sessionDeleteDays: $("#sessionDeleteDays"),
    sessionDialog: $("#sessionDialog"),
    sessionDialogKicker: $("#sessionDialogKicker"),
    sessionDialogTitle: $("#sessionDialogTitle"),
    sessionDialogMeta: $("#sessionDialogMeta"),
    sessionDialogBody: $("#sessionDialogBody"),
    sessionDialogState: $("#sessionDialogState"),
    settingsTabs: $("#settingsTabs"),
    settingsScopeHint: $("#settingsScopeHint"),
    setOtherDevice: $("#setOtherDevice"),
    setMicDevice: $("#setMicDevice"),
    deviceHint: $("#deviceHint"),
    modelStatus: $("#modelStatus"),
    llmTestResult: $("#llmTestResult"),
    setMicGain: $("#setMicGain"),
    setMicGainValue: $("#setMicGainValue"),
    setLongPassageDelay: $("#setLongPassageDelay"),
    setEchoGuard: $("#setEchoGuard"),
    setLlmProvider: $("#setLlmProvider"),
    providerHint: $("#providerHint"),
    deepseekCard: $("#deepseekCard"),
    btnResetDeepSeek: $("#btnResetDeepSeek"),
    apiBaseLabel: $("#apiBaseLabel"),
    apiModelLabel: $("#apiModelLabel"),
    apiThinkingLabel: $("#apiThinkingLabel"),
    apiFields: $("#apiFields"),
    setApiBase: $("#setApiBase"),
    setApiModel: $("#setApiModel"),
    setApiKey: $("#setApiKey"),
    apiKeyStatus: $("#apiKeyStatus"),
    btnClearApiProfile: $("#btnClearApiProfile"),
    setApiDisableThinking: $("#setApiDisableThinking"),
    apiHint: $("#apiHint"),
    setLocalModel: $("#setLocalModel"),
    setAsrModel: $("#setAsrModel"),
    setUiLanguage: $("#setUiLanguage"),
    btnRefreshModels: $("#btnRefreshModels"),
    modelManagerSummary: $("#modelManagerSummary"),
    modelManagerList: $("#modelManagerList"),
    btnRefreshModelManagement: $("#btnRefreshModelManagement"),
    btnAddCustomModel: $("#btnAddCustomModel"),
    setCustomModelKind: $("#setCustomModelKind"),
    setCustomModelLabel: $("#setCustomModelLabel"),
    setCustomModelPath: $("#setCustomModelPath"),
    setCustomModelSource: $("#setCustomModelSource"),
    setCallAsrLanguage: $("#setCallAsrLanguage"),
    setCallVocabulary: $("#setCallVocabulary"),
    setCallAutoSummary: $("#setCallAutoSummary"),
    setTranslateAsrLanguage: $("#setTranslateAsrLanguage"),
    setTranslateTargetLanguage: $("#setTranslateTargetLanguage"),
    setMeetingAsrLanguage: $("#setMeetingAsrLanguage"),
    modeSwitch: $("#modeSwitch"),
    transcriptKicker: $("#transcriptKicker"),
    transcriptHeading: $("#transcriptHeading"),
    featureNav: $("#featureNav"),
    featurePlaceholder: $("#featurePlaceholder"),
    placeholderTitle: $("#placeholderTitle"),
    placeholderText: $("#placeholderText"),
    placeholderKicker: $("#placeholderKicker"),
    btnRailSettings: $("#btnRailSettings"),
    globalModelLabel: $("#globalModelLabel"),
    btnOpenModelTab: $("#btnOpenModelTab"),
    meetingPanel: $("#meetingPanel"),
    meetingNotesBody: $("#meetingNotesBody"),
    meetingNotesEmpty: $("#meetingNotesEmpty"),
    meetingNotesMeta: $("#meetingNotesMeta"),
    btnGenerateNotes: $("#btnGenerateNotes"),
    btnExportNotes: $("#btnExportNotes"),
    setMeetingAutoNotes: $("#setMeetingAutoNotes"),
    setMeetingNotesLanguage: $("#setMeetingNotesLanguage"),
    setMeetingContext: $("#setMeetingContext"),
    setMeetingDiarization: $("#setMeetingDiarization"),
    setDiarizationModel: $("#setDiarizationModel"),
    diarizationStatus: $("#diarizationStatus"),
    meetingDiarizationHint: $("#meetingDiarizationHint"),
    btnDownloadDiarization: $("#btnDownloadDiarization"),
    btnRefreshDiarization: $("#btnRefreshDiarization"),
    hardwareSummary: $("#hardwareSummary"),
    hardwareHint: $("#hardwareHint"),
    btnApplyHardware: $("#btnApplyHardware"),
    setHardwareAuto: $("#setHardwareAuto"),
    setVoiceHotkey: $("#setVoiceHotkey"),
    setVoiceHotkeyHold: $("#setVoiceHotkeyHold"),
    setVoiceTriggerMode: $("#setVoiceTriggerMode"),
    setVoiceInputLanguage: $("#setVoiceInputLanguage"),
    setVoiceTargetLanguage: $("#setVoiceTargetLanguage"),
    setVoiceNoOverlayInsert: $("#setVoiceNoOverlayInsert"),
    setVoiceMaxSeconds: $("#setVoiceMaxSeconds"),
    setVoiceOverlayEnabled: $("#setVoiceOverlayEnabled"),
    setVoiceCueEnabled: $("#setVoiceCueEnabled"),
    setVoiceFocusSkipFullscreen: $("#setVoiceFocusSkipFullscreen"),
    setVoiceAutoPunctuation: $("#setVoiceAutoPunctuation"),
    setVoiceAutoProofread: $("#setVoiceAutoProofread"),
    setVoiceVocabulary: $("#setVoiceVocabulary"),
    setVoiceFocusSilence: $("#setVoiceFocusSilence"),
    setVoiceFocusTitles: $("#setVoiceFocusTitles"),
    voiceHotkeyText: $("#voiceHotkeyText"),
    voiceStatusText: $("#voiceStatusText"),
    voiceVocabularyMeta: $("#voiceVocabularyMeta"),
    voiceVocabularyPreview: $("#voiceVocabularyPreview"),
    voiceHistoryList: $("#voiceHistoryList"),
    btnEditVoiceVocabulary: $("#btnEditVoiceVocabulary"),
    btnClearVoiceHistory: $("#btnClearVoiceHistory"),
    btnVoiceStart: $("#btnVoiceStart"),
    btnVoiceCancel: $("#btnVoiceCancel"),
    brandEyebrow: $("#brandEyebrow"),
    brandTitle: $("#brandTitle"),
    brandSub: $("#brandSub"),
    emptyTitle: $("#emptyTitle"),
    emptyText: $("#emptyText"),
    emptySteps: $("#emptySteps"),
    btnEmptyStart: $("#btnEmptyStart"),
    otherChannelLabel: $("#otherChannelLabel"),
    footHint: $("#footHint"),
    btnOverlay: $("#btnOverlay"),
    btnProofread: $("#btnProofread"),
    btnExportSubtitles: $("#btnExportSubtitles"),
    btnStuck: $("#btnStuck"),
    btnCallSummary: $("#btnCallSummary"),
    callSummaryDialog: $("#callSummaryDialog"),
    callSummaryBody: $("#callSummaryBody"),
    callSummaryMeta: $("#callSummaryMeta"),
    btnCallSummaryRefresh: $("#btnCallSummaryRefresh"),
    btnExportCallSummary: $("#btnExportCallSummary"),
    btnCloseCallSummary: $("#btnCloseCallSummary"),
    btnCloseCallSummaryFoot: $("#btnCloseCallSummaryFoot"),
    setCallAutoSummary: $("#setCallAutoSummary"),
    btnProfile: $("#btnProfile"),
    continuationPanel: $("#continuationPanel"),
    continuationHint: $("#continuationHint"),
    continuationBody: $("#continuationBody"),
    btnContinuationRefresh: $("#btnContinuationRefresh"),
    subtitleDialog: $("#subtitleDialog"),
    subtitleEditorBody: $("#subtitleEditorBody"),
    subtitleEditorState: $("#subtitleEditorState"),
    btnCloseSubtitle: $("#btnCloseSubtitle"),
    btnCancelSubtitle: $("#btnCancelSubtitle"),
    btnSaveSubtitles: $("#btnSaveSubtitles"),
    setMediaType: $("#setMediaType"),
    setMediaTitle: $("#setMediaTitle"),
    setMediaContext: $("#setMediaContext"),
    setSubtitleStyle: $("#setSubtitleStyle"),
    setSubtitleSilence: $("#setSubtitleSilence"),
    setPartialInterval: $("#setPartialInterval"),
    setPartialWindow: $("#setPartialWindow"),
    setSubtitleMaxSegment: $("#setSubtitleMaxSegment"),
    setOverlayPoll: $("#setOverlayPoll"),
    setTranslateDelay: $("#setTranslateDelay"),
    setTranslateContext: $("#setTranslateContext"),
    setPartialContext: $("#setPartialContext"),
    setOverlayEnabled: $("#setOverlayEnabled"),
  };

  const APP_WINDOW_TITLE_MARKER = "\u2063";

  // ------------------------------------------------------------------ helpers
  function fmtClock(ts) {
    const d = new Date(ts * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
  }

  function fmtDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  async function api(path, options = {}) {
    const opts = { headers: { "Content-Type": "application/json" }, ...options };
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    const res = await fetch(path, opts);
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      data = {};
    }
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `请求失败 (${res.status})`);
    }
    return data;
  }

  function toast(message, kind = "") {
    const node = make("div", `toast ${kind}`.trim(), message);
    els.toastWrap.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transition = "opacity .2s";
      setTimeout(() => node.remove(), 220);
    }, 2300);
  }

  function showAlert(message, kind = "bad", key = message) {
    if (!message) return;
    const dedupeKey = `${kind}:${key}`;
    if (state.alertKeys.has(dedupeKey)) return;
    state.alertKeys.add(dedupeKey);
    const node = make("div", `alert ${kind === "info" ? "info" : ""}`.trim());
    const icon = make("span", "", kind === "info" ? "ℹ" : "!");
    icon.style.fontWeight = "700";
    node.appendChild(icon);
    node.appendChild(make("span", "", message));
    const close = make("button", "", "✕");
    close.setAttribute("aria-label", "关闭提示");
    close.addEventListener("click", () => {
      node.remove();
      state.alertKeys.delete(dedupeKey);
    });
    node.appendChild(close);
    els.alertWrap.appendChild(node);
    if (kind === "info") {
      setTimeout(() => {
        node.remove();
        state.alertKeys.delete(dedupeKey);
      }, 8000);
    }
  }

  function clearAlert(key) {
    for (const node of $$(".alert", els.alertWrap)) {
      if (node.textContent.includes(key)) node.remove();
    }
  }

  function isTypingTarget(target) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
  }

  function lastOtherTurn() {
    for (let i = state.turns.length - 1; i >= 0; i--) {
      if (state.turns[i].speaker === "other") return state.turns[i];
    }
    return null;
  }

  // -------------------------------------------------------------------- boot
  async function boot() {
    bind();
    try {
      const data = await api("/api/state");
      applyState(data);
    } catch (err) {
      showAlert(`无法连接本地服务：${err.message}。请确认启动窗口仍在运行。`, "bad", "boot");
    }
    if (window.I18N) window.I18N.apply(state.settings.ui_language || "zh");
    renderAll();
    connectEvents();
    startClock();
    loadDevices().catch(() => {});
    const bootParams = new URLSearchParams(location.search);
    if (
      !state.profile.name &&
      !state.profile.job &&
      !bootParams.has("demo") &&
      bootParams.get("settings") !== "1" &&
      bootParams.get("profile") !== "1"
    ) {
      setTimeout(() => openProfile(), 450);
    }
    if (new URLSearchParams(location.search).get("demo") === "1") {
      renderDemo();
    }
    const params = new URLSearchParams(location.search);
    if (params.get("still") === "1") document.documentElement.classList.add("still");
    if (params.get("settings") === "1") {
      openSettings(params.get("tab") || state.mode);
      if (params.get("provider")) {
        els.setLlmProvider.value = params.get("provider");
        syncApiFields(true);
      }
    }
    if (params.get("profile") === "1") openProfile();
    if (params.get("proofread") === "1") openSubtitleEditor();
  }

  function applyState(data) {
    state.version = data.version || "";
    state.displayVersion = data.display_version || "";
    if (els.appVersionLabel && state.displayVersion) {
      els.appVersionLabel.textContent = state.displayVersion;
    }
    state.profile = data.profile || {};
    state.settings = data.settings || {};
    state.hardware = data.hardware || {};
    state.diarization = data.diarization || state.diarization || {};
    state.diarizationRun = data.diarization_run || state.diarizationRun || {};
    if (els.diarizationStatus) renderDiarization();
    state.hardwareRecommendation = data.hardware_recommendation || null;
    state.models = data.models || state.models || { whisper: [], llm: [] };
    state.mode = data.mode || state.settings.app_mode || "call";
    state.overlayRunning = !!data.overlay_running;
    state.continuation = data.continuation || null;    state.callSummary = data.call_summary || null;
    state.meetingNotes = data.meeting_notes || null;
    state.voiceInput = data.voice_input || { status: "idle", id: 0 };
    state.voiceHistory = Array.isArray(data.voice_history) ? data.voice_history : [];
    state.turns = Array.isArray(data.turns) ? data.turns : [];
    state.suggestion = data.suggestion || null;
    state.status = data.status || state.status;
    state.partial = data.partial || { other: null, me: null };
    state.levels = data.levels || { other: 0, me: 0 };
    state.startedAt = state.status.capture && state.status.capture.running ? state.status.capture.started_at : null;
    state.endedAt = null;
  }

  function connectEvents() {
    if (state.es) state.es.close();
    const es = new EventSource("/api/events");
    state.es = es;
    es.onopen = () => {
      clearAlert("连接中断");
      clearAlert("无法连接");
      if (state.hasOpened) {
        // Events may have been missed while the stream was down.
        syncState();
      }
      state.hasOpened = true;
    };
    es.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      handleEvent(payload);
    };
    es.onerror = () => {
      showAlert("与本地服务的连接中断，正在自动重连…", "info", "连接中断");
    };
  }

  async function syncState() {
    if (new URLSearchParams(location.search).get("demo") === "1") return;
    try {
      const data = await api("/api/state");
      applyState(data);
      renderAll();
    } catch (_) {
      // The next reconnect attempt will try again.
    }
  }

  function handleEvent(ev) {
    const d = ev.data;    switch (ev.type) {
      case "hello":
        clearAlert("连接中断");
        break;
      case "status": {
        const wasRunning = state.status.capture && state.status.capture.running;
        state.status = d;
        if (d.capture && d.capture.running) {
          state.startedAt = d.capture.started_at;
          state.endedAt = null;
        } else if (wasRunning) {
          state.endedAt = Date.now();
        }
        renderStatus();
        renderTurns();
        renderSuggestion();
        break;
      }
      case "turn": {
        state.turns.push(d);
        state.partial[d.speaker] = null;
        renderTurns(true);
        renderPartial();
        if (d.speaker === "other") {
          // The old suggestion is for the previous sentence; wait for the
          // backend's "thinking" event instead of showing stale advice.
          state.suggestion = null;
          renderSuggestion();
        }
        break;
      }
      case "turn_update": {
        const turn = state.turns.find((t) => t.id === d.id);
        if (turn) {
          turn.zh = d.zh || turn.zh || "";
          if (d.en !== undefined) turn.en = d.en;
          if (d.speaker_label !== undefined) turn.speaker_label = d.speaker_label;
        }
        if (state.suggestion && state.suggestion.for_turn === d.id && !state.suggestion.translation) {
          state.suggestion.translation = d.zh || "";
        }
        renderTurns(false);
        renderSuggestion();
        break;
      }
      case "subtitles_updated": {
        const updates = Array.isArray(d) ? d : [];
        for (const item of updates) {
          const turn = state.turns.find((t) => t.id === item.id);
          if (turn) {
            turn.en = item.en || turn.en;
            turn.zh = item.zh || "";
            turn.edited = true;
          }
        }
        renderTurns(false);
        if (els.subtitleDialog.open) {
          els.subtitleEditorState.textContent = "字幕已在另一个页面更新，重新打开可查看最新内容";
        }
        break;
      }
      case "continuation":
        state.continuation = d || null;
        renderContinuation();
        break;
      case "call_summary":
        state.callSummary = d || null;
        renderCallSummary();
        if (d && d.status === "ready" && !els.callSummaryDialog.open) {
          toast("通话总结已生成，点击「通话总结」查看", "good");
        }
        break;
      case "meeting_notes":
        state.meetingNotes = d || null;
        renderMeetingNotes();
        break;
      case "voice_input":
        state.voiceInput = d || { status: "idle", id: 0 };
        renderVoiceInput();
        break;
      case "voice_history":
        state.voiceHistory = Array.isArray(d) ? d : [];
        renderVoiceHistory();
        break;
      case "turn_remove": {
        state.turns = state.turns.filter((t) => t.id !== (d && d.id));
        renderTurns(false);
        break;
      }
      case "partial":
        state.partial[d.speaker] = d;
        renderPartial();
        break;
      case "mode":
        state.mode = d.mode || state.mode;
        if (els.settingsDialog.open) applySettingsTab(state.mode);
        renderAll();
        break;
      case "overlay":
        state.overlayRunning = !!(d && d.running);
        renderMode();
        break;
      case "diarization":
        state.diarizationRun = d || {};
        if (d && d.status === "ready") refreshDiarization(
          (els.setDiarizationModel && els.setDiarizationModel.value) || "",
          false
        );
        if (state.mode === "meeting") renderMeetingNotes();
        break;
      case "ui_navigate": {
        const suffix = String((d && d.suffix) || "/");
        const query = suffix.includes("?") ? suffix.split("?")[1] : "";
        const params = new URLSearchParams(query);
        if (params.get("settings") === "1") {
          openSettings(params.get("tab") || state.mode);
        } else if (params.get("proofread") === "1") {
          openSubtitleEditor();
        } else {
          window.location.href = suffix;
        }
        break;
      }
      case "levels":
        state.levels = d;
        renderLevels();
        break;
      case "suggestion":
        state.suggestion = d;
        renderSuggestion();
        break;
      case "clear":
        state.turns = [];
        state.suggestion = null;
        state.continuation = null;
        state.callSummary = null;
        state.meetingNotes = null;
        state.partial = { other: null, me: null };
        renderTurns();
        renderSuggestion();
        renderContinuation();
        renderCallSummary();
        renderMeetingNotes();
        renderPartial();
        break;
      case "error":
        if (d && d.message) {
          const isInfo = d.scope === "translate" || d.scope === "llm";
          showAlert(d.message, isInfo ? "info" : "bad", d.scope + d.message);
          if (d.scope === "audio") toast(d.message, "bad");
        }
        break;
      default:
        break;
    }
  }

  // ------------------------------------------------------------------ render
  function renderAll() {
    renderMode();
    renderProfile();
    renderStatus();
    renderTurns();
    renderPartial();
    renderSuggestion();
    renderContinuation();
    renderCallSummary();
    renderMeetingNotes();
    renderVoiceInput();
    renderHardware();
    renderLevels();
    updateGlobalModelLabel();
    renderTimer();
  }

  function renderMode() {
    const mode = ["call", "translate", "voice", "meeting"].includes(state.mode) ? state.mode : "call";
    const call = mode === "call";
    const translate = mode === "translate";
    const voice = mode === "voice";
    const meeting = mode === "meeting";
    const placeholder = voice;
    for (const name of ["call", "translate", "voice", "meeting"]) {
      document.body.classList.toggle(`mode-${name}`, mode === name);
    }
    for (const button of $$(".rail-item", els.featureNav)) {
      const active = button.dataset.mode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    }
    els.meetingPanel.classList.toggle("hidden", !meeting);

    if (placeholder) {
      const title = "语音输入";
      els.placeholderKicker.textContent = "GLOBAL DICTATION";
      els.placeholderTitle.textContent = title;
      els.placeholderText.textContent = "在任意应用的输入框里按一次快捷键开始录音，再按一次结束；校对浮窗确认后，文字会回到刚才的输入框。";
      els.brandEyebrow.textContent = "LAN.ASSISTANT";
      els.brandTitle.textContent = "Lan.Assistant";
      els.brandSub.textContent = "语音输入 · 全局快捷键，校对后插入";
      els.featurePlaceholder.classList.remove("hidden");
      els.footHint.textContent = "语音输入 · 全局快捷键 · 校对浮窗 · 目标语言翻译";
      document.title = `${title} · Lan.Assistant`;
    } else {
      els.featurePlaceholder.classList.add("hidden");
      if (meeting) {
        els.transcriptKicker.textContent = "MEETING TRANSCRIPT";
        els.transcriptHeading.textContent = "会议转录";
        els.brandEyebrow.textContent = "LAN.ASSISTANT";
        els.brandTitle.textContent = "Lan.Assistant";
        els.brandSub.textContent = "会议纪要 · 全程转录，自动生成纪要";
        document.title = "Lan.Assistant · 会议纪要";
        els.otherChannelLabel.textContent = "系统声音 · 会议";
        els.emptyTitle.textContent = "开始记录这场会议";
        els.emptyText.textContent = "系统和麦克风会分别转写会议与你的发言；停止后自动生成摘要、关键讨论、决定和待办。";
        els.btnEmptyStart.textContent = "开始会议记录";
        els.footHint.innerHTML = "会议纪要 · 停止后自动生成 · 可导出 Markdown";
      } else {
        els.transcriptKicker.textContent = translate ? "LISTEN & TRANSLATE" : "LIVE TRANSCRIPT";
        els.transcriptHeading.textContent = translate ? "实时字幕" : "对方在说什么";
        els.brandEyebrow.textContent = "LAN.ASSISTANT";
        els.brandTitle.textContent = "Lan.Assistant";
        els.brandSub.textContent = translate
          ? "听读翻译 · 听剧、看电影，实时目标语言字幕"
          : "通话副驾 · 听对方，帮你想好下一句";
        document.title = translate ? "Lan.Assistant · 听读翻译" : "Lan.Assistant · 通话副驾";
        els.otherChannelLabel.textContent = translate ? "系统声音 · 正在播放" : "系统声音 · 对方";
        els.emptyTitle.textContent = translate ? "播放你想看懂的内容" : "把耳机戴好，剩下的交给它";
        els.emptyText.textContent = translate
          ? "把剧集、视频或播客的声音从电脑播放出来，这里会实时显示原文 + 目标语言字幕，不再给出回答建议。"
          : "开始通话后，它会实时听清对方，并用对方的语言给出可以直接说的回答。";
        els.btnEmptyStart.textContent = translate ? "开始听读" : "开始第一次通话";
        els.footHint.innerHTML = translate
          ? "听读模式 · 字幕自动生成 · 可把窗口缩小放在视频下方"
          : '按 <kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> 复制对应回答 · <kbd>R</kbd> 换一批';
      }
    }

    els.btnOverlay.classList.toggle("hidden", !translate);
    els.btnProofread.classList.toggle("hidden", !translate);
    els.btnExportSubtitles.classList.toggle("hidden", !translate);
    els.btnStuck.classList.toggle("hidden", !call);
    if (els.btnCallSummary) {
      const hasSummary = !!state.callSummary;
      els.btnCallSummary.classList.toggle("hidden", !call);
      els.btnCallSummary.textContent =
        state.callSummary && state.callSummary.status === "thinking" ? "总结生成中…" : "通话总结";
      els.btnCallSummary.disabled =
        !call || !!(state.callSummary && state.callSummary.status === "thinking") || !state.turns.length;
    }
    els.btnProfile.classList.toggle("hidden", !call);
    els.btnExport.classList.toggle("hidden", placeholder);
    els.btnStart.classList.toggle("hidden", placeholder);
    const exportCopy = {
      call: {
        label: "导出通话 JSON",
        title: "导出本次通话记录（JSON：双方原文、译文、AI 建议；不含通话总结 Markdown）",
      },
      translate: {
        label: "导出听读 JSON",
        title: "导出本次听读记录（JSON：字幕原文、译文、时间；不含 SRT）",
      },
      meeting: {
        label: "导出会议 JSON",
        title: "导出本次会议记录（JSON：转录、译文、会议纪要）",
      },
      voice: {
        label: "导出 JSON",
        title: "语音输入不使用此导出",
      },
    };
    if (els.btnExport) {
      const copy = exportCopy[mode] || { label: "导出 JSON", title: "导出当前会话的文字记录（JSON）" };
      els.btnExport.textContent = copy.label;
      els.btnExport.title = copy.title;
      els.btnExport.setAttribute("aria-label", copy.title);
    }
    if (!translate && els.subtitleDialog.open) els.subtitleDialog.close();
    if (translate) {
      els.btnOverlay.textContent = state.overlayRunning ? "关闭悬浮字幕" : "悬浮字幕";
    }
    // Invisible marker lets the tray distinguish this app window from another
    // browser tab whose title also happens to contain "Lan.Assistant".
    document.title = document.title.replace(/\u2063/g, "") + APP_WINDOW_TITLE_MARKER;
  }

  function updateGlobalModelLabel() {
    if (!els.globalModelLabel) return;
    const s = state.settings || {};
    const provider = s.llm_provider || "local";
    if (provider === "local") {
      const model = String(s.llm_model || "qwen3.5-copilot");
      els.globalModelLabel.textContent = model.includes("2.5") || model.includes("qwen-copilot")
        ? "本地 Qwen2.5-7B"
        : "本地 Qwen3.5-9B";
    } else if (provider === "deepseek") {
      els.globalModelLabel.textContent = `DeepSeek ${s.api_model || "deepseek-flash"}`;
    } else {
      els.globalModelLabel.textContent = `OpenAI 兼容 · ${s.api_model || "自定义模型"}`;
    }
  }

  function renderProfile() {
    const p = state.profile || {};
    const name = (p.name || "").trim();
    const job = (p.job || "").trim();
    const goal = (p.goal || "").trim();
    els.profileName.textContent = name ? `${name}${job ? " · " + job : ""}` : "还没有填写预设";
    els.profileGoal.textContent = goal || "告诉副驾你是谁、这次想聊什么，建议会更贴合你。";
    els.profileAvatar.textContent = name ? Array.from(name)[0] : "我";
  }

  function pillState(status) {
    if (status === "ready") return ["ready", "就绪"];
    if (status === "error") return ["error", "失败"];
    if (status === "loading") return ["loading", "准备中"];
    return ["idle", "待机"];
  }

  function renderStatus() {
    const asr = state.status.asr || {};
    const llm = state.status.llm || {};
    const [asrState, asrText] = pillState(asr.status);
    const [llmState, llmText] = pillState(llm.status);
    els.pillAsr.dataset.state = asrState;
    els.pillAsr.querySelector(".pill-detail").textContent = asrText;
    els.pillAsr.title = asr.message || "语音识别状态";
    els.pillLlm.dataset.state = llmState;
    els.pillLlm.querySelector(".pill-detail").textContent = llmText;
    els.pillLlm.title = llm.message || "语言模型状态";

    const running = !!(state.status.capture && state.status.capture.running);
    const isTranslate = state.mode === "translate";
    const isMeeting = state.mode === "meeting";
    els.btnStart.classList.toggle("running", running);
    els.btnStartLabel.textContent = isMeeting
      ? (running ? "结束记录" : "开始记录")
      : isTranslate
        ? (running ? "结束听读" : "开始听读")
        : (running ? "结束通话" : "开始通话");
    els.captureState.classList.toggle("on", running);
    els.captureState.querySelector("span").textContent = running
      ? (isMeeting ? "正在记录" : isTranslate ? "正在听读" : "正在通话")
      : "未开始";
    els.emptyState.classList.toggle("hidden", running || state.turns.length > 0);
    if (asr.status === "ready" && state.turns.length) {
      const last = state.turns[state.turns.length - 1];
      els.asrLatency.textContent = last.asr_ms ? `识别 ${last.asr_ms} ms` : "实时识别中";
    } else if (running) {
      els.asrLatency.textContent = "正在聆听…";
    } else {
      els.asrLatency.textContent = "等待开始";
    }
    updateModelStatus();
  }

  function renderLevels() {
    const o = Math.max(0, Math.min(1, state.levels.other || 0));
    const m = Math.max(0, Math.min(1, state.levels.me || 0));
    els.meterOther.style.width = `${Math.round(o * 100)}%`;
    els.meterMe.style.width = `${Math.round(m * 100)}%`;
    els.channelOther.classList.toggle("active", o > 0.035);
    els.channelMe.classList.toggle("active", m > 0.035);
  }

  function updateCallSummaryButton() {
    if (!els.btnCallSummary) return;
    const call = state.mode === "call";
    const thinking = !!(state.callSummary && state.callSummary.status === "thinking");
    els.btnCallSummary.classList.toggle("hidden", !call);
    els.btnCallSummary.textContent = thinking ? "总结生成中…" : "通话总结";
    els.btnCallSummary.disabled = thinking || !state.turns.length;
  }

  function renderTurns(scroll = false) {
    updateCallSummaryButton();
    const scroller = els.transcriptScroll;
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 90;
    const running = !!(state.status.capture && state.status.capture.running);
    els.turns.replaceChildren();
    const latestOther = lastOtherTurn();

    if (!state.turns.length) {
      if (running) {
        const card = make("div", "listening-card");
        const rings = make("div", "listening-rings");
        rings.appendChild(make("i"));
        rings.appendChild(make("i"));
        rings.appendChild(make("i"));
        card.appendChild(rings);
        card.appendChild(make("h3", "", state.mode === "translate"
          ? "正在听剧集 / 视频"
          : state.mode === "meeting" ? "正在记录会议" : "正在听对方说话"));
        card.appendChild(make("p", "", state.mode === "translate"
          ? "声音进来后会自动切句并生成目标语言字幕，看剧时可以把窗口缩小放在屏幕下方。"
          : state.mode === "meeting"
            ? "会议侧和麦克风会分别转写；停止后自动整理摘要、决定和待办。"
            : "等对方说完一句，回答会立刻出现在右边。建议戴耳机，避免麦克风把对方的声音也收进来。"));
        els.turns.appendChild(card);
      }
      els.emptyState.classList.toggle("hidden", running);
      els.partialWrap.classList.add("hidden");
      return;
    }

    for (const turn of state.turns) {
      const isOther = turn.speaker === "other";
      const article = make("article", `turn ${isOther ? "other" : "me"}` + (latestOther && turn.id === latestOther.id ? " latest" : ""));
      article.dataset.id = turn.id;
      const avatar = make("div", "turn-avatar", isOther ? "O" : "ME");
      const body = make("div", "turn-body");
      const meta = make("div", "turn-meta");
      meta.appendChild(make("span", "turn-speaker", isOther
        ? (turn.speaker_label || (state.mode === "meeting" ? "会议" : "对方"))
        : "我"));
      meta.appendChild(make("span", "turn-time", fmtClock(turn.ts)));
      body.appendChild(meta);
      body.appendChild(make("p", "turn-en", turn.en || ""));
      if (isOther && state.settings.translate !== false) {
        if (turn.zh) {
          body.appendChild(make("p", "turn-zh", turn.zh));
        } else if (state.suggestion && state.suggestion.status === "thinking" &&
                   (!state.suggestion.for_turn || state.suggestion.for_turn === turn.id)) {
          body.appendChild(make("p", "turn-zh pending", "译文准备中…"));
        }
      }
      article.appendChild(avatar);
      article.appendChild(body);
      els.turns.appendChild(article);
    }

    if (running || atBottom || scroll) {
      requestAnimationFrame(() => {
        scroller.scrollTop = scroller.scrollHeight;
      });
    }
    updateCallSummaryButton();
  }

  function renderPartial() {
    if (!state.turns || !(state.status.capture && state.status.capture.running)) {
      els.partialWrap.classList.add("hidden");
      return;
    }    const other = state.partial.other;
    const me = state.partial.me;
    let p = null;
    if (other && me) p = (other.ts || 0) >= (me.ts || 0) ? other : me;
    else p = other || me;
    if (!p || !p.text) {
      els.partialWrap.classList.add("hidden");
      return;
    }
    els.partialWrap.classList.remove("hidden");
    els.partialSpeaker.textContent = state.mode === "translate"
      ? "字幕识别中"
      : (p.speaker === "me" ? "我 · 识别中" : "对方 · 识别中");
    els.partialText.textContent = p.text;
    const partialZh = String(p.zh || "").trim();
    els.partialZh.textContent = partialZh;
    els.partialZh.classList.toggle("hidden", !partialZh);
    const scroller = els.transcriptScroll;
    if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120) {
      scroller.scrollTop = scroller.scrollHeight;
    }
  }

  function quoteCard(turn, label) {
    const card = make("div", "quote-card");
    card.appendChild(make("div", "quote-label", label));
    if (turn) {
      card.appendChild(make("div", "quote-en", turn.en || ""));
      if (state.settings.translate !== false) {
        card.appendChild(make("div", turn.zh ? "quote-zh" : "quote-zh pending", turn.zh || "译文准备中…"));
      }
    }
    return card;
  }

  function detectSpeechLanguage(text) {
    if (/[\u3040-\u30ff]/.test(text)) return "ja-JP";
    if (/[\uac00-\ud7af]/.test(text)) return "ko-KR";
    if (/[A-Za-z]/.test(text)) return "en-US";
    if (/[\u4e00-\u9fff]/.test(text)) return "zh-CN";
    return "en-US";
  }

  function resetSpeechButton(button) {
    if (speechPollTimer) {
      clearInterval(speechPollTimer);
      speechPollTimer = null;
    }
    if (!button) return;
    button.classList.remove("speaking");
    button.textContent = "朗读";
    button.setAttribute("aria-pressed", "false");
    if (activeSpeechButton === button) activeSpeechButton = null;
  }

  async function ttsRequest(path, options = {}) {
    const response = await fetch(TTS_ORIGIN + path, options);
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "朗读服务没有响应");
    }
    return data;
  }

  async function speakText(text, button) {
    if (!text) return;
    const wasActive = activeSpeechButton === button && button.classList.contains("speaking");
    if (activeSpeechButton) resetSpeechButton(activeSpeechButton);
    if (wasActive) {
      try {
        await ttsRequest("/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "stop" }),
        });
      } catch {
        // ignore stop errors
      }
      return;
    }
    try {
      const res = await ttsRequest("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, lang: detectSpeechLanguage(text) }),
      });
      if (!res.ok) {
        showAlert("朗读启动失败；请确认 Windows 语音服务可用。", "bad", "tts");
        return;
      }
      activeSpeechButton = button;
      button.classList.add("speaking");
      button.textContent = "停止";
      button.setAttribute("aria-pressed", "true");
      speechPollTimer = setInterval(async () => {
        try {
          const status = await ttsRequest("/api/tts/status");
          if (!status.running) resetSpeechButton(button);
        } catch {
          resetSpeechButton(button);
        }
      }, 350);
    } catch (err) {
      showAlert(
        `${err.message}。朗读服务未启动时，重启托盘服务会自动拉起。`,
        "bad",
        "tts",
      );
      resetSpeechButton(button);
    }
  }

  function makeSpeakButton(text) {
    const button = make("button", "speak-btn", "朗读");
    button.type = "button";
    button.title = "朗读这句话";
    button.setAttribute("aria-label", "朗读这句话");
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      speakText(text, button);
    });
    button.addEventListener("keydown", (event) => event.stopPropagation());
    return button;
  }

  function coachEmpty() {
    const wrap = make("div", "coach-empty");
    const icon = make("div", "coach-empty-icon");
    icon.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/><path d="M8 9h8M8 13h5"/></svg>';
    wrap.appendChild(icon);
    wrap.appendChild(make("h3", "", "回答会出现在这里"));
    wrap.appendChild(make("p", "", "对方说完一句后，这里会给出 1–4 个可以直接念的回答（默认 2 个），下面附中文翻译。"));
    const btn = make("button", "btn btn-ghost btn-small", "先给我一句开场白");
    btn.addEventListener("click", requestOpening);
    wrap.appendChild(btn);
    return wrap;
  }

  function renderSuggestion() {
    if (state.mode !== "call") return;
    const body = els.suggestionBody;
    body.replaceChildren();
    const s = state.suggestion;
    const latest = lastOtherTurn();
    const quoted = s && s.for_turn ? state.turns.find((t) => t.id === s.for_turn) || latest : latest;
    const introDetail = s && s.self_intro && s.intro_seconds
      ? (s.intro_seconds < 90
        ? ` · 约${s.intro_seconds}秒`
        : ` · 约${(s.intro_seconds / 60).toFixed(s.intro_seconds % 60 ? 1 : 0)}分钟`)
      : "";
    const modeLabel = s && s.self_intro
      ? `对方请你做自我介绍${introDetail}`
      : s && s.long_passage
        ? `对方刚才说 · 合并 ${s.merged_turns || 2} 句`
        : "对方刚才说";

    if (!s) {
      body.appendChild(coachEmpty());
      els.suggestionMeta.textContent = "等待对方说话…";
      return;
    }

    if (s.status === "thinking") {
      if (quoted) body.appendChild(quoteCard(quoted, s.opening ? "开场" : modeLabel));
      const head = make("div", "thinking-head");
      head.appendChild(make("div", "thinking-orb"));
      head.appendChild(make("span", "", s.translation || (latest && latest.zh) ? "翻译好了，正在组织更自然的回答…" : "正在听清并组织回答…"));
      body.appendChild(head);
      const skeletonCount = Math.max(1, Math.min(4, Number(state.settings.suggest_count) || 2));
      for (let i = 0; i < skeletonCount; i++) {
        const card = make("div", "skeleton-card");
        card.appendChild(make("div", "skeleton-line w80"));
        card.appendChild(make("div", "skeleton-line w55"));
        card.appendChild(make("div", "skeleton-line w35"));
        body.appendChild(card);
      }
      els.suggestionMeta.textContent = s.self_intro ? "正在准备自我介绍…" : s.long_passage ? "正在理解整段话…" : "正在生成回答…";
      return;
    }

    if (!(s.options && s.options.length)) {
      if (quoted) body.appendChild(quoteCard(quoted, modeLabel));
      const err = make("div", "suggestion-error");
      err.appendChild(make("div", "", s.error || "这次没有生成可用的回答。"));
      const retry = make("button", "btn btn-tiny", "重新生成");
      retry.addEventListener("click", requestRefresh);
      err.appendChild(retry);
      body.appendChild(err);
      els.suggestionMeta.textContent = "生成失败";
      return;
    }

    if (quoted && !s.opening) body.appendChild(quoteCard(quoted, modeLabel));

    if (s.fallback) {
      body.appendChild(make("div", "warn-strip", "本地模型暂时繁忙，先给你通用回答。稍后点「换一批」可以获得更贴合语境的版本。"));
    }

    const list = make("div", "options");
    s.options.forEach((option, index) => {
      const card = make("div", "option-card");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `复制第 ${index + 1} 个回答`);
      card.dataset.index = String(index);
      card.appendChild(make("div", "option-index", String(index + 1).padStart(2, "0")));
      card.appendChild(make("p", "option-en", option.en || ""));
      if (state.settings.translate !== false && option.zh) card.appendChild(make("p", "option-zh", option.zh));
      const foot = make("div", "option-foot");
      if (option.tone) foot.appendChild(make("span", "tone-chip", option.tone));
      const speak = makeSpeakButton(option.en || "");
      if (speak) foot.appendChild(speak);
      foot.appendChild(make("span", "copy-hint", "点击复制 ↗"));
      card.appendChild(foot);
      card.addEventListener("click", () => copyOption(index, card));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          copyOption(index, card);
        }
      });
      list.appendChild(card);
    });
    body.appendChild(list);

    if (s.note) {
      const note = make("div", "coach-note");
      note.appendChild(make("b", "", "提示"));
      note.appendChild(make("span", "", s.note));
      body.appendChild(note);
    }

    const latency = s.latency_ms ? ` · ${(s.latency_ms / 1000).toFixed(1)}s` : "";
    const modelLabel = s.model
      ? (String(s.model).includes("qwen") ? "本地模型" : String(s.model))
      : "模型";
    const modePrefix = s.self_intro
      ? `自我介绍${s.intro_seconds ? ` · 约${s.intro_seconds < 90 ? `${s.intro_seconds}秒` : `${(s.intro_seconds / 60).toFixed(s.intro_seconds % 60 ? 1 : 0)}分钟`}` : ""} · `
      : s.long_passage
        ? "长段落 · "
        : "";
    els.suggestionMeta.textContent = s.fallback ? "通用回答" : `${modePrefix}${modelLabel}${latency}`;
  }

  function lastMeTurn() {
    for (let i = state.turns.length - 1; i >= 0; i--) {
      if (state.turns[i].speaker === "me") return state.turns[i];
    }
    return null;
  }

  function latestMeFragment() {
    const partial = state.partial && state.partial.me;
    if (partial && partial.text) return String(partial.text).trim();
    const turn = lastMeTurn();
    return turn ? String(turn.en || "").trim() : "";
  }

  async function requestContinuation(fragmentOverride = "") {
    const fragment =
      String(fragmentOverride || "").trim() ||
      (state.continuation && state.continuation.fragment) ||
      latestMeFragment();
    if (!fragment) {
      toast("还没听到你说话；先说半句再点我卡住了");
      return;
    }
    try {
      await api("/api/continue", { method: "POST", body: { fragment } });
      toast("正在帮你想怎么接…");
    } catch (err) {
      showAlert(err.message, "bad", "continuation");
    }
  }

  async function copyContinuation(text, card) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      toast("已复制，可以接着说", "good");
      if (card) {
        card.classList.add("copied");
        setTimeout(() => card.classList.remove("copied"), 900);
      }
    } catch {
      showAlert("复制失败，请手动选择文字。", "bad", "copy-continuation");
    }
  }

  function prettyHotkey(value) {
    const parts = String(value || "ctrl+alt+space").split("+").map((part) => {
      if (part === "ctrl") return "Ctrl";
      if (part === "alt") return "Alt";
      if (part === "shift") return "Shift";
      if (part === "space") return "Space";
      return part.toUpperCase();
    });
    return parts.join(" + ");
  }

  function renderHardware() {
    if (!els.hardwareSummary) return;
    const h = state.hardware || {};
    if (!h.profile) {
      els.hardwareSummary.textContent = "正在检测 CPU / 显卡 / 内存…";
      els.hardwareHint.textContent = "";
      return;
    }
    const gpu = h.gpu_label || "未识别到 GPU";
    els.hardwareSummary.textContent =
      `${h.cpu || "未知 CPU"} · ${h.cpu_cores || "?"}C/${h.cpu_threads || "?"}T · ${h.ram_gb || "?"} GB · ${gpu}`;
    const modeLabels = {
      nvidia: "NVIDIA CUDA",
      amd_vulkan: "AMD Vulkan（核显/显卡）",
      intel_vulkan: "Intel Vulkan（核显/显卡）",
      cpu: "纯 CPU",
    };
    const s = state.settings || {};
    const rec = state.hardwareRecommendation || {};
    const currentMode = modeLabels[s.local_gpu_mode] || s.local_gpu_mode || "自动";
    els.hardwareHint.textContent =
      `检测：${modeLabels[h.profile] || h.profile} · 当前：${currentMode} / ASR ${s.asr_device || "auto"} / ${s.llm_model || ""}` +
      (rec.llm_model && rec.llm_model !== s.llm_model ? ` · 推荐模型 ${rec.llm_model}` : "");
  }

  function renderVoiceInput() {
    if (!els.voiceStatusText) return;
    const v = state.voiceInput || { status: "idle" };
    els.voiceHotkeyText.textContent = prettyHotkey(v.hotkey);
    let text = "待机 · 按快捷键开始";
    if (v.hotkey_error) text = `快捷键不可用：${v.hotkey_error}`;
    if (v.status === "recording") {
      text = v.hold
        ? "正在录音… 松开快捷键结束"
        : v.automatic ? "正在录音… 停顿后自动结束" : "正在录音… 再按一次快捷键结束";
    }
    if (v.status === "transcribing") text = "正在识别/校对…";
    if (v.status === "ready") text = v.proofread ? "识别完成 · 已自动校对" : "识别完成 · 校对浮窗已打开";
    if (v.status === "error") text = `失败：${v.error || "请重试"}`;
    els.voiceStatusText.textContent = text;
    els.btnVoiceStart.textContent = v.status === "recording" ? "结束录音" : "开始语音输入";
    els.btnVoiceStart.disabled = v.status === "transcribing" || v.status === "ready";
    els.btnVoiceCancel.classList.toggle("hidden", !["recording", "ready", "error"].includes(v.status));
    renderVoiceVocabulary();
    renderVoiceHistory();
  }

  function vocabularyEntries() {
    const raw = String((state.settings && state.settings.voice_vocabulary) || "");
    const entries = [];
    for (const line of raw.split(/\r?\n/)) {
      const value = line.trim();
      if (!value || value.startsWith("#")) continue;
      const parts = value.split("=");
      const canonical = (parts.length > 1 ? parts.slice(1).join("=") : value).trim();
      if (canonical) entries.push({ raw: value, canonical });
    }
    return entries;
  }

  function renderVoiceVocabulary() {
    if (!els.voiceVocabularyMeta) return;
    const entries = vocabularyEntries();
    const unique = [...new Set(entries.map((item) => item.canonical))];
    els.voiceVocabularyMeta.textContent = entries.length
      ? `${unique.length} 个规范词 · ${entries.length} 条规则`
      : "还没有词库";
    els.voiceVocabularyPreview.replaceChildren();
    if (!entries.length) {
      els.voiceVocabularyPreview.appendChild(
        make("span", "voice-vocab-empty", "添加公司名、项目名或常用术语，识别会更准。")
      );
      return;
    }
    for (const entry of entries.slice(0, 10)) {
      els.voiceVocabularyPreview.appendChild(make("span", "voice-vocab-chip", entry.raw));
    }
    if (entries.length > 10) {
      els.voiceVocabularyPreview.appendChild(
        make("span", "voice-vocab-chip more", `+${entries.length - 10}`)
      );
    }
  }

  function voiceHistoryTime(value) {
    const date = new Date((Number(value) || 0) * 1000);
    if (!Number.isFinite(date.getTime())) return "";
    return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
  }

  async function copyVoiceText(text, button) {
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      if (button) {
        const old = button.textContent;
        button.textContent = "已复制";
        setTimeout(() => { button.textContent = old; }, 1200);
      }
    } catch {
      showAlert("复制失败，请手动选择文字。", "bad", "voice-history-copy");
    }
  }

  function renderVoiceHistory() {
    const box = els.voiceHistoryList;
    if (!box) return;
    box.replaceChildren();
    const rows = state.voiceHistory || [];
    if (!rows.length) {
      const empty = make("div", "voice-history-empty");
      empty.appendChild(make("b", "", "还没有语音输入记录"));
      empty.appendChild(make("span", "", "按快捷键说一句，识别结果会出现在这里。"));
      box.appendChild(empty);
      return;
    }
    for (const item of rows.slice(0, 20)) {
      const card = make("article", "voice-history-item");
      const head = make("div", "voice-history-head");
      head.appendChild(make("span", "voice-history-time", voiceHistoryTime(item.created_at)));
      head.appendChild(
        make(
          "span",
          `voice-history-badge${item.proofread ? " good" : ""}`,
          item.proofread ? "已校对" : "原始识别"
        )
      );
      card.appendChild(head);
      const original = make("p", "voice-history-text", item.original || "");
      card.appendChild(original);
      if (item.translated) {
        card.appendChild(make("p", "voice-history-translation", item.translated));
      }
      const actions = make("div", "voice-history-actions");
      const copyOriginal = make("button", "link subtle", "复制原文");
      copyOriginal.type = "button";
      copyOriginal.addEventListener("click", () => copyVoiceText(item.original || "", copyOriginal));
      actions.appendChild(copyOriginal);
      if (item.translated) {
        const copyTranslation = make("button", "link subtle", "复制译文");
        copyTranslation.type = "button";
        copyTranslation.addEventListener("click", () =>
          copyVoiceText(item.translated || "", copyTranslation)
        );
        actions.appendChild(copyTranslation);
      }
      card.appendChild(actions);
      box.appendChild(card);
    }
  }

  function renderMeetingNotes() {
    if (!els.meetingPanel) return;
    if (state.mode !== "meeting") return;
    const data = state.meetingNotes;
    const notes = data && data.notes;
    const status = data && data.status;
    const diarRunning = (state.diarizationRun || {}).status === "running";
    els.meetingNotesBody.replaceChildren();
    els.meetingNotesEmpty.classList.toggle("hidden", !!data);
    els.btnGenerateNotes.disabled = status === "thinking" || diarRunning;
    els.btnGenerateNotes.textContent = diarRunning
      ? "区分与会人中…"
      : status === "thinking" ? "整理中…" : "生成纪要";
    els.btnExportNotes.disabled = !(status === "ready" && notes);
    if (!data) {
      const diag = state.diarizationRun || {};
      if (["running", "downloading", "extracting"].includes(diag.status)) {
        els.meetingNotesMeta.textContent =
          `${diag.message || "正在区分与会人…"} ${diag.progress || 0}%`;
      } else {
        els.meetingNotesMeta.textContent = "等待会议内容…";
      }
      return;
    }
    if (status === "thinking") {
      const box = make("div", "meeting-loading");
      box.textContent = data.chunks
        ? `正在整理会议纪要…（${data.chunks_done || 0}/${data.chunks} 段）`
        : "正在整理会议纪要…";
      els.meetingNotesBody.appendChild(box);
      els.meetingNotesMeta.textContent = `${data.transcript_chars || 0} 字转录 · 正在生成`;
      return;
    }
    if (status === "error") {
      els.meetingNotesBody.appendChild(make("div", "meeting-error", data.error || "生成失败，请重试。"));
      els.meetingNotesMeta.textContent = "生成失败";
      return;
    }
    if (!notes) {
      els.meetingNotesMeta.textContent = "还没有纪要";
      return;
    }
    const head = make("div", "meeting-note-section");
    head.appendChild(make("h2", "meeting-title", notes.title || "会议纪要"));
    const meta = make("div", "meeting-submeta",
      `${notes.model || "模型"}${data.generated_at ? " · " + new Date(data.generated_at).toLocaleString("zh-CN", { hour12: false }) : ""}`);
    head.appendChild(meta);
    if (notes.summary) head.appendChild(make("p", "", notes.summary));
    els.meetingNotesBody.appendChild(head);
    const sections = [
      ["KEY POINTS · 关键讨论", "key_points"],
      ["DECISIONS · 已确认决定", "decisions"],
      ["OPEN QUESTIONS · 未解决", "open_questions"],
    ];
    for (const [label, key] of sections) {
      const rows = Array.isArray(notes[key]) ? notes[key] : [];
      if (!rows.length) continue;
      const section = make("div", "meeting-note-section");
      section.appendChild(make("h3", "", label));
      const list = make("ul");
      for (const row of rows) list.appendChild(make("li", "", row));
      section.appendChild(list);
      els.meetingNotesBody.appendChild(section);
    }
    const actions = Array.isArray(notes.action_items) ? notes.action_items : [];
    if (actions.length) {
      const section = make("div", "meeting-note-section");
      section.appendChild(make("h3", "", "ACTION ITEMS · 待办"));
      const list = make("div");
      list.style.display = "grid";
      list.style.gap = "6px";
      for (const item of actions) {
        const row = make("div", "meeting-action");
        row.appendChild(make("span", "", item.task || ""));
        row.appendChild(make("small", "", item.owner || "负责人待定"));
        row.appendChild(make("small", "", item.due || "时间待定"));
        list.appendChild(row);
      }
      section.appendChild(list);
      els.meetingNotesBody.appendChild(section);
    }
    els.meetingNotesMeta.textContent = `${data.transcript_chars || 0} 字转录 · ${notes.latency_ms ? (notes.latency_ms / 1000).toFixed(1) + " 秒" : "已生成"}`;
  }

  function renderContinuation() {
    const box = els.continuationPanel;
    if (!box) return;
    const c = state.continuation;
    if (state.mode !== "call" || !c) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    const fragment = String(c.fragment || "").trim();
    els.continuationHint.textContent = c.status === "thinking"
      ? (c.auto ? "捕捉到你停顿了，正在想怎么接…" : "正在根据你刚才的话想怎么接…")
      : `你刚说到：${fragment.slice(0, 72)}${fragment.length > 72 ? "…" : ""}`;
    els.continuationBody.replaceChildren();
    els.btnContinuationRefresh.classList.toggle("hidden", c.status === "thinking");
    if (c.status === "thinking") {
      els.continuationBody.appendChild(make("div", "continuation-thinking", "正在组织一句可以顺着说下去的话…"));
      return;
    }
    if (c.status === "error") {
      const wrap = make("div", "continuation-thinking", c.error || "这次没有生成可用的续接建议");
      els.continuationBody.appendChild(wrap);
      return;
    }
    for (const option of c.options || []) {
      const card = make("div", "continuation-card");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.appendChild(make("div", "continuation-reply", option.en || ""));
      if (option.zh) card.appendChild(make("div", "continuation-zh", option.zh));
      const speak = makeSpeakButton(option.en || "");
      if (speak) {
        const foot = make("div", "continuation-foot");
        foot.appendChild(speak);
        card.appendChild(foot);
      }
      card.addEventListener("click", () => copyContinuation(option.en || "", card));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          copyContinuation(option.en || "", card);
        }
      });
      els.continuationBody.appendChild(card);
    }
  }

  function renderCallSummary() {
    const box = els.callSummaryBody;
    if (!box) return;
    updateCallSummaryButton();
    const c = state.callSummary;
    box.replaceChildren();
    if (!c) {
      els.callSummaryMeta.textContent = "通话结束后会自动生成，也可以点「重新生成」。";
      box.appendChild(make("div", "continuation-thinking", "还没有通话总结。"));
      return;
    }
    const status = c.status || "";
    if (status === "thinking") {
      els.callSummaryMeta.textContent = "正在整理问答复盘和英语反馈…";
      box.appendChild(make("div", "continuation-thinking", "正在根据本次通话整理总结…"));
      return;
    }
    const meta = [];
    if (c.model) meta.push(c.model);
    if (c.latency_ms) meta.push(`${(c.latency_ms / 1000).toFixed(1)} 秒`);
    els.callSummaryMeta.textContent = meta.length ? meta.join(" · ") : "通话总结";
    if (c.error) {
      box.appendChild(make("div", "continuation-thinking", `模型总结失败，已回退为逐题记录：${c.error}`));
    }
    const addSection = (title, body) => {
      const section = make("section", "meeting-note-section");
      section.appendChild(make("h3", "", title));
      section.appendChild(body);
      box.appendChild(section);
    };
    if (c.overview) {
      const p = make("p", "");
      p.textContent = c.overview;
      addSection("概述", p);
    }
    const questions = c.questions || [];
    if (questions.length) {
      const qaWrap = make("div", "");
      for (const [index, item] of questions.entries()) {
        const wrap = make("div", "");
        const q = make("p", "");
        q.textContent = `${index + 1}. ${item.question || ""}`;
        wrap.appendChild(q);
        if (item.answer) {
          const answer = make("p", "");
          answer.textContent = `你的回答：${item.answer}`;
          wrap.appendChild(answer);
        }
        if (item.improved_answer) {
          const improved = make("p", "");
          improved.textContent = `更自然：${item.improved_answer}`;
          wrap.appendChild(improved);
        }
        if (item.feedback) {
          const feedback = make("p", "");
          feedback.textContent = `反馈：${item.feedback}`;
          wrap.appendChild(feedback);
        }
        qaWrap.appendChild(wrap);
      }
      addSection("问答复盘", qaWrap);
    }
    for (const [key, title] of [["strengths", "做得好的地方"], ["improvements", "可改进的地方"]]) {
      const values = c[key] || [];
      if (!values.length) continue;
      const list = make("ul", "");
      for (const value of values) {
        const li = make("li", "");
        li.textContent = value;
        list.appendChild(li);
      }
      addSection(title, list);
    }
    const phrases = c.phrases || [];
    if (phrases.length) {
      const list = make("ul", "");
      for (const item of phrases) {
        const li = make("li", "");
        li.textContent = `${item.phrase || ""} — ${item.meaning || ""}`;
        list.appendChild(li);
      }
      addSection("可复习短语", list);
    }
    const intro = c.intro || {};
    if (intro.present) {
      const wrap = make("div", "");
      if (intro.feedback) {
        const p = make("p", "");
        p.textContent = intro.feedback;
        wrap.appendChild(p);
      }
      if (intro.improved) {
        const p = make("p", "");
        p.textContent = `改进版：${intro.improved}`;
        wrap.appendChild(p);
      }
      addSection("自我介绍复盘", wrap);
    }
    if (!box.children.length) {
      box.appendChild(make("div", "continuation-thinking", "本次总结没有可显示的内容。"));
    }
  }

  async function requestCallSummary() {
    try {
      await api("/api/call/summary", { method: "POST", body: {} });
      if (!els.callSummaryDialog.open) els.callSummaryDialog.showModal();
      renderCallSummary();
    } catch (err) {
      showAlert(err.message, "bad", "call-summary");
    }
  }

  function openCallSummary() {
    if (!els.callSummaryDialog.open) els.callSummaryDialog.showModal();
    if (!state.callSummary) requestCallSummary();
    else renderCallSummary();
  }

  function exportCallSummary() {
    if (!state.callSummary || state.callSummary.status !== "ready") {
      toast("通话总结还没有生成完成");
      return;
    }
    window.location.href = `/api/call/summary/export?t=${Date.now()}`;
  }

  async function copyOption(index, card) {
    const s = state.suggestion;
    if (!s || !s.options || !s.options[index]) return;
    const text = s.options[index].en || "";
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      card.classList.add("copied");
      const hint = $(".copy-hint", card);
      if (hint) hint.textContent = "已复制 ✓";
      toast("已复制，直接念出来就好", "good");
      setTimeout(() => {
        card.classList.remove("copied");
        if (hint) hint.textContent = "点击复制 ↗";
      }, 1800);
    } catch (_) {
      toast("复制失败，请手动选中文本", "bad");
    }
  }

  function renderTimer() {
    let elapsed = 0;
    if (state.startedAt) {
      const end = state.endedAt ? state.endedAt / 1000 : Date.now() / 1000;
      elapsed = end - state.startedAt;
    }
    els.timer.textContent = fmtDuration(elapsed);
  }

  function startClock() {
    if (state.clock) clearInterval(state.clock);
    state.clock = setInterval(renderTimer, 1000);
    renderTimer();
  }

  // ------------------------------------------------------------------ actions
  async function toggleCall() {
    if (state.mode === "voice") {
      toast("语音输入使用全局快捷键，不通过开始按钮采集");
      return;
    }
    if (state.busy) return;
    const running = !!(state.status.capture && state.status.capture.running);
    const translate = state.mode === "translate";
    const meeting = state.mode === "meeting";
    const actionName = meeting ? "会议记录" : translate ? "听读" : "通话";
    if (running) {
      if (!window.confirm(`结束本次${actionName}？文字记录会保留，音频不会被保存。`)) return;
    }
    state.busy = true;
    els.btnStart.disabled = true;
    try {
      if (running) {
        await api("/api/stop", { method: "POST", body: {} });
        state.endedAt = Date.now();
        toast(`已结束本次${actionName}`, "good");
      } else {
        const res = await api("/api/start", { method: "POST", body: { fresh: true } });
        state.startedAt = Date.now() / 1000;
        state.endedAt = null;
        toast(meeting ? "开始记录会议，停止后自动生成纪要" : translate ? "开始听读，字幕会自动出现" : "开始聆听，把耳机戴好", "good");
        if (res.warning) showAlert(res.warning, "bad", "mic-missing");
        // Pick up the cleared transcript immediately.
        const fresh = await api("/api/state");
        state.turns = fresh.turns || [];
        state.suggestion = fresh.suggestion || null;
        state.status = fresh.status || state.status;
        state.overlayRunning = !!fresh.overlay_running;
        renderAll();
      }
    } catch (err) {
      showAlert(err.message, "bad", "toggle");
    } finally {
      state.busy = false;
      els.btnStart.disabled = false;
      renderStatus();
    }
  }

  async function switchMode(mode) {
    if (!mode || mode === state.mode) return;
    const running = !!(state.status.capture && state.status.capture.running);
    if (running && !window.confirm("切换模式会停止当前采集并清空当前字幕，继续？")) {
      renderMode();
      return;
    }
    try {
      const res = await api("/api/mode", { method: "POST", body: { mode } });
      state.mode = res.mode || mode;
      state.settings.app_mode = state.mode;
      state.turns = [];
      state.suggestion = null;
      state.partial = { other: null, me: null };
      state.status.capture.running = false;
      state.overlayRunning = false;
      state.continuation = null;
      state.startedAt = null;
      state.endedAt = null;
      if (els.settingsDialog.open) applySettingsTab(state.mode);
      renderAll();
      const labels = { call: "通话副驾", translate: "听读翻译", voice: "语音输入", meeting: "会议纪要" };
      toast(`已切换到${labels[state.mode] || "通话副驾"}`, "good");
    } catch (err) {
      showAlert(err.message, "bad", "mode");
      renderMode();
    }
  }

  async function toggleOverlay() {
    try {
      const res = await api("/api/overlay", { method: "POST", body: { action: "toggle" } });
      state.overlayRunning = !!res.running;
      state.settings.overlay_enabled = state.overlayRunning;
      renderMode();
      toast(state.overlayRunning ? "悬浮字幕已开启，没有字幕时会自动隐藏" : "悬浮字幕已关闭", "good");
    } catch (err) {
      showAlert(err.message, "bad", "overlay");
    }
  }

  async function requestRefresh() {
    if (state.mode !== "call") return;    const latest = lastOtherTurn();
    try {
      await api("/api/suggest", { method: "POST", body: { opening: !latest } });
      if (!latest) toast("正在生成开场白…");
    } catch (err) {
      showAlert(err.message, "bad", "suggest");
    }
  }

  async function requestOpening() {
    if (state.mode !== "call") return;
    try {
      await api("/api/suggest", { method: "POST", body: { opening: true } });
      toast("正在生成开场白…");
    } catch (err) {
      showAlert(err.message, "bad", "opening");
    }
  }

  function subtitleTurns() {
    return state.turns.filter((turn) => turn.speaker === "other" && (turn.en || turn.zh));
  }

  function subtitleClock(turn) {
    try {
      return new Date((Number(turn.ts) || 0) * 1000).toLocaleTimeString("zh-CN", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return "";
    }
  }

  function translateTargetLabel() {
    const labels = { zh: "中文", en: "English", ja: "日本語", ko: "한국어" };
    const target = (state.settings && state.settings.translate_target_language) || "zh";
    return labels[target] || "译文";
  }

  function openSubtitleEditor() {
    const turns = subtitleTurns();
    if (!turns.length) {
      toast("还没有字幕可以校对");
      return;
    }
    els.subtitleEditorBody.replaceChildren();
    for (const turn of turns) {
      const row = make("div", "subtitle-edit-row");
      if (turn.edited) row.classList.add("edited");
      row.dataset.id = String(turn.id || "");
      row.appendChild(make("div", "subtitle-edit-time", subtitleClock(turn)));
      const fields = make("div", "subtitle-edit-fields");
      for (const [field, label, value] of [
        ["en", "原文", turn.en || ""],
        ["zh", translateTargetLabel(), turn.zh || ""],
      ]) {
        const wrap = make("label", "subtitle-edit-field");
        wrap.appendChild(make("span", "", label));
        const area = document.createElement("textarea");
        area.dataset.field = field;
        area.rows = 2;
        area.value = value;
        area.spellcheck = false;
        wrap.appendChild(area);
        fields.appendChild(wrap);
      }
      row.appendChild(fields);
      els.subtitleEditorBody.appendChild(row);
    }
    els.subtitleEditorState.textContent = `共 ${turns.length} 条 · 保存后悬浮字幕和导出文件立即使用修改版`;
    if (!els.subtitleDialog.open) els.subtitleDialog.showModal();
  }

  async function saveSubtitles() {
    const rows = $$(".subtitle-edit-row", els.subtitleEditorBody);
    if (!rows.length) {
      toast("没有可保存的字幕");
      return;
    }
    const updates = [];
    for (const row of rows) {
      const en = $('textarea[data-field="en"]', row)?.value.trim() || "";
      const zh = $('textarea[data-field="zh"]', row)?.value.trim() || "";
      if (!en) {
        showAlert("原文不能为空；如果这条不需要，可以保留原样。", "bad", "subtitle-save");
        return;
      }
      const turn = state.turns.find((t) => t.id === row.dataset.id);
      if (turn && (en !== (turn.en || "") || zh !== (turn.zh || ""))) {
        updates.push({ id: row.dataset.id, en, zh });
      }
    }
    if (!updates.length) {
      toast("字幕没有修改");
      return;
    }
    els.btnSaveSubtitles.disabled = true;
    els.btnSaveSubtitles.textContent = "保存中…";
    try {
      const res = await api("/api/subtitles/update", {
        method: "POST",
        body: { updates },
      });
      for (const item of res.turns || []) {
        const turn = state.turns.find((t) => t.id === item.id);
        if (turn) {
          turn.en = item.en;
          turn.zh = item.zh;
          turn.edited = true;
        }
      }
      renderTurns(false);
      for (const row of rows) row.classList.add("edited");
      els.subtitleEditorState.textContent = `已保存 ${res.updated || updates.length} 条 · 悬浮字幕和导出文件已更新`;
      toast("字幕校对已保存", "good");
    } catch (err) {
      showAlert(err.message, "bad", "subtitle-save");
    } finally {
      els.btnSaveSubtitles.disabled = false;
      els.btnSaveSubtitles.textContent = "保存字幕";
    }
  }

  function exportSubtitles() {
    const turns = subtitleTurns();
    if (!turns.length) {
      toast("还没有字幕可以导出");
      return;
    }
    const a = document.createElement("a");
    a.href = `/api/subtitles/export?format=srt&lang=both&t=${Date.now()}`;
    a.download = "lan-assistant-subtitles.srt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast("正在导出双语 SRT 字幕");
  }

  async function clearSession() {
    if (!state.turns.length && !state.suggestion) {
      toast("目前没有记录");
      return;
    }
    if (!window.confirm("清空本次对话记录和回答？")) return;
    try {
      await api("/api/clear", { method: "POST", body: {} });
      if (els.subtitleDialog.open) els.subtitleDialog.close();
      state.turns = [];
      state.suggestion = null;
      state.continuation = null;
      state.partial = { other: null, me: null };
      state.startedAt = state.status.capture && state.status.capture.running ? state.startedAt : null;
      state.endedAt = null;
      renderAll();
      toast("已清空");
    } catch (err) {
      showAlert(err.message, "bad", "clear");
    }
  }

  // ------------------------------------------------------------------ profile
  function collectProfile() {
    const data = {};
    for (const field of $$("[name]", els.profileForm)) {
      data[field.name] = field.value;
    }
    return data;
  }

  function setProfileSaveState(text) {
    if (els.profileSaveState) els.profileSaveState.textContent = text;
  }

  async function persistProfile() {
    if (state.profileSaving) return false;
    const data = collectProfile();
    const snapshot = JSON.stringify(data);
    state.profileSaving = true;
    try {
      const res = await api("/api/profile", { method: "POST", body: data });
      state.profile = res.profile || data;
      if (JSON.stringify(collectProfile()) === snapshot) {
        state.profileDirty = false;
      } else {
        state.profileDirty = true;
        if (!state.profileSaveTimer) {
          state.profileSaveTimer = setTimeout(() => {
            state.profileSaveTimer = null;
            persistProfile();
          }, 300);
        }
      }
      renderProfile();
      const now = new Date();
      setProfileSaveState(
        `已保存到本机 data/profile.json · ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`,
      );
      return true;
    } catch (err) {
      setProfileSaveState("自动保存失败，点「保存并使用」重试");
      return false;
    } finally {
      state.profileSaving = false;
    }
  }

  function scheduleProfileSave() {
    state.profileDirty = true;
    setProfileSaveState("正在输入… 自动保存");
    if (state.profileSaveTimer) clearTimeout(state.profileSaveTimer);
    state.profileSaveTimer = setTimeout(() => {
      state.profileSaveTimer = null;
      persistProfile();
    }, 650);
  }

  function fillProfileForm() {
    const p = state.profile || {};
    for (const field of $$("[name]", els.profileForm)) {
      if (field.tagName === "SELECT" || field.tagName === "INPUT" || field.tagName === "TEXTAREA") {
        field.value = p[field.name] !== undefined ? p[field.name] : "";
      }
    }
    state.profileDirty = false;
    setProfileSaveState(
      p.name || p.job ? "已从本机载入 · 修改会自动保存" : "填写后会自动保存到本机",
    );
  }

  async function openProfile() {
    await waitForProfileSave();
    fillProfileForm();
    if (!els.profileDialog.open) els.profileDialog.showModal();
    setTimeout(() => {
      const first = $('input[name="name"]', els.profileForm);
      if (first) first.focus();
    }, 80);
  }

  async function waitForProfileSave() {
    for (let i = 0; i < 20 && state.profileSaving; i++) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  async function closeProfile() {
    if (state.profileSaveTimer) {
      clearTimeout(state.profileSaveTimer);
      state.profileSaveTimer = null;
    }
    await waitForProfileSave();
    if (state.profileDirty) await persistProfile();
    if (els.profileDialog.open) els.profileDialog.close();
  }

  async function saveProfile(event) {
    event.preventDefault();
    if (state.profileSaveTimer) {
      clearTimeout(state.profileSaveTimer);
      state.profileSaveTimer = null;
    }
    await waitForProfileSave();
    const ok = await persistProfile();
    if (!ok) {
      showAlert("预设保存失败，请检查服务是否在运行", "bad", "profile-save");
      return;
    }
    if (els.profileDialog.open) els.profileDialog.close();
    toast("预设已保存到本机，下次打开会自动载入", "good");
  }

  // ----------------------------------------------------------------- settings
  async function loadDevices() {
    const data = await api("/api/devices");
    state.devices = data.devices || state.devices;
    fillDeviceSelects();
    return state.devices;
  }

  function fillDeviceSelects() {
    const devices = state.devices || {};
    const loop = devices.loopback || [];
    const mics = devices.microphones || [];
    const currentOther = state.settings.other_device;
    const currentMic = state.settings.mic_device;

    els.setOtherDevice.replaceChildren();
    if (!loop.length) {
      const opt = new Option("未找到系统声音设备", "");
      els.setOtherDevice.appendChild(opt);
    } else {
      for (const d of loop) {
        const opt = new Option(`${d.name}${d.is_default ? "（默认）" : ""}`, String(d.index));
        opt.dataset.name = d.name;
        els.setOtherDevice.appendChild(opt);
      }
      const target = currentOther && loop.find((d) => d.name === currentOther.name);
      const defaultLoop = devices.default_loopback
        ? loop.find((d) => d.index === devices.default_loopback.index)
        : null;
      els.setOtherDevice.value = String((target || defaultLoop || loop[0]).index);
    }

    els.setMicDevice.replaceChildren();
    const noneOpt = new Option("不采集我的声音（只建议对方的话）", "none");
    els.setMicDevice.appendChild(noneOpt);
    for (const d of mics) {
      const opt = new Option(`${d.name}${d.is_default ? "（默认）" : ""}`, String(d.index));
      opt.dataset.name = d.name;
      els.setMicDevice.appendChild(opt);
    }
    const targetMic = currentMic && mics.find((d) => d.name === currentMic.name);
    const defaultMic = devices.default_mic
      ? mics.find((d) => d.index === devices.default_mic.index)
      : null;
    if (state.settings.mic_enabled === false) {
      els.setMicDevice.value = "none";
    } else {
      els.setMicDevice.value = targetMic
        ? String(targetMic.index)
        : String((defaultMic || mics[0] || { index: "none" }).index);
    }

    const hint = els.deviceHint;
    hint.classList.remove("warn");
    if (!loop.length) {
      hint.textContent = "没有检测到系统声音回环设备。请先确认扬声器/耳机已连接并在 Windows 声音设置中启用，然后点「刷新设备」。";
      hint.classList.add("warn");
    } else {
      const otherName = (currentOther && currentOther.name) || (devices.default_loopback && devices.default_loopback.name) || loop[0].name;
      hint.textContent = `已找到 ${loop.length} 个系统声音设备、${mics.length} 个麦克风。当前对方声音：${otherName}。请选择微信通话实际播放声音的那个设备。`;
    }
  }

  function renderModelChoices(s = state.settings || {}) {
    const models = state.models || { whisper: [], llm: [] };
    const asr = els.setAsrModel;
    if (asr) {
      const current = String(s.asr_model || "auto");
      asr.replaceChildren();
      const auto = make("option", "", "自动（按硬件选择）");
      auto.value = "auto";
      asr.appendChild(auto);
      let matched = current === "auto";
      for (const item of models.whisper || []) {
        const option = make("option", "", `${item.label || item.name} · ${item.size || ""}`);
        option.value = item.name;
        if (current === item.name || current === item.path || current.split(/[\\/]/).pop() === item.name) {
          matched = true;
        }
        asr.appendChild(option);
      }
      if (!matched) {
        const option = make("option", "", current.split(/[\\/]/).pop() || current);
        option.value = current;
        asr.appendChild(option);
      }
      asr.value = current === "auto" ? "auto" : (
        Array.from(asr.options).find((opt) => opt.value === current ||
          current === (models.whisper || []).find((x) => x.name === opt.value)?.path ||
          current.split(/[\\/]/).pop() === opt.value)?.value || "auto"
      );
    }
    const llm = els.setLocalModel;
    if (llm) {
      const current = String(s.llm_model || "");
      llm.replaceChildren();
      let found = false;
      for (const item of models.llm || []) {
        const option = make("option", "", `${item.label || item.name} · ${item.size || ""}`);
        option.value = item.name;
        if (item.name === current) found = true;
        llm.appendChild(option);
      }
      if (!found) {
        const option = make("option", "", current || "未找到本地 GGUF 模型");
        option.value = current || "qwen3.5-copilot";
        llm.appendChild(option);
      }
      llm.value = current || (models.llm[0] && models.llm[0].name) || "qwen3.5-copilot";
    }
  }

  function formatBytes(value) {
    const size = Number(value) || 0;
    if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(1)} GB`;
    if (size >= 1024 ** 2) return `${Math.max(1, Math.round(size / 1024 ** 2))} MB`;
    return `${Math.max(0, Math.round(size / 1024))} KB`;
  }

  function renderModelManagement() {
    if (!els.modelManagerList) return;
    const data = state.modelManagement || { models: [], downloaded: 0, total: 0, total_size_bytes: 0 };
    if (els.modelManagerSummary) {
      els.modelManagerSummary.textContent =
        `${data.downloaded || 0}/${data.total || 0} 个已下载 · 模型目录 ${formatBytes(data.disk_size_bytes || data.total_size_bytes)}`;
    }
    els.modelManagerList.replaceChildren();
    if (!(data.models || []).length) {
      els.modelManagerList.appendChild(make("div", "model-status", "还没有模型条目。"));
      return;
    }
    const kindOrder = { whisper: 0, llm: 1, diarization: 2 };
    const kindLabels = {
      whisper: "语音识别模型",
      llm: "语言模型",
      diarization: "说话人区分模型",
    };
    const kindBadges = { whisper: "ASR", llm: "LLM", diarization: "DIA" };
    const models = [...(data.models || [])].sort(
      (a, b) => (kindOrder[a.kind] ?? 9) - (kindOrder[b.kind] ?? 9)
    );
    let lastKind = "";
    for (const item of models) {
      if (item.kind !== lastKind) {
        lastKind = item.kind;
        els.modelManagerList.appendChild(
          make("div", "model-group-title", kindLabels[item.kind] || item.kind)
        );
      }
      const row = make("div", "model-row");
      const main = make("div", "model-row-main");
      const title = make("div", "model-row-title");
      title.appendChild(make("span", "model-kind", kindBadges[item.kind] || "MODEL"));
      title.appendChild(make("b", "", item.label || item.name || "未命名模型"));
      if (item.custom) title.appendChild(make("span", "model-kind", "自定义"));
      main.appendChild(title);
      const path = String(item.path || "");
      const relative = path.replace(String(data.models_dir || ""), "models").replace(/\\/g, "/");
      main.appendChild(make("div", "model-row-path", relative || path));
      row.appendChild(main);
      const status = make(
        "span",
        `model-row-status${item.downloaded ? " ok" : ""}`,
        item.downloaded ? "已下载" : "未下载"
      );
      row.appendChild(status);
      row.appendChild(make("span", "model-row-size", item.downloaded ? item.size || "" : "—"));
      if (item.custom) {
        row.appendChild(make("span", "model-row-note", "手动导入"));
      } else if (!item.downloaded) {
        const download = make("button", "btn btn-tiny", "下载");
        download.type = "button";
        download.addEventListener("click", async () => {
          download.disabled = true;
          download.textContent = "下载中…";
          try {
            if (item.kind === "diarization") {
              await api("/api/diarization/download-component", {
                method: "POST",
                body: { component: item.id },
              });
            } else {
              await api("/api/models/download", {
                method: "POST",
                body: { kind: item.kind, id: item.id },
              });
            }
            const poll = async (tries = 0) => {
              await new Promise((resolve) => setTimeout(resolve, 1500));
              try {
                state.modelManagement = await api("/api/model-management");
                renderModelManagement();
                const rowNow = (state.modelManagement.models || []).find((rowItem) => rowItem.id === item.id);
                if (rowNow && rowNow.downloaded) {
                  state.models = await api("/api/models?refresh=1");
                  renderModelChoices(state.settings);
                  if (item.kind === "diarization") {
                    await refreshDiarization(
                      (els.setDiarizationModel && els.setDiarizationModel.value) || "",
                      false
                    );
                  }
                  toast(`${item.label || item.name} 已下载完成`, "good");
                } else if (tries < 800) {
                  setTimeout(() => poll(tries + 1), 1500);
                }
              } catch (err) {
                toast(`下载状态刷新失败：${err.message}`, "bad");
              }
            };
            setTimeout(() => poll(), 800);
          } catch (err) {
            toast(`下载失败：${err.message}`, "bad");
            renderModelManagement();
          }
        });
        row.appendChild(download);
      } else {
        const del = make("button", "btn btn-tiny", "删除");
        del.type = "button";
        del.addEventListener("click", async () => {
          if (!window.confirm(`删除本地模型「${item.label || item.name}」？\n这会删除磁盘文件，不能撤销。`)) return;
          del.disabled = true;
          del.textContent = "删除中…";
          try {
            const res = await api("/api/model-management/delete", {
              method: "POST",
              body: { id: item.id },
            });
            state.modelManagement = res.management || null;
            if (res.settings) state.settings = res.settings;
            state.models = await api("/api/models?refresh=1");
            renderModelChoices(state.settings);
            renderModelManagement();
            renderHardware();
            if (item.kind === "diarization") {
              await refreshDiarization(
                (els.setDiarizationModel && els.setDiarizationModel.value) || "",
                false
              );
            }
            toast("模型已删除", "good");
          } catch (err) {
            toast(err.message, "bad");
            del.disabled = false;
            del.textContent = "删除";
          }
        });
        row.appendChild(del);
      }
      els.modelManagerList.appendChild(row);
    }
  }

  async function loadModelManagement() {
    try {
      state.modelManagement = await api("/api/model-management");
      renderModelManagement();
    } catch (err) {
      if (els.modelManagerSummary) els.modelManagerSummary.textContent = `读取失败：${err.message}`;
    }
  }

  async function addCustomModel() {
    const kind = els.setCustomModelKind.value;
    const label = els.setCustomModelLabel.value.trim();
    const path = els.setCustomModelPath.value.trim();
    const source = els.setCustomModelSource.value.trim();
    if (!path) {
      toast("请填写模型文件或目录路径", "bad");
      return;
    }
    els.btnAddCustomModel.disabled = true;
    try {
      const res = await api("/api/model-management/add", {
        method: "POST",
        body: { kind, label, path, source },
      });
      state.modelManagement = res.management || null;
      els.setCustomModelLabel.value = "";
      els.setCustomModelPath.value = "";
      els.setCustomModelSource.value = "";
      state.models = await api("/api/models?refresh=1");
      renderModelChoices(state.settings);
      renderModelManagement();
      toast("模型已加入列表", "good");
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      els.btnAddCustomModel.disabled = false;
    }
  }

  function renderApiKeyStatus(saved) {
    if (!els.apiKeyStatus) return;
    const has = !!saved;
    els.apiKeyStatus.textContent = has
      ? "已保存在本机（不会随项目迁移）；留空表示不修改。"
      : "尚未在本机保存；填写后只加密保存在当前电脑。";
    els.apiKeyStatus.classList.toggle("saved", has);
    els.apiKeyStatus.classList.toggle("empty", !has);
    els.setApiKey.placeholder = has
      ? "已保存在本机；留空不修改"
      : "sk-...（只保存在本机）";
    if (els.btnClearApiProfile) els.btnClearApiProfile.disabled = !has;
  }

  function fillSettingsForm() {
    const s = state.settings || {};
    if (els.setUiLanguage) els.setUiLanguage.value = s.ui_language || "zh";
    $("#setAutoSuggest").checked = s.auto_suggest !== false;
    $("#setTranslate").checked = s.translate !== false;
    $("#setPartial").checked = s.partial_asr !== false;
    $("#setSaveTranscript").checked = s.save_transcript !== false;
    $("#setRestoreLastMode").checked = s.restore_last_mode !== false;
    $("#setStartWithWindows").checked = s.start_with_windows === true;
    els.setEchoGuard.checked = s.echo_guard !== false;
    $("#setSuggestCount").value = String(s.suggest_count || 2);
    $("#setCallSilenceMs").value = String(s.call_silence_ms || s.silence_ms || 750);
    els.setCallVocabulary.value = s.call_vocabulary || "";
    els.setCallAutoSummary.checked = s.call_auto_summary !== false;
    els.setLongPassageDelay.value = String(s.long_passage_delay_ms || 2800);
    const gain = Number(s.mic_gain || 1);
    els.setMicGain.value = String(gain);
    els.setMicGainValue.textContent = `${gain.toFixed(1)}×`;
    els.setLlmProvider.value = s.llm_provider || "local";
    els.setApiBase.value = s.api_base_url || "https://api.deepseek.com";
    els.setApiModel.value = s.api_model || "deepseek-flash";
    els.setApiKey.value = "";
    renderApiKeyStatus(!!s.api_key_saved);
    els.setApiDisableThinking.checked = s.api_disable_thinking !== false;
    renderModelChoices(s);
    els.setLocalModel.value = s.llm_model || (state.models.llm[0] && state.models.llm[0].name) || "qwen3.5-copilot";
    const fallbackAsrLanguage = s.asr_language || "en";
    els.setCallAsrLanguage.value = s.call_asr_language || fallbackAsrLanguage;
    els.setTranslateAsrLanguage.value = s.translate_asr_language || fallbackAsrLanguage;
    els.setTranslateTargetLanguage.value = s.translate_target_language || "zh";
    els.setMeetingAsrLanguage.value = s.meeting_asr_language || "zh";
    els.setMediaType.value = s.media_type || "auto";
    els.setMediaTitle.value = s.media_title || "";
    els.setMediaContext.value = s.media_context || "";
    els.setSubtitleStyle.value = s.subtitle_style || "natural";
    els.setSubtitleSilence.value = String(s.subtitle_silence_ms || 350);
    els.setPartialInterval.value = String(s.partial_interval_ms || 800);
    els.setPartialWindow.value = String(s.partial_window_ms || 6000);
    els.setSubtitleMaxSegment.value = String(s.subtitle_max_segment_s || 6);
    els.setOverlayPoll.value = String(s.overlay_poll_ms || 150);
    els.setTranslateDelay.value = String(s.translate_delay_ms ?? 80);
    els.setTranslateContext.value = String(s.translate_context_turns ?? 6);
    els.setPartialContext.value = String(s.partial_context_turns ?? 4);
    els.setOverlayEnabled.checked = s.overlay_enabled !== false;
    els.setMeetingAutoNotes.checked = s.meeting_auto_notes !== false;
    els.setMeetingNotesLanguage.value = s.meeting_notes_language || "zh";
    els.setMeetingContext.value = s.meeting_context || "";
    els.setMeetingDiarization.checked = s.diarization_enabled === true;
    if (els.setDiarizationModel) {
      els.setDiarizationModel.value = s.diarization_model || "wespeaker_en_voxceleb_resnet34";
    }
    els.setRunAsAdmin.checked = s.run_as_admin === true;
    els.setVoiceHotkey.value = s.voice_hotkey || "ctrl+alt+space";
    els.setVoiceHotkeyHold.value = s.voice_hotkey_hold ? "true" : "false";
    els.setHardwareAuto.checked = s.hardware_auto_configure !== false;
    els.setVoiceTriggerMode.value = s.voice_trigger_mode || "hotkey";
    els.setVoiceInputLanguage.value = s.voice_input_language || "zh";
    els.setVoiceTargetLanguage.value = s.voice_target_language || "zh";
    els.setVoiceNoOverlayInsert.value = s.voice_no_overlay_insert || "translation";
    els.setVoiceMaxSeconds.value = String(s.voice_max_seconds || 90);
    els.setVoiceOverlayEnabled.checked = s.voice_overlay_enabled !== false;
    els.setVoiceCueEnabled.checked = s.voice_cue_enabled !== false;
    els.setVoiceFocusSkipFullscreen.checked = s.voice_focus_skip_fullscreen !== false;
    els.setVoiceAutoPunctuation.checked = s.voice_auto_punctuation !== false;
    els.setVoiceAutoProofread.checked = s.voice_auto_proofread !== false;
    els.setVoiceVocabulary.value = s.voice_vocabulary || "";
    els.setVoiceFocusSilence.value = String(s.voice_focus_silence_ms || 1200);
    els.setVoiceFocusTitles.value = s.voice_focus_titles || "DeepSeek Harness";
    setMediaPresetState("已从本机载入 · 修改会自动保存");
    syncApiFields(false);
    renderDiarization();
    els.llmTestResult.classList.add("hidden");
    els.llmTestResult.textContent = "";
  }

  function diarizationModelInfo(modelId) {
    const models = (state.diarization && state.diarization.models) || [];
    return models.find((row) => row.id === modelId) || null;
  }

  function renderDiarization() {
    if (!els.diarizationStatus || !els.setMeetingDiarization) return;
    const data = state.diarization || {};
    const modelId = (els.setDiarizationModel && els.setDiarizationModel.value)
      || (state.settings && state.settings.diarization_model)
      || "wespeaker_en_voxceleb_resnet34";
    const info = diarizationModelInfo(modelId);
    const status = data.status || {};
    const running = ["downloading", "extracting"].includes(status.status);
    const ready = !!(info && info.downloaded && data.segmentation_downloaded) && !running;
    const library = data.library !== false;
    let text;
    if (!library) {
      text = "缺少 sherpa-onnx 运行库，无法使用说话人区分。";
    } else if (running) {
      text = `${status.message || "正在下载模型…"} ${status.progress || 0}%`;
    } else if (status.status === "error" && status.error) {
      text = `模型准备失败：${status.error}`;
    } else if (ready) {
      text = `已就绪：${info ? info.short : "声纹模型"} + pyannote 分割模型。停止记录后会自动区分与会人。`;
    } else {
      text = `尚未下载${info ? ` ${info.label}` : ""}，需要先下载约 ${info ? info.size : "几十 MB"} 的模型。`;
    }
    els.diarizationStatus.textContent = text;
    if (els.btnDownloadDiarization) {
      els.btnDownloadDiarization.disabled = running;
      els.btnDownloadDiarization.textContent = running
        ? `下载中 ${status.progress || 0}%`
        : ready ? "重新下载模型" : "下载说话人区分模型";
    }
    const enabled = ready && library;
    els.setMeetingDiarization.disabled = !enabled;
    if (!enabled && els.setMeetingDiarization.checked) {
      els.setMeetingDiarization.checked = false;
    }
    if (els.meetingDiarizationHint) {
      els.meetingDiarizationHint.textContent = enabled
        ? "停止记录后会在本机离线区分不同与会人，再生成带 Speaker 标签的纪要。"
        : "还没有可用的声纹模型；请先到「模型设置 → 本地模型选择」下载。";
    }
  }

  async function refreshDiarization(modelId = "", showToast = false) {
    try {
      const query = modelId ? `?model=${encodeURIComponent(modelId)}` : "";
      const data = await api(`/api/diarization${query}`);
      state.diarization = data || {};
      renderDiarization();
      if (showToast) toast("说话人区分模型状态已刷新", "good");
      return state.diarization;
    } catch (err) {
      if (showToast) showAlert(err.message, "bad", "diarization");
      return state.diarization;
    }
  }

  async function downloadDiarization() {
    const model = (els.setDiarizationModel && els.setDiarizationModel.value)
      || "wespeaker_en_voxceleb_resnet34";
    try {
      await api("/api/diarization/download", { method: "POST", body: { model } });
      await refreshDiarization(model, false);
      const poll = async () => {
        const data = await refreshDiarization(model, false);
        const status = (data && data.status) || {};
        if (["downloading", "extracting"].includes(status.status)) {
          setTimeout(poll, 1200);
        } else if (status.status === "ready") {
          toast("说话人区分模型已下载完成", "good");
        } else if (status.status === "error") {
          showAlert(`模型下载失败：${status.error || "未知错误"}`, "bad", "diarization");
        }
      };
      setTimeout(poll, 1200);
    } catch (err) {
      showAlert(`开始下载失败：${err.message}`, "bad", "diarization");
    }
  }


  function syncApiFields(autofill) {
    const provider = els.setLlmProvider.value;
    const isDeepSeek = provider === "deepseek";
    const isCustom = provider === "openai";
    els.apiFields.classList.toggle("hidden", provider === "local");
    els.deepseekCard.classList.toggle("hidden", !isDeepSeek);
    els.apiThinkingLabel.classList.toggle("hidden", !isDeepSeek);
    els.apiBaseLabel.textContent = isDeepSeek ? "API 地址（DeepSeek 官方）" : "API 地址";
    els.apiModelLabel.textContent = isDeepSeek ? "DeepSeek 模型" : "模型名";
    if (autofill) {
      if (isDeepSeek) {
        els.setApiBase.value = "https://api.deepseek.com";
        els.setApiModel.value = "deepseek-flash";
        els.setApiDisableThinking.checked = true;
      } else if (isCustom) {
        if (!els.setApiBase.value || els.setApiBase.value.includes("deepseek.com")) {
          els.setApiBase.value = "https://api.openai.com/v1";
        }
        if (!els.setApiModel.value || els.setApiModel.value.includes("deepseek")) {
          els.setApiModel.value = "gpt-4o-mini";
        }
      }
    }
    if (isDeepSeek) {
      els.providerHint.textContent = "已选 DeepSeek 官方专用接口：地址、模型、关闭思考已预填，通常只需要填 API Key。";
      els.apiHint.textContent = "DeepSeek 官方 API：默认 deepseek-flash（也可填 deepseek-v4-pro），使用 API 时回答和字幕文本会发送给 DeepSeek，音频仍不上传。";
    } else if (isCustom) {
      els.providerHint.textContent = "这里用于 OpenAI、OpenRouter、硅基流动、本地 vLLM 等其他兼容服务；如果使用 DeepSeek，请选上面的官方专用项。";
      els.apiHint.textContent = "任何 OpenAI 兼容接口。使用 API 时，回答和字幕文本会发送给你填写的服务商。";
    } else {
      els.providerHint.textContent = "本地模型：回答和字幕翻译都在本机完成，音频和文字都不上传，离线可用。可在下方「本地模型版本」切换。";
      els.apiHint.textContent = "本地模型：回答和字幕翻译都在本机完成，音频和文字都不上传，离线可用。";
    }
  }

  const SESSION_MODE_LABELS = {
    call: "通话副驾",
    translate: "听读翻译",
    meeting: "会议纪要",
    unknown: "未分类",
  };

  function formatSessionDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || "");
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatSessionBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function sessionSpeakerLabel(turn) {
    if (turn.speaker === "me") return "我";
    if (turn.speaker_label) return String(turn.speaker_label);
    return "对方";
  }

  function renderSessions() {
    if (!els.sessionList) return;
    const data = state.sessions;
    if (!data) {
      els.sessionList.replaceChildren(make("div", "session-empty", "正在读取会话…"));
      return;
    }
    const filter = els.sessionTypeFilter ? els.sessionTypeFilter.value : "all";
    const all = Array.isArray(data.sessions) ? data.sessions : [];
    const items = filter === "all" ? all : all.filter((row) => row.mode === filter);
    if (els.sessionSummary) {
      els.sessionSummary.textContent =
        `${data.total || 0} 个会话 · ${formatSessionBytes(data.total_size)}` +
        (filter === "all" ? "" : ` · 当前显示 ${items.length} 个`);
    }
    els.sessionList.replaceChildren();
    if (!items.length) {
      els.sessionList.appendChild(
        make("div", "session-empty", filter === "all" ? "还没有保存的会话。" : "这个类型下没有会话。")
      );
      return;
    }
    for (const row of items) {
      const item = make("div", "session-row");
      const main = make("div", "session-row-main");
      const title = make("div", "session-row-title");
      title.appendChild(
        make("span", `session-type ${row.mode || "unknown"}`, SESSION_MODE_LABELS[row.mode] || "未分类")
      );
      title.appendChild(make("b", "", row.title || "未命名会话"));
      if (row.active) title.appendChild(make("span", "session-current", "记录中"));
      main.appendChild(title);
      const duration = Number(row.duration_s || 0);
      const durationText =
        duration >= 60 ? `${Math.floor(duration / 60)} 分 ${Math.round(duration % 60)} 秒` : `${duration.toFixed(1)} 秒`;
      main.appendChild(
        make(
          "div",
          "session-row-meta",
          `${formatSessionDate(row.started_at)} · ${row.turns || 0} 轮 · ${durationText} · ${formatSessionBytes(row.size_bytes)}`
        )
      );
      item.appendChild(main);
      const actions = make("div", "session-row-actions");
      const open = make("button", "btn btn-tiny", "打开");
      open.type = "button";
      open.addEventListener("click", () => openSessionDetail(row.id));
      actions.appendChild(open);
      const del = make("button", "btn btn-tiny session-danger", "删除");
      del.type = "button";
      del.disabled = !!row.active;
      del.addEventListener("click", () => deleteSessionById(row.id));
      actions.appendChild(del);
      item.appendChild(actions);
      els.sessionList.appendChild(item);
    }
  }

  async function loadSessions() {
    if (!els.sessionList) return;
    try {
      state.sessions = await api("/api/sessions");
      renderSessions();
    } catch (err) {
      els.sessionList.replaceChildren(make("div", "session-empty", `读取失败：${err.message}`));
    }
  }

  function appendSessionNotes(container, notes) {
    if (!notes || typeof notes !== "object") return;
    const box = make("div", "session-notes");
    box.appendChild(make("h4", "", "会议纪要"));
    if (notes.summary) box.appendChild(make("p", "", String(notes.summary)));
    const section = (title, values) => {
      const rows = Array.isArray(values) ? values.filter(Boolean) : [];
      if (!rows.length) return;
      box.appendChild(make("h4", "", title));
      const list = make("ul", "");
      for (const value of rows) list.appendChild(make("li", "", String(value)));
      box.appendChild(list);
    };
    section("要点", notes.key_points);
    section("决定", notes.decisions);
    section(
      "待办",
      (Array.isArray(notes.action_items) ? notes.action_items : []).map((item) => {
        if (typeof item === "string") return item;
        const parts = [item.task, item.owner, item.due].filter(Boolean);
        return parts.join(" · ");
      })
    );
    section("未决问题", notes.open_questions);
    container.appendChild(box);
  }

  async function openSessionDetail(id) {
    if (!els.sessionDialog) return;
    els.sessionDialogBody.replaceChildren(make("div", "session-empty", "正在读取会话…"));
    els.sessionDialogTitle.textContent = "历史会话";
    els.sessionDialogMeta.textContent = "";
    els.sessionDialogState.textContent = "";
    try {
      const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
      const session = data.session || {};
      const listed = ((state.sessions && state.sessions.sessions) || []).find((row) => row.id === session.id);
      session.active = !!(listed && listed.active);
      state.sessionOpen = session;
      els.sessionDialogKicker.textContent =
        `${SESSION_MODE_LABELS[session.mode] || "未分类"} · ${formatSessionDate(session.started_at)}`;
      els.sessionDialogTitle.textContent = session.title || "历史会话";
      els.sessionDialogMeta.textContent =
        `${(session.turns || []).length} 轮 · ${formatSessionBytes(session.size_bytes)} · ${session.id}`;
      els.sessionDialogBody.replaceChildren();
      const turns = Array.isArray(session.turns) ? session.turns : [];
      if (!turns.length) {
        els.sessionDialogBody.appendChild(make("div", "session-empty", "这个会话没有转录内容。"));
      } else {
        for (const turn of turns) {
          const row = make("div", "session-turn");
          const head = make("div", "session-turn-head");
          head.appendChild(make("b", "", sessionSpeakerLabel(turn)));
          if (turn.ts) {
            const date = new Date(Number(turn.ts) * 1000);
            if (!Number.isNaN(date.getTime())) {
              head.appendChild(make("span", "", date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })));
            }
          }
          if (turn.language) head.appendChild(make("span", "", String(turn.language)));
          row.appendChild(head);
          row.appendChild(make("div", "session-turn-text", String(turn.en || "")));
          if (turn.zh) row.appendChild(make("div", "session-turn-zh", String(turn.zh)));
          els.sessionDialogBody.appendChild(row);
        }
      }
      appendSessionNotes(els.sessionDialogBody, session.notes);
      const deleteButton = $("#btnDeleteOpenSession");
      if (deleteButton) {
        deleteButton.disabled = session.active;
        deleteButton.textContent = session.active ? "记录中的会话不能删除" : "删除此会话";
      }
      if (!els.sessionDialog.open) els.sessionDialog.showModal();
    } catch (err) {
      els.sessionDialogBody.replaceChildren(make("div", "session-empty", `读取失败：${err.message}`));
      if (!els.sessionDialog.open) els.sessionDialog.showModal();
    }
  }

  async function deleteSessionById(id) {
    if (!window.confirm("删除这个会话？删除后不能恢复。")) return;
    try {
      await api("/api/sessions/delete", { method: "POST", body: { ids: [id] } });
      if (state.sessionOpen && state.sessionOpen.id === id) {
        state.sessionOpen = null;
        if (els.sessionDialog.open) els.sessionDialog.close();
      }
      await loadSessions();
      toast("会话已删除", "good");
    } catch (err) {
      toast(`删除失败：${err.message}`, "bad");
    }
  }

  async function deleteFilteredSessions() {
    if (!state.sessions) return;
    const mode = els.sessionDeleteMode ? els.sessionDeleteMode.value : "all";
    const rawDays = els.sessionDeleteDays ? els.sessionDeleteDays.value : "30";
    const allTime = rawDays === "0";
    const days = allTime ? 0 : Number(rawDays) || 30;
    const cutoff = allTime ? Number.POSITIVE_INFINITY : Date.now() - days * 86400000;
    const predicted = (state.sessions.sessions || []).filter(
      (row) => !row.active && (mode === "all" || row.mode === mode) && new Date(row.started_at).getTime() < cutoff
    ).length;
    const modeLabel = mode === "all" ? "全部类型" : SESSION_MODE_LABELS[mode] || "全部类型";
    const timeLabel = allTime ? "全部时间" : `早于 ${days} 天`;
    if (!window.confirm(`删除${timeLabel}的「${modeLabel}」会话，共 ${predicted} 个？\n删除后不能恢复。`)) return;
    const button = $("#btnDeleteSessionsFiltered");
    if (button) {
      button.disabled = true;
      button.textContent = "删除中…";
    }
    try {
      const result = await api("/api/sessions/delete", {
        method: "POST",
        body: { older_than_days: days, mode },
      });
      await loadSessions();
      const skipped = (result.skipped_active || []).length;
      toast(`已删除 ${result.deleted || 0} 个会话${skipped ? `，跳过 ${skipped} 个正在记录的` : ""}`, "good");
    } catch (err) {
      toast(`删除失败：${err.message}`, "bad");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "按条件删除";
      }
    }
  }

  async function openSettings(tab = state.mode) {    await flushMediaPreset();
    try {
      state.models = await api("/api/models?refresh=1");
    } catch (_) {
      // Keep the cached catalog.
    }
    fillSettingsForm();
    const settingsTab = ["common", "model", "call", "translate", "voice", "meeting", "sessions"].includes(tab)
      ? tab
      : state.mode;
    applySettingsTab(settingsTab);
    if (els.settingsSaveStatus) {
      els.settingsSaveStatus.textContent = "";
      els.settingsSaveStatus.classList.remove("good", "bad");
    }
    if (!els.settingsDialog.open) els.settingsDialog.showModal();
    try {
      await loadDevices();
    } catch (err) {
      els.deviceHint.textContent = `读取设备失败：${err.message}`;
      els.deviceHint.classList.add("warn");
    }
    await loadModelManagement();
    updateModelStatus();
  }

  function applySettingsTab(tab) {
    const valid = new Set(["common", "call", "translate", "voice", "meeting", "model", "sessions"]);
    const active = valid.has(tab) ? tab : "common";
    state.settingsTab = active;
    for (const section of $$(".settings-section[data-settings-tab]", els.settingsDialog)) {
      const own = section.dataset.settingsTab;
      const show = own === active;
      section.classList.toggle("hidden", !show);
    }
    for (const button of $$("[data-settings-tab]", els.settingsTabs)) {
      const selected = button.dataset.settingsTab === active;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
    }
    if (els.settingsScopeHint) {
      const labels = {
        call: "通话副驾", translate: "听读翻译", voice: "语音输入", meeting: "会议纪要",
      };
      els.settingsScopeHint.textContent = active === "common"
        ? "全局设置：音频设备、存储和运行环境，对所有工作区生效。"
        : active === "model"
          ? "模型设置：语音识别、语言模型、说话人区分模型，以及本地模型文件管理。"
          : active === "sessions"
            ? "会话管理：查看、打开和删除本机保存的历史转录；不会上传。"
            : `功能设置：以下只影响「${labels[active] || active}」。音频设备、识别模型和模型设置在「全局设置 / 模型设置」里。`;
    }
    if (active === "sessions") loadSessions();
  }

  async function closeSettings() {
    await flushMediaPreset();
    if (els.settingsDialog.open) els.settingsDialog.close();
  }

  async function flushMediaPreset() {
    if (!state.mediaSaveTimer) return;
    clearTimeout(state.mediaSaveTimer);
    state.mediaSaveTimer = null;
    await persistMediaPreset();
  }

  function setMediaPresetState(text) {
    if (els.mediaPresetState) els.mediaPresetState.textContent = text;
  }

  function mediaPresetPayload() {
    return {
      media_type: els.setMediaType.value,
      media_title: els.setMediaTitle.value.trim(),
      media_context: els.setMediaContext.value.trim(),
      subtitle_style: els.setSubtitleStyle.value,
      subtitle_silence_ms: Number(els.setSubtitleSilence.value),
      overlay_enabled: els.setOverlayEnabled.checked,
    };
  }

  async function persistMediaPreset() {
    const payload = mediaPresetPayload();
    try {
      const res = await api("/api/media-preset", { method: "POST", body: payload });
      state.settings = { ...state.settings, ...(res.settings || payload) };
      setMediaPresetState("已保存到本机 data/settings.json");
    } catch (err) {
      setMediaPresetState("自动保存失败，点「保存设置」重试");
    }
  }

  function scheduleMediaPresetSave() {
    setMediaPresetState("正在输入… 自动保存");
    if (state.mediaSaveTimer) clearTimeout(state.mediaSaveTimer);
    state.mediaSaveTimer = setTimeout(() => {
      state.mediaSaveTimer = null;
      persistMediaPreset();
    }, 600);
  }

  function updateModelStatus() {
    if (!els.modelStatus) return;
    const asr = state.status.asr || {};
    const llm = state.status.llm || {};
    const lines = [
      `语音识别：${asr.message || "未知"}`,
      `语言模型：${llm.message || "未知"}`,
    ];
    els.modelStatus.textContent = lines.join("\n");
    els.modelStatus.classList.toggle("ok", asr.status === "ready" && llm.status === "ready");
    els.modelStatus.classList.toggle("bad", asr.status === "error" || llm.status === "error");
  }

  async function saveSettings(event) {
    event.preventDefault();
    let saved = false;
    const previousRunAsAdmin = state.settings.run_as_admin === true;
    if (state.mediaSaveTimer) {
      clearTimeout(state.mediaSaveTimer);
      state.mediaSaveTimer = null;
    }
    const payload = { ...state.settings };
    const previousUiLanguage = state.settings.ui_language || "zh";
    const saveBtn = $("#btnSaveSettings");
    saveBtn.disabled = true;
    saveBtn.textContent = "保存中…";
    if (els.settingsSaveStatus) {
      els.settingsSaveStatus.textContent = "正在保存…";
      els.settingsSaveStatus.classList.remove("good", "bad");
    }
    const otherVal = els.setOtherDevice.value;
    const other = (state.devices.loopback || []).find((d) => String(d.index) === otherVal);
    payload.other_device = other ? { index: other.index, name: other.name } : null;
    const micVal = els.setMicDevice.value;
    payload.mic_enabled = micVal !== "none";
    if (micVal === "none") {
      payload.mic_device = null;
    } else {
      const mic = (state.devices.microphones || []).find((d) => String(d.index) === micVal);
      payload.mic_device = mic ? { index: mic.index, name: mic.name } : null;
    }
    payload.auto_suggest = $("#setAutoSuggest").checked;
    payload.translate = $("#setTranslate").checked;
    payload.partial_asr = $("#setPartial").checked;
    payload.save_transcript = $("#setSaveTranscript").checked;
    payload.restore_last_mode = $("#setRestoreLastMode").checked;
    payload.start_with_windows = $("#setStartWithWindows").checked;
    payload.run_as_admin = els.setRunAsAdmin.checked;
    payload.echo_guard = els.setEchoGuard.checked;
    payload.suggest_count = Number($("#setSuggestCount").value);
    payload.call_silence_ms = Number($("#setCallSilenceMs").value);
    payload.call_vocabulary = els.setCallVocabulary.value.trim();
    payload.call_auto_summary = els.setCallAutoSummary.checked;
    payload.long_passage_delay_ms = Number(els.setLongPassageDelay.value);
    payload.mic_gain = Number(els.setMicGain.value);
    payload.llm_provider = els.setLlmProvider.value;
    payload.api_base_url = els.setApiBase.value.trim();
    payload.api_model = els.setApiModel.value.trim();
    payload.api_key = els.setApiKey.value.trim();
    payload.api_disable_thinking = els.setApiDisableThinking.checked;
    payload.llm_model = els.setLocalModel.value;
    payload.asr_model = els.setAsrModel.value;
    payload.ui_language = els.setUiLanguage.value;
    payload.call_asr_language = els.setCallAsrLanguage.value;
    payload.translate_asr_language = els.setTranslateAsrLanguage.value;
    payload.translate_target_language = els.setTranslateTargetLanguage.value;
    payload.meeting_asr_language = els.setMeetingAsrLanguage.value;
    payload.media_type = els.setMediaType.value;
    payload.media_title = els.setMediaTitle.value.trim();
    payload.media_context = els.setMediaContext.value.trim();
    payload.subtitle_style = els.setSubtitleStyle.value;
    payload.subtitle_silence_ms = Number(els.setSubtitleSilence.value);
    payload.partial_interval_ms = Number(els.setPartialInterval.value);
    payload.partial_window_ms = Number(els.setPartialWindow.value);
    payload.subtitle_max_segment_s = Number(els.setSubtitleMaxSegment.value);
    payload.overlay_poll_ms = Number(els.setOverlayPoll.value);
    payload.translate_delay_ms = Number(els.setTranslateDelay.value);
    payload.translate_context_turns = Number(els.setTranslateContext.value);
    payload.partial_context_turns = Number(els.setPartialContext.value);
    payload.overlay_enabled = els.setOverlayEnabled.checked;
    payload.meeting_auto_notes = els.setMeetingAutoNotes.checked;
    payload.meeting_notes_language = els.setMeetingNotesLanguage.value;
    payload.meeting_context = els.setMeetingContext.value.trim();
    payload.diarization_enabled = els.setMeetingDiarization.checked;
    payload.diarization_model = els.setDiarizationModel.value;
    payload.hardware_auto_configure = els.setHardwareAuto.checked;
    payload.voice_hotkey = els.setVoiceHotkey.value;
    payload.voice_hotkey_hold = els.setVoiceHotkeyHold.value === "true";
    payload.voice_trigger_mode = els.setVoiceTriggerMode.value;
    payload.voice_input_language = els.setVoiceInputLanguage.value;
    payload.voice_target_language = els.setVoiceTargetLanguage.value;
    payload.voice_no_overlay_insert = els.setVoiceNoOverlayInsert.value;
    payload.voice_max_seconds = Number(els.setVoiceMaxSeconds.value);
    payload.voice_overlay_enabled = els.setVoiceOverlayEnabled.checked;
    payload.voice_cue_enabled = els.setVoiceCueEnabled.checked;
    payload.voice_focus_skip_fullscreen = els.setVoiceFocusSkipFullscreen.checked;
    payload.voice_auto_punctuation = els.setVoiceAutoPunctuation.checked;
    payload.voice_auto_proofread = els.setVoiceAutoProofread.checked;
    payload.voice_vocabulary = els.setVoiceVocabulary.value.trim();
    payload.voice_focus_silence_ms = Number(els.setVoiceFocusSilence.value);
    payload.voice_focus_titles = els.setVoiceFocusTitles.value.trim();
    if (payload.diarization_enabled) {
      const info = diarizationModelInfo(payload.diarization_model);
      const ready = !!(state.diarization && state.diarization.segmentation_downloaded && info && info.downloaded);
      if (!ready) {
        saveBtn.disabled = false;
        saveBtn.textContent = "保存设置";
        if (els.settingsSaveStatus) {
          els.settingsSaveStatus.textContent = "需要先下载说话人区分模型";
          els.settingsSaveStatus.classList.remove("good");
          els.settingsSaveStatus.classList.add("bad");
        }
        applySettingsTab("model");
        showAlert("开启与会人区分前，请先下载所选声纹模型和 pyannote 分割模型。", "bad", "diarization");
        return false;
      }
    }
    try {
      const res = await api("/api/settings", { method: "POST", body: payload });
      state.settings = res.settings || payload;
      saved = true;
      els.setApiKey.value = "";
      renderApiKeyStatus(!!state.settings.api_key_saved);
      if (previousUiLanguage !== (state.settings.ui_language || "zh")) {
        fillSettingsForm();
        if (window.I18N) window.I18N.apply(state.settings.ui_language || "zh");
      }
      setMediaPresetState("已保存到本机 data/settings.json");
      updateGlobalModelLabel();
      renderTurns();
      renderSuggestion();
      renderVoiceInput();
      const extra =
        (res.released_local ? "，正在释放本地模型显存" : "") +
        (state.status.capture && state.status.capture.running ? "，音频设备将在下次开始时生效" : "");
      if (res.startup_error) {
        els.settingsSaveStatus.textContent = `设置保存成功，但开机启动设置失败：${res.startup_error}`;
        els.settingsSaveStatus.classList.remove("good");
        els.settingsSaveStatus.classList.add("bad");
        toast("开机启动设置失败", "bad");
      } else {
        if (els.settingsSaveStatus) {
          els.settingsSaveStatus.textContent = `✓ 设置保存成功${extra}`;
          els.settingsSaveStatus.classList.remove("bad");
          els.settingsSaveStatus.classList.add("good");
        }
        toast("设置保存成功" + extra, "good");
      }
      if ((state.settings.run_as_admin === true) !== previousRunAsAdmin) {
        if (els.settingsSaveStatus) {
          els.settingsSaveStatus.textContent = "设置已保存；需要重启应用才能生效。";
          els.settingsSaveStatus.classList.remove("good", "bad");
        }
        toast("需要重启应用才能生效", "good");
      }
    } catch (err) {
      if (els.settingsSaveStatus) {
        els.settingsSaveStatus.textContent = `保存失败：${err.message}`;
        els.settingsSaveStatus.classList.remove("good");
        els.settingsSaveStatus.classList.add("bad");
      }
      showAlert(err.message, "bad", "settings-save");
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "保存设置";
    }
    return saved;
  }

  async function restartApp() {
    if (!window.confirm("重启应用？当前录音、字幕和回答会中断。\n如果开启了管理员权限，会出现 UAC 提示。")) return;
    const btn = els.btnRestartApp;
    btn.disabled = true;
    btn.textContent = "重启中…";
    const ok = await saveSettings({ preventDefault() {} });
    if (!ok) {
      btn.disabled = false;
      btn.textContent = "重启应用";
      return;
    }
    try {
      await api("/api/restart", { method: "POST", body: {} });
      btn.textContent = "正在重启…";
      const startedAt = Date.now();
      const waitForServer = async () => {
        await new Promise((resolve) => setTimeout(resolve, 2200));
        while (Date.now() - startedAt < 120000) {
          try {
            const response = await fetch("/api/health", { cache: "no-store" });
            if (response.ok) {
              window.location.reload();
              return;
            }
          } catch (_) {
            // Server is restarting; keep polling.
          }
          await new Promise((resolve) => setTimeout(resolve, 900));
        }
        btn.disabled = false;
        btn.textContent = "重启应用";
        toast("服务没有恢复，请手动启动 Lan.Assistant。", "bad");
      };
      waitForServer();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "重启应用";
      toast(`重启失败：${err.message}`, "bad");
    }
  }

  async function testLlm() {
    const btn = $("#btnTestLlm");
    const box = els.llmTestResult;
    btn.disabled = true;
    btn.textContent = "测试中…";
    box.classList.remove("hidden");
    box.textContent = "正在调用模型写一句回答，首次调用可能要加载模型，请稍候…";
    try {
      const res = await api("/api/test-llm", { method: "POST", body: {} });
      const result = res.result || {};
      const first = (result.options && result.options[0]) || {};
      box.textContent = [
        `翻译测试：${result.translation || "（空）"}`,
        `回答测试：${first.en || "（空）"}`,
        `${translateTargetLabel()}：${first.zh || "（空）"}`,
        `耗时：${((result.total_ms || 0) / 1000).toFixed(1)} 秒`,
      ].join("\n");
      toast("模型工作正常", "good");
    } catch (err) {
      box.textContent = `测试失败：${err.message}`;
      showAlert(`模型测试失败：${err.message}`, "bad", "test-llm");
    } finally {
      btn.disabled = false;
      btn.textContent = "测试模型";
    }
  }

  // ------------------------------------------------------------------- demo
  function renderDemo() {
    state.profile = {
      name: "林哲",
      job: "SaaS 产品经理",
      city: "上海",
      personality: "偏内向，但聊到专业话题会很放松；说话直接，不喜欢客套。",
      hobbies: "周末骑行、手冲咖啡、科幻小说。",
      goal: "了解对方团队的真实需求，争取下周发一份方案；建立轻松可信的第一印象。",
      relationship: "潜在客户，第一次正式通话",
      other_person: "美国总部运营负责人，语速快，喜欢直接反馈。",
      english_level: "intermediate",
      tone: "friendly",
      notes: "不要过度承诺交付时间。",
      phrases: "To be honest... / Let me think out loud...",
      key_facts: "这次方案源于客服工单里大量“不会设置”的反馈；我们访谈了 12 个新用户；我自己刚接手时也卡在第一步。",
    };
    state.turns = [
      { id: "d1", speaker: "other", en: "Hey, thanks for making the time today. I've been looking forward to this call.", zh: "嘿，谢谢你今天抽时间。我一直很期待这次通话。", ts: Date.now() / 1000 - 150, asr_ms: 540 },
      { id: "d2", speaker: "me", en: "Same here. Thanks for reaching out.", zh: "", ts: Date.now() / 1000 - 132, asr_ms: 430 },
      { id: "d3", speaker: "other", en: "So, I read your proposal about the new onboarding flow. I'm curious — what made you decide to focus on that first?", zh: "我读了你关于新用户引导流程的方案。我很好奇，你为什么决定先聚焦这个？", ts: Date.now() / 1000 - 18, asr_ms: 610 },
    ];
    state.suggestion = {
      status: "ready",
      for_turn: "d3",
      translation: "我读了你关于新用户引导流程的方案。我很好奇，你为什么决定先聚焦这个？",
      options: [
        { en: "Honestly, it came from watching users drop off in the first five minutes. That felt like the highest-leverage place to start.", zh: "说实话，是因为我们看到用户在前五分钟就流失了。我觉得那是最值得先下手的地方。", tone: "坦诚" },
        { en: "Good question. We noticed most support tickets were about setup, so we wanted to fix the root cause first. Does that match what you're seeing?", zh: "好问题。我们发现大多数支持工单都和初始设置有关，所以想先解决根因。这跟你们看到的情况一致吗？", tone: "追问" },
      ],
      note: "先讲用户流失的观察，再把话题交回给对方。",
      latency_ms: 2400,
      fallback: false,
    };
    state.status.capture.running = true;
    state.status.capture.started_at = Date.now() / 1000 - 160;
    state.startedAt = state.status.capture.started_at;
    state.status.asr = { status: "ready", message: "语音识别就绪（CUDA / float16）" };
    state.status.llm = { status: "ready", message: "语言模型就绪（qwen3.5-copilot）" };
    renderAll();
  }

  // ------------------------------------------------------------------ binding
  function bind() {
    $("#btnStart").addEventListener("click", toggleCall);
    $("#btnEmptyStart").addEventListener("click", toggleCall);
    $("#btnProfile").addEventListener("click", openProfile);
    $("#btnProfileInline").addEventListener("click", openProfile);
    $("#btnCloseProfile").addEventListener("click", closeProfile);
    $("#btnCancelProfile").addEventListener("click", closeProfile);
    els.profileForm.addEventListener("input", scheduleProfileSave);
    els.profileForm.addEventListener("change", scheduleProfileSave);
    els.profileForm.addEventListener("submit", saveProfile);
    els.profileDialog.addEventListener("close", () => {
      if (state.profileDirty) persistProfile();
    });

    $("#btnSettings").addEventListener("click", openSettings);
    for (const button of $$("[data-settings-tab]", els.settingsTabs)) {
      button.addEventListener("click", () => applySettingsTab(button.dataset.settingsTab));
    }
    els.btnOverlay.addEventListener("click", toggleOverlay);
    els.btnStuck.addEventListener("click", () => requestContinuation());
    els.btnContinuationRefresh.addEventListener("click", () => requestContinuation());
    if (els.btnCallSummary) els.btnCallSummary.addEventListener("click", openCallSummary);
    if (els.btnCallSummaryRefresh) els.btnCallSummaryRefresh.addEventListener("click", requestCallSummary);
    if (els.btnExportCallSummary) els.btnExportCallSummary.addEventListener("click", exportCallSummary);
    if (els.btnCloseCallSummary) els.btnCloseCallSummary.addEventListener("click", () => els.callSummaryDialog.close());
    if (els.btnCloseCallSummaryFoot) els.btnCloseCallSummaryFoot.addEventListener("click", () => els.callSummaryDialog.close());
    els.btnProofread.addEventListener("click", openSubtitleEditor);
    els.btnExportSubtitles.addEventListener("click", exportSubtitles);
    els.btnCloseSubtitle.addEventListener("click", () => els.subtitleDialog.close());
    els.btnCancelSubtitle.addEventListener("click", () => els.subtitleDialog.close());
    els.btnSaveSubtitles.addEventListener("click", saveSubtitles);
    for (const button of $$(".rail-item", els.featureNav)) {
      button.addEventListener("click", () => switchMode(button.dataset.mode));
    }
    els.btnRailSettings.addEventListener("click", () => openSettings());
    els.btnOpenModelTab.addEventListener("click", () => applySettingsTab("model"));
    els.btnApplyHardware.addEventListener("click", async () => {
      try {
        const res = await api("/api/hardware/apply", { method: "POST", body: {} });
        if (res.settings) state.settings = res.settings;
        if (res.hardware) state.hardware = res.hardware;
        if (res.recommendation) state.hardwareRecommendation = res.recommendation;
        fillSettingsForm();
        renderHardware();
        updateGlobalModelLabel();
        toast(res.changed ? "已按当前硬件应用推荐配置" : "当前配置已经匹配硬件", "good");
      } catch (err) {
        toast(err.message, "bad");
      }
    });
    els.btnRefreshModels.addEventListener("click", async () => {
      try {
        state.models = await api("/api/models?refresh=1");
        renderModelChoices(state.settings);
        await loadModelManagement();
        toast(`已发现 ${state.models.whisper.length} 个 Whisper、${state.models.llm.length} 个本地大模型`, "good");
      } catch (err) {
        toast(err.message, "bad");
      }
    });
    if (els.btnRefreshModelManagement) {
      els.btnRefreshModelManagement.addEventListener("click", loadModelManagement);
    }
    if (els.btnAddCustomModel) {
      els.btnAddCustomModel.addEventListener("click", addCustomModel);
    }
    els.btnGenerateNotes.addEventListener("click", async () => {
      try {
        const res = await api("/api/meeting/notes", { method: "POST", body: {} });
        if (res && res.ok === false) {
          toast(res.error || "还不能生成纪要", "bad");
          return;
        }
        if (res.notes) {
          state.meetingNotes = res.notes;
          renderMeetingNotes();
        }
        toast("正在整理会议纪要…", "good");
      } catch (err) {
        toast(err.message, "bad");
      }
    });
    els.btnExportNotes.addEventListener("click", () => {
      if (state.meetingNotes && state.meetingNotes.status === "ready") {
        window.location.href = "/api/meeting/export";
      }
    });
    if (els.btnEditVoiceVocabulary) {
      els.btnEditVoiceVocabulary.addEventListener("click", () => openSettings("voice"));
    }
    if (els.btnClearVoiceHistory) {
      els.btnClearVoiceHistory.addEventListener("click", async () => {
        if (!window.confirm("清空全部语音输入历史？")) return;
        try {
          await api("/api/voice/history/clear", { method: "POST", body: {} });
          state.voiceHistory = [];
          renderVoiceHistory();
        } catch (err) {
          showAlert(err.message, "bad", "voice-history-clear");
        }
      });
    }
    els.btnVoiceStart.addEventListener("click", async () => {
      const status = state.voiceInput && state.voiceInput.status;
      try {
        const path = status === "recording" ? "/api/voice/stop" : "/api/voice/start";
        const res = await api(path, { method: "POST", body: {} });
        if (res && res.ok === false) {
          toast(res.error || "语音输入无法启动", "bad");
        }
        if (res && res.state) {
          state.voiceInput = res.state;
          renderVoiceInput();
        }
      } catch (err) {
        toast(err.message, "bad");
      }
    });
    els.btnVoiceCancel.addEventListener("click", async () => {
      try {
        await api("/api/voice/cancel", { method: "POST", body: {} });
      } catch (err) {
        toast(err.message, "bad");
      }
    });
    $("#btnCloseSettings").addEventListener("click", closeSettings);
    $("#btnCancelSettings").addEventListener("click", closeSettings);
    if (els.btnRestartApp) els.btnRestartApp.addEventListener("click", restartApp);
    if (els.setRunAsAdmin) {
      els.setRunAsAdmin.addEventListener("change", () => {
        if (!els.settingsSaveStatus) return;
        els.settingsSaveStatus.textContent = "管理员权限设置已修改；点击「重启应用」后生效。";
        els.settingsSaveStatus.classList.remove("good", "bad");
      });
    }
    if ($("#btnRefreshSessions")) {
      $("#btnRefreshSessions").addEventListener("click", loadSessions);
    }
    if (els.sessionTypeFilter) {
      els.sessionTypeFilter.addEventListener("change", renderSessions);
    }
    if ($("#btnDeleteSessionsFiltered")) {
      $("#btnDeleteSessionsFiltered").addEventListener("click", deleteFilteredSessions);
    }
    if ($("#btnCloseSessionDialog")) {
      $("#btnCloseSessionDialog").addEventListener("click", () => els.sessionDialog.close());
    }
    if ($("#btnCloseSessionDialogFoot")) {
      $("#btnCloseSessionDialogFoot").addEventListener("click", () => els.sessionDialog.close());
    }
    if ($("#btnDeleteOpenSession")) {
      $("#btnDeleteOpenSession").addEventListener("click", () => {
        if (state.sessionOpen && !state.sessionOpen.active) {
          deleteSessionById(state.sessionOpen.id);
        }
      });
    }
    els.settingsDialog.addEventListener("close", () => {
      if (state.mediaSaveTimer) flushMediaPreset();
    });
    els.settingsForm.addEventListener("submit", saveSettings);
    if (els.btnDownloadDiarization) {
      els.btnDownloadDiarization.addEventListener("click", downloadDiarization);
    }
    if (els.btnRefreshDiarization) {
      els.btnRefreshDiarization.addEventListener("click", () => {
        const model = (els.setDiarizationModel && els.setDiarizationModel.value) || "";
        refreshDiarization(model, true);
      });
    }
    if (els.setDiarizationModel) {
      els.setDiarizationModel.addEventListener("change", () => {
        renderDiarization();
      });
    }
    if (els.setMeetingDiarization) {
      els.setMeetingDiarization.addEventListener("change", () => {
        if (els.setMeetingDiarization.checked && els.setMeetingDiarization.disabled) {
          els.setMeetingDiarization.checked = false;
        }
      });
    }
    $("#btnRefreshDevices").addEventListener("click", async () => {
      els.deviceHint.textContent = "正在刷新音频设备…";
      try {
        await loadDevices();
        toast("设备列表已刷新", "good");
      } catch (err) {
        els.deviceHint.textContent = `刷新失败：${err.message}`;
        els.deviceHint.classList.add("warn");
      }
    });
    $("#btnTestLlm").addEventListener("click", testLlm);
    els.setLlmProvider.addEventListener("change", () => syncApiFields(true));
    els.btnResetDeepSeek.addEventListener("click", () => {
      els.setApiBase.value = "https://api.deepseek.com";
      els.setApiModel.value = "deepseek-flash";
      els.setApiDisableThinking.checked = true;
      syncApiFields(false);
      toast("已恢复 DeepSeek 官方默认地址和模型", "good");
    });
    if (els.btnClearApiProfile) {
      els.btnClearApiProfile.addEventListener("click", async () => {
        if (!window.confirm("清除这台电脑上保存的 API Key？\n项目设置不会受影响，但下次使用 API 需要重新填写。")) return;
        try {
          const res = await api("/api/api-profile/clear", { method: "POST", body: {} });
          if (res.settings) state.settings = res.settings;
          fillSettingsForm();
          syncApiFields(true);
          toast("本机 API 配置已清除", "good");
        } catch (err) {
          toast(err.message, "bad");
        }
      });
    }
    els.setMicGain.addEventListener("input", () => {
      els.setMicGainValue.textContent = `${Number(els.setMicGain.value).toFixed(1)}×`;
    });
    for (const field of [
      els.setMediaType,
      els.setMediaTitle,
      els.setMediaContext,
      els.setSubtitleStyle,
      els.setSubtitleSilence,
      els.setOverlayEnabled,
    ]) {
      field.addEventListener("input", scheduleMediaPresetSave);
      field.addEventListener("change", scheduleMediaPresetSave);
    }

    $("#btnRefresh").addEventListener("click", requestRefresh);
    $("#btnOpening").addEventListener("click", requestOpening);
    $("#btnOpeningEmpty").addEventListener("click", requestOpening);
    $("#btnClear").addEventListener("click", clearSession);
    $("#btnExport").addEventListener("click", () => {
      const a = document.createElement("a");
      a.href = "/api/export";
      a.download = "lan-assistant-export.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      const messages = {
        call: "正在导出通话记录 JSON（原文/译文/AI 建议）",
        translate: "正在导出听读记录 JSON；SRT 请用右侧「导出 SRT 字幕」",
        meeting: "正在导出会议记录 JSON（转录/译文/纪要）",
        voice: "语音输入记录不支持此导出",
      };
      toast(messages[state.mode] || "正在导出文字记录（JSON）");
    });

    document.addEventListener("keydown", (event) => {
      if (isTypingTarget(event.target)) return;
      if (els.profileDialog.open || els.settingsDialog.open) return;
      if (event.key === "r" || event.key === "R") {
        event.preventDefault();
        requestRefresh();
      } else if (["1", "2", "3", "4"].includes(event.key)) {
        const idx = Number(event.key) - 1;
        const card = $$(".option-card", els.suggestionBody)[idx];
        if (card) {
          event.preventDefault();
          copyOption(idx, card);
        }
      }
    });

    // Close dialogs when clicking the backdrop area.
    for (const dialog of [els.profileDialog, els.settingsDialog]) {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
      });
    }
  }

  boot();
})();
