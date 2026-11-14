# 打包与发布脚本

当前版本：由根目录 `version.py` 统一维护（当前 `1.0.0` / `v1.0`）。

## 前置工具

```powershell
cd D:\Works\DSH_workspace\lan-assistant

# 安装 PyInstaller 到 .venv
.\scripts\install_build_tools.ps1

# 需要自动安装 Inno Setup 6 时
.\scripts\install_build_tools.ps1 -InstallInnoSetup
```

## 打包

只生成免安装目录：

```powershell
.\scripts\build_release.ps1 -SkipInstaller
```

生成目录和安装包：

```powershell
.\scripts\build_release.ps1
```

指定 Inno Setup：

```powershell
.\scripts\build_release.ps1 -IsccPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

## 产物

```text
dist\Lan.Assistant\                 PyInstaller onedir（不依赖目标机器 Python）
dist\installer\Lan.Assistant-Setup-v1.0.exe       需要管理员权限
dist\installer\Lan.Assistant-Setup-v1.0-User.exe  当前用户安装，不触发 UAC
dist\installer\*.sha256
dist\installer\release.json
```

## 安装包规则

### 管理员安装版

- 默认安装目录：`D:\Lan_Assistant`
- 没有 D 盘：`C:\Lan_Assistant`
- 用户选择父目录后，安装程序自动追加 `\Lan_Assistant`
- 会触发 UAC；未做代码签名时，Windows 显示“发布者未知”

### 当前用户安装版（`-User`）

- 默认安装目录：`%LOCALAPPDATA%\Programs\Lan_Assistant`
- 不需要管理员权限，不触发 UAC 的发布者提示
- 仍会创建开始菜单快捷方式和标准卸载程序

### 共同规则

- **不打包** Ollama、Whisper、GGUF 或 Ollama blob
- 首次启动由 `setup_manager.py` 下载运行环境和模型
- 要显示可信发布者，需要使用受信任的 Authenticode 代码签名证书对安装包签名；自签名证书不能消除“未知发布者”提示

## 版本发布

修改根目录 `version.py`：

```python
VERSION = "1.0.0"
DISPLAY_VERSION = "v1.0"
INSTALLER_VERSION = "1.0.0.0"
```

然后重新运行打包脚本。安装包文件名、Windows 文件版本、`release.json` 和程序内版本显示都会使用这里的内容。
