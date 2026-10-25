"""Auto Lumin 的独立 Qt/Fluent 启动器 v1.1（不会自动安装依赖或修改 Git）。

运行：python Launcher.py [--workspace 工作区]
打包：将 icon.ico 作为资源和 exe 图标一起打包；工作区必须位于 exe
旁边、启动时的当前目录，或通过 --workspace 指定，不能放进 _MEIPASS。

Task/<任务目录>/this.task 示例（所有路径相对于 this.task）：
{"title": "示例", "description": "任务文字说明", "editor": "作者",
 "version": "1.0", "description_image": "说明.png", "run_target": "run.py",
 "var": {"value": {"times": 50}, "type": {"times": "int"},
         "description": {"times": "执行次数"}}}
原需求两次使用 description；JSON 不应使用重复键。因此文字使用 description，
图片使用 description_image；兼容旧版 description=图片路径 + description_text=文字，
以及 description={"text": "说明", "image": "说明.png"}。
描述图片可选：省略 description_image 或设为 "" 均表示无图，不影响运行。

var 可选，支持 int、float、string、bool。详情页把 value/type/description 呈现为
可折叠卡片中的配置控件；点击运行时先写回 this.task，再用
`--this-task <this.task 的绝对路径>` 启动脚本（Core.var 读取同一文件）。
快捷导入支持 .zip、.7z 和 .rar；.rar 需要完整版 7z.exe（含 7z.dll）。
未安装 7-Zip 时任务页不提供快捷导入；启动页可一键安装完整版（可选）。
以管理员身份运行时若拖放被 Windows 拦截，任务页使用「选择任务包」，并尝试传统 WM_DROPFILES 通道。
"""
from __future__ import annotations

import argparse
import codecs
import ctypes
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Callable
from urllib.parse import urljoin, urlparse

from PySide6.QtCore import (
    QAbstractNativeEventFilter, QByteArray, QObject, QProcess, QProcessEnvironment,
    QPropertyAnimation, QSettings, QRectF, QSize, QStandardPaths, Qt, QThread,
    QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, DoubleSpinBox, FluentIcon as FIF,
    FluentWidget, ComboBox, IconWidget, IndeterminateProgressRing, LineEdit, ListWidget,
    PrimaryPushButton, PushButton, ScrollArea, SearchLineEdit, SpinBox, StrongBodyLabel,
    SubtitleLabel, SwitchButton, Theme, TitleLabel, TransparentPushButton, isDarkTheme,
    qconfig, setTheme, themeColor,
)

APP_NAME = "Auto Lumin"
LAUNCHER_VERSION = "1.1"
PYTHON_MIN = (3, 10)
PYTHON_URL = "https://www.python.org/downloads/"
UV_URL = "https://docs.astral.sh/uv/getting-started/installation/"
GIT_URL = "https://git-scm.com/downloads"
SEVEN_ZIP_URL = "https://www.7-zip.org/"
GITHUB_URL = "https://github.com/sueenthear/Auto-Lumin"
RELEASES_URL = "https://github.com/sueenthear/Auto-Lumin/releases/latest"
BACKGROUND_GUIDE_URL = "https://www.bettergi.com/tutorial/rdp.html#%E5%BC%80%E5%A7%8B%E5%AE%89%E8%A3%85"
QQ_GROUP = "1108948355"
PAGE_TITLES = ("启动", "任务", "详情", "运行")
REQUIRED_CHECKS = ("python", "uv", "workspace", "bash", "windows", "admin")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG_PATTERN = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]"
    r"\[来源[：:]do\.[^\]\r\n]+\]<.*>$"
)


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin(argv: list[str] | None = None) -> bool:
    """未提权时以管理员身份重启自身；返回 True 表示由新进程接管。"""
    if os.name != "nt" or is_admin():
        return False
    arguments = list(sys.argv[1:] if argv is None else argv)
    # 源码运行需要把脚本路径作为参数重新传入；打包后 sys.executable 就是启动器本身。
    executable = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(executable), subprocess.list2cmdline(arguments), None, 1)
    except (AttributeError, OSError):
        return False
    return result > 32


def workspace_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    app_dir = (Path(sys.executable) if getattr(sys, "frozen", False)
               else Path(__file__)).resolve().parent
    candidates = ([app_dir, Path.cwd()] if getattr(sys, "frozen", False)
                  else [Path.cwd(), app_dir])
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file():
            return candidate.resolve()
    return app_dir


def resource_path(relative: str, workspace: Path | None = None) -> Path:
    app_dir = (Path(sys.executable) if getattr(sys, "frozen", False)
               else Path(__file__)).resolve().parent
    roots = [root for root in (workspace, app_dir,
             Path(getattr(sys, "_MEIPASS", app_dir))) if root is not None]
    for root in roots:
        if (root / relative).is_file():
            return root / relative
    return roots[0] / relative


def launcher_settings_path(workspace: Path | None = None) -> Path:
    """返回工作区内的配置文件路径。"""
    return (workspace or workspace_path()).resolve() / "launcher_set.ini"


def create_desktop_shortcut(workspace: Path) -> Path:
    """创建或覆盖当前启动器的桌面快捷方式，仅支持 Windows。"""
    if os.name != "nt":
        raise RuntimeError("桌面快捷方式仅支持 Windows")
    desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    if not desktop:
        raise RuntimeError("无法定位桌面目录")
    shortcut_path = Path(desktop) / f"{APP_NAME}.lnk"
    import win32com.client
    shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(shortcut_path))
    frozen = getattr(sys, "frozen", False)
    target = Path(sys.executable).resolve()
    shortcut.TargetPath = str(target)
    arguments = ["--workspace", str(workspace.resolve())]
    if not frozen:
        arguments.insert(0, str(Path(__file__).resolve()))
    shortcut.Arguments = subprocess.list2cmdline(arguments)
    shortcut.WorkingDirectory = str(workspace.resolve())
    icon = resource_path("icon.ico", workspace)
    shortcut.IconLocation = f"{icon},0" if icon.is_file() else f"{target},0"
    shortcut.Description = f"{APP_NAME} 工作区启动器"
    shortcut.Save()
    return shortcut_path


def venv_python(workspace: Path) -> Path:
    return workspace / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for folder in (Path.home() / ".local/bin", Path.home() / ".cargo/bin"):
        candidate = folder / ("uv.exe" if os.name == "nt" else "uv")
        if candidate.is_file():
            return str(candidate)
    return None


def find_bash() -> str | None:
    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        parent = Path(git).resolve().parent
        for root in (parent, parent.parent, parent.parent.parent):
            candidates.extend((root / "bin/bash.exe", root / "usr/bin/bash.exe"))
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(variable):
            root = Path(os.environ[variable])
            candidates.extend((root / "Git/bin/bash.exe",
                               root / "Programs/Git/bin/bash.exe"))
    found = shutil.which("bash")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        # System32/bash.exe 是 WSL 入口，不能用它激活 Windows 的 .venv。
        if candidate.is_file() and not (os.name == "nt" and
                "windows" in [part.lower() for part in candidate.parts]):
            return str(candidate)
    return None


def windows_build() -> int:
    return sys.getwindowsversion().build if os.name == "nt" else 0


def workspace_environment(workspace: Path) -> dict[str, str]:
    # 不允许继承的 UV_PROJECT_ENVIRONMENT / UV_TARGET 等将依赖写入别处。
    # uv 自身的配置文件、代理和系统 PATH 不受影响。
    excluded = {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV",
                "PIP_TARGET", "PIP_PREFIX", "PIP_USER"}
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("UV_") and key.upper() not in excluded}
    env["UV_PROJECT_ENVIRONMENT"] = str((workspace / ".venv").resolve())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def command_result(args: list[str], cwd: Path, timeout: int = 20) -> str:
    env = workspace_environment(cwd)
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-1600:]
                           or f"命令失败，退出码 {result.returncode}")
    return result.stdout.strip()


@dataclass(frozen=True)
class CheckResult:
    key: str
    ok: bool
    detail: str


