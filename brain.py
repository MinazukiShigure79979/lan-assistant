"""Local LLM brain: translation, reply suggestions and Ollama lifecycle."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

import config
import hardware
import models

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class BrainError(RuntimeError):
    pass


def _json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in response")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("JSON root is not an object")
    return obj


def _clean_translation(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # The small model occasionally appends a fake assistant turn, a JSON
    # fragment, or a tool-call marker after the actual translation.  Keep only
    # the clean prefix.
    for marker in (
        "<tool_call>", "</text>", "assistant", "usercontent", "yynote",
        "},{", "}]}", "”}", "“}", '{"', '"}', "}\n",
    ):
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    text = text.split("\n")[0].strip()
    text = re.sub(r"^\s*\d+\s*[.、)]\s*", "", text)
    text = text.strip().strip('"').strip("“”")
    text = re.sub(r"[\s\"”'}\]]+$", "", text).strip()
    return text


def _profile_block(profile: dict[str, Any]) -> str:
    rows = [
        ("Name", profile.get("name", "")),
        ("Job / role", profile.get("job", "")),
        ("City", profile.get("city", "")),
        ("Personality", profile.get("personality", "")),
        ("Hobbies and interests", profile.get("hobbies", "")),
        ("Relationship to the caller", profile.get("relationship", "")),
        ("About the caller", profile.get("other_person", "")),
        ("Goal of this call", profile.get("goal", "")),
        ("Preferred tone", profile.get("tone", "friendly")),
        ("English level", profile.get("english_level", "intermediate")),
        ("Things to mention or avoid", profile.get("notes", "")),
        ("Key facts / examples I can mention", profile.get("key_facts", "")),
        ("Phrases I like to use", profile.get("phrases", "")),
    ]
    return "\n".join(f"- {label}: {value}" for label, value in rows if str(value or "").strip())


def _history_block(history: list[dict[str, Any]], limit: int = 12) -> str:
    rows = []
    for turn in history[-limit:]:
        speaker = "Me" if turn.get("speaker") == "me" else "Other"
        text = str(turn.get("en", "") or "").strip()
        if text:
            rows.append(f"{speaker}: {text}")
    return "\n".join(rows) if rows else "(no previous turns)"


def fallback_suggestions(latest: str, count: int = 3) -> dict[str, Any]:
    text = (latest or "").strip()
    is_question = text.endswith("?") or bool(
        re.match(r"^(what|why|how|when|where|who|which|do|does|did|are|is|can|could|would|will|have|has)\b", text, re.I)
    )
    if is_question:
        pool = [
            ("That's a good question. Let me think about that for a second.", "这个问题问得好，让我想一下。", "自然回应"),
            ("I'd say it depends. What's your take on it?", "我觉得要看情况。你怎么看？", "反问"),
            ("Honestly, I'm still figuring that out. But I'd love to hear your thoughts.", "说实话，我还在摸索。不过我很想听听你的想法。", "坦诚"),
        ]
    else:
        pool = [
            ("That's really interesting. Tell me more about that.", "这很有意思，多跟我说说。", "追问"),
            ("I can totally see why you'd feel that way.", "我完全理解你为什么会有这种感觉。", "共情"),
            ("Yeah, I've been thinking about that too. What made you bring it up?", "是啊，我也一直在想这件事。你怎么突然提到这个？", "接话"),
        ]
    return {
        "options": [{"en": en, "zh": zh, "tone": tone} for en, zh, tone in pool[: max(1, min(3, count))]],
        "note": "本地模型暂时不可用，这是通用回复",
        "fallback": True,
    }


_MEETING_LANGUAGE_LABELS = {
    "zh": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
}


def _meeting_language_label(language: str) -> str:
    return _MEETING_LANGUAGE_LABELS.get(str(language or "zh"), "Simplified Chinese")


class Brain:
    def __init__(
        self,
        settings: dict[str, Any],
        profile_getter: Callable[[], dict[str, Any]],
        on_status: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.settings = settings
        self.profile_getter = profile_getter
        self.on_status = on_status or (lambda scope, message: None)
        self.base_url = str(settings.get("llm_base_url", "http://127.0.0.1:11434")).rstrip("/")
        self.provider = str(settings.get("llm_provider", "local") or "local")
        self.api_base_url = str(settings.get("api_base_url", "https://api.deepseek.com") or "").rstrip("/")
        self.api_model = str(settings.get("api_model", "deepseek-flash") or "deepseek-flash")
        self.api_key = str(settings.get("api_key", "") or "")
        self.api_disable_thinking = bool(settings.get("api_disable_thinking", True))
        self.model = self.api_model if self.provider != "local" else str(settings.get("llm_model", "qwen3.5-copilot"))
        self.status = "unknown"
        self.last_error = ""
        self._lock = threading.Lock()
        self._spawn_lock = threading.Lock()
        self._spawned = False
        self._process: Optional[subprocess.Popen] = None

    # ---------------------------------------------------------------- lifecycle
    def set_settings(self, settings: dict[str, Any]) -> None:
        old = (self.provider, self.model, self.api_base_url, self.api_key, self.api_disable_thinking)
        self.settings = settings
        self.base_url = str(settings.get("llm_base_url", self.base_url)).rstrip("/")
        self.provider = str(settings.get("llm_provider", "local") or "local")
        self.api_base_url = str(settings.get("api_base_url", self.api_base_url) or "").rstrip("/")
        self.api_model = str(settings.get("api_model", self.api_model) or self.api_model)
        self.api_key = str(settings.get("api_key", "") or "")
        self.api_disable_thinking = bool(settings.get("api_disable_thinking", True))
        self.model = self.api_model if self.provider != "local" else str(settings.get("llm_model", "qwen3.5-copilot"))
        if old != (self.provider, self.model, self.api_base_url, self.api_key, self.api_disable_thinking):
            self.status = "unknown"
            self.last_error = ""

    def _effective_api_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        return os.environ.get("DEEPSEEK_API_KEY", "").strip() if self.provider == "deepseek" else os.environ.get("OPENAI_API_KEY", "").strip()

    def _api_chat_url(self) -> str:
        base = self.api_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _http(self, method: str, path: str, payload: Optional[dict[str, Any]] = None, timeout: float = 5.0) -> Any:
        url = self.base_url + path
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _port_open(self, timeout: float = 1.0) -> bool:
        try:
            self._http("GET", "/api/tags", timeout=timeout)
            return True
        except Exception:
            return False

    def _spawn_server(self) -> None:
        if self._spawned or not self.base_url.startswith("http://127.0.0.1"):
            return
        with self._spawn_lock:
            if self._spawned:
                return
            self._spawned = True
            exe = config.OLLAMA_BIN
            if not exe.exists():
                raise BrainError("未找到本地模型运行时 tools/ollama/ollama.exe")
            config.OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["OLLAMA_HOST"] = "127.0.0.1:11434"
            env["OLLAMA_MODELS"] = str(config.OLLAMA_MODELS_DIR)
            env["OLLAMA_KEEP_ALIVE"] = "30m"
            env["OLLAMA_NUM_PARALLEL"] = "1"
            env.update(hardware.ollama_env(self.settings))
            log_path = config.DATA_DIR / "ollama.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = log_path.open("a", encoding="utf-8")
            self._process = subprocess.Popen(
                [str(exe), "serve"],
                cwd=str(config.OLLAMA_DIR),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )

    def _model_names(self) -> list[str]:
        data = self._http("GET", "/api/tags", timeout=5.0)
        names = []
        for item in data.get("models", []) or []:
            name = str(item.get("name") or item.get("model") or "")
            if name:
                names.append(name)
        return names

    def _create_model(self) -> None:
        spec = models.find_llm(self.model)
        model_path = Path(spec["path"]) if spec else (
            config.LLM_MODEL_PATH_35
            if ("3.5" in self.model or "9b" in self.model.lower())
            else config.LLM_MODEL_PATH_25
        )
        if not model_path.exists():
            raise BrainError(f"本地模型文件不存在：{model_path}")
        if model_path.stat().st_size < 100 * 1024 * 1024:
            raise BrainError("本地模型文件不完整（小于 100 MB）")
        params = float(spec.get("params_b", 0) or 0) if spec else 0.0
        num_ctx = 4096 if (params and params <= 4.0) else 8192
        modelfile = config.DATA_DIR / f"Modelfile-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', self.model)}"
        modelfile.parent.mkdir(parents=True, exist_ok=True)
        modelfile.write_text(
            f'FROM "{model_path}"\nPARAMETER num_ctx {num_ctx}\nPARAMETER temperature 0.7\nPARAMETER top_p 0.9\n',
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["OLLAMA_MODELS"] = str(config.OLLAMA_MODELS_DIR)
        self.on_status("loading", "首次使用，正在注册本地模型（约需 1 分钟）…")
        proc = subprocess.run(
            [str(config.OLLAMA_BIN), "create", self.model, "-f", str(modelfile)],
            cwd=str(config.OLLAMA_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            err = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", err)
            raise BrainError(f"注册本地模型失败：{err[:500]}")

    def warmup(self) -> None:
        """Load the local model into VRAM so the first live suggestion is fast."""
        if self.provider != "local":
            return
        try:
            self.chat(
                [{"role": "user", "content": "Reply with the single word OK."}],
                temperature=0.0,
                max_tokens=8,
                timeout=240.0,
                json_mode=False,
            )
        except Exception:
            # A one-token reply can legitimately be empty; loading is what matters.
            pass

    def ensure_ready(self) -> bool:
        with self._lock:
            if self.status == "ready":
                return True
            if self.provider != "local":
                self.status = "starting"
                self.on_status("loading", "正在检查 API 设置…")
                if not self.api_base_url:
                    self.status = "error"
                    self.last_error = "请先在设置中填写 API 地址"
                    self.on_status("error", self.last_error)
                    return False
                if not self.api_model:
                    self.status = "error"
                    self.last_error = "请先在设置中填写 API 模型名"
                    self.on_status("error", self.last_error)
                    return False
                if not self._effective_api_key():
                    self.status = "error"
                    self.last_error = "请先在设置中填写 API Key（或设置 DEEPSEEK_API_KEY 环境变量）"
                    self.on_status("error", self.last_error)
                    return False
                self.status = "ready"
                self.last_error = ""
                label = "DeepSeek" if self.provider == "deepseek" else "API"
                self.on_status("ready", f"{label} API 就绪（{self.api_model}）")
                return True
            self.status = "starting"
            self.on_status("loading", "正在启动本地语言模型…")
            try:
                if not self._port_open():
                    self._spawn_server()
                    deadline = time.time() + 35
                    while time.time() < deadline:
                        if self._port_open():
                            break
                        time.sleep(0.8)
                    else:
                        raise BrainError("本地模型服务启动超时")
                names = self._model_names()
                wanted = self.model.split(":")[0]
                if not any(n == self.model or n.split(":")[0] == wanted for n in names):
                    self._create_model()
                self.status = "ready"
                self.last_error = ""
                self.on_status("ready", f"语言模型就绪（{self.model}）")
                return True
            except Exception as exc:
                self.status = "error"
                self.last_error = str(exc)
                self.on_status("error", self.last_error)
                return False

    # --------------------------------------------------------------------- chat
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 650,
        timeout: float = 120.0,
        json_mode: bool = True,
        format_schema: Optional[dict[str, Any]] = None,
    ) -> str:
        if not self.ensure_ready():
            raise BrainError(self.last_error or "语言模型未就绪")
        if self.provider != "local":
            return self._chat_api(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                json_mode=json_mode,
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
                "repeat_penalty": 1.05,
                "num_ctx": 8192,
                "num_predict": max_tokens,
            },
        }
        if format_schema is not None:
            payload["format"] = format_schema
        elif json_mode:
            payload["format"] = "json"
        if (
            self.settings.get("local_disable_thinking", True)
            and ("qwen3.5" in self.model.lower() or "qwen3_5" in self.model.lower())
        ):
            payload["think"] = False
        try:
            try:
                data = self._http("POST", "/api/chat", payload, timeout=timeout)
            except urllib.error.HTTPError as exc:
                # Older Ollama builds may reject a JSON schema; fall back to
                # plain JSON mode rather than failing the whole suggestion.
                if format_schema is not None and exc.code in (400, 422):
                    payload["format"] = "json"
                    data = self._http("POST", "/api/chat", payload, timeout=timeout)
                else:
                    raise
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise BrainError(f"模型调用失败（HTTP {exc.code}）{body}") from exc
        except Exception as exc:
            raise BrainError(f"模型调用失败：{exc}") from exc
        message = data.get("message") or {}
        content = str(message.get("content", "") or "").strip()
        if not content:
            raise BrainError("模型返回为空")
        return content

    def _chat_api(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
        json_mode: bool,
    ) -> str:
        url = self._api_chat_url()
        payload: dict[str, Any] = {
            "model": self.api_model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.provider == "deepseek" and self.api_disable_thinking:
            # DeepSeek V4.1 Flash / V4 Pro enable thinking by default.
            # This is the OpenAI-format switch documented by DeepSeek.
            payload["thinking"] = {"type": "disabled"}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._effective_api_key()}",
        }

        def call() -> Any:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}

        data: dict[str, Any] = {}
        for attempt in range(2):
            try:
                data = call()
                break
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if exc.code == 400 and attempt == 0:
                    # OpenAI-compatible servers differ: some reject JSON mode
                    # or the DeepSeek thinking flag.  Retry once without the
                    # optional field.
                    if "response_format" in payload:
                        payload.pop("response_format", None)
                        continue
                    if "thinking" in payload:
                        payload.pop("thinking", None)
                        continue
                if exc.code == 401:
                    raise BrainError("API Key 无效或已过期，请在设置中重新填写。") from exc
                if exc.code == 402:
                    raise BrainError("API 账户余额不足。") from exc
                if exc.code == 429:
                    raise BrainError("API 请求过于频繁，请稍后再试。") from exc
                detail = ""
                try:
                    detail = str(json.loads(body).get("error", {}).get("message", ""))[:300]
                except Exception:
                    detail = body[:300]
                raise BrainError(f"API 调用失败（HTTP {exc.code}）{detail}") from exc
            except urllib.error.URLError as exc:
                raise BrainError(f"无法连接 API：{getattr(exc, 'reason', exc)}") from exc
            except TimeoutError as exc:
                raise BrainError("API 响应超时") from exc
            except Exception as exc:
                raise BrainError(f"API 调用失败：{exc}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise BrainError("API 返回为空，请检查模型名和账户状态。")
        message = choices[0].get("message") or {}
        content = str(message.get("content", "") or "").strip()
        if not content:
            raise BrainError("API 返回内容为空")
        return content

    def _subtitle_context(self) -> str:
        if str(self.settings.get("app_mode", "call")) != "translate":
            return ""
        media_type = str(self.settings.get("media_type", "series") or "series")
        type_prompts = {
            "series": "TV series dialogue: conversational, consistent character names, natural spoken Chinese.",
            "movie": "Film dialogue: concise and cinematic, natural spoken Chinese.",
            "anime": "Anime dialogue: keep character names and honorifics consistent, natural spoken Chinese.",
            "documentary": "Documentary narration: accurate, formal, consistent terminology.",
            "other": "Spoken dialogue: natural, concise, consistent names.",
        }
        style_prompts = {
            "natural": "Prefer natural colloquial Chinese over literal wording.",
            "literal": "Stay close to the original wording while remaining readable.",
            "terminology": "Keep names and technical terms consistent; do not translate established terms loosely.",
        }
        title = str(self.settings.get("media_title", "") or "").strip()
        context = str(self.settings.get("media_context", "") or "").strip()
        style = str(self.settings.get("subtitle_style", "natural") or "natural")
        parts = [
            "SUBTITLE CONTEXT:",
            f"- Content type: {media_type}",
            f"- Title / show: {title or '(not specified)'}",
            f"- Style: {type_prompts.get(media_type, type_prompts['other'])} {style_prompts.get(style, style_prompts['natural'])}",
        ]
        if context:
            parts.append(f"- Setting, characters, terminology, user preferences: {context[:1800]}")
        glossary = config.parse_glossary(context)
        if glossary:
            pairs = "; ".join(f"{src} -> {dst}" for src, dst in list(glossary.items())[:40])
            parts.append(f"- GLOSSARY (must follow): {pairs}")
        parts.append("- Translate for on-screen subtitles: keep each line short and speakable; preserve the genre and tone.")
        parts.append(
            "- If a source name or term resembles a name/term in the context, use its established Chinese name/translation; "
            "never reinterpret a proper noun as an unrelated common noun."
        )
        return "\n".join(parts)

    # ----------------------------------------------------------------- translate
    def translate(self, text: str, target: str = "zh") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        labels = {
            "zh": "natural, colloquial Simplified Chinese",
            "en": "natural English",
            "ja": "natural Japanese",
            "ko": "natural Korean",
        }
        target_label = labels.get(target, labels["zh"])
        schema = {
            "type": "object",
            "properties": {"zh": {"type": "string"}},
            "required": ["zh"],
        }
        context = self._subtitle_context()
        system = (
            f"You are a professional interpreter. Translate the utterance inside <text> into {target_label}. "
            "The source may be English, Japanese, Korean, Chinese or another language. "
            "Preserve the tone and implied meaning. Do not explain, comment, continue the conversation, "
            "or add anything else. Return only JSON: {\"zh\":\"...\"}"
        )
        if context:
            system = context + "\n\n" + system
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"<text>{text[:2500]}</text>"},
        ]
        content = self.chat(
            messages,
            temperature=0.1,
            max_tokens=260,
            timeout=50.0,
            format_schema=schema,
        )
        obj = _json_object(content)
        zh = _clean_translation(str(obj.get("zh", "") or ""))
        if zh:
            return zh
        # A very small model sometimes adds a fake assistant turn.  One plain
        # retry with a minimal prompt is more reliable than showing garbage.
        try:
            raw = self.chat(
                [
                    {"role": "system", "content": f"Translate to {target_label}. Output only the translation."},
                    {"role": "user", "content": text[:1800]},
                ],
                temperature=0.1,
                max_tokens=220,
                timeout=35.0,
                json_mode=False,
            )
            return _clean_translation(raw)
        except Exception:
            return ""

    def translate_many(self, texts: list[str], target: str = "zh") -> list[str]:
        """Translate several short turns in one call (used to backfill subtitles)."""
        cleaned = [str(t or "").strip() for t in texts]
        if not cleaned:
            return []
        if len(cleaned) == 1:
            return [self.translate(cleaned[0], target)]
        labels = {
            "zh": "natural, colloquial Simplified Chinese",
            "en": "natural English",
            "ja": "natural Japanese",
            "ko": "natural Korean",
        }
        target_label = labels.get(target, labels["zh"])
        numbered = "\n".join(f"{i + 1}. {text[:800]}" for i, text in enumerate(cleaned))
        schema = {
            "type": "object",
            "properties": {
                "zh": {
                    "type": "array",
                    "minItems": len(cleaned),
                    "maxItems": len(cleaned),
                    "items": {"type": "string"},
                }
            },
            "required": ["zh"],
        }
        context = self._subtitle_context()
        system = (
            f"Translate each numbered utterance inside <text> into {target_label}. "
            "The source may be English, Japanese, Korean, Chinese or another language. Keep the same order. "
            "Each result must contain only the translation, with no number, no explanation, and no added "
            f'conversation. Return strict JSON: {{"zh":["..."]}} with exactly {len(cleaned)} strings.'
        )
        if context:
            system = context + "\n\n" + system
        messages = [
            {
                "role": "system",
                "content": system,
            },
            {"role": "user", "content": f"<text>\n{numbered}\n</text>"},
        ]
        content = self.chat(
            messages,
            temperature=0.1,
            max_tokens=min(700, 100 + len(cleaned) * 100),
            timeout=70.0,
            format_schema=schema,
        )
        obj = _json_object(content)
        rows = obj.get("zh", [])
        if not isinstance(rows, list):
            rows = []
        result = [_clean_translation(str(x or "")) for x in rows[: len(cleaned)]]
        result.extend([""] * (len(cleaned) - len(result)))
        for index, value in enumerate(result):
            if not value:
                try:
                    result[index] = self.translate(cleaned[index], target)
                except Exception:
                    result[index] = ""
        return result

    def translate_to(self, text: str, target: str = "zh") -> str:
        """Translate a short utterance into an explicit target language."""
        text = (text or "").strip()
        if not text:
            return ""
        labels = {
            "zh": "Simplified Chinese",
            "en": "natural English",
            "ja": "natural Japanese",
            "ko": "natural Korean",
        }
        target_label = labels.get(target, labels["zh"])
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        system = (
            f"You are a professional interpreter. Translate the utterance inside <text> into {target_label}. "
            "Preserve tone, names, numbers and implied meaning. Do not explain, comment, answer, or add "
            'anything else. Return only JSON: {"text":"..."}'
        )
        content = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"<text>{text[:4000]}</text>"},
            ],
            temperature=0.1,
            max_tokens=700,
            timeout=60.0,
            format_schema=schema,
        )
        try:
            obj = _json_object(content)
            return str(obj.get("text", "") or "").strip()
        except Exception:
            return ""

    def proofread_text(self, text: str, language: str = "zh", vocabulary: str = "") -> str:
        """Correct only obvious ASR homophones/typos, never rewrite meaning."""
        original = (text or "").strip()
        if not original:
            return ""
        labels = {
            "zh": "Simplified Chinese",
            "yue": "Cantonese written Chinese",
            "en": "English",
            "ja": "Japanese",
            "ko": "Korean",
        }
        label = labels.get(str(language or "").lower(), "the same language as the input")
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        system = (
            f"You are a conservative ASR proofreader for {label}. The text inside <text> is raw speech "
            "recognition output. Correct only obvious recognition errors: homophones/wrong characters, "
            "missing or wrong punctuation, and English capitalization. Do not translate, rephrase, "
            "summarize, answer, add facts, remove information, or change the speaker's tone. Keep names, "
            "numbers and technical terms unless they are clearly misrecognized. If a correction is uncertain, "
            "leave the original wording unchanged. Only replace a character when the original phrase is "
            "clearly ungrammatical or nonsensical; never replace a valid word with a synonym or a more "
            "common word just because it sounds similar. Return only JSON: {\"text\":\"corrected text\"}.\n\n"
            "Example:\n"
            "Raw: 如果我设置了不翻译之转写那么会不会做文字交对\n"
            "Corrected: 如果我设置了不翻译只转写，那么会不会做文字校对。\n"
            "Here 转写 is already correct and must stay 转写; only 之→只 and 交对→校对 are fixed."
        )
        if str(vocabulary or "").strip():
            system += (
                "\n\nVOICE VOCABULARY (authoritative spelling for names/terms; apply only when the "
                "recognized word is clearly the same term; do not insert terms that were not spoken):\n"
                + str(vocabulary)[:3000]
            )
        content = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"<text>{original[:4000]}</text>"},
            ],
            temperature=0.1,
            max_tokens=min(1200, max(220, len(original) * 2 + 80)),
            timeout=45.0,
            format_schema=schema,
        )
        try:
            obj = _json_object(content)
            corrected = str(obj.get("text", "") or "").strip()
        except Exception:
            return original
        if not corrected:
            return original
        # Be defensive: a local model sometimes translates instead of
        # proofreading. Reject large length changes or a script switch.
        ratio = len(corrected) / max(1, len(original))
        if ratio < 0.5 or ratio > 1.8:
            return original
        original_cjk = len(re.findall(r"[\u3400-\u9fff]", original))
        corrected_cjk = len(re.findall(r"[\u3400-\u9fff]", corrected))
        if original_cjk >= max(2, len(original) * 0.25) and corrected_cjk < len(corrected) * 0.2:
            return original
        if len(re.findall(r"[A-Za-z]", original)) > len(original) * 0.5 and corrected_cjk > len(corrected) * 0.5:
            return original
        return corrected

    def call_review(
        self, transcript: str, language: str = "English", profile: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build a post-call speaking review from the call transcript."""
        text = (transcript or "").strip()
        if not text:
            raise BrainError("没有通话内容可以总结")
        profile = profile or {}
        schema = {
            "type": "object",
            "properties": {
                "overview": {"type": "string"},
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "answer": {"type": "string"},
                            "improved_answer": {"type": "string"},
                            "feedback": {"type": "string"},
                        },
                        "required": ["question", "answer", "improved_answer", "feedback"],
                    },
                },
                "strengths": {"type": "array", "items": {"type": "string"}},
                "improvements": {"type": "array", "items": {"type": "string"}},
                "phrases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "phrase": {"type": "string"},
                            "meaning": {"type": "string"},
                        },
                        "required": ["phrase", "meaning"],
                    },
                },
                "intro": {
                    "type": "object",
                    "properties": {
                        "present": {"type": "boolean"},
                        "feedback": {"type": "string"},
                        "improved": {"type": "string"},
                    },
                    "required": ["present", "feedback", "improved"],
                },
            },
            "required": ["overview", "questions", "strengths", "improvements", "phrases", "intro"],
        }
        profile_bits = []
        for key in ("name", "job", "city", "hobbies", "goal", "english_level", "notes", "key_facts"):
            value = str(profile.get(key, "") or "").strip()
            if value:
                profile_bits.append(f"{key}: {value[:500]}")
        system = (
            "You are a supportive English speaking coach reviewing a real conversation. "
            "Use only the transcript and profile; never invent facts, questions or results. "
            "The transcript comes from speech recognition: ignore obvious ASR noise/fillers, and do not "
            "present a likely recognition error as a confirmed grammar mistake. "
            "Write the analysis in Simplified Chinese; keep the learner's improved English answers in English. "
            "For each interviewer question, pair the candidate's following answer. improved_answer must preserve "
            "the candidate's meaning and use only known facts. "
            "strengths should be 2-5 short Chinese points. improvements should be 3-6 actionable Chinese coaching "
            "points (for example pronunciation, word choice, sentence length), not rewritten example sentences. "
            "phrases should be 3-8 useful, natural English "
            "expressions from or relevant to this conversation, each with a short Chinese meaning. "
            "If the interviewer asked for a self-introduction, set intro.present=true and provide a concise, "
            "natural 3-5 sentence improved self-introduction (roughly 60-120 English words) using only profile "
            "facts; never make up projects, metrics, employers, degrees or dates. "
            "Return only JSON with keys: overview, questions, strengths, improvements, phrases, intro."
        )
        if profile_bits:
            system += "\n\nPROFILE:\n" + "\n".join(profile_bits)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Interview language: {language}\n"
                    "Transcript:\n<transcript>\n" + text[:14000] + "\n</transcript>"
                ),
            },
        ]
        content = self.chat(
            messages,
            temperature=0.2,
            max_tokens=2200,
            timeout=180.0,
            format_schema=schema,
        )
        try:
            obj = _json_object(content)
        except Exception as exc:
            raise BrainError(f"通话总结解析失败：{exc}") from exc
        if not isinstance(obj, dict):
            raise BrainError("通话总结格式无效")
        return obj

    def meeting_chunk_summary(self, text: str, language: str = "zh") -> str:
        label = _meeting_language_label(language)
        content = self.chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"You are a meeting secretary. Summarize this section of a meeting transcript in {label}. "
                        "Keep names, numbers, decisions and action items. Use short bullet points. "
                        "Do not invent anything. Output only the bullets."
                    ),
                },
                {"role": "user", "content": text[:9000]},
            ],
            temperature=0.2,
            max_tokens=700,
            timeout=90.0,
            json_mode=False,
        )
        return (content or "").strip()[:5000]

    def meeting_notes(
        self,
        transcript: str,
        context: str = "",
        language: str = "zh",
    ) -> dict[str, Any]:
        label = _meeting_language_label(language)
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "owner": {"type": "string"},
                            "due": {"type": "string"},
                        },
                        "required": ["task", "owner", "due"],
                    },
                },
                "open_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "title", "summary", "key_points", "decisions",
                "action_items", "open_questions",
            ],
        }
        system = f"""You are a precise meeting secretary. Read the transcript and write structured meeting notes in {label}.

Rules:
- Use ONLY information in the transcript and CONTEXT. Never invent facts, owners, dates, numbers or decisions.
- If an owner or due date is not stated, leave that field as an empty string.
- "summary" should be 2-4 short paragraphs and must never be empty; if the transcript is very short, write one factual sentence.
- "key_points" are the important facts and discussion points.
- "decisions" are only explicit decisions; do not turn suggestions into decisions.
- "action_items" are concrete follow-ups with owner/due when stated.
- "open_questions" are unresolved questions.
- Preserve names, product names and terminology.
Return JSON only in the required shape."""
        user = f"CONTEXT:\n{context.strip() or '(none)'}\n\nTRANSCRIPT:\n{transcript[:24000]}"
        started = time.perf_counter()
        content = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.25,
            max_tokens=1800,
            timeout=150.0,
            format_schema=schema,
        )
        obj = _json_object(content)
        def strings(key: str) -> list[str]:
            value = obj.get(key, [])
            if not isinstance(value, list):
                return []
            return [str(x).strip() for x in value if str(x or "").strip()][:12]
        actions = []
        for item in obj.get("action_items", []) or []:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task", "") or "").strip()
            if not task:
                continue
            actions.append({
                "task": task,
                "owner": str(item.get("owner", "") or "").strip(),
                "due": str(item.get("due", "") or "").strip(),
            })
        return {
            "title": str(obj.get("title", "") or "").strip()[:120] or "会议纪要",
            "summary": str(obj.get("summary", "") or "").strip(),
            "key_points": strings("key_points"),
            "decisions": strings("decisions"),
            "action_items": actions[:12],
            "open_questions": strings("open_questions"),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": self.model,
        }

    def _reshape_intro(
        self,
        profile: dict[str, Any],
        text: str,
        zh: str,
        words_min: int,
        words_max: int,
        reply_language: str,
    ) -> Optional[dict[str, str]]:
        schema = {
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
                "zh": {"type": "string"},
                "tone": {"type": "string"},
            },
            "required": ["reply", "zh", "tone"],
        }
        system = f"""You are editing a spoken self-introduction for a real call.
Rewrite the CURRENT INTRODUCTION so the "reply" is {words_min}-{words_max} words in {reply_language}.
Keep it natural and speakable, in 4-6 short paragraphs.

Absolute fact rules:
- Use ONLY facts present in PROFILE and KEY FACTS.
- Do not invent or change employers, job titles, projects, clients, metrics, numbers, dates, degrees or outcomes.
- If KEY FACTS is empty or lacks a concrete example, describe the current role generally. Do not fabricate an example to fill space.
- Remove any unsupported number or claim from the current draft.
Keep the same language and provide a colloquial Simplified Chinese translation.

Return JSON only: {{"reply":"...","zh":"...","tone":"补充"}}"""
        if not str(profile.get("key_facts", "") or "").strip():
            system += (
                "\nThe PROFILE has no KEY FACTS. Remove every specific project, migration, client, "
                "outcome, employer detail or numerical claim from the current draft; keep only general "
                "role responsibilities, skills, personal details and motivation."
            )
        user = (
            "PROFILE:\n"
            + (_profile_block(profile) or "(not filled in)")
            + "\n\nCURRENT INTRODUCTION ("
            + str(len(re.findall(r"\b[\w'-]+\b", text)))
            + " words):\n"
            + text
        )
        try:
            content = self.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.35,
                max_tokens=min(3000, max(1200, int(words_max * 6) + 500)),
                timeout=120.0,
                format_schema=schema,
            )
            obj = _json_object(content)
            reply = str(obj.get("reply", "") or "").strip()
            if not reply:
                return None
            return {
                "en": reply,
                "zh": _clean_translation(str(obj.get("zh", "") or "")),
                "tone": str(obj.get("tone", "") or "").strip()[:8] or "补充",
            }
        except Exception:
            return None

    # ------------------------------------------------------------------ suggest
    def continue_text(
        self,
        fragment: str,
        history: list[dict[str, Any]],
        reply_language: str = "English",
        count: int = 2,
    ) -> dict[str, Any]:
        fragment = (fragment or "").strip()
        if not fragment:
            raise BrainError("还没有听到你说话")
        count = max(1, min(3, int(count)))
        schema = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "reply": {"type": "string"},
                            "zh": {"type": "string"},
                            "tone": {"type": "string"},
                        },
                        "required": ["reply", "zh", "tone"],
                    },
                }
            },
            "required": ["options"],
        }
        system = f"""You are a live speaking coach. The user is speaking {reply_language} in a real conversation and has hit a mental block mid-thought.

The user has just said:
<fragment>{fragment[:1200]}</fragment>

Give exactly {count} possible continuations the user can say next.
Rules:
1. Continue seamlessly from the end of the fragment. Do not repeat the fragment or start a new topic.
2. If the fragment ends with a full stop, add one related follow-up sentence instead of completing it.
3. Each option must be speakable in one breath: 5-20 words, natural spoken {reply_language}.
4. No greeting, no stage directions, no explanation, no markdown, no emoji.
5. The "reply" field must be entirely in {reply_language}; "zh" is a colloquial Simplified Chinese translation.
6. "tone" is a real 2-4 character Chinese label such as 补充, 举例, 收尾, 转折.

Return JSON only in this exact shape:
{{"options":[{{"reply":"...","zh":"...","tone":"补充"}}]}}
No text outside the JSON."""
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "RECENT TRANSCRIPT:\n"
                + (_history_block(history) or "(none)")
                + "\n\nCONTINUE FROM:\n"
                + fragment[:1200],
            },
        ]
        started = time.perf_counter()
        content = self.chat(
            messages,
            temperature=0.65,
            max_tokens=320,
            timeout=80.0,
            format_schema=schema,
        )
        obj = _json_object(content)
        options: list[dict[str, str]] = []
        fragment_key = re.sub(r"\s+", " ", fragment).strip().lower()
        for item in obj.get("options", []) or []:
            if not isinstance(item, dict):
                continue
            reply = str(item.get("reply", item.get("en", "")) or "").strip()
            if not reply:
                continue
            reply_key = re.sub(r"\s+", " ", reply).strip().lower()
            if fragment_key and reply_key.startswith(fragment_key):
                reply = reply[len(fragment) :].lstrip(" ,.;:，。；：")
            reply = reply.lstrip("… .")
            if not reply:
                continue
            zh = _clean_translation(str(item.get("zh", "") or ""))
            tone = str(item.get("tone", "") or "").strip()
            if tone and ("汉字" in tone or len(tone) > 8 or tone.isascii()):
                tone = "补充"
            options.append({"en": reply, "zh": zh, "tone": tone[:8] or "补充"})
        if not options:
            raise BrainError("模型没有返回可用的续接建议")
        return {
            "fragment": fragment,
            "options": options[:count],
            "model": self.model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    def suggest(
        self,
        history: list[dict[str, Any]],
        latest: str,
        count: int = 2,
        opening: bool = False,
        nonce: str = "",
        avoid: Optional[list[str]] = None,
        self_intro: bool = False,
        long_passage: bool = False,
        reply_language: str = "English",
        intro_words: Optional[tuple[int, int]] = None,
        intro_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        profile = self.profile_getter()
        count = max(1, min(4, int(count)))
        one = count == 1
        mode = ""
        if self_intro:
            has_facts = bool(str(profile.get("key_facts", "") or "").strip())
            example_point = (
                "(3) one concrete project or example from KEY FACTS, "
                if has_facts
                else "(3) general responsibilities and transferable skills; do NOT invent a specific project or example, "
            )
            fact_rule = (
                "Use the concrete example from KEY FACTS only."
                if has_facts
                else "KEY FACTS is empty: do not invent any project, client, migration, outcome or example."
            )
            if intro_words:
                words_min, words_max = intro_words
                if intro_seconds and intro_seconds >= 90:
                    duration = f"about {intro_seconds / 60:.1f} minutes".replace(".0 minutes", " minutes")
                elif intro_seconds:
                    duration = f"about {intro_seconds} seconds"
                else:
                    duration = "the requested length"
                mode = (
                    "SPECIAL MODE — SELF-INTRODUCTION REQUEST: The other person is asking for a self-introduction. "
                    f"The requested target is {duration}, roughly {words_min}-{words_max} words. "
                    "Write one coherent, natural spoken introduction in 3-6 short paragraphs. Use this structure: "
                    "(1) name and current role, (2) relevant experience, "
                    f"{example_point}"
                    "(4) one personal detail or interest, (5) why you are on this call or what you are looking for. "
                    "Start with a natural spoken hook, not a generic 'I am hardworking' claim. Use short, easy-to-"
                    "pronounce sentences and avoid rare or very long words unless they are in PROFILE. "
                    "For each claim, prefer one concrete proof point from KEY FACTS (action + result); if KEY FACTS "
                    "is empty, state only general responsibilities. End with a short bridge back to the interviewer. "
                    f"Stay within {words_min}-{words_max} words and do not pad with generic filler. "
                    f"{fact_rule} Never invent numbers, employers, degrees, project names or results. "
                    "If there is a second option, make it a shorter version or an introduction plus one question back. "
                    "This overrides the usual 1-2 sentence limit. Use only PROFILE and KEY FACTS; never invent employers, "
                    "degrees, metrics or numbers."
                )
            else:
                mode = (
                    "SPECIAL MODE — SELF-INTRODUCTION REQUEST: The other person is asking for a self-introduction. "
                    "Option 1 must be a coherent 3-5 sentence introduction (45-90 words) using: name, current role, "
                    "relevant experience, one personal detail, and why you are on this call. Option 2 should be a "
                    "shorter 2-3 sentence version, or a warm introduction plus one question back. This overrides the "
                    "usual 1-2 sentence limit. Use only PROFILE and KEY FACTS; never invent employers, degrees or numbers."
                )
        elif long_passage:
            mode = (
                "SPECIAL MODE — LONG PASSAGE: The latest item is a longer passage, possibly split by pauses. "
                "Respond to the main point of the whole passage, not only its last sentence. Option 1 should "
                "acknowledge or summarise the key point and answer it directly. Option 2 may ask one clarifying "
                "or follow-up question. Keep each option speakable in one breath unless a short summary is needed."
            )
        if one:
            mode += (
                "\nONE-OPTION MODE: return only the single best reply. Ignore any instruction above about "
                "Option 2 or comparing two alternatives."
            )
        first_rule = (
            "The single option must answer the latest message directly. You may add one short question after the answer."
            if one
            else "Both options must answer the latest message directly. Option 2 may add one short question after the answer."
        )
        difference_rule = (
            "Choose the single strongest angle; do not waste words contrasting alternatives."
            if one
            else "Make the options meaningfully different: one direct/warm answer, one honest answer that hands the conversation back."
        )
        answer_rule = (
            "The option must answer the question in its first sentence; you may add one short question about the other person's experience after the answer."
            if one
            else "Both options must answer the question in the first sentence. For option 2, you may add one short question about the other person's experience after the answer."
        )
        detail_rule = (
            "If KEY FACTS exist, include one concrete detail from them. Sound specific and human."
            if one
            else "If KEY FACTS exist, option 1 should include one concrete detail from them. Sound specific and human."
        )
        system = f"""You are Call Copilot, a discreet live assistant for {profile.get('name') or 'the user'}, a Chinese professional on a live voice call in {reply_language}.
Your job is to help the user respond naturally, in real time.

You receive the user's PROFILE, KEY FACTS, the RECENT TRANSCRIPT, and the LATEST message from the other person.
The other person is speaking {reply_language}. Write {"one reply option" if one else "both reply options"} in {reply_language}. Keep Chinese translations in Simplified Chinese.
IMPORTANT: Every character of the "reply" field must be {reply_language}. Do not write English there unless {reply_language} is English.
Write exactly {count} reply {"option" if one else "options"} the user can say out loud right now.

{mode}

Rules:
1. {first_rule}
2. Each option is normally 1-2 natural spoken sentences, 12-32 words. Unless SPECIAL MODE explicitly allows more, stay within that.
3. Use the KEY FACTS when relevant. Never invent metrics, names, dates, clients or experiences.
4. {detail_rule}
5. Avoid generic one-liners and corporate phrases such as "crucial", "ensure", "leverage", "seamless", "robust", "allows us to".
6. {difference_rule}
7. {answer_rule}
8. Never start with "That's a good/interesting question", "Let me think", or refer to prior conversations that are not in RECENT TRANSCRIPT.
9. Match the other person's language ({reply_language}) and the user's level. Do not switch to English unless the other person is speaking English.
10. Do not start with a greeting. No stage directions, no emoji.
11. Chinese translations must be colloquial spoken Chinese, not literal. "tone" is a real 2-4 character Chinese label such as 坦诚, 追问, 共情, 直接.

Style examples below are in English only to show tone; do not copy their language. Write your actual {"option" if one else "options"} in {reply_language}.{" For one-option mode, use only the first example." if one else ""}
LATEST: "What made you focus on onboarding first?"
- "Honestly, we kept hearing that new users got stuck in the first five minutes. That felt like the most useful place to start."
- "It started as a small bet, to be honest. We saw the drop-off and wanted to fix the root cause. Does that match what you're seeing?"

Return JSON only in this exact shape:
{{"options":[{{"reply":"...","zh":"...","tone":"坦诚"}}]}}
No markdown, no note, no text outside the JSON."""

        if opening:
            latest_text = "(The call is just starting. Suggest natural opening lines that fit the goal and relationship.)"
        else:
            latest_text = latest.strip() or "(No new message; offer a natural follow-up.)"

        user_parts = [
            "PROFILE:\n" + (_profile_block(profile) or "(not filled in)"),
            "RECENT TRANSCRIPT:\n" + _history_block(history),
            "LATEST (other person):\n" + latest_text,
        ]
        if self_intro:
            user_parts.append("MODE: the other person is asking for a self-introduction.")
        elif long_passage:
            user_parts.append("MODE: the latest item is a longer passage; respond to its main point.")
        if avoid:
            user_parts.append("Do not reuse these previous options:\n" + "\n".join(f"- {x}" for x in avoid[:4]))
        if nonce:
            user_parts.append("This is a regeneration: take clearly different angles from the previous batch.")
        user = "\n\n".join(user_parts)

        schema = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "reply": {"type": "string"},
                            "zh": {"type": "string"},
                            "tone": {"type": "string"},
                        },
                        "required": ["reply", "zh", "tone"],
                    },
                }
            },
            "required": ["options"],
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        started = time.perf_counter()
        max_tokens = 180 + count * 90 + (220 if long_passage else 0)
        if self_intro:
            target_max = intro_words[1] if intro_words else 90
            max_tokens += max(700, int(target_max * 6) + 500)
        max_tokens = min(3000, max_tokens)
        content = self.chat(
            messages,
            temperature=0.65,
            max_tokens=max_tokens,
            timeout=150.0,
            format_schema=schema,
        )
        try:
            obj = _json_object(content)
        except Exception:
            # One stricter retry, then fall back to the generic options.
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Return only valid JSON in the required shape."})
            content = self.chat(
                messages,
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=120.0,
                format_schema=schema,
            )
            obj = _json_object(content)

        options: list[dict[str, str]] = []
        for item in obj.get("options", []) or []:
            if not isinstance(item, dict):
                continue
            en = str(item.get("reply", item.get("en", "")) or "").strip()
            zh = _clean_translation(str(item.get("zh", "") or ""))
            tone = str(item.get("tone", "") or "").strip()
            if tone and ("汉字" in tone or len(tone) > 8 or tone.isascii()):
                tone = "自然"
            if en:
                options.append({"en": en, "zh": zh, "tone": tone[:8]})
        if not options:
            raise BrainError("模型没有返回可用的回答")
        if self_intro and intro_words and options:
            words_min, words_max = intro_words
            current_words = len(re.findall(r"\b[\w'-]+\b", options[0]["en"]))
            if current_words < words_min * 0.95 or current_words > words_max * 1.05:
                revised = self._reshape_intro(
                    profile,
                    options[0]["en"],
                    options[0].get("zh", ""),
                    words_min,
                    words_max,
                    reply_language,
                )
                if revised:
                    revised_words = len(re.findall(r"\b[\w'-]+\b", revised["en"]))
                    complete = revised["en"].rstrip().endswith((".", "!", "?", "。", "！", "？"))
                    center = (words_min + words_max) / 2
                    old_distance = abs(current_words - center)
                    new_distance = abs(revised_words - center)
                    if complete and revised_words >= words_min * 0.85 and new_distance <= old_distance * 1.05:
                        options[0] = revised
        return {
            "options": options[:count],
            "note": str(obj.get("note", "") or "").strip()[:80],
            "model": self.model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "fallback": False,
        }

    def test(self) -> dict[str, Any]:
        started = time.perf_counter()
        zh = self.translate("I've heard a lot about your work. What made you interested in this field?")
        result = self.suggest(
            history=[],
            latest="I've heard a lot about your work. What made you interested in this field?",
            count=max(1, min(4, int(self.settings.get("suggest_count", 2)))),
        )
        result["translation"] = zh
        result["total_ms"] = int((time.perf_counter() - started) * 1000)
        return result
