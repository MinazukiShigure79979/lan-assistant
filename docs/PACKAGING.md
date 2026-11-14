# 迁移打包要求（待执行）

> 状态：**脚本已预置，暂不执行打包**。当前版本 **v1.0**；等主程序功能测试通过后再生成安装包。

## 一键打包

```powershell
# 只生成免安装目录
.\scripts\build_release.ps1 -SkipInstaller

# 生成安装包（需要 Inno Setup 6）
.\scripts\build_release.ps1
```

产物：

```text
dist\Lan.Assistant\
dist\installer\Lan.Assistant-Setup-v1.0.exe
dist\installer\release.json
```

安装包不包含 Ollama 和模型；首次启动下载。详见 [`scripts/README.md`](../scripts/README.md)。

## 目标

迁移到 AMD / Intel 或干净的 Windows 机器时，用户不应该还需要安装 Python、创建虚拟环境或手动执行 `start.bat`。项目根目录下的 Python 脚本应打包为 Windows EXE，双击或开机启动即可运行。

## 需要处理的脚本

当前入口和子进程：

- `tray.py`：主入口、托盘、服务生命周期
- `server.py`、`brain.py`、`audio_engine.py`、`transcriber.py` 等：主程序模块
- `tts_server.py`：当前由 tray 用 `pythonw.exe` 启动的独立子进程
- `subtitle_overlay.py`：悬浮字幕独立进程
- `voice_overlay.py`：语音输入校对浮窗独立进程

不要简单地为每个文件生成一个 EXE。更合适的是：

1. 打包一个主程序 `Lan.Assistant.exe`
2. 在同一个 EXE 内提供隐藏入口参数：
   - `--tts-server`
   - `--subtitle-overlay`
   - `--voice-overlay`
3. `tray.py` 启动子进程时使用 `sys.executable` + 对应参数，而不是 `pythonw.exe` + `.py`

这样只需要维护一个可执行文件、一个版本号和一套依赖。

## 推荐打包方式

首选 **PyInstaller `--onedir --noconsole`**，不建议先用 `--onefile`：

- `--onedir` 启动更快，CUDA / CTranslate2 的 DLL 更容易正确加载
- `--noconsole` 保证托盘程序不弹黑框
- 主入口：`tray.py`
- 图标：`static/favicon.ico`
- 版本信息 / 清单：需要单独添加
- 需要收集的隐藏依赖和二进制：
  - `faster_whisper` / `ctranslate2`
  - `pyaudiowpatch` / PortAudio
  - `pystray`
  - `PIL`
  - `onnxruntime`（Silero VAD）
  - Windows SAPI 相关运行时
- 打包后在无 Python 环境的干净 Windows 机器上验证 GPU 和 CPU 两条路径。

如果后续对启动速度、单文件分发或代码保护有更高要求，再评估 Nuitka 或安装器方案。

## 模型和 Ollama

模型体积很大，不应打进 EXE：

- Whisper：`models/faster-whisper-*`
- GGUF：`models/llm/*.gguf`
- Ollama 模型：`models/ollama/`
- Ollama 运行时：`tools/ollama/`

建议：

- EXE 只包含代码、静态资源和必要 DLL
- 模型继续外置，首次启动时检测并提示下载
- Ollama 可以随安装包附带，或作为可选组件安装
- 打包后仍保留硬件检测和自动选择模型逻辑
- 设置 → 模型（全局）→ 模型管理可以查看已下载/未下载、删除本地模型和添加自定义模型；迁移前可先删除大模型

## 首次启动流程

安装包不包含 Ollama、Whisper 或 GGUF。`setup_manager.py` 和 `static/setup.html` 已经预置首次启动流程：

- 检测不到 Ollama 时自动下载 `ollama-windows-amd64.zip` 便携版
- 安装到 `%LOCALAPPDATA%\Lan.Assistant\ollama`，不需要管理员权限
- 根据硬件自动勾选一个 Whisper 和一个 LLM，其它模型可手动勾选
- 模型下载到 `models/`；安装目录不可写时回退到 `%LOCALAPPDATA%\Lan.Assistant\models`
- 下载完成后才启动 ASR/LLM 后台线程并进入主界面
- 完成标记：`data/setup.json`

安装包打包时不要包含 `tools/ollama/`、`models/faster-whisper-*`、`models/llm/*.gguf`、`models/ollama/`。已经带齐模型和 Ollama 的开发机第一次运行会自动写入完成标记，不会弹出首次设置。

## API 凭据和预设

API Key、API 提供商配置和个人预设都属于**当前电脑的本机配置**，不进入安装包、`data/settings.json` 或模型迁移包。

- API：`%LOCALAPPDATA%\Lan.Assistant\api_profile.dat`
- 预设：`%LOCALAPPDATA%\Lan.Assistant\profile.dat`
- 两者都使用 Windows DPAPI 加密，绑定当前 Windows 用户/电脑
- 复制项目目录不会带走；复制到新电脑后必须重新填写
- 预设与 `data/profile.json` 无关；旧文件已在本机迁移并移除
- 设置里提供「清除本机 API 配置」

## 路径和启动项

打包后需要同步修改：

- `config.ROOT` 当前基于 `__file__`，在 PyInstaller 下要区分：
  - 可执行文件所在目录
  - 内部资源解压目录（`sys._MEIPASS`）
- `startup.py` 的开机启动快捷方式目标要从：
  - `.venv\Scripts\pythonw.exe tray.py`
  - 改为 `Lan.Assistant.exe`
- 桌面快捷方式同样改指向 EXE
- 日志、会话和设置建议放到：
  - `%LOCALAPPDATA%\Lan.Assistant\`
  - 或安装目录旁的可写 `data/`
- 避免把用户数据写进 `Program Files`。

## 迁移验收清单

- [ ] 干净 Windows 机器不安装 Python 也能启动托盘
- [ ] 不出现控制台窗口
- [ ] 系统托盘菜单、页面、快捷键正常
- [ ] TTS、悬浮字幕、语音校对浮窗能作为同一 EXE 的子进程启动
- [ ] CUDA 机器走 GPU，Intel/AMD/CPU 机器能回退
- [ ] 模型扫描仍能找到外置模型
- [ ] 设置、预设、会话和日志路径正确
- [ ] 开机启动指向 EXE
- [ ] 卸载/升级不会误删用户数据
- [ ] 在 Windows Defender / SmartScreen 下验证是否需要代码签名

## 记录原因

这是迁移计划的一部分，不是当前功能开发的阻塞项。当前阶段继续用 `.venv` 开发调试，等目标硬件和分发方式确定后再进入打包实施。
