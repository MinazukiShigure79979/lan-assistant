/* Lightweight UI language packs.
 *
 * The application source strings are Chinese.  This file translates the
 * visible interface at the DOM level, including dynamically inserted labels,
 * while deliberately leaving transcript/user content untouched.  Missing
 * Japanese/Korean entries fall back to English, never to Chinese.
 */
(() => {
  "use strict";

  const EN = {
    "选择工作区": "Workspaces",
    "通话副驾": "Call Copilot",
    "实时建议 · 续接": "Live suggestions · Continue",
    "听读翻译": "Listen & Translate",
    "字幕 · 悬浮 · 导出": "Subtitles · Overlay · Export",
    "语音输入": "Voice Input",
    "快捷键 · 翻译": "Hotkey · Translate",
    "会议纪要": "Meeting Notes",
    "转录 · 摘要 · 待办": "Transcript · Summary · Actions",
    "设置": "Settings",
    "语音识别": "Speech recognition",
    "语言模型": "Language model",
    "准备中": "Preparing",
    "就绪": "Ready",
    "错误": "Error",
    "未开始": "Not started",
    "待机": "Idle",
    "待机 · 按快捷键开始": "Idle · press the hotkey to start",
    "正在通话": "On call",
    "通话总结": "Call summary",
    "导出": "Export",
    "我的预设": "My profile",
    "结束通话": "End call",
    "开始通话": "Start call",
    "编辑预设": "Edit profile",
    "对方在说什么": "What they're saying",
    "你可以这样回答": "You can reply",
    "我卡住了": "I'm stuck",
    "换一批": "Refresh",
    "开始听读": "Start listening",
    "结束听读": "Stop listening",
    "开始记录": "Start recording",
    "结束记录": "Stop recording",
    "开始语音输入": "Start dictation",
    "结束录音": "Stop recording",
    "取消": "Cancel",
    "保存设置": "Save settings",
    "保存并使用": "Save & use",
    "关闭": "Close",
    "导出字幕": "Export subtitles",
    "保存字幕": "Save subtitles",
    "生成开场白": "Opening line",
    "清空本次记录": "Clear this session",
    "提示": "Tip",
    "复制": "Copy",
    "已复制": "Copied",
    "朗读": "Read aloud",
    "等待通话内容…": "Waiting for call content…",
    "开始第一次通话": "Start your first call",
    "填好预设": "Set up profile",
    "先给我一句开场白": "Give me an opening line",
    "生成开场白": "Opening line",
    "生成中文翻译": "Translate to Chinese",
    "准备中…": "Preparing…",
    "正在加载…": "Loading…",
    "正在生成…": "Generating…",
    "暂无建议": "No suggestions yet",
    "请先开始通话": "Start a call first",
    "正在听对方说话": "Listening to the other side",
    "正在听剧集 / 视频": "Listening to the show / video",
    "正在记录会议": "Recording the meeting",
    "回答会出现在这里": "Answers will appear here",
    "会议纪要将出现在这里": "Meeting notes will appear here",
    "全局设置": "General",
    "模型（全局）": "Models",
    "识别与翻译": "Recognition & translation",
    "界面语言 / Interface language": "Interface language",
    "首次安装按 Windows 系统语言选择；切换后会同步各功能的默认语言。": "On first install this follows Windows; changing it updates each feature's default language.",
    "语言模型（全局）": "Language model",
    "回答与翻译使用的模型": "Model for answers and translation",
    "本地模型": "Local model",
    "本地模型版本（自动扫描 models/llm）": "Local model version (auto-scans models/llm)",
    "模型管理": "Model management",
    "刷新状态": "Refresh status",
    "添加自定义模型": "Add custom model",
    "刷新模型列表": "Refresh model list",
    "测试模型": "Test model",
    "API 地址": "API endpoint",
    "API Key": "API key",
    "API Key 使用 Windows DPAPI 加密，只保存在当前电脑；复制项目或迁移到新电脑后必须重新填写。": "The API key is encrypted with Windows DPAPI and stays on this PC. Re-enter it after copying or migrating.",
    "清除本机 API 配置": "Clear local API profile",
    "关闭深度思考": "Disable deep thinking",
    "全局快捷键": "Global hotkey",
    "保存文字记录": "Save text transcript",
    "启动时恢复上次工作区": "Restore last workspace on launch",
    "开机自动启动": "Start with Windows",
    "外放回声抑制": "Speaker echo suppression",
    "对方停顿多久算一句": "Pause length for one utterance",
    "对方停顿后自动刷新建议": "Refresh suggestions after a pause",
    "停止通话后生成问答复盘和英语反馈": "Generate a Q&A review and English feedback after the call",
    "字幕预设": "Subtitle preset",
    "字幕识别语言": "Subtitle source language",
    "字幕翻译目标语言": "Subtitle target language",
    "字幕翻译风格": "Subtitle translation style",
    "内容类型": "Content type",
    "自动识别（推荐）": "Auto-detect (recommended)",
    "剧名 / 节目名": "Show / title",
    "剧情、角色名、术语和翻译偏好": "Plot, names, terms and translation preferences",
    "字幕停顿（越短越快）": "Subtitle pause (shorter is faster)",
    "保存字幕": "Save subtitles",
    "语音输入": "Voice Input",
    "语音词库": "Voice vocabulary",
    "最近输入": "Recent dictation",
    "编辑词库": "Edit vocabulary",
    "清空": "Clear",
    "还没有词库": "No vocabulary yet",
    "还没有语音输入记录": "No dictation history yet",
    "按快捷键说一句，识别结果会出现在这里。": "Press the hotkey and speak; results appear here.",
    "语音输入": "Voice input",
    "全局设置": "General settings",
    "会议识别语言": "Meeting source language",
    "停止后自动生成纪要": "Generate notes after stopping",
    "会议背景 / 参会人 / 关注点（可选）": "Meeting context / attendees / focus (optional)",
    "仅使用会议转录和这里的背景生成纪要，不会编造未出现的负责人或日期。": "Notes use only the transcript and this context; no invented owners or dates.",
    "我的称呼": "What should I call you",
    "你的称呼": "Your name",
    "你是谁、对方是谁、这次想聊什么": "Who you are, who they are, and what this call is about",
    "对方是谁 / 你们的关系": "Who they are / your relationship",
    "这次的目的 / 想达到什么": "Purpose / what you want to achieve",
    "可以提到的关键事实 / 例子（很有用）": "Key facts / examples you can mention (very useful)",
    "对方的背景 / 性格": "Their background / personality",
    "你常用的表达 / 口头禅（可选）": "Expressions / catchphrases you use (optional)",
    "你的外语水平": "Your language level",
    "希望的语气": "Desired tone",
    "填写后会自动保存到本机": "Saved locally after editing",
    "保存并使用": "Save & use",
    "关闭": "Close",
    "基础：能说简单句，需要短句": "Basic: simple sentences, keep it short",
    "专业稳重": "Professional",
    "轻松自然": "Casual",
    "其他要求 / 想避开的话题": "Other requests / topics to avoid",
    "你的性格": "Your personality",
    "你是谁、对方是谁、这次想聊什么": "Who you are, who they are, and what this call is about",
    "你的称呼": "Your name",
    "第一次使用先填预设": "Set up your profile first",
    "告诉副驾你是谁、这次想聊什么，建议会更贴合你。": "Tell the copilot who you are and what this call is about.",
    "选对「系统声音」设备，是能听见对方的关键。设备改动在下次开始通话时生效。": "Choose the right system-audio device so the copilot can hear them. Changes apply on the next call.",
    "刷新设备": "Refresh devices",
    "系统声音（对方）— 选扬声器 / 耳机的 Loopback": "System audio (them) — speaker / headphone loopback",
    "麦克风（我）": "Microphone (me)",
    "正在读取音频设备…": "Reading audio devices…",
    "本地 Qwen3.5-9B": "Local Qwen3.5-9B",
    "对所有工作区生效": "Applies to all workspaces",
    "更改": "Change",
    "再生成一组": "Generate another set",
    "复制对应回答 ·": "Copy the matching answer ·",
    "本地运行 · 不上传音频 · 建议佩戴耳机": "Runs locally · No audio upload · Headphones recommended",
    "本地运行 · 不上传音频 · 建议佩戴耳机 ·": "Runs locally · No audio upload · Headphones recommended ·",
    "推理中…": "Thinking…",
    "已完成": "Done",
    "已停止": "Stopped",
    "已保存": "Saved",
    "已保存到本机": "Saved locally",
    "正在保存…": "Saving…",
    "设置已保存": "Settings saved",
    "保存失败": "Save failed",
    "连接中断": "Connection lost",
    "重新连接": "Reconnect",
    "模型已删除": "Model deleted",
    "已按当前硬件应用推荐配置": "Recommended settings applied",
    "当前配置已经匹配硬件": "Current settings already match the hardware",
    "本机 API 配置已清除": "Local API profile cleared",
    "通话总结已生成，点击「通话总结」查看": "Call summary is ready; open “Call summary”",
    "还没有语音输入记录": "No dictation history yet",
    "还没有词库": "No vocabulary yet",
    "添加公司名、项目名或常用术语，识别会更准。": "Add company names, projects or terms to improve recognition.",
    "专名、公司名、技术术语和反复听错的词；一行一个，支持「错词=正确词」。": "Names, company terms and recurring mistakes, one per line; supports wrong=correct.",
    "在任意应用的输入框里按快捷键开始录音，校对后再回到刚才的输入框。": "Press the hotkey in any app to record, proofread, then return to the same field.",
    "开始语音输入": "Start dictation",
    "取消": "Cancel",
    "语音输入 · 全局快捷键，校对后插入": "Voice input · global hotkey, insert after proofreading",
    "在任意应用的输入框里按一次快捷键开始录音，再按一次结束；校对浮窗确认后，文字会回到刚才的输入框。": "Press the hotkey once in any app to record, then press again to stop. After proofreading, the text returns to the same field.",
    "在任意应用的输入框里按快捷键开始录音，校对后再回到刚才的输入框。": "Press the hotkey in any app to record, proofread, then return to the same field.",
    "语音输入 · 全局快捷键：校对浮窗 · 目标语言翻译": "Voice input · global hotkey · proofreading overlay · translation",
    "语音输入 · 全局快捷键 · 校对浮窗 · 目标语言翻译": "Voice input · global hotkey · proofreading overlay · translation",
    "会议纪要 · 停止后自动生成 · 可导出 Markdown": "Meeting notes · generated after stopping · export Markdown",
    "听读模式 · 字幕自动生成 · 可把窗口缩小放在视频下方": "Listen mode · subtitles generated automatically · keep the window under the video",
    "实时字幕": "Live subtitles",
    "按 1 2 3 复制对应回答 · R 换一批": "Press 1 2 3 to copy an answer · R for another set",
    "复制原文": "Copy original",
    "复制译文": "Copy translation",
    "已校对": "Proofread",
    "原始识别": "Raw transcript",
    "欢迎使用": "Welcome",
    "首次启动设置": "First-run setup",
    "准备本地运行环境": "Prepare the local runtime",
    "选择要下载的模型": "Choose models to download",
    "下载并启动": "Download & start",
    "安装 Ollama": "Install Ollama",
    "下载模型": "Download models",
    "启动主界面": "Open the app",
    "正在检查本机环境…": "Checking this computer…",
    "正在下载 Ollama（约 1.6 GB）…": "Downloading Ollama (about 1.6 GB)…",
    "正在解压 Ollama…": "Extracting Ollama…",
    "重试安装": "Retry install",
    "下载中…": "Downloading…",
    "下载完成": "Download complete",
    "准备完成": "Ready",
    "模型已就绪，正在启动主界面…": "Models are ready. Opening the app…",
    "语音识别": "Speech to text",
    "回答模型": "Answer model",
    "5 个选项": "5 options",
    "6 个选项": "6 options",
  };

  const JA = {
    "选择工作区": "ワークスペース",
    "通话副驾": "通話コパイロット",
    "实时建议 · 续接": "リアルタイム提案・続き",
    "听读翻译": "聞き取り翻訳",
    "字幕 · 悬浮 · 导出": "字幕・オーバーレイ・書き出し",
    "语音输入": "音声入力",
    "快捷键 · 翻译": "ショートカット・翻訳",
    "会议纪要": "会議メモ",
    "转录 · 摘要 · 待办": "文字起こし・要約・ToDo",
    "设置": "設定",
    "语音识别": "音声認識",
    "语言模型": "言語モデル",
    "准备中": "準備中",
    "就绪": "準備完了",
    "开始通话": "通話開始",
    "结束通话": "通話終了",
    "开始听读": "聞き取り開始",
    "结束听读": "聞き取り終了",
    "开始记录": "記録開始",
    "结束记录": "記録終了",
    "开始语音输入": "音声入力を開始",
    "结束录音": "録音終了",
    "取消": "キャンセル",
    "保存设置": "設定を保存",
    "关闭": "閉じる",
    "通话总结": "通話サマリー",
    "导出": "書き出し",
    "我的预设": "マイプロフィール",
    "编辑预设": "プロフィール編集",
    "对方在说什么": "相手の発言",
    "你可以这样回答": "こう返せます",
    "我卡住了": "詰まった",
    "换一批": "別の候補",
    "提示": "ヒント",
    "复制": "コピー",
    "朗读": "読み上げ",
    "已复制": "コピーしました",
    "界面语言 / Interface language": "表示言語",
    "识别与翻译": "認識と翻訳",
    "全局设置": "一般",
    "模型（全局）": "モデル",
    "语言模型（全局）": "言語モデル（全体）",
    "回答与翻译使用的模型": "回答と翻訳に使うモデル",
    "本地模型": "ローカルモデル",
    "模型管理": "モデル管理",
    "添加自定义模型": "カスタムモデルを追加",
    "语音词库": "音声用語集",
    "最近输入": "最近の入力",
    "编辑词库": "用語集を編集",
    "清空": "クリア",
    "还没有词库": "用語集はまだありません",
    "还没有语音输入记录": "音声入力の履歴はまだありません",
    "保存并使用": "保存して使用",
    "下载并启动": "ダウンロードして開始",
    "准备本地运行环境": "ローカル環境を準備",
    "选择要下载的模型": "ダウンロードするモデルを選択",
    "安装 Ollama": "Ollama をインストール",
    "下载模型": "モデルをダウンロード",
    "启动主界面": "アプリを起動",
    "准备完成": "準備完了",
    "首次启动设置": "初回セットアップ",
    "欢迎使用": "ようこそ",
    "语音输入 · 全局快捷键，校对后插入": "音声入力・グローバルホットキー・校正後に挿入",
    "在任意应用的输入框里按一次快捷键开始录音，再按一次结束；校对浮窗确认后，文字会回到刚才的输入框。": "任意のアプリでホットキーを押して録音し、もう一度押して終了。校正後、同じ入力欄に文字が戻ります。",
    "语音输入 · 全局快捷键：校对浮窗 · 目标语言翻译": "音声入力・グローバルホットキー・校正オーバーレイ・翻訳",
  };

  const KO = {
    "选择工作区": "작업 공간",
    "通话副驾": "통화 코파일럿",
    "实时建议 · 续接": "실시간 제안 · 이어가기",
    "听读翻译": "듣기 번역",
    "字幕 · 悬浮 · 导出": "자막 · 오버레이 · 내보내기",
    "语音输入": "음성 입력",
    "快捷键 · 翻译": "단축키 · 번역",
    "会议纪要": "회의록",
    "转录 · 摘要 · 待办": "받아쓰기 · 요약 · 할 일",
    "设置": "설정",
    "语音识别": "음성 인식",
    "语言模型": "언어 모델",
    "准备中": "준비 중",
    "就绪": "준비됨",
    "开始通话": "통화 시작",
    "结束通话": "통화 종료",
    "开始听读": "듣기 시작",
    "结束听读": "듣기 종료",
    "开始记录": "기록 시작",
    "结束记录": "기록 종료",
    "开始语音输入": "음성 입력 시작",
    "结束录音": "녹음 종료",
    "取消": "취소",
    "保存设置": "설정 저장",
    "关闭": "닫기",
    "通话总结": "통화 요약",
    "导出": "내보내기",
    "我的预设": "내 프로필",
    "编辑预设": "프로필 편집",
    "对方在说什么": "상대방 발언",
    "你可以这样回答": "이렇게 답할 수 있어요",
    "我卡住了": "막혔어요",
    "换一批": "다른 제안",
    "提示": "팁",
    "复制": "복사",
    "朗读": "읽기",
    "已复制": "복사됨",
    "界面语言 / Interface language": "인터페이스 언어",
    "识别与翻译": "인식 및 번역",
    "全局设置": "일반",
    "模型（全局）": "모델",
    "语言模型（全局）": "언어 모델(전체)",
    "回答与翻译使用的模型": "답변과 번역에 사용할 모델",
    "本地模型": "로컬 모델",
    "模型管理": "모델 관리",
    "添加自定义模型": "사용자 모델 추가",
    "语音词库": "음성 용어집",
    "最近输入": "최근 입력",
    "编辑词库": "용어집 편집",
    "清空": "지우기",
    "还没有词库": "용어집이 없습니다",
    "还没有语音输入记录": "음성 입력 기록이 없습니다",
    "保存并使用": "저장 후 사용",
    "下载并启动": "다운로드 후 시작",
    "准备本地运行环境": "로컬 환경 준비",
    "选择要下载的模型": "다운로드할 모델 선택",
    "安装 Ollama": "Ollama 설치",
    "下载模型": "모델 다운로드",
    "启动主界面": "앱 열기",
    "准备完成": "준비 완료",
    "首次启动设置": "첫 실행 설정",
    "欢迎使用": "환영합니다",
    "语音输入 · 全局快捷键，校对后插入": "음성 입력 · 전역 단축키 · 교정 후 삽입",
    "在任意应用的输入框里按一次快捷键开始录音，再按一次结束；校对浮窗确认后，文字会回到刚才的输入框。": "아무 앱에서 단축키를 한 번 눌러 녹음하고 다시 눌러 종료하세요. 교정 후 같은 입력란으로 돌아갑니다.",
    "语音输入 · 全局快捷键：校对浮窗 · 目标语言翻译": "음성 입력 · 전역 단축키 · 교정 오버레이 · 번역",
  };

  const PACKS = { en: EN, ja: JA, ko: KO };
  const SKIP = [
    "#turns", "#partialWrap", "#suggestionBody", "#continuationBody",
    "#meetingNotesBody", "#callSummaryBody", ".turn-en", ".turn-zh",
    ".voice-history-text", ".voice-history-translation", "textarea", "input",
    "script", "style", "pre", "code", "[contenteditable='true']", "[data-no-i18n]",
  ];
  const records = new WeakMap();
  const attrRecords = new WeakMap();
  let current = "zh";
  let applying = false;

  function norm(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function skipped(node) {
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    if (!element) return true;
    return SKIP.some((selector) => {
      try { return !!element.closest(selector); } catch (_) { return false; }
    });
  }

  function lookup(text) {
    const key = norm(text);
    if (!key) return "";
    if (current === "zh") return key;
    return (PACKS[current] && PACKS[current][key]) || EN[key] || "";
  }

  function translateText(node) {
    if (!node.nodeValue || skipped(node)) return;
    let record = records.get(node);
    if (!record) {
      record = { source: node.nodeValue, output: node.nodeValue };
      records.set(node, record);
    } else if (!applying && node.nodeValue !== record.output && node.nodeValue !== record.source) {
      record.source = node.nodeValue;
    }
    const translation = lookup(record.source);
    if (!translation) return;
    const leading = (record.source.match(/^\s*/) || [""])[0];
    const trailing = (record.source.match(/\s*$/) || [""])[0];
    const output = leading + translation + trailing;
    record.output = output;
    if (node.nodeValue !== output) node.nodeValue = output;
  }

  function translateAttributes(element) {
    if (skipped(element)) return;
    const attrRecord = attrRecords.get(element) || {};
    for (const attr of ["placeholder", "title", "aria-label"]) {
      const value = element.getAttribute(attr);
      if (!value) continue;
      if (!attrRecord[attr] || (value !== attrRecord[attr].output && value !== attrRecord[attr].source)) {
        attrRecord[attr] = { source: value, output: value };
      }
      const translation = lookup(attrRecord[attr].source);
      if (translation) {
        const output = translation;
        attrRecord[attr].output = output;
        if (value !== output) element.setAttribute(attr, output);
      }
    }
    attrRecords.set(element, attrRecord);
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      translateText(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) translateAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.TEXT_NODE) translateText(node);
      else translateAttributes(node);
    }
  }

  function apply(language) {
    current = ["zh", "en", "ja", "ko"].includes(language) ? language : "en";
    document.documentElement.lang = current === "zh" ? "zh-CN" : current;
    applying = true;
    try {
      walk(document.body);
    } finally {
      applying = false;
    }
  }

  const observer = new MutationObserver((mutations) => {
    if (applying) return;
    applying = true;
    try {
      for (const mutation of mutations) {
        if (mutation.type === "characterData") translateText(mutation.target);
        else {
          for (const node of mutation.addedNodes) walk(node);
          if (mutation.target && mutation.target.nodeType === Node.ELEMENT_NODE) {
            translateText(mutation.target);
          }
        }
      }
    } finally {
      applying = false;
    }
  });

  window.I18N = { apply, get language() { return current; }, t: lookup };
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
