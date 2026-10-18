from script_core import Core


do = Core("Lumin.exe")
do.window.activate()
print("已绑定窗口:", do.window.title)
