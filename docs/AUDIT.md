# 代码审计摘要（2026-11-14）

审计范围：项目全部 Python、静态前端、启动脚本、音频/转写/托盘/浮窗与模型调用。
本次为只读审计；审计后仅修复了下列“已修复”项，其余保留待后续处理。

## 已修复

- **中文/日文/韩文最终识别结果被过滤**：`server.py` 的 `_norm()` 原来只保留 ASCII，中文 turn 会被直接丢弃。已改为 Unicode 字符归一化。这是最高优先级修复，否则中文会议/中文字幕实际不可用。
- **focus/both 模式首次启动失效**：`_focus_cooldown_until` 未初始化，读取时抛 `AttributeError` 并被静默吞掉。已初始化。
- **托盘菜单轮询线程跨线程销毁 Win32 菜单**：轮询只更新状态，不再重建原生菜单；显式操作和模式切换才重建。
- **ASR worker 无异常保护**：单条坏数据/坏配置可永久杀死转写线程。已加逐条异常保护。
- **设置页“开机启动”假成功**：PowerShell 非终止错误可能返回 0，且未校验快捷方式。已加 `$ErrorActionPreference='Stop'`、try/catch 和文件校验。
- **`cancel_voice_input` 的 `voice_timer` 笔误**：改为 `_voice_timer`。
- **`DEFAULT_SETTINGS` 重复 `asr_model`**：删除被覆盖的死默认值。
- **启动设置轮询/菜单**：右键菜单按当前功能动态显示；开启/关闭功能、开始/停止、语音状态等操作按当前状态变化。
- **中文识别基础验证**：`_norm("你好，今天天气不错。")` 返回 `你好 今天天气不错`；日语/韩语也保留。

## 仍存在的重点风险（未在本次全部修复）

### P0

1. **单实例保护不可靠**：`ThreadingHTTPServer.allow_reuse_address` 在 Windows 上允许第二个实例绑定同一端口；`tray.py` 的 `_health()` 是先检查后绑定。可能双托盘、双份显存、注册快捷键冲突。
2. **TTS/overlay 子进程没有父进程死亡联动**：托盘崩溃/被强杀后 `tts_server.py`、`subtitle_overlay.py`、`voice_overlay.py` 可能成为孤儿；下次启动只按自己持有的 Popen 句柄判重。
3. **音频 stop 可能跳过 `Pa_Terminate`**：采集线程/`stream.read()` 卡住时，`AudioEngine.stop()` 可能只标记停止并返回，旧流/旧 PyAudio 实例泄漏，再次开始会重复采集或设备占用。
4. **语音输入状态机仍有竞态**：并发 start 可创建两个 `VoiceRecorder`；stop/cancel 在 `rec.stop()` 阻塞窗口内可重复转写或永久停在 `transcribing`；自动插入阻塞期间状态可被覆盖。
5. **start_capture 非幂等、无采集失败返回值**：并发 `/api/start` 可泄漏音频源；设备全开失败仍显示采集中；停止/重启时旧 ASR 队列可能写进新会话。

### P1

6. `/api/state`、`/api/devices`、`/api/settings` 返回明文 `api_key`；写接口无 Origin/CSRF/Token 校验。
7. `config.save_settings()` 的校验在一个大 try 中，任一坏值会跳过后续校验并写盘；`load_settings()` 不校验旧文件。
8. 异步字幕翻译可能发布 `turn_update` 覆盖用户已校对字幕；跨会话 turn id 会碰撞。
9. `transcriber` 全局锁会阻塞语音输入；60 秒语音超时后结果可能静默丢弃；运行期 CUDA OOM 不回退 CPU。
10. 英文短句（“Yes.”/“No.”/“Thank you.”）可能被幻觉过滤器误删；`faster-whisper-tiny` 的 100MB 完整性阈值会误判。
11. 会议纪要异步 worker 没有会话代际校验，清空/切换后旧纪要可能“复活”并写入新会话。
12. 采集中清空记录会令 `session_path=None`，之后直到重新开始采集前不再写文件。
13. TTS 助手存在 CORS 全开、端口冲突检测失效、退出时 `TerminateProcess` 导致朗读 PowerShell 孤儿。
14. `voice_overlay` 在 Tk 主线程做最长 12 秒 HTTP 请求；插入/取消时窗口可能假死。
15. 设置页存在全量覆盖、设备加载失败清空设备、服务端改写模型后前端不回填等数据丢失路径。

## 验证状态

- Python 编译通过。
- `static/app.js` 语法检查通过。
- `pip check` 无破损依赖。
- 健康接口：ASR/LLM ready。
- 当前模式：语音输入；语音状态 idle。
- 本地模型：6 个（新增 Qwen3.5-4B、Qwen3.5-2B）。
- 中文 `_norm` 回归已通过。

## 说明

上面的 P0/P1 是代码审计发现的真实风险，不代表当前每次都会触发。最优先的下一批修复应是：单实例/父子进程、语音输入状态机、音频 stop、start_capture 幂等与采集失败返回。
