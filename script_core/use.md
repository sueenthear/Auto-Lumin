# script_core

```python
from script_core import Core

do = Core("Lumin.exe", timeout=10)
```

`Core` 在 timeout 秒内查找可见目标窗口；绑定失败会以错误信息退出（`SystemExit`），参数无效抛出 `ValueError`。

## 安装与 IDE 导入

在项目根目录安装 editable package，之后从任意子目录都可以直接导入，无需在脚本中修改 `sys.path`：

```powershell
py -3 -m pip install -e .
```

请确保安装命令使用的 Python 解释器与 IDE 项目配置的解释器一致。使用虚拟环境时，先在该环境中运行 `python -m pip install -e .`，再在 IDE 中选择同一个环境。editable install 会让包直接指向当前源码目录，修改源码后无需重复安装。完成后脚本直接写：

```python
from script_core import Core
```

## 等待 API

```python
do.wait(0.5)  # 当前线程暂停 0.5 秒；不操作窗口，也不改变步骤执行实例的计时器
do.wait(0)    # 允许零秒等待
```

`do.wait(seconds)` 接收非负、有限的整数或浮点数，单位是秒，阻塞当前调用线程并返回 `None`。负数、`nan`、无穷大、布尔值及非数值会抛 `ValueError`。这是脚本中统一使用的定时等待接口，与 `do.steps.pause(execute)`（暂停回放）不同。

## 鼠标与键盘

### 鼠标 API

鼠标按键事件通过属性访问立即执行，不加括号：

```python
do.mouse.click.left
do.mouse.click.right
do.mouse.click.middle
do.mouse.click.xfront
do.mouse.click.xbehind

do.mouse.down.left
do.mouse.down.right
do.mouse.down.middle
do.mouse.down.xfront
do.mouse.down.xbehind

do.mouse.up.left
do.mouse.up.right
do.mouse.up.middle
do.mouse.up.xfront
do.mouse.up.xbehind

do.mouse.roll.up
do.mouse.roll.down
```

平滑移动和默认值设置使用方法调用：

```python
do.mouse.move.abs(x, y, duration=0.5, fps=60)
do.mouse.move.rel(dx, dy, duration=0.5, fps=60)
do.mouse.set.abs(key, value)          # 例如 do.mouse.set.abs("fps", 30)
do.mouse.set.rel(key, value)          # 例如 do.mouse.set.rel("duration", 0.2)
# do.mouse.move.set.abs / do.mouse.move.set.rel 也指向同一设置对象
```

`abs` 的 `(x, y)` 是窗口客户区坐标，不是屏幕坐标；`rel` 的 `(dx, dy)` 是相对当前鼠标位置的偏移。`duration` 单位为秒且不能小于 0；`fps` 必须至少为 1。`key` 只能是 `"duration"` 或 `"fps"`。滚轮方向按 `up/down` 指定。

### 键盘 API

所有按键操作都通过显式 `key` 参数调用，不要把按键名写成属性：

```python
do.keyboard.click(key, down_time=0.02)
do.keyboard.down(key)
do.keyboard.up(key)
do.keyboard.text(text, interval=0.0)
```

`click` 按下按键，等待 `down_time` 秒后释放；`down` 按下并保持，`up` 释放。`down_time` 和 `interval` 单位均为秒且不能为负。`text` 使用 `pynput` 输入字符串；它与逐键事件不同，受当前键盘布局和系统输入支持范围影响。

`key` 支持下列字符串：

