"""自动闪避示例：监听绑定进程的提示音，匹配成功后按 Shift。

运行前请确认：
1. 包内已有可用的 script_core/audio_manager/template.voice（录音工具制作）；
2. 游戏以管理员运行时，本脚本也要以管理员身份运行；
3. 阈值按实际效果调整，0.8 太严时从 0.35 起试。
"""
from script_core import Core

do = Core("Lumin.exe", timeout=10)   # 绑定窗口，10 秒内找不到则退出

do.thisfile                          # VS Code 调试时把工作目录切到本文件所在目录

do.window.activate()                 # 激活目标窗口


def main():
    def on_sound():
        # 匹配到提示音时调用，运行在独立线程，可以放心使用阻塞接口。
        print("匹配到声音")
        do.keyboard.click("shift")

    do.audio.action(on_sound)        # 注册匹配后调用的无参数函数

    # 相似度阈值，取值 (0, 1]，默认 0.8。
    # 实测：0.8 几乎不触发，0.35 能稳定匹配，0.2 会多闪。
    do.audio.listen.threshold(0.2)

    do.audio.listen.start()          # 不传路径：只用包内 template.voice
    print(f"开始监听，listening={do.audio.listen.listening}")

    try:
        while True:
            do.wait(1)               # 保活轮询；F12 全局中断终止进程
    except KeyboardInterrupt:
        pass
    finally:
        do.audio.listen.stop()
        if do.audio.listen.error is not None:
            print(f"监听结束，错误：{do.audio.listen.error!r}")


if __name__ == "__main__":
    main()
