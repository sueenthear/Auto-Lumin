# .voice 文件规范

`.voice` 是 `do.audio` 使用的音频模板文件格式。**扩展名是 `.voice`，内容是标准 RIFF/WAV**，可直接用任何支持 WAV 的播放器打开试听。

规范由 `script_core/audio_manager/template.py` 强制校验，不符合时在 `do.audio.listen.start()` 阶段即抛出 `ValueError`。

## 1. 容器格式

| 项目 | 要求 |
| --- | --- |
| 容器 | RIFF/WAV（`wave` 模块可解析） |
| 编码 | 未压缩 PCM（`comptype == "NONE"`） |
| 采样率 | **16000 Hz** |
| 声道数 | **1（单声道）** |
| 位深 | **16 bit，有符号小端整数（PCM16）** |
| 字节序 | 小端（`<i2`） |

以上四项必须**完全匹配**，不做重采样或声道转换。不符合时报错：

```
ValueError: .voice 必须为 16 kHz / mono / 16-bit PCM WAV
```

## 2. 时长限制

| 项目 | 值 |
| --- | --- |
| 最短 | **0.04 秒**（640 帧） |
| 最长 | **10 秒**（160000 帧） |

超出范围报错：

```
ValueError: 模板长度应为 0.04 至 10 秒
```

单帧字节数 = 声道数 × 位深字节数 = `1 × 2 = 2` 字节，因此文件数据区长度必须是 2 的整数倍。

## 3. 内容要求

### 3.1 不能是静音

峰值振幅必须 **大于 96**（即 `max(|sample|) > 96`，满量程 32767）。否则报错：

```
ValueError: 不能用静音模板进行匹配；请录制真实的目标音效
```

### 3.2 必须有足够变化

匹配前会减去均值并计算能量，模板能量需大于 `96²`，否则报错：

```
ValueError: 模板音频变化过小，无法匹配
```

恒定直流信号（如恒定单音调平直段）无法用于归一化互相关匹配。

### 3.3 帧数据必须完整

数据区声明的帧数与实际字节数不一致时报错：

```
ValueError: .voice WAV 音频帧数据不完整
```

## 4. 文件结构示例

一个合法的 `.voice` 文件（`template.voice`，4206 帧）：

```
偏移    内容
0x00    "RIFF"
0x04    文件长度 - 8      (小端 uint32)
0x08    "WAVE"
0x0C    "fmt "
0x10    16                (fmt 块长度)
0x14    1                 (WAVE_FORMAT_PCM)
0x16    1                 (声道数 = 1)
0x18    16000             (采样率)
0x1C    32000             (字节率 = 16000 × 1 × 2)
0x20    2                 (块对齐 = 1 × 2)
0x22    16                (位深)
0x24    "data"
0x28    8412              (数据长度 = 4206 × 2)
0x2C    ...               (8412 字节 PCM16 小端样本)
```

## 5. 制作方式

由于录制 GUI 已移除，目前有两种方式生成 `.voice`：

### 5.1 用 Python 直接写出

```python
import wave

# 已有 16 kHz / 单声道 / PCM16 的 PCM 数据
pcm: bytes = ...            # 长度必须是 2 的整数倍

with wave.open("my.voice", "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(16000)
    wav.writeframes(pcm)
```

### 5.2 用库函数转换

`script_core/audio_manager/template.py` 提供辅助函数：

```python
from script_core.audio_manager import save_voice, load_voice, trim_voice_samples

# 写出：校验非空且帧完整
save_voice("my.voice", pcm)

# 读取：返回 float32 样本数组，同时完成全部规范校验
samples = load_voice("my.voice")

# 按秒剪辑，返回 PCM 字节
clip = trim_voice_samples(samples, 0.05, 0.35)
save_voice("clip.voice", clip)
```

`save_voice` 只接受 `.voice` 扩展名，其他后缀报错：

```
ValueError: 录音文件扩展名必须为 .voice
```

### 5.3 从其他格式转换

若手上是 44.1 kHz 或立体声素材，需先转换为 16 kHz 单声道 PCM16。可用 ffmpeg：

```powershell
ffmpeg -i input.wav -ar 16000 -ac 1 -acodec pcm_s16le output.wav
Move-Item output.wav output.voice
```

## 6. 默认模板位置

`do.audio.listen.start()` 不传参数时**只读取包内**：

```
script_core/audio_manager/template.voice
```

工作目录下的同名文件会被忽略。要更换默认模板，直接覆盖这个文件；或显式传入路径：

```python
do.audio.listen.start("D:/templates/dodge.voice")
```

打包配置已在 `pyproject.toml` 的 package-data 中声明该文件随包分发。

## 7. 校验错误对照表

| 错误信息 | 原因 |
| --- | --- |
| `.voice 必须为 16 kHz / mono / 16-bit PCM WAV` | 采样率、声道数、位深或编码不符 |
| `模板长度应为 0.04 至 10 秒` | 帧数不在 640–160000 之间 |
| `.voice WAV 音频帧数据不完整` | 数据区字节数与声明帧数不符 |
| `无效的 .voice WAV 模板` | 文件损坏，或根本不是 WAV |
| `不能用静音模板进行匹配` | 峰值振幅 ≤ 96 |
| `模板音频变化过小，无法匹配` | 能量过低，接近直流 |
| `录音文件扩展名必须为 .voice` | `save_voice` 收到其他后缀 |
| `录音必须为非空且完整的 PCM 帧` | `save_voice` 收到空数据或长度非 2 的倍数 |
| `剪辑起止时间必须是有效数字` | `trim_voice_samples` 收到 `nan`/`inf` |
| `剪辑范围必须位于音频时长内…` | 起止越界或结束早于开始 |
| `剪辑长度不能短于 0.04 秒` | 剪辑区间过短 |