- 单个 ASCII 可打印字符（区分大小写）：英文字母 `abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ`、数字 `0123456789`、空格，以及标点 `` `~!@#$%^&*()-_=+[]{}\|;:'",.<>/? ``（其中 `\` 表示一个反斜杠字符）。字符必须单独传入；例如 `do.keyboard.click("?")`、`do.keyboard.click("\\")`、`do.keyboard.click("\"")`、`do.keyboard.click("'")`、`do.keyboard.click(" ")`。大写字母和需要 Shift 的标点会自动按住 Shift 完成输入。
- 修饰键名称（大小写不敏感）：`SHIFT`、`CTRL`、`ALT`、`ALTGR`、`WIN`。示例：`do.keyboard.down("CTRL")`，之后用 `do.keyboard.up("CTRL")` 释放。
- 命名键（大小写不敏感）：`ENTER`、`TAB`、`SPACE`、`BACKSPACE`、`ESC`、`DELETE`、`INSERT`、`HOME`、`END`、`PAGEUP`、`PAGEDOWN`、`LEFT`、`UP`、`RIGHT`、`DOWN`、`CAPSLOCK`。
- 功能键（大小写不敏感）：`F1` 至 `F20`，前提是当前平台的 `pynput` 提供该键。

也可传入整数 Windows virtual-key code，例如 `do.keyboard.click(0x41)`。空字符串、多字符按键名（除上述命名键和功能键外）、非 ASCII 字符及不支持的名称会抛出 `ValueError`。输入 Unicode 文本请使用 `do.keyboard.text(text)`。

组合键按“先按修饰键、再按普通键；先释放普通键、再释放修饰键”的顺序调用：

```python
do.keyboard.down("CTRL")
do.keyboard.click("a")
do.keyboard.up("CTRL")
```

## 窗口 API

```python
hwnd = do.window.hwnd                 # 窗口句柄，只读
alive = do.window.alive               # 窗口句柄是否仍有效，bool
visible = do.window.visible           # 窗口是否可见，bool
minimized = do.window.minimized       # 窗口是否最小化，bool
position = do.window.position         # 窗口外框左上角屏幕坐标 (x, y)，只读
size = do.window.size                 # 客户区尺寸 (width, height)
do.window.size(100, 100, 1280, 720)   # 移动外框左上角到 (100, 100)，将客户区设为 1280×720
title = do.window.title               # 当前窗口标题
do.window.title("new title")         # 设置窗口标题
ok = do.window.activate(timeout=2.0) # 恢复最小化窗口并尝试切换前台，返回 bool
foreground = do.window.is_foreground() # 是否是前台窗口，返回 bool
do.window.require_foreground()        # 若不在前台则尝试激活；失败抛 RuntimeError
```

`size` 和 `title` 在读取时分别表现为 tuple 和 str，读取结果同时支持调用以修改；`size` 的宽高始终指客户区，不是包含标题栏/边框的外框。已失效窗口的多数查询和操作会抛异常；可先检查 `alive`。输入事件会自动要求目标窗口进入前台。

## 图像与像素 API

```python
frame = do.image.screenshot                    # 读取属性即截取客户区，返回 OpenCV/NumPy 图像
template = do.image.load("button.png")      # 加载图片，返回图像数组
images = do.image.loads("images")           # 读取目录下的 *.png，返回 {文件名去后缀: 图像数组}
do.image.save(frame, "capture.png")         # 保存图像

rgb = do.pixel.point(10, 20)                  # 客户区点 (x, y) 的大写六位 RGB 字符串，如 "FF0000"
hits = do.pixel.count("FF0000", tolerance=0) # 统计整个客户区中匹配的像素数
```

`point` 与 `count` 每次调用均重新截图。`point` 越界抛 `ValueError`；颜色不带 `#`，恰好六位十六进制字符，不区分输入大小写。`tolerance` 为 0 到 1 的数；逐 RGB 通道允许最大差值 `round(tolerance * 255)`，三通道均在范围内才计入。`0` 严格匹配；`1` 全部放行，返回截图的像素总数。

## 模板匹配 API

```python
point = do.match.pos(template, source=None, threshold=0.8, roi=None, title="match!")
found = do.match.exist(template, source=None, threshold=0.8, roi=None, title="match!")
do.match.set.printlog(True)                    # True / False，是否打印最佳结果
do.match.set.way("ccoeff")                    # 选择匹配算法
```

`template` 可为图像或图片路径，`source=None` 时截取当前客户区；`roi=(x, y, width, height)` 在 source 上裁剪，返回的 `(x, y)` 自动加上 ROI 的起点偏移。`pos` 返回最佳模板中心坐标；低于 `threshold` 返回 `None`。`exist` 返回 bool。日志包含 `title`、坐标和分数（即使未达阈值也打印）。可选 `way`：`ccoeff` / `TM_CCOEFF_NORMED`（默认）、`sqdiff` / `TM_SQDIFF_NORMED`、`ccorr` / `TM_CCORR_NORMED`；`sqdiff` 的分数转换为 `1 - 原始分数`，使其与其他方式一样数值越高越相似。

## 连续操作 API

```python
execute = do.steps.load("actions.stepscript", end="pause")
executables = do.steps.loads("steps", end="pause")  # {文件名（不含后缀）: 可执行对象}
info = do.steps.info(execute)
do.steps.start(execute)                       # 后台线程开始回放
print(execute.state, execute.error)            # ready / running / paused / failed；error 保存线程异常
print(execute.time)                            # 累计运行秒数
```

`load(filepath, end="pause")` 读取并验证单个 `ok-lumin.stepscript` v2 文件，直接返回尚未启动的可执行对象；`loads(dirpath, end="pause")` 读取目录内所有 `.stepscript` 文件并返回 `{文件名（不含后缀）: 可执行对象}`。不再调用 `do.steps.execute(...)`。

`do.steps.info(execute)` 返回该执行序列的文件头信息，不含动作数组：

```python
{
    "format": "ok-lumin.stepscript",
    "version": 2,
    "timestamp_unit": "relative_ns",
    "info": {"duration_ns": 48306145600, "action_count": 228},
}
```

`start` 在后台线程回放；`end="pause"` 时完成后保持 `paused`，`end="abort"` 时完成后自动清理对象。执行异常不会静默丢失：实例状态变为 `failed`，异常保存在 `execute.error`。`pause` 可暂停，之后再次 `start` 从暂停处继续；`reset` 在工作线程结束后归零；`abort` 会等待线程退出并清理实例。清理后不得查询或重用该实例。由于工作线程是非 daemon 线程，解释器会等待已启动的序列结束；需要提前退出时调用 `do.steps.abort(execute)`。
