# Auto Lumin

面向 Windows 的游戏自动化工具：绑定目标窗口，采集画面、匹配图像与声音、回放输入动作。

## 组件

| 路径 | 说明 |
| --- | --- |
| `script_core/` | 可复用的 Python API：窗口、键盘、鼠标、截图、像素、模板匹配、音频匹配、动作序列。 |
| `dev_tool/ok_lumin_tool/` | 桌面工具：窗口定位、截图、动作录制与回放。 |
| `dev_tool/audio_recorder.py` | 桌面工具：按进程录制、试听、剪辑 `.voice` 音频模板。 |
| `example/` | 示例脚本：`window_info.py` 查看窗口信息，`auto-dodge.py` 声音触发自动闪避。 |
| `tests/` | 单元测试。 |

## 环境准备

Windows 前置条件、虚拟环境、依赖安装、测试与桌面工具启动方式，见 [agent-read-this-to-setup-environment.md](agent-read-this-to-setup-environment.md)。

核心 API 详见 [script_core/use.md](script_core/use.md)，桌面工具有独立的 [README](dev_tool/README.md)。

## 音频匹配

`do.audio` 只匹配 `Core(...)` 绑定进程播放的声音，不采集系统混音：

```python
from script_core import Core

do = Core("Lumin.exe", timeout=10)

do.audio.action(lambda: do.keyboard.click("shift"))
do.audio.listen.start()
```

快速上手、模板录制流程、触发规则与常见问题见 [script_core/audio_manager/使用说明.md](script_core/audio_manager/使用说明.md)；API 速查见 [script_core/use.md](script_core/use.md)。

> 该功能依赖 Windows 进程独立 loopback，需要 **Windows 10 Build 20348 及以上**。低版本会直接报错，不会退回系统混音。

## 安全提示

自动化 API 会向绑定窗口发送真实的键盘和鼠标事件。运行任何脚本前请先审阅内容、确认目标窗口；`example/` 中的 `window_info.py` 不会发送输入事件，`auto-dodge.py` 会。
