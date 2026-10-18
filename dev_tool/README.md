# OK Lumin 开发工具

`dev_tool/ok_lumin_tool` 将 `dna_pos_tool` 和 `action_recorder_player` 合并为一个 Tk 工具，原目录保持不变。

## 启动

在项目根目录执行：

```powershell
py -3 -m pip install -r dev_tool\ok_lumin_tool\requirements.txt
py -m dev_tool.ok_lumin_tool
```

也可以双击 `dev_tool\启动.bat`。

## 功能

- `位置与截图`：红色十字准心用于取点，蓝色区域框用于移动、缩放和区域截图。
- `动作录制与播放`：使用 Tk `Treeview` 显示动作，保留 `.stepscript` v2 格式和 F8/F9/F10 全局快捷键。
- 截图保存到当前工作目录的 `screenshot` 文件夹；动作录制不保存鼠标坐标，播放时使用当前鼠标位置。

目标程序以管理员权限运行时，请使用管理员权限启动本工具。
