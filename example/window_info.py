from script_core import Core        # 导入包

do = Core("Lumin.exe")              # 绑定窗口

do.thisfile                         # VS code 必写，工作路径更改

do.window.activate()                # 激活窗口

def main():
    do.mouse.click.left
    do.mouse.click.right


    do.mouse.move.abs(100, 100)


    do.keyboard.click("m")
