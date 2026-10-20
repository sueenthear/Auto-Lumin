# Auto Lumin

具体任务脚本加群获取：1108948355

面向 Windows 的游戏自动化工具：绑定目标窗口，采集画面、匹配图像与声音、回放输入动作。

## 组件

| 路径 | 说明 |
| --- | --- |
| `script_core/` | 可复用的 Python API：窗口、键盘、鼠标、截图、像素、模板匹配、音频匹配、动作序列。 |
| `dev_tool/ok_lumin_tool/` | 桌面工具：窗口定位、截图、动作录制与回放。 |
| `example/` | 示例脚本：`window_info.py` 查看窗口信息，`auto-dodge.py` 声音触发自动闪避。 |
| `tests/` | 单元测试。 |

## 环境准备

Windows 前置条件、虚拟环境、依赖安装、测试与桌面工具启动方式，见 [agent-read-this-to-setup-environment.md](agent-read-this-to-setup-environment.md)。

完整 API 参考见 [script_core/use.md](script_core/use.md)，桌面工具有独立的 [README](dev_tool/README.md)。

## 绑定窗口

所有操作都从 `Core(...)` 开始，它在超时内查找可见目标窗口并完成绑定：

```python
from script_core import Core

do = Core("Lumin.exe", timeout=10)   # 也可用 title="窗口标题" 形式
```

## 窗口操作

```python
do.window.activate()                  # 恢复最小化并切到前台
print(do.window.alive, do.window.position, do.window.size)

do.window.size(100, 100, 1280, 720)   # 外框移到 (100,100)，客户区设为 1280×720
do.window.size(1600, 900)             # 只改客户区尺寸，保持位置
```

## 键盘

```python
do.keyboard.click("SPACE")            # 按下并抬起，默认间隔 0.02 秒
do.keyboard.click("a", down_time=0.1)

do.keyboard.down("CTRL")              # 组合键：先按修饰键
do.keyboard.click("a")
do.keyboard.up("CTRL")

do.keyboard.text("hello world", interval=0.05)   # 整段文本，支持 Unicode
```

## 鼠标

```python
do.mouse.click.left                   # 按键是属性，不加括号
do.mouse.click.right

do.mouse.down.left                    # 长按
do.mouse.up.left

do.mouse.roll.up                      # 滚轮
do.mouse.roll.down

do.mouse.move.abs(640, 360)           # 移动到客户区坐标 (640, 360)
do.mouse.move.rel(50, -30)            # 相对当前位置偏移
do.mouse.set.abs("duration", 0.2)     # 之后 move.abs 默认用 0.2 秒
do.mouse.set.rel("fps", 120)
```

## 截图与像素

```python
frame = do.image.screenshot           # 属性访问即截图，返回 NumPy 图像
do.image.save(frame, "capture.png")

template = do.image.load("button.png")
images = do.image.loads("images")     # {文件名去后缀: 图像数组}

rgb = do.pixel.point(10, 20)          # "FF0000" 形式的大写六位 RGB
hits = do.pixel.count("FF0000", tolerance=0.05)   # 容差 0~1
```

## 图像模板匹配

```python
point = do.match.pos("button.png", threshold=0.9)      # 返回中心坐标，未命中为 None
found = do.match.exist("button.png", roi=(0, 0, 800, 600))   # 只在 ROI 内查找

do.match.set.printlog(True)           # 打印最佳结果的坐标与分数
do.match.set.way("sqdiff")            # ccoeff（默认）/ sqdiff / ccorr
```

## 音频匹配

只匹配 `Core(...)` 绑定进程播放的声音，不采集系统混音：

```python
def on_sound():
    do.keyboard.click("shift")        # 动作在独立线程执行，可放心用阻塞接口

do.audio.action(on_sound)             # 注册匹配后调用的无参数函数
do.audio.listen.threshold(0.35)       # 阈值 (0,1]，默认 0.8
do.audio.listen.start()               # 只用包内 template.voice

while True:
    do.wait(1)                        # 保活；F12 全局中断
```

> 依赖 Windows 进程独立 loopback，需要 **Windows 10 Build 20348 及以上**。低版本直接报错，不会退回系统混音。
> 模板格式与制作方式见 [script_core/audio_manager/voice文件规范.md](script_core/audio_manager/voice文件规范.md)。

## 动作序列回放

```python
execute = do.steps.load("actions.stepscript")     # 读取但尚未启动
do.steps.start(execute)                           # 后台线程开始回放
do.steps.wait(execute)                            # 阻塞到回放结束

if execute.error is not None:                     # 异常不会自动抛出
    raise execute.error

print(do.steps.info(execute))                     # 文件头信息
do.steps.abort(execute)                           # 提前结束并清理
```

## 等待与日志

```python
do.wait(0.5)                          # 阻塞当前线程 0.5 秒

do.log.output(True)                   # 打印每次 do API 调用
do.log.output(False)                  # 默认关闭
```

## 工作目录与中断

```python
do.thisfile                           # 属性：切换工作目录到本文件所在目录

do.interrupt.set("F10")               # 更换全局中断键，默认 F12
```

`do.thisfile` 不带括号，读取即生效；`interrupt` 直接结束进程，不执行 `finally` 清理。

## 安全提示

自动化 API 会向绑定窗口发送真实的键盘和鼠标事件。运行任何脚本前请先审阅内容、确认目标窗口；`example/` 中的 `window_info.py` 不会发送输入事件，`auto-dodge.py` 会。
