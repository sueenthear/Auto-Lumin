# Agent 环境配置说明

本文说明如何在 Windows 上为本项目建立 Python 环境、安装依赖并运行测试或开发工具。请在项目根目录（包含 `pyproject.toml` 的目录）执行命令。

## 环境要求

- Windows 10/11 桌面环境。窗口绑定、输入模拟和截图功能依赖 Windows API；`pywin32` 也仅在 Windows 上安装。
- Python 3.10 或更新版本，并确保 Python Launcher (`py`) 可用。
- Git 仅在克隆或推送项目时需要。
- 项目依赖由 `pyproject.toml` 管理；不要将本机 `.venv` 或已安装的依赖目录提交到版本库。

确认 Python 版本：

```powershell
py -3 --version
```

## 创建并安装核心库环境

在项目根目录创建虚拟环境并激活：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止当前会话运行激活脚本，可以不激活环境，直接使用 `.venv\Scripts\python.exe` 执行下面的命令。

安装项目及其依赖：

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

`-e .` 会将当前源码以 editable 模式安装，修改 `script_core` 后无需重新安装。之后可以从项目脚本中导入：

```python
from script_core import Core
```

## 安装开发工具依赖

`dev_tool/ok_lumin_tool` 有自己的依赖清单，与核心库依赖分开管理。在已激活的项目虚拟环境中执行：

```powershell
python -m pip install -r dev_tool\ok_lumin_tool\requirements.txt
```

开发工具通过 Tkinter 提供 GUI。使用项目虚拟环境启动：

```powershell
python -m dev_tool.ok_lumin_tool
```

也可以从项目根目录双击 `dev_tool\启动.bat`。工具需要桌面会话；无图形桌面的 CI 或远程会话中不要把 GUI 启动作为自动化测试步骤。

## 测试

在项目根目录运行单元测试：

```powershell
python -m unittest discover -s tests -v
```

仅运行某个测试文件时，例如：

```powershell
python -m unittest discover -s tests -p test_steps_execution.py -v
```

## 使用注意

- 自动化 API 需要目标程序正在运行且存在可见窗口。例如 `Core("Lumin.exe")` 会按进程名查找窗口；具体 API 见 `script_core/use.md`。
- 如果目标程序以管理员权限运行，自动化脚本或 `dev_tool` 可能也需要以管理员权限启动，否则 Windows 可能阻止窗口操作或输入注入。
- 自动化脚本会向目标窗口发送真实键盘和鼠标事件。运行示例或用户脚本前，先确认目标窗口、坐标、按键和动作内容，避免在不期望的窗口执行输入。
- `dev_tool` 的截图和日志可能写入当前用户目录或当前工作目录。不要把运行时截图、录制文件、日志、缓存、`.venv` 或临时文件当作源码提交。
- 不要在仓库中保存访问令牌、密码、个人配置或其他凭据。

## 项目入口

- `pyproject.toml`：核心库的 Python 版本要求和依赖。
- `script_core/use.md`：核心自动化 API 用法。
- `dev_tool/ok_lumin_tool/requirements.txt`：开发工具依赖。
- `dev_tool/README.md`：开发工具启动和功能概览。
- `tests/`：单元测试。
