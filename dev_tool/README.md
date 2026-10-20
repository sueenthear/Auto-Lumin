# OK Lumin 开发工具

`dev_tool/ok_lumin_tool` 将 `dna_pos_tool` 和 `action_recorder_player` 合并为一个 Tk 工具，原目录保持不变。

## 启动

在项目根目录执行：

```powershell
uv sync --locked
uv run python -m dev_tool.ok_lumin_tool
```

也可以双击 `dev_tool\启动.bat`。启动脚本会按自身所在位置找到项目根目录，自动运行 `uv sync --locked`，再使用项目 `.venv` 中的 `pythonw.exe` 启动，不依赖当前工作目录或全局 Python 的依赖状态。

## 功能

- `位置与截图`：红色十字准心用于取点，蓝色区域框用于移动、缩放和区域截图。
- `动作录制与播放`：使用 Tk `Treeview` 显示动作，记录键盘和鼠标按下/抬起（支持长按），保留 `.stepscript` v2 格式和 F8/F9/F10 全局快捷键。
- 截图保存到当前工作目录的 `screenshot` 文件夹；动作录制不保存鼠标坐标，鼠标按钮动作在播放时使用当前指针位置。

## 录音剪辑工具

`dev_tool/audio_recorder.py` 是独立的进程音频录制与剪辑界面，用于制作 `do.audio` 使用的 `.voice` 模板：

```powershell
uv run python -m dev_tool.audio_recorder
```

也可以双击项目根目录的 `启动录音剪辑工具.bat`（自动以管理员权限启动，便于绑定管理员权限运行的游戏）。绑定目标 exe 后按 F8 开始录制、F9 结束并保存，最长 10 秒；随后可打开 `.voice` 试听整段或按起止秒数试听剪辑，并把剪辑保存为新模板。输出文件必须是 `.voice`，内容为 16 kHz、单声道、16 位 PCM WAV。录音与剪辑的默认保存路径就是包内默认模板 `script_core/audio_manager/template.voice`，覆盖后 `do.audio.listen.start()` 立即生效。

目标程序以管理员权限运行时，请使用管理员权限启动本工具。