def inspect_environment(workspace: Path, progress: Callable, cancelled: Callable) -> list[CheckResult]:
    results = []

    def record(key: str, ok: bool, detail: str):
        results.append(CheckResult(key, ok, detail))
        progress(min(100, len(results) * 100 // 7), detail)

    commands: list[list[str]] = []
    local_python = venv_python(workspace)
    if local_python.is_file():
        commands.append([str(local_python)])
    if not getattr(sys, "frozen", False):
        commands.append([sys.executable])
    if shutil.which("py"):
        commands.append([shutil.which("py"), "-3"])
    for name in ("python", "python3"):
        executable = shutil.which(name)
        if executable and "windowsapps" not in executable.lower():
            commands.append([executable])
    python_info = "未发现可用的本机 Python（打包程序的内置 Python 不算本机安装）"
    python_ok = False
    for command in commands:
        if cancelled():
            return []
        try:
            output = command_result(command + ["-I", "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3])))"], workspace, 8)
            version = tuple(int(part) for part in output.split("."))
            python_info = f"{output} · {command[0]}"
            if version >= PYTHON_MIN:
                python_ok = True
                break
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            continue
    record("python", python_ok, python_info)
    uv = find_uv()
    try:
        uv_detail = command_result([uv, "--version"], workspace, 8) if uv else "未安装 uv"
        uv_ok = bool(uv)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        uv_ok, uv_detail = False, str(exc)
    record("uv", uv_ok, uv_detail)
    bash = find_bash()
    record("bash", bool(bash), str(bash) if bash else
           "未找到 Git Bash；请安装 Git for Windows 并重新检查")
    if cancelled():
        return []
    try:
        for name in ("pyproject.toml", "uv.lock", "script_core/__init__.py"):
            if not (workspace / name).is_file():
                raise RuntimeError(f"缺少 {name}；请选择完整工作区")
        if not local_python.is_file():
            raise RuntimeError("缺少工作区 .venv，请点击“一键准备工作区”")
        if not bash:
            raise RuntimeError("未找到 Git Bash；请安装 Git for Windows 并重新检查")
        if not uv_ok:
            raise RuntimeError("需要先安装 uv，再检查工作区依赖")
        command_result([uv, "sync", "--check", "--locked", "--offline",
                        "--project", str(workspace)], workspace, 30)
        probe = ("import importlib.util,json,sys; "
                 "s=importlib.util.find_spec('script_core'); "
                 "print(json.dumps({'version':list(sys.version_info[:3]),"
                 "'origin':s.origin if s else None}))")
        info = json.loads(command_result([str(local_python), "-I", "-c", probe], workspace, 10))
        if tuple(info["version"]) < PYTHON_MIN:
            raise RuntimeError("工作区解释器版本过低，请重新创建 .venv")
        origin = info.get("origin")
        if not origin or Path(origin).resolve() != (workspace / "script_core/__init__.py").resolve():
            raise RuntimeError("script_core 未以 editable 方式挂载到本工作区")
        record("workspace", True, f"依赖同步、script_core 挂载和 Bash 均就绪 · {workspace}")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        record("workspace", False, str(exc))
    build = windows_build()
    record("windows", os.name == "nt" and build >= 20348,
           f"Build {build}，要求 ≥ 20348" if build else "仅支持 Windows")
    admin = is_admin()
    record("admin", admin, "已以管理员身份运行" if admin else "请以管理员身份重新启动启动器")
    seven_zip = find_7zip(workspace)
    if seven_zip is None:
        seven_zip_detail = "未安装 7-Zip；快捷导入不可用（可选）"
    elif supports_rar(seven_zip):
        seven_zip_detail = f"{seven_zip}（完整版，支持 RAR）"
    else:
        kind = "独立版" if seven_zip.name.lower() == "7za.exe" else "精简版"
        seven_zip_detail = f"{seven_zip}（{kind}，不支持 RAR；可一键安装完整版）"
    record("seven_zip", bool(seven_zip), seven_zip_detail)
    return results


@dataclass(frozen=True)
class TaskVariable:
    name: str
    value: int | float | str | bool
    type: str
    description: str


VARIABLE_TYPES = ("int", "float", "string", "bool")
_VARIABLE_ALIASES = {"int": "int", "float": "float", "str": "string",
                     "string": "string", "bool": "bool"}


def variable_type_name(value) -> str:
    """Return the this.task type name of a supported Python value."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    raise ValueError("变量值只能是 int、float、string 或 bool")


def normalize_variable_type(value) -> str:
    if isinstance(value, str) and value.strip().lower() in _VARIABLE_ALIASES:
        return _VARIABLE_ALIASES[value.strip().lower()]
    raise ValueError(f"变量类型必须是 {', '.join(VARIABLE_TYPES)} 之一")


def coerce_variable_value(value, expected: str, *, source: str):
    """Validate a value; int is accepted for float and converted."""
    actual = variable_type_name(value)
    if actual == expected:
        return value
    if expected == "float" and actual == "int":
        return float(value)
    raise ValueError(f"{source}应为 {expected}，实际为 {actual}")


def parse_task_variables(raw) -> tuple[TaskVariable, ...]:
    """Parse the ``var`` section, rejecting values that do not match their type."""
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("var 必须是对象")
    values = raw.get("value", {})
    types = raw.get("type", {})
    descriptions = raw.get("description", {})
    for key, section in (("value", values), ("type", types),
                         ("description", descriptions)):
        if not isinstance(section, dict):
            raise ValueError(f"var.{key} 必须是对象")
    variables = []
    for name, value in values.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("变量名必须是非空字符串")
        actual = variable_type_name(value)
        declared = types.get(name)
        expected = actual if declared is None else normalize_variable_type(declared)
        coerced = coerce_variable_value(value, expected, source=f'变量 "{name}" 的值')
        description = descriptions.get(name, "")
        if not isinstance(description, str):
            raise ValueError(f'变量 "{name}" 的描述必须是字符串')
        variables.append(TaskVariable(name, coerced, expected, description))
    return tuple(variables)


def save_task_variables(manifest: Path, variables: tuple[TaskVariable, ...],
                        values: dict) -> None:
    """Write edited variable values back into this.task, keeping other keys."""
    raw = Path(manifest).read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("this.task 必须是 JSON 对象")
    var = data.get("var")
    if var is None:
        var = {}
        data["var"] = var
    if not isinstance(var, dict):
        raise ValueError("var 必须是对象")
    sections = {}
    for key in ("value", "type", "description"):
        section = var.get(key)
        if section is None:
            section = {}
            var[key] = section
        if not isinstance(section, dict):
            raise ValueError(f"var.{key} 必须是对象")
        sections[key] = section
    for variable in variables:
        value = coerce_variable_value(
            values.get(variable.name, variable.value), variable.type,
            source=f'变量 "{variable.name}" 的值')
        sections["value"][variable.name] = value
        sections["type"][variable.name] = variable.type
        sections["description"].setdefault(variable.name, variable.description)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    Path(manifest).write_text(text, encoding="utf-8-sig" if bom else "utf-8")


def format_build_time(timestamp: float) -> str:
    if not timestamp:
        return "未知"
    return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class Task:
    path: Path
    title: str
    description: str
    editor: str
    version: str
    image: Path | None
    run_target: Path | None
    modified_at: float = 0.0
    error: str = ""
    variables: tuple[TaskVariable, ...] = ()


def _relative_path(manifest: Path, value: str, workspace: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("任务路径必须为非空字符串")
    portable = value.replace(chr(92), "/")
    if Path(portable).is_absolute() or PureWindowsPath(value).drive or portable.startswith("/"):
        raise ValueError("任务路径必须是相对于 this.task 的路径")
    resolved = (manifest.parent / portable).resolve()
    if not resolved.is_relative_to(workspace.resolve()):
        raise ValueError("任务路径不可越出工作区")
    return resolved


def load_task(path: Path, workspace: Path | None = None) -> Task:
    path = path.resolve()
    workspace = (workspace or path.parent).resolve()
    title = path.parent.name
    description = ""
    editor = version = "未知"
    image = target = None
    variables: tuple[TaskVariable, ...] = ()
    try:
        modified_at = path.stat().st_mtime
        if not path.is_relative_to(workspace):
            raise ValueError("任务文件不可越出工作区")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("this.task 必须是 JSON 对象")
        raw_title = data.get("title")
        if not isinstance(raw_title, str) or not raw_title.strip():
            raise ValueError("title 必须是非空任务名称")
        title = raw_title.strip()
        editor, version = str(data.get("editor", "未知")), str(data.get("version", "未知"))
        raw_description = data.get("description", "")
        # 图片是可选项；显式留空表示无图，不能再从兼容字段回填。
        explicit_image = "description_image" in data or "image" in data
        image_value = data.get("description_image", data.get("image"))
        if isinstance(raw_description, dict):
            description = str(raw_description.get("text", ""))
            if not explicit_image:
                image_value = raw_description.get("image")
        elif isinstance(raw_description, str):
            if raw_description.lower().endswith(".png"):
                if not explicit_image:
                    image_value = raw_description
                description = str(data.get("description_text", ""))
            else:
                description = raw_description or str(data.get("description_text", ""))
        else:
            raise ValueError("description 应为文字或包含 text/image 的对象")
        if image_value is not None:
            if not isinstance(image_value, str):
                raise ValueError("描述图片路径应为字符串；不提供图片时可留空或省略该键")
            image_value = image_value.strip()
        if image_value:
            image = _relative_path(path, image_value, workspace)
            if image.suffix.lower() != ".png":
                raise ValueError("描述图片必须为 .png")
        target = _relative_path(path, data.get("run_target", "run.py"), workspace)
        if target.is_dir():
            target = target / "run.py"
        if target.suffix.lower() != ".py" or not target.is_file():
            raise ValueError(f"找不到 Python 运行文件：{target}")
        variables = parse_task_variables(data.get("var"))
        return Task(path=path, title=title.strip(), description=description,
                    editor=editor, version=version, image=image, run_target=target,
                    modified_at=modified_at, variables=variables)
    except (OSError, ValueError, TypeError) as exc:
        return Task(path=path, title=title or path.parent.name, description=description,
                    editor=editor, version=version, image=image, run_target=target,
                    modified_at=locals().get("modified_at", 0.0), error=str(exc),
                    variables=variables)


def scan_tasks(workspace: Path, progress: Callable, cancelled: Callable) -> list[Task]:
    root = workspace / "Task"
    if not root.is_dir():
        return []
    files = sorted((p for p in root.rglob("this.task")
                    if p.name == "this.task" and p.is_file()), key=lambda p: str(p).casefold())
    tasks = []
    for number, path in enumerate(files, 1):
        if cancelled():
            break
        tasks.append(load_task(path, workspace))
        progress(number * 100 // len(files), f"读取任务 {number}/{len(files)}")
    return tasks


SEVEN_ZIP_NAMES = ("7z.exe", "7za.exe", "7zr.exe")
SEVEN_ZIP_MSI_PAGE = "https://www.7-zip.org/download.html"
SEVEN_ZIP_MSI_X64 = re.compile(
    r'((?:https?:)?//[^"\']+?/7z[0-9]+-x64\.msi|a/7z[0-9]+-x64\.msi)',
    re.IGNORECASE)
SEVEN_ZIP_MSI_X86 = re.compile(
    r'((?:https?:)?//[^"\']+?/7z[0-9]+\.msi|a/7z[0-9]+\.msi)',
    re.IGNORECASE)
SEVEN_ZIP_MSI_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SEVEN_ZIP_MSI_MIN_BYTES = 1_000_000
SEVEN_ZIP_MAX_BYTES = 32 * 1024 * 1024
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
UNCATEGORIZED = "未分类"


def find_7zip(workspace: Path) -> Path | None:
    """Locate a usable 7-Zip executable placed in the workspace root."""
    for name in SEVEN_ZIP_NAMES:
        candidate = workspace / name
        if not candidate.is_file():
            continue
        if name.lower() == "7z.exe" and not (workspace / "7z.dll").is_file():
            continue
        return candidate
    return None


def supports_rar(executable: Path | None) -> bool:
    """Only the full 7z.exe build includes the RAR decoder."""
    return executable is not None and executable.name.lower() in ("7z.exe", "7z")


def _urlopen_bytes(url: str, limit: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"{APP_NAME}/{LAUNCHER_VERSION}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("下载内容过大，已中止")
    return data


def seven_zip_msi_url() -> str | None:
    """Find the current 7-Zip MSI link on the official page."""
    try:
        page = _urlopen_bytes(SEVEN_ZIP_MSI_PAGE, 2 * 1024 * 1024)
    except OSError:
        return None
    html = page.decode("utf-8", "replace")
    patterns = ((SEVEN_ZIP_MSI_X64, SEVEN_ZIP_MSI_X86) if sys.maxsize > 2**32
                else (SEVEN_ZIP_MSI_X86, SEVEN_ZIP_MSI_X64))
    for pattern in patterns:
        match = pattern.search(html)
        if match:
            return urljoin(SEVEN_ZIP_MSI_PAGE, match.group(1))
    return None


def _extract_msi(msi: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={destination}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, creationflags=NO_WINDOW,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise RuntimeError("7-Zip 安装包解压失败：" +
                           (detail or f"退出码 {result.returncode}"))


def install_7zip(workspace: Path, progress: Callable, cancelled: Callable) -> Path:
    """Install the full 7-Zip build (7z.exe + 7z.dll) that can extract RAR."""
    if cancelled():
        raise RuntimeError("已取消")
    existing = find_7zip(workspace)
    if supports_rar(existing):
        progress(100, f"已安装完整版 7-Zip：{existing}")
        return existing
    progress(5, "正在查找 7-Zip 完整版下载地址…")
    msi_url = seven_zip_msi_url()
    if msi_url is None:
        raise RuntimeError(
            "无法获取 7-Zip 完整版下载地址；请手动将 7z.exe 和 7z.dll 放到工作区根目录")
    progress(20, "正在下载 7-Zip 完整版…")
    try:
        data = _urlopen_bytes(msi_url, SEVEN_ZIP_MAX_BYTES)
    except OSError as exc:
        raise RuntimeError(f"下载 7-Zip 完整版失败：{exc}") from exc
    if len(data) < SEVEN_ZIP_MSI_MIN_BYTES or not data.startswith(SEVEN_ZIP_MSI_MAGIC):
        raise RuntimeError("下载内容不是有效的 7-Zip 安装包")
    temporary_dir = Path(tempfile.mkdtemp(prefix="lumin-7zip-"))
    try:
        msi_path = temporary_dir / "7z.msi"
        msi_path.write_bytes(data)
        extract_dir = temporary_dir / "extract"
        progress(45, "正在解压 7-Zip 安装包…")
        _extract_msi(msi_path, extract_dir)
        deadline = time.monotonic() + 10
        candidates = []
        while time.monotonic() < deadline:
            candidates = sorted(extract_dir.rglob("7z.exe"),
                                key=lambda path: str(path).casefold())
            if candidates:
                break
            time.sleep(0.2)
        if not candidates:
            raise RuntimeError("7-Zip 安装包中未找到 7z.exe")
        source = candidates[0]
        library = source.with_name("7z.dll")
        if not library.is_file():
            raise RuntimeError("7-Zip 安装包中缺少 7z.dll")
        progress(80, "正在安装 7-Zip 到工作区…")
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "7z.exe"
        try:
            shutil.copy2(source, target)
            shutil.copy2(library, workspace / "7z.dll")
        except OSError:
            target.unlink(missing_ok=True)
            (workspace / "7z.dll").unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)
    progress(100, f"7-Zip 完整版已安装：{target}")
    return target


@dataclass(frozen=True)
class TaskPackage:
    archive: Path
    temp: Path | None
    root: Path | None
    name: str
    title: str
    version: str
    files: int
    error: str = ""


def _extract_zip(archive: Path, destination: Path) -> None:
    base = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(base):
                raise ValueError(f"压缩包包含越界路径：{info.filename}")
        package.extractall(destination)


def _extract_7z(executable: Path, archive: Path, destination: Path) -> None:
    result = subprocess.run(
        [str(executable), "x", "-y", f"-o{destination}", str(archive)],
        cwd=destination, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, creationflags=NO_WINDOW,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise ValueError("7-Zip 解压失败：" + (detail or f"退出码 {result.returncode}"))


def _check_extracted_paths(root: Path) -> None:
    base = root.resolve()
    for path in root.rglob("*"):
        if not path.resolve().is_relative_to(base):
            raise ValueError("压缩包包含越界路径")


def _package_name(root: Path, temp: Path, archive: Path) -> str:
    name = archive.stem if root.resolve() == temp.resolve() else root.name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "导入任务"


def inspect_task_package(archive: str | Path, seven_zip: Path | None,
                         progress: Callable, cancelled: Callable) -> TaskPackage:
    """Extract an archive and validate that it contains one runnable task."""
    archive = Path(archive)
    progress(5, f"正在检查 {archive.name}…")
    temp = Path(tempfile.mkdtemp(prefix="lumin-import-"))

    def fail(message: str) -> TaskPackage:
        shutil.rmtree(temp, ignore_errors=True)
        return TaskPackage(archive, None, None, "", "", "", 0, message)

    try:
        if not archive.is_file():
            raise ValueError("任务包不存在")
        suffix = archive.suffix.lower()
        if suffix == ".zip":
            _extract_zip(archive, temp)
        elif suffix in (".7z", ".rar"):
            if seven_zip is None:
                raise ValueError(f"未安装 7-Zip，无法解压 {suffix} 任务包")
            if suffix == ".rar" and not supports_rar(Path(seven_zip)):
                raise ValueError(
                    "当前 7-Zip 版本不支持 RAR（需要完整版 7z.exe）；请安装完整版")
            _extract_7z(Path(seven_zip), archive, temp)
        else:
            raise ValueError("只支持 .zip、.7z 或 .rar 任务包")
        if cancelled():
            raise RuntimeError("已取消")
        _check_extracted_paths(temp)
        progress(65, "正在检查任务环境完整性…")
        manifests = sorted(p for p in temp.rglob("this.task") if p.is_file())
        if not manifests:
            raise ValueError("压缩包中没有 this.task")
        if len(manifests) > 1:
            raise ValueError("压缩包包含多个 this.task，无法确定要导入的任务")
        manifest = manifests[0]
        top = [p for p in temp.iterdir() if p.name not in ("__MACOSX", ".DS_Store")]
        candidate = top[0] if len(top) == 1 and top[0].is_dir() else None
        root = candidate if candidate is not None and manifest.is_relative_to(candidate) else manifest.parent
        task = load_task(manifest, root)
        if task.error:
            raise ValueError(task.error)
        if task.image is not None and not task.image.is_file():
            raise ValueError(f"描述图片缺失：{task.image.name}")
        files = sum(1 for path in root.rglob("*") if path.is_file())
        name = _package_name(root, temp, archive)
        progress(100, f"任务包检查通过：{task.title}")
        return TaskPackage(archive, temp, root, name, task.title, task.version, files)
    except (OSError, ValueError, TypeError, RuntimeError, zipfile.BadZipFile,
            subprocess.TimeoutExpired) as exc:
        return fail(str(exc))


def unique_task_name(parent: Path, name: str) -> str:
    candidate = name
    number = 2
    while (parent / candidate).exists():
        candidate = f"{name} ({number})"
        number += 1
    return candidate


def import_task_package(package: TaskPackage, workspace: Path,
                        progress: Callable, cancelled: Callable) -> Path:
    if package.error or package.root is None or package.temp is None:
        raise ValueError(package.error or "任务包未通过检查")
    if not package.root.is_dir() or not package.root.resolve().is_relative_to(package.temp.resolve()):
        raise ValueError("任务包目录已失效，请重新选择")
    if cancelled():
        raise RuntimeError("已取消")
    folder = workspace / "Task" / UNCATEGORIZED
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / unique_task_name(folder, package.name)
    progress(30, "正在复制任务文件…")
    try:
        shutil.copytree(package.root, target,
                        ignore=shutil.ignore_patterns("__MACOSX", ".DS_Store"))
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    progress(100, f"已导入：{target}")
    return target


class Job(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable, parent: QObject):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            result = self.function(self.progress.emit, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.result.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class GitState:
    ahead: int
    behind: int
    upstream: str


def _github_remote(remote: str) -> bool:
    if re.match(r"^(?:[^@/:]+@)?github\.com:", remote, re.IGNORECASE):
        return True
    return urlparse(remote).hostname in ("github.com", "www.github.com")


def check_git(workspace: Path, progress: Callable, cancelled: Callable) -> GitState:
    if not shutil.which("git"):
        raise RuntimeError("未安装 Git（不影响必做检查）")
    origin = command_result(["git", "remote", "get-url", "origin"], workspace)
    if not _github_remote(origin):
        raise RuntimeError("origin 不是 GitHub 地址；不执行检查或拉取")
    upstream = command_result(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], workspace)
    if not upstream.startswith("origin/"):
        raise RuntimeError("当前分支未跟踪 origin，请先配置 upstream")
    progress(30, "正在查询 GitHub（仅 fetch，不改工作文件）…")
    if cancelled():
        raise RuntimeError("已取消")
    command_result(["git", "fetch", "--quiet", "origin"], workspace, 45)
    output = command_result(["git", "rev-list", "--left-right", "--count",
                             "HEAD...@{upstream}"], workspace)
    ahead, behind = map(int, output.split())
    return GitState(ahead, behind, upstream)


class CommandRunner(QObject):
    output = Signal(str)
    done = Signal(bool)

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.busy = False
        self.commands: list[list[str]] = []
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def start(self, commands: list[list[str]], workspace: Path):
        if self.busy:
            return
        self.busy = True
        self.commands = list(commands)
        self.process.setWorkingDirectory(str(workspace))
        env = QProcessEnvironment()
        for name, value in workspace_environment(workspace).items():
            env.insert(name, value)
        self.process.setProcessEnvironment(env)
        self._next()

    def _next(self):
        if not self.commands:
            self.busy = False
            self.done.emit(True)
            return
        command = self.commands.pop(0)
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.output.emit("$ " + subprocess.list2cmdline(command) + "\n")
        self.process.start(command[0], command[1:])

    def _read(self):
        text = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        if text:
            self.output.emit(text)

    def _finished(self, code, status):
        if not self.busy:
            return
        self._read()
        tail = self.decoder.decode(b"", final=True)
        if tail:
            self.output.emit(tail)
        if code or status != QProcess.ExitStatus.NormalExit:
            self.busy = False
            self.commands.clear()
            self.output.emit(f"\n命令失败，退出码 {code}\n")
            self.done.emit(False)
        else:
            self._next()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.busy:
            self.busy = False
            self.commands.clear()
            self.output.emit(self.process.errorString() + "\n")
            self.done.emit(False)


def plain_label(text: str, parent: QWidget | None = None, strong=False) -> QLabel:
    label = (StrongBodyLabel if strong else BodyLabel)(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


# 应用 icon.ico 只用于应用/窗口；操作按钮使用 Fluent 功能图标。
BUTTON_ICONS = {
    "打开官网": FIF.LINK,
    "一键准备工作区": FIF.DOWNLOAD,
    "挂载 script_core": FIF.LINK,
    "重新检查": FIF.SYNC,
    "选择工作区": FIF.FOLDER,
    "检查版本": FIF.INFO,
    "一键拉取": FIF.DOWNLOAD,
    "重新扫描": FIF.SYNC,
    "打开 Task 文件夹": FIF.FOLDER,
    "任务提示指南": FIF.HELP,
    "添加桌面快捷方式": FIF.ADD,
    "复制群号": FIF.COPY,
    "打开 GitHub 仓库": FIF.GITHUB,
    "下载启动器（Release）": FIF.DOWNLOAD,
    "后台运行指南": FIF.HELP,
    "运行 · F9": FIF.PLAY,
    "重新运行 · F9": FIF.SYNC,
    "返回": FIF.LEFT_ARROW,
}


def button(text: str, parent: QWidget, callback: Callable) -> PushButton:
    control = PushButton(text, parent)
    icon = BUTTON_ICONS.get(text)
    if icon is not None:
        control.setIcon(icon)
    control.clicked.connect(callback)
    return control


class EnvironmentCheck(QWidget):
    """检查名称、状态徽标与详细信息分层，长路径可以自然换行。"""

    def __init__(self, title: str, parent: QWidget, url=""):
        super().__init__(parent)
        self.state = "waiting"
        self.url = url
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(plain_label(title, self, strong=True), 1)
        if url:
            link = TransparentPushButton(FIF.LINK, "官网", self)
            link.setObjectName("officialLink")
            link.setFixedHeight(24)
            link.setToolTip("打开官网；不会自动安装")
            link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            heading.addWidget(link)
        self.badge = CaptionLabel("待检查", self)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(56, 24)
        heading.addWidget(self.badge)
        layout.addLayout(heading)
        self.detail = plain_label("等待检查", self)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)
        qconfig.themeChangedFinished.connect(self._update_colors)
        self._update_colors()

    def set_state(self, state: str):
        self.state = state
        self.badge.setText({"waiting": "待检查", "checking": "检查中",
                            "passed": "已通过", "failed": "未通过"}[state])
        self._update_colors()

    def _update_colors(self):
        dark = isDarkTheme()
        colors = {
            "waiting": ("#b5b5b5", "#383838") if dark else ("#666666", "#f0f0f0"),
            "checking": ("#8fc9ff", "#203b50") if dark else ("#005a9e", "#e9f3fc"),
            "passed": ("#85d8a0", "#243e30") if dark else ("#166534", "#eaf6ee"),
            "failed": ("#ffaaa3", "#492e2b") if dark else ("#b42318", "#fceeed"),
        }
        foreground, background = colors[self.state]
        self.badge.setStyleSheet(
            f"color:{foreground};background:{background};border-radius:6px;")
        self.detail.setStyleSheet("color:#b5b5b5;" if dark else "color:#616161;")


def section_card(title: str, parent: QWidget) -> tuple[CardWidget, QVBoxLayout]:
    card = CardWidget(parent)
    card.setClickEnabled(False)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 14, 20, 14)
    layout.setSpacing(0)
    layout.addWidget(plain_label(title, card, strong=True))
    layout.addSpacing(4)
    return card, layout


class CheckPage(QWidget):
    passed = Signal()
    choose_workspace = Signal()

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace
        self.ready = False
        self.job: Job | None = None
        self.job_kind = ""
        self.git_state: GitState | None = None
        self.runner = CommandRunner(self)
        self.runner.output.connect(self._operation_output)
        self.runner.done.connect(self._operation_done)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(12)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(TitleLabel("启动", self))
        heading.addWidget(CaptionLabel(f"检查运行所需的环境，就绪后即可开始任务。 · 启动器 v{LAUNCHER_VERSION}", self))
        header.addLayout(heading, 1)
        self.retry_button = button("重新检查", self, self.run_checks)
        header.addWidget(self.retry_button)
        root.addLayout(header)

        # 内容可滚动，长错误信息或最小窗口不会挤掉底部的进入按钮。
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea {background:transparent;border:none;}")
        body = QWidget()
        body.setStyleSheet("QWidget#checkBody {background:transparent;}")
        body.setObjectName("checkBody")
        self.scroll.setWidget(body)
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 8, 4)
        content.setSpacing(12)
        root.addWidget(self.scroll, 1)

        workspace_card, workspace_layout = section_card("当前工作区", body)
        workspace_layout.setSpacing(8)
        workspace_row = QHBoxLayout()
        self.workspace_label = plain_label(str(workspace), workspace_card)
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        workspace_row.addWidget(self.workspace_label, 1)
        self.folder_button = button("选择工作区", workspace_card, self.choose_workspace.emit)
        workspace_row.addWidget(self.folder_button)
        workspace_layout.addLayout(workspace_row)
        content.addWidget(workspace_card)

        self.rows: dict[str, QLabel] = {}
        self.check_items: dict[str, EnvironmentCheck] = {}
        groups = QHBoxLayout()
        groups.setSpacing(12)
        runtime_card, runtime = section_card("运行环境", body)
        system_card, system = section_card("系统与权限", body)
        checks = (("python", "Python ≥ 3.10", PYTHON_URL, runtime),
                  ("uv", "uv 包管理器", UV_URL, runtime),
                  ("workspace", "工作区依赖与 script_core", "", runtime),
                  ("bash", "Git Bash", GIT_URL, system),
                  ("windows", "Windows Build ≥ 20348", "", system),
                  ("admin", "管理员权限", "", system))
        for key, title, url, layout in checks:
            check = EnvironmentCheck(title, layout.parentWidget(), url)
            check.setObjectName(f"check_{key}")
            self.check_items[key] = check
            self.rows[key] = check.detail
            layout.addWidget(check)
        runtime_actions = QHBoxLayout()
        runtime_actions.setSpacing(8)
        self.setup_button = button("一键准备工作区", runtime_card, self.setup)
        self.setup_button.setToolTip("uv sync 后显式 editable 挂载 script_core，仅操作工作区 .venv")
        self.mount_button = button("挂载 script_core", runtime_card, self.mount)
        runtime_actions.addWidget(self.setup_button)
        runtime_actions.addWidget(self.mount_button)
        runtime_actions.addStretch(1)
        runtime.addSpacing(6)
        runtime.addLayout(runtime_actions)
        system.addStretch(1)
        note = CaptionLabel("以上均为必做检查。\n安装依赖、挂载与更新只在你点击后执行。", system_card)
        note.setWordWrap(True)
        system.addWidget(note)
        groups.addWidget(runtime_card, 3)
        groups.addWidget(system_card, 2)
        content.addLayout(groups)

        zip_card, zip_layout = section_card("快捷导入 · 可选，不影响进入任务", body)
        zip_row = QHBoxLayout()
        zip_row.setSpacing(12)
        self.zip_check = EnvironmentCheck("7-Zip（工作区根目录）", zip_card, SEVEN_ZIP_URL)
        self.zip_check.setObjectName("check_seven_zip")
        self.check_items["seven_zip"] = self.zip_check
        self.rows["seven_zip"] = self.zip_check.detail
        zip_row.addWidget(self.zip_check, 1)
        self.install_zip_button = button("一键安装 7-Zip", zip_card, self.install_zip)
        zip_row.addWidget(self.install_zip_button)
        zip_layout.addLayout(zip_row)
        zip_note = CaptionLabel(
            "安装后可在任务页通过『选择任务包』导入 .zip / .7z / .rar 任务包；"
            "以管理员身份运行时拖放可能被 Windows 拦截。", zip_card)
        zip_note.setWordWrap(True)
        zip_layout.addWidget(zip_note)
        content.addWidget(zip_card)
        self.actions = [self.setup_button, self.mount_button, self.retry_button,
                        self.folder_button, self.install_zip_button]

        git_card, git_layout = section_card("GitHub 更新 · 可选，不影响进入任务", body)
        optional = QHBoxLayout()
        git_text = QVBoxLayout()
        self.git_label = plain_label("未检查；点击检查后才会连接 GitHub。", git_card)
        git_text.addWidget(self.git_label)
        self.git_label.setToolTip("检查失败不会影响进入任务；拉取前会再次确认。")
        optional.addLayout(git_text, 1)
        self.git_button = button("检查版本", git_card, self.check_version)
        self.pull_button = button("一键拉取", git_card, self.pull)
        self.pull_button.setEnabled(False)
        optional.addWidget(self.git_button)
        optional.addWidget(self.pull_button)
        git_layout.addLayout(optional)
        content.addWidget(git_card)

        shortcut_card, shortcut_layout = section_card("快捷方式", body)
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(CaptionLabel("将当前工作区启动器添加到桌面。", shortcut_card), 1)
        self.shortcut_button = button("添加桌面快捷方式", shortcut_card, self.create_shortcut)
        shortcut_row.addWidget(self.shortcut_button)
        shortcut_layout.addLayout(shortcut_row)
        content.addWidget(shortcut_card)

        group_card, group_layout = section_card("脚本交流群", body)
        group_row = QHBoxLayout()
        group_row.addWidget(CaptionLabel(f"加群获取具体脚本：{QQ_GROUP}", group_card), 1)
        self.group_button = button("复制群号", group_card, self.copy_group_number)
        group_row.addWidget(self.group_button)
        group_layout.addLayout(group_row)
        content.addWidget(group_card)

        guide_card, guide_layout = section_card("使用指南", body)
        guide_row = QHBoxLayout()
        guide_row.addWidget(CaptionLabel("仓库、启动器下载与后台运行说明。", guide_card), 1)
        self.repository_button = button("打开 GitHub 仓库", guide_card,
            lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        self.release_button = button("下载启动器（Release）", guide_card,
            lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        self.background_button = button("后台运行指南", guide_card,
            lambda: QDesktopServices.openUrl(QUrl(BACKGROUND_GUIDE_URL)))
        guide_row.addWidget(self.repository_button)
        guide_row.addWidget(self.release_button)
        guide_row.addWidget(self.background_button)
        guide_layout.addLayout(guide_row)
        content.addWidget(guide_card)

        self.output_toggle = TransparentPushButton(FIF.COMMAND_PROMPT, "操作输出", body)
        self.output_toggle.setCheckable(True)
        self.output_toggle.toggled.connect(self._toggle_output)
        content.addWidget(self.output_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.console = QPlainTextEdit(body)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        self.console.setMinimumHeight(160)
        self.console.setPlaceholderText("环境操作输出；只在点击安装或更新按钮后执行操作")
        self.console.hide()
        content.addWidget(self.console)
        content.addStretch(1)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        root.addWidget(self.progress)
        footer = QHBoxLayout()
        footer.setSpacing(16)
        self.status = plain_label("准备检查；不会自动安装、拉取或修改工作区。", self)
        footer.addWidget(self.status, 1)
        self.continue_button = PrimaryPushButton(FIF.CHEVRON_RIGHT, "进入任务", self)
        self.continue_button.clicked.connect(self.continue_to_tasks)
        self.continue_button.setEnabled(False)
        footer.addWidget(self.continue_button)
        root.addLayout(footer)

    def create_shortcut(self):
        try:
            shortcut = create_desktop_shortcut(self.workspace)
        except (OSError, RuntimeError, ImportError) as exc:
            self.status.setText(f"创建桌面快捷方式失败：{exc}")
            return
        self.status.setText(f"桌面快捷方式已创建：{shortcut}")

    def copy_group_number(self):
        QApplication.clipboard().setText(QQ_GROUP)
        self.status.setText(f"群号 {QQ_GROUP} 已复制到剪贴板。")

    def _toggle_output(self, expanded: bool):
        self.console.setVisible(expanded)
        self.output_toggle.setText("收起操作输出" if expanded else "操作输出")
        if expanded:
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.console))

    def continue_to_tasks(self):
        if self.ready and not self.busy:
            self.passed.emit()

    @property
    def busy(self):
        return self.job is not None or self.runner.busy

    def _controls(self):
        self.continue_button.setEnabled(self.ready and not self.busy)
        for control in self.actions + [self.git_button]:
            control.setEnabled(not self.busy)
        self.install_zip_button.setEnabled(not self.busy and not self._zip_ok())
        state = self.git_state
        self.pull_button.setEnabled(not self.busy and state is not None
                                    and state.behind > 0 and state.ahead == 0)

    def _zip_ok(self) -> bool:
        path = find_7zip(self.workspace)
        return self.check_items["seven_zip"].state == "passed" and supports_rar(path)

    def _launch(self, kind: str, function: Callable):
        if self.busy:
            return
        self.job_kind = kind
        self.job = Job(function, self)
        self.job.progress.connect(self._progress)
        self.job.result.connect(self._result)
        self.job.failed.connect(self._failed)
        self.job.finished.connect(self._job_finished)
        self._controls()
        self.job.start()

    @Slot(int, str)
    def _progress(self, value, text):
        self.progress.setValue(value)
        self.status.setText(text)

    @Slot(object)
    def _result(self, result):
        if self.job_kind == "checks":
            by_key = {item.key: item for item in result}
            self.ready = all(by_key.get(key) is not None and by_key[key].ok
                             for key in REQUIRED_CHECKS)
            for item in result:
                label = self.rows.get(item.key)
                check = self.check_items.get(item.key)
                if label is None or check is None:
                    continue
                label.setText(item.detail)
                check.set_state("passed" if item.ok else "failed")
            self.install_zip_button.setVisible(not self._zip_ok())
            seven_zip = find_7zip(self.workspace)
            self.install_zip_button.setText(
                "安装完整版 7-Zip" if seven_zip is not None and not supports_rar(seven_zip)
                else "一键安装 7-Zip")
            self.status.setText("必做检查全部通过，点击“进入任务”继续。" if self.ready else "请解决未通过项目后重新检查。")
            self.progress.setValue(100)
        elif self.job_kind == "seven_zip":
            self.zip_check.detail.setText(f"已安装完整版：{result}")
            self.zip_check.set_state("passed")
            self.install_zip_button.setVisible(False)
            self.status.setText("7-Zip 完整版已安装，现在可以在任务页导入 .zip / .7z / .rar 任务包。")
            self.progress.setValue(100)
        else:
            self.git_state = result
            self.git_label.setText(f"本地领先 {result.ahead}，落后 {result.behind} 个提交 · {result.upstream}"
                                   + ("（已分叉，不能自动拉取）" if result.ahead and result.behind else ""))
            self.status.setText("GitHub 版本检查完成。")

    @Slot(str)
    def _failed(self, message):
        if self.job_kind == "git":
            self.git_state = None
            self.git_label.setText("选做检查失败：" + message)
        elif self.job_kind == "seven_zip":
            self.zip_check.set_state("failed")
            self.zip_check.detail.setText(message)
            self.install_zip_button.setVisible(True)
        else:
            self.ready = False
            for check in self.check_items.values():
                if check.state == "checking":
                    check.set_state("failed")
                    check.detail.setText("检查未完成，请查看操作输出并重试。")
        self.status.setText(message)
        self.console.appendPlainText(message)
        self.output_toggle.setChecked(True)

    @Slot()
    def _job_finished(self):
        if self.job:
            self.job.deleteLater()
        self.job = None
        self._controls()

    def run_checks(self):
        if self.busy:
            return
        self.ready = False
        self.progress.setValue(0)
        self.status.setText("正在检查本机和工作区；不会更改环境…")
        for check in self.check_items.values():
            check.detail.setText("正在检查，请稍候…")
            check.set_state("checking")
        workspace = self.workspace
        self._launch("checks", lambda progress, cancelled:
                     inspect_environment(workspace, progress, cancelled))

    def check_version(self):
        workspace = self.workspace
        self._launch("git", lambda progress, cancelled: check_git(workspace, progress, cancelled))

    def install_zip(self):
        if self.busy:
            return
        self.zip_check.set_state("checking")
        self.zip_check.detail.setText("正在下载 7-Zip 完整版（含 RAR 支持）…")
        workspace = self.workspace
        self._launch("seven_zip", lambda progress, cancelled:
                     install_7zip(workspace, progress, cancelled))

    def _start_commands(self, commands):
        self.ready = False
        self.console.clear()
        self.output_toggle.setChecked(True)
        self.progress.setRange(0, 0)
        self.status.setText("正在操作工作区，请稍候…")
        self.runner.start(commands, self.workspace)
        self._controls()

    def setup(self):
        uv = find_uv()
        if self.busy:
            return
        if not uv or not (self.workspace / "pyproject.toml").is_file():
            self.status.setText("请先安装 uv 并选择含 pyproject.toml 的工作区。")
            return
        sync = [uv, "sync", "--project", str(self.workspace)]
        if (self.workspace / "uv.lock").is_file():
            sync.append("--locked")
        self._start_commands([sync, self._mount_command(uv)])

    def _mount_command(self, uv):
        return [uv, "pip", "install", "--python", str(venv_python(self.workspace)),
                "--no-deps", "--editable", str(self.workspace)]

    def mount(self):
        uv = find_uv()
        if self.busy:
            return
        if not uv or not venv_python(self.workspace).is_file():
            self.status.setText("请先点击“一键准备工作区”创建 .venv。")
            return
        self._start_commands([self._mount_command(uv)])

    def pull(self):
        state = self.git_state
        if self.busy or not state or state.ahead or not state.behind:
            return
        try:
            dirty = command_result(["git", "status", "--porcelain",
                                    "--untracked-files=no"], self.workspace, 5)
            if dirty:
                raise RuntimeError("存在未提交的已跟踪文件修改；为保护本地改动，不执行拉取")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.status.setText(str(exc))
            return
        answer = QMessageBox.question(self, "拉取更新",
            "仅 fast-forward 拉取，不覆盖本地改动、不提交也不推送。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.git_state = None
            self._start_commands([["git", "pull", "--ff-only", "--no-rebase", "--no-autostash"]])

    @Slot(str)
    def _operation_output(self, text):
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.console.setTextCursor(cursor)

    @Slot(bool)
    def _operation_done(self, ok):
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if ok else 0)
        self._controls()
        self.status.setText("操作完成，正在重新检查…" if ok else "操作失败，请查看输出；不会视为检查通过。")
        if ok:
            QTimer.singleShot(0, self.run_checks)


def task_location(task: Task, workspace: Path) -> str:
    """显示相对 Task 的完整分类目录，不限定分类深度。"""
    try:
        parts = task.path.parent.relative_to((workspace / "Task").resolve()).parts
    except ValueError:
        return "工作区外的任务"
    return "  ›  ".join(parts) if parts else "Task 根目录"


def task_category(task: Task, workspace: Path) -> str:
    """展示上级分类；任务自身目录名不作为重复标题显示。"""
    try:
        parts = task.path.parent.relative_to((workspace / "Task").resolve()).parts
    except ValueError:
        return ""
    return "  ›  ".join(parts[:-1])


class TaskRowFrame(QWidget):
    """紧凑任务卡片；仅内部开始按钮进入详情，不展开、不直接运行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        qconfig.themeChangedFinished.connect(self.update)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#2b2b2b" if isDarkTheme() else "#ffffff"))
        painter.setPen(QColor("#62666d" if isDarkTheme() else "#c5cbd3"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


class TaskInlineLabel(CaptionLabel):
    """任务单行文字：空间不足省略，完整内容可悬停查看。"""

    def __init__(self, text: str, parent: QWidget, *, title=False):
        # Fluent 的 text 构造重载会回调 self.__init__，子类使用 parent 重载。
        super().__init__(parent)
        self.setText(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setToolTip(text)
        if title:
            font = self.font()
            font.setPointSize(10)
            font.setBold(True)
            self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self):
        # 可以省略长描述，但固定宽度标签仍需占据对应的布局空间。
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class DropCard(CardWidget):
    """任务页的拖放卡片：明确接受文件拖入并反馈拖动状态。"""

    file_dropped = Signal(object)
    drag_state_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setClickEnabled(False)
        self.setAcceptDrops(True)

    @staticmethod
    def _paths(mime) -> list[Path]:
        if not mime.hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event):
        if self._paths(event.mimeData()):
            event.acceptProposedAction()
            self.drag_state_changed.emit(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drag_state_changed.emit(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        paths = self._paths(event.mimeData())
        self.drag_state_changed.emit(False)
        if paths:
            event.acceptProposedAction()
            self.file_dropped.emit(paths[0])
        else:
            event.ignore()


class TaskPage(QWidget):
    selected = Signal(object)
    TASK_GUIDE = "将包含this.task的文件夹移入根目录的Task文件夹中即可，嵌套深度无关，可做分类。"

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace
        self.job: Job | None = None
        self.package_job: Job | None = None
        self.package: TaskPackage | None = None
        self.import_message = ""
        self.scan_error = ""
        self.tasks: list[Task] = []
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 24)
        root.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(TitleLabel("任务", self))
        heading.addWidget(plain_label("日常、周常，或更细的分类，都可以按自己的习惯整理。", self))
        header.addLayout(heading, 1)
        self.refresh_button = button("重新扫描", self, self.refresh)
        self.task_guide_button = button("任务提示指南", self, self.show_task_guide)
        self.task_folder_button = button("打开 Task 文件夹", self, self.open_task_folder)
        header.addWidget(self.task_guide_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.task_folder_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        toolbar = QHBoxLayout()
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索任务名称、描述或分类路径")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(260)
        self.search.setMaximumWidth(420)
        self.search.textChanged.connect(self._filter)
        toolbar.addWidget(self.search, 1)
        self.sort_box = ComboBox(self)
        self.sort_box.addItems(["默认排序", ".task 修改日期（新→旧）"])
        self.sort_box.setCurrentIndex(0)
        self.sort_box.setToolTip("选择任务列表排序方式")
        self.sort_box.currentIndexChanged.connect(self._sort_tasks)
        toolbar.addWidget(self.sort_box)
        toolbar.addStretch(1)
        self.status = CaptionLabel("递归扫描 Task 下的全部分类目录", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        toolbar.addWidget(self.status)
        root.addLayout(toolbar)
        self.import_card = DropCard(self)
        self.import_card.file_dropped.connect(self.choose_package)
        self.import_card.drag_state_changed.connect(self._drag_state)
        import_layout = QHBoxLayout(self.import_card)
        import_layout.setContentsMargins(16, 10, 16, 10)
        import_layout.setSpacing(12)
        import_text = QVBoxLayout()
        import_text.setSpacing(2)
        self.import_title = plain_label("快捷导入 · 拖入任务包", self.import_card, strong=True)
        import_text.addWidget(self.import_title)
        self.import_status = CaptionLabel("", self.import_card)
        self.import_status.setWordWrap(True)
        import_text.addWidget(self.import_status)
        import_layout.addLayout(import_text, 1)
        self.import_choose = PrimaryPushButton(FIF.FOLDER, "选择任务包", self.import_card)
        self.import_choose.clicked.connect(lambda: self.choose_package())
        self.import_confirm = PrimaryPushButton(FIF.DOWNLOAD, "导入到未分类", self.import_card)
        self.import_confirm.clicked.connect(lambda: self.import_package())
        self.import_confirm.hide()
        import_layout.addWidget(self.import_choose)
        import_layout.addWidget(self.import_confirm)
        root.addWidget(self.import_card)
        self._update_import_state()
        self.progress = QProgressBar(self)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.animation = QPropertyAnimation(self.progress, b"value", self)
        self.animation.setDuration(180)
        root.addWidget(self.progress)

        self.content = QStackedWidget(self)
        self.list = ListWidget(self.content)
        self.list.setSpacing(3)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setAcceptDrops(False)
        self.list.viewport().setAcceptDrops(False)
        self.content.addWidget(self.list)

        empty_host = QWidget(self.content)
        centered = QVBoxLayout(empty_host)
        centered.setContentsMargins(24, 24, 24, 24)
        centered.addStretch(1)
        self.empty_card = CardWidget(empty_host)
        self.empty_card.setMaximumWidth(580)
        card = QVBoxLayout(self.empty_card)
        card.setContentsMargins(36, 32, 36, 32)
        card.setSpacing(14)
        self.empty_icon = IconWidget(FIF.FOLDER, self.empty_card)
        self.empty_icon.setFixedSize(44, 44)
        card.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = SubtitleLabel("还没有任务", self.empty_card)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(self.empty_title)
        self.empty_body = plain_label("把任务放进 Task 的任意层级目录，扫描时会自动发现。", self.empty_card)
        self.empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(self.empty_body)
        self.examples = CaptionLabel(
            "Task / 日常 / 采集 / this.task\nTask / 周常 / 副本 / 困难 / this.task", self.empty_card)
        self.examples.setTextFormat(Qt.TextFormat.PlainText)
        self.examples.setWordWrap(True)
        self.examples.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.examples.setMargin(8)
        card.addWidget(self.examples)
        self.open_folder_button = button("打开任务目录", self.empty_card, self.open_task_folder)
        self.open_folder_button.setIcon(FIF.FOLDER)
        card.addWidget(self.open_folder_button, 0, Qt.AlignmentFlag.AlignHCenter)
        centered.addWidget(self.empty_card, 0, Qt.AlignmentFlag.AlignHCenter)
        centered.addStretch(1)
        self.empty_host = empty_host
        self.content.addWidget(empty_host)
        self.content.setCurrentWidget(empty_host)
        root.addWidget(self.content, 1)
        self.scope = CaptionLabel("扫描范围：工作区根目录的 Task 文件夹 · 包含所有子目录 · 仅识别 this.task", self)
        self.scope.setWordWrap(True)
        root.addWidget(self.scope)
        self._update_folder_hint()

    def _update_folder_hint(self):
        folder = self.workspace / "Task"
        self.scope.setToolTip(str(folder))
        self.open_folder_button.setText("打开任务目录" if folder.is_dir() else "打开工作区")
        self.open_folder_button.setToolTip(str(folder if folder.is_dir() else self.workspace))

    def _update_import_state(self):
        admin = is_admin()
        self.import_card.setAcceptDrops(not admin)
        self.setAcceptDrops(not admin)
        self.import_title.setText("快捷导入 · 拖入或选择任务包")
        available = find_7zip(self.workspace)
        self.import_choose.setEnabled(available is not None and self.package_job is None)
        if available is None:
            text = "未安装 7-Zip；请到启动页一键安装，再点击『选择任务包』导入。"
            if admin:
                text = "以管理员身份运行时拖放可能被 Windows 拦截。" + text
            self.import_status.setText(text)
            self.import_confirm.hide()
            return
        if self.import_message:
            self.import_status.setText(self.import_message)
            return
        if supports_rar(available):
            text = "支持 .zip / .7z / .rar 任务包，检查通过后可导入到 Task / 未分类。"
        else:
            text = f"已安装 {available.name}：支持 .zip / .7z；RAR 需在启动页安装完整版。"
        if admin:
            text = "以管理员身份运行时拖放可能被 Windows 拦截；也可点击『选择任务包』导入。" + text
        self.import_status.setText(text)

    def _drag_state(self, active: bool):
        if is_admin():
            return
        if active:
            self.import_status.setText("松开鼠标即可检查任务包…")
        else:
            self._update_import_state()

    @staticmethod
    def _first_local_path(mime):
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                return Path(url.toLocalFile())
        return None

    def dragEnterEvent(self, event):
        if self._first_local_path(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = self._first_local_path(event.mimeData())
        if path is not None:
            event.acceptProposedAction()
            self.choose_package(path)
        else:
            event.ignore()

    def choose_package(self, path=None):
        if self.package_job is not None:
            return
        if path is None or isinstance(path, bool):
            selected, _ = QFileDialog.getOpenFileName(
                self, "选择任务包", str(self.workspace / "Task"),
                "任务包 (*.zip *.7z *.rar)")
            if not selected:
                return
            path = Path(selected)
        path = Path(path)
        if path.suffix.lower() not in ARCHIVE_SUFFIXES:
            self.import_message = "只支持 .zip、.7z 或 .rar 任务包。"
            self._update_import_state()
            return
        seven_zip = find_7zip(self.workspace)
        if seven_zip is None:
            self.import_message = "未安装 7-Zip：快捷导入不可用。请到启动页一键安装后再试。"
            self._update_import_state()
            return
        if path.suffix.lower() == ".rar" and not supports_rar(seven_zip):
            self.import_message = (
                "当前 7-Zip 版本不支持 RAR（需要完整版 7z.exe）；请在启动页安装完整版。")
            self._update_import_state()
            return
        self._clear_package()
        self.import_message = f"正在检查 {path.name}…"
        self.import_status.setText(self.import_message)
        self.import_confirm.hide()
        self.import_choose.setEnabled(False)
        self.package_job = Job(
            lambda progress, cancelled: inspect_task_package(
                path, seven_zip, progress, cancelled), self)
        self.package_job.progress.connect(self._package_progress)
        self.package_job.result.connect(self._package_loaded)
        self.package_job.failed.connect(self._package_failed)
        self.package_job.finished.connect(self._package_finished)
        self.package_job.start()

    def _package_progress(self, _value, text):
        self.import_status.setText(text)

    def _package_loaded(self, package):
        self.package = package
        if package.error:
            self.import_message = "任务包检查失败：" + package.error
            self.import_confirm.hide()
        else:
            self.import_message = (
                f"检查通过：{package.title} · v{package.version} · "
                f"{package.files} 个文件 · 将导入为 {package.name}")
            self.import_confirm.setVisible(True)
            self.import_confirm.setEnabled(True)
        self.import_status.setText(self.import_message)

    def _package_failed(self, text):
        self.import_message = "任务包处理失败：" + text
        self.import_status.setText(self.import_message)
        self.import_confirm.hide()

    def _package_finished(self):
        if self.package_job is not None:
            self.package_job.deleteLater()
        self.package_job = None
        self.import_choose.setEnabled(find_7zip(self.workspace) is not None)

    def import_package(self):
        package = self.package
        if package is None or package.error or self.package_job is not None:
            return
        self.import_confirm.setEnabled(False)
        self.import_message = "正在导入到 Task / 未分类…"
        self.import_status.setText(self.import_message)
        workspace = self.workspace
        self.package_job = Job(
            lambda progress, cancelled: import_task_package(
                package, workspace, progress, cancelled), self)
        self.package_job.progress.connect(self._package_progress)
        self.package_job.result.connect(self._imported)
        self.package_job.failed.connect(self._package_failed)
        self.package_job.finished.connect(self._package_finished)
        self.package_job.start()

    def _imported(self, target):
        target = Path(target)
        self.import_message = f"已导入到 Task / 未分类：{target.name}"
        self.import_status.setText(self.import_message)
        self.import_confirm.hide()
        self._clear_package()
        self.refresh()

    def _clear_package(self):
        package = self.package
        self.package = None
        if package is not None and package.temp is not None:
            shutil.rmtree(package.temp, ignore_errors=True)

    def closeEvent(self, event):
        if self.package_job is not None:
            self.package_job.requestInterruption()
        self._clear_package()
        super().closeEvent(event)

    def show_task_guide(self):
        QMessageBox.information(self, "任务提示指南", self.TASK_GUIDE)

    def open_task_folder(self):
        folder = self.workspace / "Task"
        target = folder if folder.is_dir() else self.workspace
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.status.setText("无法打开目录，请检查工作区是否存在")

    def _empty(self, title, body, *, examples=False, folder_action=False):
        self.empty_title.setText(title)
        self.empty_body.setText(body)
        self.examples.setVisible(examples)
        self.open_folder_button.setVisible(folder_action)
        self.content.setCurrentWidget(self.empty_host)

    def refresh(self):
        if self.job:
            return
        self.scan_error = ""
        self.list.clear()
        self.list.setEnabled(False)
        self.search.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.animation.stop()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText("正在递归扫描…")
        self._update_folder_hint()
        self._update_import_state()
        self._empty("正在整理任务", "正在读取 Task 下所有分类目录中的 this.task…")
        workspace = self.workspace
        self.job = Job(lambda progress, cancelled: scan_tasks(workspace, progress, cancelled), self)
        self.job.progress.connect(self._progress)
        self.job.result.connect(self.populate)
        self.job.failed.connect(self._failed)
        self.job.finished.connect(self._finished)
        self.job.start()

    @Slot(int, str)
    def _progress(self, value, text):
        self.status.setText(text)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self.animation.stop()
        self.animation.setStartValue(max(0, self.progress.value()))
        self.animation.setEndValue(value)
        self.animation.start()

    @Slot(str)
    def _failed(self, text):
        self.scan_error = text
        self.status.setText("扫描失败")
        self._empty("暂时无法读取任务", text, folder_action=True)

    @Slot(object)
    def populate(self, tasks):
        self.tasks = list(tasks)
        self.scan_error = ""
        self._render_tasks()

    def _sort_tasks(self, _index=None):
        if self.job is None:
            self._render_tasks()

    def _ordered_tasks(self):
        if self.sort_box.currentIndex() == 1:
            return sorted(self.tasks, key=lambda task: (-task.modified_at,
                                                        str(task.path).casefold()))
        return list(self.tasks)

    def _render_tasks(self):
        self.list.clear()
        self._update_folder_hint()
        for task in self._ordered_tasks():
            location = task_location(task, self.workspace)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, task)
            item.setSizeHint(QSize(0, 50))
            item.setToolTip(str(task.path))
            self.list.addItem(item)
            row = TaskRowFrame(self.list)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(10)
            title = TaskInlineLabel(task.title, row, title=True)
            title.setObjectName("taskTitle")
            title.setFixedWidth(min(title.fontMetrics().horizontalAdvance(task.title) + 4, 230))
            layout.addWidget(title)
            text = task.error or task.description or "无文字描述"
            desc = TaskInlineLabel(text, row)
            desc.setObjectName("taskDescription")
            if task.error:
                desc.setStyleSheet("color:#c42b1c")
            layout.addWidget(desc, 1)
            category_text = task_category(task, self.workspace)
            category = TaskInlineLabel(category_text, row)
            category.setObjectName("taskCategory")
            category.setFixedWidth(min(category.fontMetrics().horizontalAdvance(category_text) + 4, 160))
            category.setToolTip(location)
            category.setVisible(bool(category_text))
            layout.addWidget(category)
            version = TaskInlineLabel(task.version, row)
            version.setObjectName("taskVersion")
            version.setFixedWidth(min(version.fontMetrics().horizontalAdvance(task.version) + 4, 80))
            layout.addWidget(version)
            start = PrimaryPushButton("开始", row)
            start.setObjectName("taskStart")
            start.setIcon(FIF.PLAY)
            start.setFixedSize(78, 32)
            start.setToolTip("进入任务详情；不会直接运行")
            start.setAccessibleName(f"查看任务详情：{task.title}")
            start.clicked.connect(lambda _checked=False, target=item: self._select(target))
            layout.addWidget(start)
            self.list.setItemWidget(item, row)
        self._filter()
        self._progress(100, self.status.text())

    def _filter(self, _text=None):
        if self.scan_error:
            return
        query = self.search.text().strip().casefold()
        visible = errors = 0
        for index in range(self.list.count()):
            item = self.list.item(index)
            task = item.data(Qt.ItemDataRole.UserRole)
            haystack = " ".join((task.title, task.description, task.version,
                                  task_location(task, self.workspace))).casefold()
            matches = all(word in haystack for word in query.split())
            item.setHidden(not matches)
            visible += int(matches)
            errors += int(bool(task.error))
        total = self.list.count()
        if not total:
            self.status.setText("0 个任务")
            self._empty("还没有任务", "把任务放进 Task 的任意层级目录，扫描时会自动发现。",
                        examples=True, folder_action=True)
        elif not visible:
            self.status.setText(f"0 / {total} 个任务")
            self._empty("没有匹配的任务", "换个关键词，或清空搜索以查看全部分类。")
        else:
            summary = f"{visible} / {total} 个任务" if query else f"{total} 个任务"
            self.status.setText(summary + (f" · {errors} 个配置异常" if errors else ""))
            self.content.setCurrentWidget(self.list)

    @Slot()
    def _finished(self):
        if self.job:
            self.job.deleteLater()
        self.job = None
        self.list.setEnabled(True)
        self.search.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self._update_import_state()
        QTimer.singleShot(350, self._hide_progress)

    def _hide_progress(self):
        if self.job is None:
            self.progress.hide()

    def _select(self, item):
        if self.job is None:
            self.selected.emit(item.data(Qt.ItemDataRole.UserRole))


class ImagePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = QPixmap()
        self.message = "未配置描述图片"
        self.dialog: ImageDialog | None = None
        self.zoom_enabled = True
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background:transparent;")
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_pixmap(self, pixmap: QPixmap):
        self.pixmap = QPixmap(pixmap)
        self.message = ""
        self.update()

    def set_image(self, path: Path | None):
        self._close_dialog()
        self.pixmap = QPixmap(str(path)) if path else QPixmap()
        self.message = "描述图片缺失或无法读取" if path else "未配置描述图片"
        self.setToolTip("点击放大查看" if not self.pixmap.isNull() else "")
        self.setCursor(Qt.CursorShape.PointingHandCursor if not self.pixmap.isNull()
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def _close_dialog(self):
        dialog = self.dialog
        self.dialog = None
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass

    def _dialog_destroyed(self, *_args):
        self.dialog = None

    def open_preview(self):
        """Open a large non-modal view of the current image."""
        if self.pixmap.isNull():
            return None
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.activateWindow()
            return self.dialog
        self._close_dialog()
        self.dialog = ImageDialog(self.pixmap, self.window())
        self.dialog.destroyed.connect(self._dialog_destroyed)
        self.dialog.show()
        self.dialog.raise_()
        return self.dialog

    def mousePressEvent(self, event):
        if (self.zoom_enabled and event.button() == Qt.MouseButton.LeftButton
                and not self.pixmap.isNull()):
            self.open_preview()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 只绘制图片本身，不保留固定 16:9 的背景或描边。
        # 非 16:9 和透明 PNG 的留白直接露出页面背景。
        if self.pixmap.isNull():
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
        else:
            fitted = self.pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - fitted.width()) // 2,
                               (self.height() - fitted.height()) // 2, fitted)


class ImageDialog(QDialog):
    """放大查看图片的非模态窗口；小图按屏幕尺寸放大，大图自适应缩小。"""

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("查看图片")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet("background-color:#111111;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = ImagePreview(self)
        self.view.zoom_enabled = False
        self.view.setCursor(Qt.CursorShape.ArrowCursor)
        self.view.set_pixmap(pixmap)
        layout.addWidget(self.view)
        screen = parent.screen() if parent is not None else QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            max_width = max(360, int(available.width() * 0.9))
            max_height = max(280, int(available.height() * 0.9))
        else:
            max_width, max_height = 1200, 800
        scale = min(max_width / max(1, pixmap.width()),
                    max_height / max(1, pixmap.height()))
        self.resize(max(360, int(pixmap.width() * scale)),
                    max(280, int(pixmap.height() * scale)))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


class CollapsibleCard(CardWidget):
    """详情页的折叠卡片，默认收起，展开后显示具体内容。"""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setClickEnabled(False)
        self.expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title_label = StrongBodyLabel(title, self)
        header.addWidget(self.title_label, 1)
        self.toggle_button = TransparentPushButton(FIF.DOWN, "展开", self)
        self.toggle_button.setFixedSize(96, 30)
        self.toggle_button.clicked.connect(lambda: self.set_expanded(not self.expanded))
        header.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        self.body.hide()
        layout.addWidget(self.body)

    def add_widget(self, widget: QWidget):
        self.body_layout.addWidget(widget)

    def add_layout(self, layout):
        self.body_layout.addLayout(layout)

    def set_expanded(self, expanded: bool):
        self.expanded = bool(expanded)
        self.body.setVisible(self.expanded)
        self.toggle_button.setText("收起" if self.expanded else "展开")
        self.toggle_button.setIcon(FIF.UP if self.expanded else FIF.DOWN)


class _FluentSpinEditMixin:
    """qfluentwidgets 1.11.3 的 SpinBox 会把内部 lineEdit 压成 1px。

    自定义上下箭头被放进 QSpinBox 自身的布局后，QAbstractSpinBox 计算出的
    文本区域在部分尺寸下只有 1 像素宽，数值因此不可见。这里在尺寸/显示/
    样式变化后按实际宽度重新摆放 lineEdit。
    """

    def _fix_edit_geometry(self):
        line = self.lineEdit()
        if line is None or not hasattr(self, "upButton"):
            return
        buttons = self.upButton.width() + self.downButton.width() + 12
        width = max(40, self.width() - buttons - 10)
        line.setGeometry(10, 1, width, max(1, self.height() - 2))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fix_edit_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        self._fix_edit_geometry()

    def changeEvent(self, event):
        super().changeEvent(event)
        self._fix_edit_geometry()


class FluentSpinBox(_FluentSpinEditMixin, SpinBox):
    pass


class FluentDoubleSpinBox(_FluentSpinEditMixin, DoubleSpinBox):
    pass


class VariableEditor(QWidget):
    """一个 this.task 变量对应的输入控件。"""

    def __init__(self, variable: TaskVariable, parent: QWidget | None = None):
        super().__init__(parent)
        self.variable = variable
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(12)
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(StrongBodyLabel(variable.name, self))
        self.description_label = CaptionLabel(variable.description or "未提供描述", self)
        self.description_label.setWordWrap(True)
        info.addWidget(self.description_label)
        row.addLayout(info, 1)
        self.editor = self._create_editor(variable)
        row.addWidget(self.editor)

    def _create_editor(self, variable: TaskVariable):
        value = variable.value
        if variable.type == "int":
            editor = FluentSpinBox(self)
            editor.setRange(-2_147_483_648, 2_147_483_647)
            editor.setValue(int(value))
        elif variable.type == "float":
            editor = FluentDoubleSpinBox(self)
            editor.setDecimals(6)
            editor.setRange(-1_000_000_000_000, 1_000_000_000_000)
            editor.setSingleStep(0.1)
            editor.setValue(float(value))
        elif variable.type == "string":
            editor = LineEdit(self)
            editor.setText(str(value))
        else:
            editor = SwitchButton(self)
            editor.setOnText("开")
            editor.setOffText("关")
            editor.setChecked(bool(value))
            # SwitchButton 自带 160px 最小宽度会让开关在控件内左对齐，
            # 收窄到实际内容宽度后才能真正贴住行尾。
            editor.setFixedWidth(max(80, editor.sizeHint().width()))
        if variable.type != "bool":
            editor.setMinimumWidth(260 if variable.type == "string" else 160)
        return editor

    def value(self):
        if self.variable.type == "int":
            return int(self.editor.value())
        if self.variable.type == "float":
            return float(self.editor.value())
        if self.variable.type == "string":
            return self.editor.text()
        return bool(self.editor.isChecked())

    def update(self, variable: TaskVariable):
        """Refresh this editor when the same variable schema is reused."""
        self.variable = variable
        self.description_label.setText(variable.description or "未提供描述")
        if isinstance(self.editor, SpinBox):
            self.editor.setValue(int(variable.value))
        elif isinstance(self.editor, DoubleSpinBox):
            self.editor.setValue(float(variable.value))
        elif isinstance(self.editor, LineEdit):
            self.editor.setText(str(variable.value))
        else:
            self.editor.setChecked(bool(variable.value))


class DetailPage(QWidget):
    run_requested = Signal()

    def __init__(self):
        super().__init__()
        self.task: Task | None = None
        self.variable_editors: dict[str, VariableEditor] = {}
        self.variable_signature: tuple[tuple[str, str], ...] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 24)
        root.setSpacing(12)
        root.addWidget(TitleLabel("详情", self))
        header = QHBoxLayout()
        self.title = plain_label("未选择任务", self, strong=True)
        header.addWidget(self.title, 1)
        self.start_button = PrimaryPushButton(FIF.PLAY, "运行 · F9", self)
        self.start_button.clicked.connect(self.run_requested.emit)
        self.start_button.setEnabled(False)
        header.addWidget(self.start_button)
        root.addLayout(header)
        self.suppress_log = CheckBox("运行时不输出用于开发调试的 log", self)
        self.suppress_log.setToolTip("勾选后不显示开发调试日志，运行页只保留 info")
        root.addWidget(self.suppress_log)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea {background:transparent;border:none;}")
        body = QWidget()
        body.setObjectName("detailBody")
        body.setStyleSheet("QWidget#detailBody {background:transparent;}")
        self.scroll.setWidget(body)
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 8, 4)
        content.setSpacing(12)

        self.fields: dict[str, QLabel] = {}
        self.description_card = CollapsibleCard("任务说明", body)
        self.fields["description"] = plain_label("—", self.description_card)
        self.fields["description"].setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.description_card.add_widget(self.fields["description"])
        self.preview = ImagePreview(self.description_card)
        self.preview.setMinimumHeight(360)
        self.description_card.add_widget(self.preview)
        content.addWidget(self.description_card)

        self.config_card = CollapsibleCard("任务配置", body)
        self.config_host = QWidget(self.config_card)
        self.config_layout = QVBoxLayout(self.config_host)
        self.config_layout.setContentsMargins(0, 0, 0, 0)
        self.config_layout.setSpacing(8)
        self.config_empty = plain_label("该任务没有可配置变量。", self.config_host)
        self.config_layout.addWidget(self.config_empty)
        self.config_card.add_widget(self.config_host)
        self.config_footer = QWidget(self.config_card)
        footer = QVBoxLayout(self.config_footer)
        footer.setContentsMargins(0, 6, 0, 0)
        footer.setSpacing(6)
        self.config_path = CaptionLabel("this.task：—", self.config_footer)
        self.config_path.setTextFormat(Qt.TextFormat.PlainText)
        self.config_path.setWordWrap(True)
        self.config_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        footer.addWidget(self.config_path)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.config_status = CaptionLabel("", self.config_footer)
        self.config_status.setTextFormat(Qt.TextFormat.PlainText)
        actions.addWidget(self.config_status, 1)
        self.reset_button = PushButton(FIF.SYNC, "重置", self.config_footer)
        self.reset_button.setToolTip("丢弃未保存的修改，重新读取 this.task")
        self.reset_button.clicked.connect(lambda: self.reset_variables())
        self.reset_button.setEnabled(False)
        actions.addWidget(self.reset_button)
        self.save_button = PrimaryPushButton(FIF.SAVE, "保存", self.config_footer)
        self.save_button.setToolTip("将当前配置写入 this.task；运行时也会自动保存")
        self.save_button.clicked.connect(lambda: self.save_variables())
        self.save_button.setEnabled(False)
        actions.addWidget(self.save_button)
        footer.addLayout(actions)
        self.config_card.add_widget(self.config_footer)
        content.addWidget(self.config_card)

        self.version_card = CollapsibleCard("任务版本", body)
        for key, label in (("version", "版本"), ("editor", "作者"), ("built", "构筑时间")):
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(plain_label(label, self.version_card, strong=True))
            self.fields[key] = plain_label("—", self.version_card)
            row.addWidget(self.fields[key], 1)
            self.version_card.add_layout(row)
        content.addWidget(self.version_card)
        content.addStretch(1)
        root.addWidget(self.scroll, 1)
        self.error = plain_label("", self)
        self.error.setStyleSheet("color:#c42b1c")
        root.addWidget(self.error)

    def set_task(self, task: Task):
        self.task = task
        self.title.setText(task.title)
        self.fields["description"].setText(task.description or "无文字描述")
        self.fields["editor"].setText(task.editor)
        self.fields["version"].setText(task.version)
        self.fields["built"].setText(format_build_time(task.modified_at))
        self.preview.set_image(task.image)
        self.error.setText(task.error)
        self._build_variable_editors(task)
        self._update_config_footer()
        self._set_config_status("")
        self.description_card.set_expanded(True)
        self.config_card.set_expanded(False)
        self.version_card.set_expanded(False)
        self.start_button.setEnabled(not task.error and task.run_target is not None
                                     and task.run_target.is_file())

    def _build_variable_editors(self, task: Task):
        signature = tuple((variable.name, variable.type) for variable in task.variables)
        if signature != self.variable_signature:
            while self.config_layout.count():
                item = self.config_layout.takeAt(0)
                widget = item.widget()
                if widget is not None and widget is not self.config_empty:
                    widget.deleteLater()
            self.variable_editors.clear()
            self.variable_signature = signature
            self.config_layout.addWidget(self.config_empty)
            for variable in task.variables:
                editor = VariableEditor(variable, self.config_host)
                editor.setObjectName(f"var_{variable.name}")
                self.variable_editors[variable.name] = editor
                self.config_layout.addWidget(editor)
        else:
            for variable in task.variables:
                self.variable_editors[variable.name].update(variable)
        self.config_empty.setVisible(not task.variables)

    def _set_config_status(self, text: str, ok: bool = True):
        self.config_status.setText(text)
        self.config_status.setStyleSheet("color:#16803c;" if ok else "color:#c42b1c;")

    def _update_config_footer(self):
        task = self.task
        if task is None:
            self.config_path.setText("this.task：—")
            self.config_path.setToolTip("")
            enabled = False
        else:
            self.config_path.setText(f"this.task：{task.path}")
            self.config_path.setToolTip(str(task.path))
            enabled = not task.error and bool(task.variables)
        self.save_button.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)

    def save_variables(self) -> bool:
        """Write the current editor values into this.task without running it."""
        task = self.task
        if task is None:
            self._set_config_status("未选择任务", ok=False)
            return False
        if task.error:
            self._set_config_status("任务配置无效，无法保存", ok=False)
            return False
        if not task.variables:
            self._set_config_status("该任务没有可配置变量", ok=False)
            return False
        values = {name: editor.value() for name, editor in self.variable_editors.items()}
        try:
            save_task_variables(task.path, task.variables, values)
        except (OSError, ValueError, TypeError) as exc:
            self.error.setText(f"无法保存任务配置：{exc}")
            self._set_config_status("保存失败", ok=False)
            return False
        updated = tuple(replace(variable, value=values.get(variable.name, variable.value))
                        for variable in task.variables)
        try:
            modified_at = task.path.stat().st_mtime
        except OSError:
            modified_at = task.modified_at
        self.task = replace(task, variables=updated, modified_at=modified_at)
        self.fields["built"].setText(format_build_time(modified_at))
        self.error.setText("")
        self._set_config_status("已保存到 this.task")
        return True

    def reset_variables(self) -> bool:
        """Discard unsaved edits and reload variables from this.task."""
        task = self.task
        if task is None:
            self._set_config_status("未选择任务", ok=False)
            return False
        if task.error:
            self._set_config_status("任务配置无效，无法重置", ok=False)
            return False
        try:
            data = json.loads(task.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("this.task 必须是 JSON 对象")
            variables = parse_task_variables(data.get("var"))
            modified_at = task.path.stat().st_mtime
        except (OSError, ValueError, TypeError) as exc:
            self.error.setText(f"无法读取任务配置：{exc}")
            self._set_config_status("重置失败", ok=False)
            return False
        self.task = replace(task, variables=variables, modified_at=modified_at)
        self._build_variable_editors(self.task)
        self._update_config_footer()
        self.fields["built"].setText(format_build_time(modified_at))
        self.error.setText("")
        self._set_config_status("已重置为 this.task 中的值")
        return True

    def apply_variables(self) -> bool:
        """Write the edited values back to this.task before launching."""
        task = self.task
        if task is None:
            return False
        if not task.variables:
            return True
        return self.save_variables()


# 使用常量脚本和位置参数，不把任务文件内容拼接进 shell 命令。
BASH_SCRIPT = r'''set -e
to_path() {
    if command -v cygpath >/dev/null 2>&1; then cygpath -u -- "$1"; else printf '%s' "$1"; fi
}
workspace=$(to_path "$1")
python=$(to_path "$2")
# $3 和 $4 交给原生 Windows Python，保留原生路径，不能依赖 MSYS 自动转换。
target="$3"
task="$4"
cd -- "$workspace"
if [ -f "$workspace/.venv/Scripts/activate" ]; then
    source "$workspace/.venv/Scripts/activate"
else
    source "$workspace/.venv/bin/activate"
fi
exec "$python" -u "$target" --this-task "$task"
'''


def task_command(workspace: Path, task: Task, bash: str | None = None) -> list[str]:
    if task.error or task.run_target is None or not task.run_target.is_file():
        raise ValueError(task.error or "任务运行文件不存在")
    if not task.path.is_file():
        raise ValueError("this.task 不存在，无法传递任务配置")
    if not task.path.resolve().is_relative_to((workspace / "Task").resolve()):
        raise ValueError("只能运行 Task 目录中的任务")
    target = task.run_target.resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError("运行文件不可越出工作区")
    interpreter = venv_python(workspace)
    if not interpreter.is_file():
        raise ValueError("工作区 Python 不存在，请返回启动页准备环境")
    bash = bash or find_bash()
    if not bash:
        raise ValueError("未找到 Git Bash；不使用其他 shell 静默替代")
    return [bash, "--noprofile", "--norc", "-c", BASH_SCRIPT,
            "auto-lumin-task", str(workspace.resolve()), str(interpreter), str(target),
            str(task.path.resolve())]


class RunPage(QWidget):
    finished = Signal(int)
    state_changed = Signal()
    restart_requested = Signal()

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace
        self.task: Task | None = None
        self.suppress_log = False
        self.busy = False
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pending = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 24)
        root.addWidget(TitleLabel("运行", self))
        header = QHBoxLayout()
        self.title = plain_label("未选择任务", self, strong=True)
        header.addWidget(self.title, 1)
        self.state = plain_label("等待启动", self)
        header.addWidget(self.state)
        self.ring = IndeterminateProgressRing(self, start=False)
        self.ring.setFixedSize(30, 30)
        self.ring.setStrokeWidth(3)
        self.ring.setCustomBarColor("#16803c", "#3ad877")
        header.addWidget(self.ring)
        self.stopped_indicator = plain_label("●", self)
        self.stopped_indicator.setStyleSheet("color:#c42b1c;font-size:26px")
        self.stopped_indicator.hide()
        header.addWidget(self.stopped_indicator)
        self.restart = button("重新运行 · F9", self, self.restart_requested.emit)
        self.restart.setEnabled(False)
        header.addWidget(self.restart)
        root.addLayout(header)
        root.addWidget(plain_label("任务运行时禁止返回。停止任务键由任务文件指定，默认 F12；进程退出后可返回或按 F9 重启。", self))
        panels = QHBoxLayout()
        self.log_panel = QWidget(self)
        self.info_panel = QWidget(self)
        for name, panel in (("log", self.log_panel), ("info", self.info_panel)):
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(plain_label(name, panel, strong=True))
            edit = QPlainTextEdit(panel)
            edit.setReadOnly(True)
            edit.setMaximumBlockCount(20000)
            edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            layout.addWidget(edit, 1)
            panels.addWidget(panel, 1)
            setattr(self, name, edit)
        root.addLayout(panels, 1)

    def start(self, task: Task, suppress_log: bool = False):
        if self.busy:
            return False
        self.task = task
        self.suppress_log = suppress_log
        self.title.setText(task.title)
        self.log.clear()
        self.log_panel.setVisible(not suppress_log)
        self.info_panel.setVisible(True)
        self.info.clear()
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            command = task_command(self.workspace, task)
        except (OSError, ValueError) as exc:
            self.info.appendPlainText(f"启动失败：{exc}")
            self._stop(-1)
            return False
        env = QProcessEnvironment.systemEnvironment()
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "BASH_ENV", "ENV"):
            env.remove(name)
        for name, value in (("PYTHONUNBUFFERED", "1"), ("PYTHONIOENCODING", "utf-8"),
                            ("PYTHONDONTWRITEBYTECODE", "1")):
            env.insert(name, value)
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.workspace))
        self.busy = True
        self.restart.setEnabled(False)
        self.stopped_indicator.hide()
        self.ring.show()
        self.ring.start()
        self.state.setText("执行中")
        self.state.setStyleSheet("color:#16803c")
        self.state_changed.emit()
        self.process.start(command[0], command[1:])
        return True

    def _route(self, line: str):
        line = line.rstrip("\r")
        if LOG_PATTERN.fullmatch(line):
            if not self.suppress_log:
                self.log.appendPlainText(line)
            return
        self.info.appendPlainText(line)

    def _read(self):
        self.pending += self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        lines = self.pending.split("\n")
        self.pending = lines.pop()
        for line in lines:
            self._route(line)

    def _finished(self, code, status):
        if not self.busy:
            return
        self._read()
        self.pending += self.decoder.decode(b"", final=True)
        if self.pending:
            self._route(self.pending)
            self.pending = ""
        if status == QProcess.ExitStatus.CrashExit:
            self.info.appendPlainText("进程异常终止。")
        self._stop(code)

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.busy:
            self.info.appendPlainText("无法启动 Bash：" + self.process.errorString())
            self._stop(-1)

    def _stop(self, code):
        self.busy = False
        self.ring.stop()
        self.ring.hide()
        self.stopped_indicator.show()
        self.state.setStyleSheet("color:#c42b1c")
        self.state.setText(f"已停止 · 退出码 {code}")
        self.restart.setEnabled(self.task is not None)
        self.state_changed.emit()
        self.finished.emit(code)

    def cleanup(self):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        pid = self.process.processId()
        if os.name == "nt" and pid:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=5, creationflags=NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.process.kill()
        self.process.waitForFinished(3000)


class GlobalF9(QAbstractNativeEventFilter):
    HOTKEY_ID = 0x4C39

    def __init__(self, callback: Callable):
        super().__init__()
        self.callback = callback
        self.registered = False
        self.error = ""

    def register(self) -> bool:
        if self.registered:
            return True
        if os.name != "nt":
            self.error = "全局 F9 仅支持 Windows"
            return False
        from ctypes import wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.registered = bool(self.user32.RegisterHotKey(None, self.HOTKEY_ID, 0x4000, 0x78))
        if self.registered:
            QApplication.instance().installNativeEventFilter(self)
        else:
            self.error = f"全局 F9 注册失败（可能已被占用，Windows 错误 {ctypes.get_last_error()}）"
        return self.registered

    def nativeEventFilter(self, event_type, message):
        if self.registered and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes import wintypes
            native = wintypes.MSG.from_address(int(message))
            if native.message == 0x0312 and native.wParam == self.HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            self.user32.UnregisterHotKey(None, self.HOTKEY_ID)
            QApplication.instance().removeNativeEventFilter(self)
            self.registered = False


class LegacyFileDrop(QObject):
    """尝试绕过 UIPI 的文件拖放接收器。

    Windows 会阻止普通权限的资源管理器向管理员窗口发起 OLE 拖放。这里改用
    传统 DragAcceptFiles/WM_DROPFILES 通道，并通过 ChangeWindowMessageFilterEx
    放行 WM_DROPFILES、WM_COPYDATA 和 WM_COPYGLOBALDATA。系统只传路径，
    任务包仍由本进程读取和解压。

    该通道依赖 Explorer 选择传统拖放；若仍被系统拦截，任务页的
    「选择任务包」按钮继续作为回退方案。
    """

    files_dropped = Signal(object)

    WM_DROPFILES = 0x0233
    WM_COPYDATA = 0x004A
    WM_COPYGLOBALDATA = 0x0049
    GWLP_WNDPROC = -4
    MSGFLT_ALLOW = 1

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        self.hwnd = 0
        self._old_proc = None
        self._callback = None
        self._set_long = None
        self._user32 = None
        self._shell32 = None
        self._installed = False
        self.drop_filter_enabled = False

    def install(self) -> bool:
        if self._installed or os.name != "nt":
            return False
        if QApplication.platformName().lower() == "offscreen":
            return False
        try:
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            hwnd = int(self.window.winId())
            if not hwnd:
                return False
            user32.ChangeWindowMessageFilterEx.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
            user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM]
            user32.CallWindowProcW.restype = ctypes.c_ssize_t
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragAcceptFiles.restype = None
            shell32.DragFinish.argtypes = [wintypes.HANDLE]
            set_long = (user32.SetWindowLongPtrW
                        if ctypes.sizeof(ctypes.c_void_p) == 8
                        else user32.SetWindowLongW)
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            set_long.restype = ctypes.c_void_p
            wndproc = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM)

            @wndproc
            def proc(hwnd_, message, wparam, lparam):
                try:
                    if message == self.WM_DROPFILES:
                        paths = self._query_paths(wparam)
                        shell32.DragFinish(wparam)
                        if paths:
                            self.files_dropped.emit(paths)
                        return 0
                except Exception:
                    pass
                if self._old_proc is None:
                    return 0
                return user32.CallWindowProcW(
                    self._old_proc, hwnd_, message, wparam, lparam)

            old_proc = set_long(
                hwnd, self.GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
            if not old_proc:
                return False
            self.hwnd = hwnd
            self._old_proc = old_proc
            self._callback = proc
            self._set_long = set_long
            self._user32 = user32
            self._shell32 = shell32
            self.drop_filter_enabled = bool(user32.ChangeWindowMessageFilterEx(
                hwnd, self.WM_DROPFILES, self.MSGFLT_ALLOW, None))
            for message in (self.WM_COPYDATA, self.WM_COPYGLOBALDATA):
                user32.ChangeWindowMessageFilterEx(
                    hwnd, message, self.MSGFLT_ALLOW, None)
            shell32.DragAcceptFiles(hwnd, True)
            self._installed = True
            return True
        except (AttributeError, OSError, TypeError):
            if self._old_proc is not None and self._set_long is not None:
                try:
                    self._set_long(self.hwnd, self.GWLP_WNDPROC, self._old_proc)
                except Exception:
                    pass
            self._old_proc = None
            self._callback = None
            return False

    def uninstall(self):
        if not self._installed:
            return
        try:
            self._shell32.DragAcceptFiles(self.hwnd, False)
        except Exception:
            pass
        try:
            if self._old_proc is not None and self._set_long is not None:
                self._set_long(self.hwnd, self.GWLP_WNDPROC, self._old_proc)
        except Exception:
            pass
        self._installed = False
        self._old_proc = None
        self._callback = None

    @staticmethod
    def _query_paths(wparam) -> list[Path]:
        if os.name != "nt":
            return []
        from ctypes import wintypes
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT
        count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
        if not count:
            return []
        buffer = ctypes.create_unicode_buffer(32768)
        paths = []
        for index in range(count):
            length = shell32.DragQueryFileW(
                wparam, index, buffer, len(buffer))
            if length:
                paths.append(Path(buffer.value))
        return paths


class PageSteps(QWidget):
    """只读四阶段指示器，不提供绕过页面门禁的点击入口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_index = 0
        self.setFixedHeight(40)
        self.setMinimumWidth(360)
        self.setMaximumWidth(520)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        qconfig.themeChangedFinished.connect(self.update)
        self.set_current(0)

    def set_current(self, index: int):
        if not 0 <= index < len(PAGE_TITLES):
            return
        self.current_index = index
        self.setAccessibleName(f"当前步骤：{PAGE_TITLES[index]}，第 {index + 1} 步，共 4 步")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = themeColor()
        foreground = QColor("#f3f3f3" if isDarkTheme() else "#242424")
        muted = QColor("#969696" if isDarkTheme() else "#858585")
        tint = QColor(accent)
        tint.setAlpha(36 if isDarkTheme() else 20)
        segment = self.width() / len(PAGE_TITLES)
        for index, title in enumerate(PAGE_TITLES):
            active = index == self.current_index
            left = index * segment
            if active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(tint)
                painter.drawRoundedRect(QRectF(left + 4, 3, segment - 8, 34), 9, 9)
            # 圆点编号与标签成组，保持四项对齐，不使用文本括号。
            circle = QRectF(left + segment / 2 - 34, 11, 18, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent if active else QColor(0, 0, 0, 0))
            painter.drawEllipse(circle)
            font = painter.font()
            font.setPointSize(9)
            font.setBold(active)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff") if active else muted)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(index + 1))
            painter.setPen(accent if active else (foreground if index < self.current_index else muted))
            label_rect = QRectF(circle.right() + 7, 0, 40, self.height())
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            if index < len(PAGE_TITLES) - 1:
                painter.setPen(Qt.PenStyle.NoPen)
                line_color = QColor(muted)
                line_color.setAlpha(65)
                painter.setBrush(line_color)
                painter.drawRoundedRect(QRectF(left + segment - 6, 19, 12, 2), 1, 1)


class LauncherWindow(FluentWidget):
    def __init__(self, workspace: Path | None = None, *, auto_start=True, register_hotkey=True):
        super().__init__()
        self.workspace = (workspace or workspace_path()).resolve()
        icon = QIcon(str(resource_path("icon.ico", self.workspace)))
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        settings_path = launcher_settings_path(self.workspace)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        self.resize(1180, 760)
        self.setMinimumSize(900, 620)
        self.current_index = 0
        self._closing = False
        self.window_layout = QVBoxLayout(self)
        self.window_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        self.window_layout.setSpacing(0)
        container = QWidget(self)
        self.window_layout.addWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 8, 0, 8)
        root.setSpacing(4)
        navigation = QHBoxLayout()
        navigation.setContentsMargins(28, 4, 28, 4)
        navigation.setSpacing(12)
        self.back_button = TransparentPushButton(FIF.LEFT_ARROW, "返回", container)
        self.back_button.setFixedSize(80, 36)
        self.back_button.setToolTip("返回上一步")
        self.back_button.clicked.connect(self.go_back)
        navigation.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignVCenter)
        navigation.addStretch(1)
        self.page_steps = PageSteps(container)
        navigation.addWidget(self.page_steps, 1)
        navigation.addStretch(1)
        # 两侧等宽，使步骤栏始终位于窗口中线，而不是跟随返回按钮偏移。
        navigation_balance = QWidget(container)
        navigation_balance.setFixedWidth(80)
        navigation.addWidget(navigation_balance)
        root.addLayout(navigation)
        self.titleBar.raise_()
        self.stackedWidget = QStackedWidget(container)
        self.check_page = CheckPage(self.workspace)
        self.task_page = TaskPage(self.workspace)
        self.detail_page = DetailPage()
        self.run_page = RunPage(self.workspace)
        self.pages = (self.check_page, self.task_page, self.detail_page, self.run_page)
        for name, page in zip(("checkPage", "taskPage", "detailPage", "runPage"), self.pages):
            page.setObjectName(name)
            self.stackedWidget.addWidget(page)
        root.addWidget(self.stackedWidget, 1)
        self.hotkey_status = CaptionLabel("", container)
        self.hotkey_status.setTextFormat(Qt.TextFormat.PlainText)
        self.hotkey_status.setWordWrap(True)
        self.hotkey_status.setContentsMargins(28, 0, 28, 4)
        root.addWidget(self.hotkey_status)
        self.check_page.passed.connect(self.show_tasks)
        self.check_page.choose_workspace.connect(self.choose_workspace)
        self.task_page.selected.connect(self.show_detail)
        self.detail_page.run_requested.connect(self.run_selected)
        self.run_page.restart_requested.connect(self.run_selected)
        self.run_page.state_changed.connect(self._navigation_state)
        self.stackedWidget.currentChanged.connect(self._guard_stack)
        self.hotkey = GlobalF9(self.run_selected)
        self.legacy_drop = LegacyFileDrop(self)
        self.legacy_drop.files_dropped.connect(self._legacy_files_dropped)
        self._legacy_drop_installed = False
        self.fallback = QShortcut(QKeySequence("F9"), self)
        self.fallback.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.fallback.activated.connect(self.run_selected)
        self.fallback.setEnabled(False)
        if register_hotkey:
            registered = self.hotkey.register()
            self.fallback.setEnabled(not registered)
            shortcut_status = ("全局 F9 已注册" if registered else
                               self.hotkey.error + "；仅窗口内 F9 可用，运行按钮仍可使用。")
            self.hotkey_status.setText(shortcut_status + " · 停止任务键由任务文件指定，默认 F12")
        self._navigation_state()
        geometry = self.settings.value("window/geometry", QByteArray())
        if geometry:
            self.restoreGeometry(QByteArray(geometry))
            size = self.settings.value("window/size")
            position = self.settings.value("window/position")
            if size:
                self.resize(size)
            if position:
                self.move(position)
        else:
            self.settings.setValue("window/geometry", self.saveGeometry())
            self.settings.sync()
        if auto_start:
            QTimer.singleShot(0, self.check_page.run_checks)

    @property
    def current_page(self):
        return self.pages[self.current_index]

    def showEvent(self, event):
        super().showEvent(event)
        if (not self._legacy_drop_installed and is_admin()
                and QApplication.platformName().lower() != "offscreen"):
            self._legacy_drop_installed = self.legacy_drop.install()

    def _legacy_files_dropped(self, paths):
        if self.current_index != 1:
            return
        archives = [Path(path) for path in paths
                    if Path(path).suffix.lower() in ARCHIVE_SUFFIXES]
        if not archives:
            self.task_page.import_message = "只支持 .zip、.7z 或 .rar 任务包。"
            self.task_page._update_import_state()
            return
        self.task_page.choose_package(archives[0])

    def navigate(self, index: int) -> bool:
        if self._closing or not 0 <= index < len(self.pages) or abs(index - self.current_index) != 1:
            return False
        if self.run_page.busy:
            return False
        if index > 0 and not self.check_page.ready:
            return False
        if index >= 2 and self.detail_page.task is None:
            return False
        # 执行页只能由 run_selected 进入，不能靠修改 stack 或路由启动。
        if index == 3:
            return False
        self._switch(index)
        if index == 0:
            self.check_page.run_checks()
        elif index == 1:
            self.task_page.refresh()
        return True

    def _switch(self, index):
        self.current_index = index
        self.stackedWidget.setCurrentIndex(index)
        self._navigation_state()

    @Slot(int)
    def _guard_stack(self, index):
        if index != self.current_index:
            self.stackedWidget.blockSignals(True)
            self.stackedWidget.setCurrentIndex(self.current_index)
            self.stackedWidget.blockSignals(False)

    @Slot()
    def _navigation_state(self):
        self.back_button.setEnabled(self.current_index > 0 and not self.run_page.busy)
        self.setWindowTitle(f"{PAGE_TITLES[self.current_index]} · {APP_NAME}")
        self.page_steps.set_current(self.current_index)

    def go_back(self):
        self.navigate(self.current_index - 1)

    def show_tasks(self):
        if self.current_index == 0 and self.check_page.ready:
            self.navigate(1)

    def show_detail(self, task):
        if self.current_index == 1 and not self.run_page.busy and self.check_page.ready:
            self.detail_page.set_task(task)
            self.navigate(2)

    def run_selected(self):
        if self._closing or self.run_page.busy or not self.check_page.ready:
            return
        if self.current_index == 2:
            if not self.detail_page.start_button.isEnabled():
                return
            if not self.detail_page.apply_variables():
                return
            task = self.detail_page.task
            if task is None:
                return
            self._switch(3)
            self.run_page.start(task, self.detail_page.suppress_log.isChecked())
        elif self.current_index == 3 and self.run_page.task:
            self.run_page.start(self.run_page.task, self.run_page.suppress_log)

    def choose_workspace(self):
        if self.current_index != 0 or self.check_page.busy or self.task_page.job:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择工作区根目录", str(self.workspace))
        if not folder:
            return
        self.workspace = Path(folder).resolve()
        self.check_page.workspace = self.task_page.workspace = self.run_page.workspace = self.workspace
        self.task_page._clear_package()
        self.task_page.import_message = ""
        self.task_page._update_import_state()
        self.check_page.workspace_label.setText(str(self.workspace))
        self.check_page.git_state = None
        self.check_page.git_label.setText("未检查")
        self.detail_page.task = self.run_page.task = None
        icon = QIcon(str(resource_path("icon.ico", self.workspace)))
        self.setWindowIcon(icon)
        self.check_page.run_checks()

    def closeEvent(self, event):
        if self.run_page.busy:
            answer = QMessageBox.question(self, "任务正在运行",
                "关闭启动器会终止当前 Bash 及任务进程。是否关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.run_page.cleanup()
        if self.check_page.runner.busy:
            self.check_page.status.setText("工作区安装或更新正在执行，请等待完成后关闭。")
            event.ignore()
            return
        jobs = [job for job in (self.check_page.job, self.task_page.job,
                                self.task_page.package_job) if job]
        if jobs:
            self._closing = True
            self.setEnabled(False)
            for job in jobs:
                job.requestInterruption()
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        self._closing = True
        self.legacy_drop.uninstall()
        self.hotkey.close()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/size", self.size())
        self.settings.setValue("window/position", self.pos())
        self.settings.sync()
        event.accept()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto Lumin 工作区任务启动器")
    parser.add_argument("--workspace", type=Path, help="指定含 pyproject.toml 和 Task 的工作区")
    args = parser.parse_args(argv)
    relaunch_args = [] if args.workspace is None else ["--workspace", str(args.workspace)]
    if relaunch_as_admin(relaunch_args):
        return 0
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoLumin.Launcher")
        except (AttributeError, OSError):
            pass
    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    setTheme(Theme.AUTO)
    window = LauncherWindow(workspace_path(args.workspace))
    window.show()
    return app.exec()


if __name__ == "__main__":
    # 在导入 GUI 前完成提权，避免未提权进程先创建窗口。
    if relaunch_as_admin():
        raise SystemExit(0)
    raise SystemExit(main())
