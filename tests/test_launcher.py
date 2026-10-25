"""启动器回归：仅使用临时任务，不连接游戏、不安装项目依赖。"""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import venv
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
import Launcher as launcher


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        # 前一个测试的 Qt 控件可能带有循环引用；先回收，避免 qfluentwidgets
        # 在 setTheme 迭代弱引用表时被垃圾回收打断。
        import gc
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix="lumin-tests-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "workspace 中文 $' space"
        self.folder = self.workspace / "Task" / "中文任务 ' $ space"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "this.task"
        (self.folder / "run.py").write_text("print('safe test')\n", encoding="utf-8")
        self.data = {"title": "中文任务", "description": "任务文字描述",
                     "editor": "作者", "version": "1.2.0", "run_target": "run.py",
                     "description_image": "说明.png"}
        self.write_task()

    def write_task(self, data=None):
        self.path.write_text(json.dumps(data or self.data, ensure_ascii=False), encoding="utf-8-sig")
        return launcher.load_task(self.path, self.workspace)

    def wait_until(self, condition, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.005)
        self.fail("异步操作超时")

    def test_task_text_image_and_bom(self):
        task = self.write_task()
        self.assertEqual(task.error, "")
        self.assertEqual(task.title, "中文任务")
        self.assertEqual(task.description, "任务文字描述")
        self.assertEqual(task.image, self.folder / "说明.png")
        self.assertEqual(task.run_target, self.folder / "run.py")

    def test_legacy_description_image_and_object_supported(self):
        self.data.pop("description_image")
        self.data["description"] = "说明.png"
        self.data["description_text"] = "旧格式文字"
        task = self.write_task()
        self.assertEqual(task.description, "旧格式文字")
        self.assertEqual(task.image.name, "说明.png")
        self.data["description"] = {"text": "新文字", "image": "说明.png"}
        task = self.write_task()
        self.assertEqual(task.description, "新文字")
        self.assertEqual(task.image.name, "说明.png")

    def test_optional_task_image_missing_or_empty_keeps_task_runnable(self):
        base = dict(self.data)
        base.pop("description_image")
        cases = [base, dict(base, description_image=""),
                 dict(base, description_image="   "), dict(base, image=""),
                 dict(base, description={"text": "说明文字"}),
                 dict(base, description={"text": "说明文字", "image": ""}),
                 dict(base, description=""),
                 {key: value for key, value in base.items() if key != "description"}]
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        for data in cases:
            with self.subTest(data=data):
                task = self.write_task(data)
                self.assertEqual(task.error, "")
                self.assertIsNone(task.image)
                self.assertEqual(task.run_target, self.folder / "run.py")
                detail.set_task(task)
                self.assertTrue(detail.start_button.isEnabled())
                self.assertEqual(detail.error.text(), "")
                self.assertTrue(detail.preview.pixmap.isNull())
                self.assertEqual(detail.preview.message, "未配置描述图片")
                page.populate([task])
                self.assertEqual(page.list.count(), 1)

    def test_explicit_empty_image_does_not_fall_back_to_legacy_image(self):
        for description in ("旧说明.png", {"text": "说明", "image": "旧说明.png"}):
            for key in ("description_image", "image"):
                with self.subTest(description=description, key=key):
                    data = dict(self.data, description=description)
                    data.pop("description_image")
                    data[key] = ""
                    task = self.write_task(data)
                    self.assertEqual(task.error, "")
                    self.assertIsNone(task.image)
        task = self.write_task(dict(self.data, description_image="", image="旧说明.png"))
        self.assertIsNone(task.image)
        image = QPixmap(160, 90)
        image.fill("green")
        image.save(str(self.folder / "说明.png"))
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.set_task(self.write_task())
        self.assertFalse(detail.preview.pixmap.isNull())
        detail.set_task(task)
        self.assertTrue(detail.preview.pixmap.isNull(), "切换到无图任务必须清除上一任务图片")
        self.assertTrue(detail.start_button.isEnabled())

    def test_default_run_and_directory_target(self):
        self.data.pop("run_target")
        self.assertEqual(self.write_task().run_target, self.folder / "run.py")
        self.data["run_target"] = "."
        self.assertEqual(self.write_task().run_target, self.folder / "run.py")

    def test_windows_relative_paths_and_workspace_relative_parent(self):
        nested = self.folder / "scripts"
        nested.mkdir()
        (nested / "run.py").write_text("", encoding="utf-8")
        self.data["run_target"] = "scripts" + chr(92) + "run.py"
        self.assertEqual(self.write_task().run_target, nested / "run.py")
        shared = self.workspace / "shared.py"
        shared.write_text("", encoding="utf-8")
        self.data["run_target"] = "../../shared.py"
        self.assertEqual(self.write_task().run_target, shared)

    def test_malformed_tasks_report_errors(self):
        for text in ("", "{", "[]", '{"title": 9}', '{"title": "x", "description": 12}'):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                self.assertTrue(launcher.load_task(self.path, self.workspace).error)
        self.data["run_target"] = "missing.py"
        self.assertTrue(self.write_task().error)

    def test_bad_title_does_not_break_mixed_task_list(self):
        valid = self.write_task()
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        invalid = []
        for title in (9, True, {}, ["bad"], None, "   "):
            data = dict(self.data, title=title)
            task = self.write_task(data)
            self.assertTrue(task.error)
            self.assertIsInstance(task.title, str)
            self.assertEqual(task.title, self.folder.name)
            invalid.append(task)
        page.populate(invalid + [valid])
        self.app.processEvents()
        self.assertEqual(page.list.count(), len(invalid) + 1)
        self.assertEqual(page.list.item(len(invalid)).data(launcher.Qt.ItemDataRole.UserRole), valid)
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.set_task(invalid[0])
        self.assertFalse(detail.start_button.isEnabled())

    def test_uv_environment_is_pinned_for_checks_and_operations(self):
        external = str(self.workspace.parent / "unrelated-environment")
        overrides = {"UV_PROJECT_ENVIRONMENT": external, "UV_TARGET": external,
                     "UV_PREFIX": external, "UV_PYTHON": "wrong-python",
                     "UV_WORKING_DIR": external, "UV_CONFIG_FILE": "wrong.toml",
                     "VIRTUAL_ENV": external, "PYTHONPATH": external,
                     "PYTHONHOME": external, "PIP_TARGET": external}
        with patch.dict(os.environ, overrides):
            env = launcher.workspace_environment(self.workspace)
            expected = str((self.workspace / ".venv").resolve())
            self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], expected)
            for name in overrides.keys() - {"UV_PROJECT_ENVIRONMENT"}:
                self.assertNotIn(name, env)
            with patch.object(launcher.subprocess, "run", return_value=
                              subprocess.CompletedProcess(["fake"], 0, "ok", "")) as run:
                self.assertEqual(launcher.command_result(["fake"], self.workspace), "ok")
                self.assertEqual(run.call_args.kwargs["env"]["UV_PROJECT_ENVIRONMENT"], expected)
                self.assertNotIn("UV_TARGET", run.call_args.kwargs["env"])
            owner = launcher.CheckPage(self.workspace)
            self.addCleanup(owner.close)
            with patch.object(launcher.CommandRunner, "_next"):
                owner.runner.start([["fake"]], self.workspace)
            process_env = owner.runner.process.processEnvironment()
            self.assertEqual(process_env.value("UV_PROJECT_ENVIRONMENT"), expected)
            self.assertFalse(process_env.contains("UV_TARGET"))
            self.assertFalse(process_env.contains("VIRTUAL_ENV"))
            self.assertIn(str(launcher.venv_python(self.workspace)), owner._mount_command("uv"))
            owner.runner.busy = False
        self.assertFalse(Path(external).exists())

    def test_absolute_and_escaping_task_paths_rejected(self):
        for target in ("../../../outside.py", "C:/Windows/python.py",
                       "/tmp/run.py", "//server/share/run.py"):
            with self.subTest(target=target):
                self.data["run_target"] = target
                self.assertTrue(self.write_task().error)

    def test_scan_only_task_directory_and_exact_manifest(self):
        (self.workspace / "this.task").write_text("{}", encoding="utf-8")
        (self.folder / "other.task").write_text("{}", encoding="utf-8")
        (self.folder / "this.is.task").write_text("{}", encoding="utf-8")
        tasks = launcher.scan_tasks(self.workspace, lambda *args: None, lambda: False)
        self.assertEqual([task.path for task in tasks], [self.path])
        self.assertEqual(launcher.scan_tasks(self.workspace, lambda *args: None, lambda: True), [])

    def test_recursive_categories_include_root_parent_and_deep_tasks(self):
        manifests = [self.path]
        # 父目录自身可以有任务，其子目录中的任务也不能漏掉。
        for relative in ("", "日常", "日常/采集/矿石/高优先级/路线一",
                         "周常/副本/困难/队伍一/第二阶段"):
            folder = self.workspace / "Task" / relative
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "run.py").write_text("print('not executed')", encoding="utf-8")
            manifest = folder / "this.task"
            manifest.write_text(json.dumps(dict(self.data, title="同名任务",
                run_target="run.py"), ensure_ascii=False), encoding="utf-8")
            manifests.append(manifest)
        outside = self.workspace / "日常" / "更深层"
        outside.mkdir(parents=True)
        (outside / "this.task").write_text("{}", encoding="utf-8")
        progress = []
        tasks = launcher.scan_tasks(self.workspace, lambda *args: progress.append(args), lambda: False)
        self.assertEqual({task.path for task in tasks}, set(manifests))
        self.assertTrue(all(not task.error for task in tasks))
        self.assertEqual(progress[-1][0], 100)
        for task in tasks:
            self.assertEqual(task.run_target, task.path.parent / "run.py")
        locations = [launcher.task_location(task, self.workspace) for task in tasks]
        self.assertIn("Task 根目录", locations)
        self.assertTrue(any("日常  ›  采集  ›  矿石  ›  高优先级  ›  路线一" == p for p in locations))
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        page.populate(tasks)
        self.assertEqual(page.list.count(), len(manifests))
        self.assertIs(page.content.currentWidget(), page.list)
        self.assertEqual(len(page.findChildren(launcher.CaptionLabel, "taskCategory")), len(manifests))
        page.search.setText("周常 困难")
        visible = [page.list.item(i) for i in range(page.list.count()) if not page.list.item(i).isHidden()]
        self.assertEqual(len(visible), 1)
        chosen = []
        page.selected.connect(chosen.append)
        page._select(visible[0])
        self.assertEqual(chosen[0].path, self.workspace / "Task/周常/副本/困难/队伍一/第二阶段/this.task")
        page.search.clear()
        self.assertFalse(any(page.list.item(i).isHidden() for i in range(page.list.count())))

    def test_compact_task_rows_do_not_repeat_task_directory(self):
        from dataclasses import replace
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        task = replace(self.write_task(), title=self.folder.name,
                       description="很长的任务描述，保持单行显示。" * 12)
        page.populate([task])
        item = page.list.item(0)
        row = page.list.itemWidget(item)
        category = row.findChild(launcher.CaptionLabel, "taskCategory")
        self.assertEqual(launcher.task_category(task, self.workspace), "")
        self.assertTrue(category.isHidden())
        self.assertEqual(category.text(), "")
        labels = row.findChildren(launcher.QLabel)
        self.assertEqual(sum(label.text() == task.title for label in labels), 1)
        self.assertLessEqual(item.sizeHint().height(), 64)
        desc = row.findChild(launcher.QLabel, "taskDescription")
        self.assertFalse(desc.wordWrap())
        self.assertEqual(desc.toolTip(), task.description)
        nested = replace(task, path=self.workspace / "Task/周常/副本/困难/电表倒转/this.task")
        self.assertEqual(launcher.task_category(nested, self.workspace), "周常  ›  副本  ›  困难")
        page.populate([nested])
        category = page.list.itemWidget(page.list.item(0)).findChild(launcher.CaptionLabel, "taskCategory")
        self.assertFalse(category.isHidden())
        self.assertNotIn("电表倒转", category.text())
        page.search.setText("电表倒转")
        self.assertFalse(page.list.item(0).isHidden(), "完整目录仍应可搜索")

    def test_compact_list_fits_at_least_six_rows_in_480px(self):
        from dataclasses import replace
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        task = self.write_task()
        page.populate([replace(task, title=f"任务 {i}") for i in range(12)])
        page.list.setFixedHeight(480)
        page.resize(1000, 720)
        page.show()
        self.app.processEvents()
        viewport = page.list.viewport().rect()
        fully_visible = [i for i in range(page.list.count())
                         if viewport.contains(page.list.visualItemRect(page.list.item(i)))]
        self.assertGreaterEqual(len(fully_visible), 6)
        self.assertLessEqual(page.list.itemWidget(page.list.item(0)).sizeHint().height(), 64)

    def test_task_page_empty_and_no_search_results_are_distinct(self):
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        page.populate([])
        self.assertIs(page.content.currentWidget(), page.empty_host)
        self.assertEqual(page.empty_title.text(), "还没有任务")
        self.assertIn("任意层级", page.empty_body.text())
        self.assertIn("周常 / 副本 / 困难", page.examples.text())
        self.assertFalse(page.examples.isHidden())
        self.assertFalse(page.open_folder_button.isHidden())
        self.assertNotIn(str(self.workspace), page.status.text())
        page.populate([self.write_task()])
        self.assertIs(page.content.currentWidget(), page.list)
        page.search.setText("不匹配的关键词")
        self.assertEqual(page.empty_title.text(), "没有匹配的任务")
        self.assertTrue(page.examples.isHidden())
        self.assertTrue(page.open_folder_button.isHidden())
        page.search.clear()
        self.assertIs(page.content.currentWidget(), page.list)
        page._failed("没有读取权限")
        self.assertEqual(page.empty_title.text(), "暂时无法读取任务")
        self.assertIn("没有读取权限", page.empty_body.text())
        page.search.setText("搜索不应覆盖错误信息")
        self.assertEqual(page.empty_title.text(), "暂时无法读取任务")

    def test_task_folder_action_does_not_create_or_scan_other_folders(self):
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        with patch.object(launcher.QDesktopServices, "openUrl", return_value=True) as opened:
            page.open_task_folder()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), self.workspace / "Task")
            missing = self.workspace / "尚未准备的工作区"
            missing.mkdir()
            page.workspace = missing
            page.populate([])
            self.assertEqual(page.open_folder_button.text(), "打开工作区")
            page.open_task_folder()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), missing)
            self.assertFalse((missing / "Task").exists())

    def test_packaged_workspace_not_extraction_directory(self):
        install = self.workspace / "dist"
        install.mkdir()
        extraction = self.workspace / "_bundle"
        extraction.mkdir()
        (install / "pyproject.toml").write_text("", encoding="utf-8")
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(install / "Launcher.exe")), \
             patch.object(sys, "_MEIPASS", str(extraction), create=True):
            self.assertEqual(launcher.workspace_path(), install)
            (extraction / "icon.ico").write_bytes(b"icon")
            self.assertEqual(launcher.resource_path("icon.ico", install), extraction / "icon.ico")
            (install / "icon.ico").write_bytes(b"override")
            self.assertEqual(launcher.resource_path("icon.ico", install), install / "icon.ico")

    def test_environment_checks_dependencies_and_editable_mount(self):
        for name in ("pyproject.toml", "uv.lock", "script_core/__init__.py"):
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        interpreter = launcher.venv_python(self.workspace)
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"fake")
        def execute(args, cwd, timeout=20):
            if args[0] == "fake-uv":
                return "uv 0.12.1" if "--version" in args else ""
            if "find_spec" in args[-1]:
                return json.dumps({"version": [3, 12, 0],
                                   "origin": str(self.workspace / "script_core/__init__.py")})
            return "3.12.0"
        with patch.object(launcher, "command_result", side_effect=execute) as command, \
             patch.object(launcher, "find_uv", return_value="fake-uv"), \
             patch.object(launcher, "find_bash", return_value="fake-bash"), \
             patch.object(launcher, "windows_build", return_value=20348), \
             patch.object(launcher, "is_admin", return_value=True):
            results = launcher.inspect_environment(self.workspace, lambda *args: None, lambda: False)
            self.assertTrue(next(item for item in results if item.key == "workspace").ok)
            self.assertTrue(any("--check" in call.args[0] for call in command.call_args_list))
            (self.workspace / "uv.lock").unlink()
            results = launcher.inspect_environment(self.workspace, lambda *args: None, lambda: False)
            self.assertFalse(next(item for item in results if item.key == "workspace").ok)

    def test_buttons_use_fluent_icons_not_application_icon(self):
        with patch.object(launcher.QApplication, "windowIcon",
                          side_effect=AssertionError("按钮不能复用应用图标")), \
             patch.object(launcher, "PushButton") as factory:
            for text, icon in launcher.BUTTON_ICONS.items():
                with self.subTest(text=text):
                    factory.reset_mock()
                    launcher.button(text, None, lambda: None)
                    factory.return_value.setIcon.assert_called_once_with(icon)
            factory.reset_mock()
            launcher.button("无图标按钮", None, lambda: None)
            factory.return_value.setIcon.assert_not_called()
        self.assertEqual(launcher.BUTTON_ICONS["运行 · F9"], launcher.FIF.PLAY)
        self.assertEqual(launcher.BUTTON_ICONS["返回"], launcher.FIF.LEFT_ARROW)

    def test_fluent_title_bar_steps_and_theme_rendering(self):
        from qfluentwidgets import FluentTitleBar
        from PySide6.QtTest import QTest
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        self.assertIsInstance(window, launcher.FluentWidget)
        self.assertIsInstance(window.titleBar, FluentTitleBar)
        self.assertFalse(hasattr(window, "breadcrumb"))
        steps = window.page_steps
        self.assertEqual(steps.current_index, 0)
        self.assertTrue(steps.testAttribute(launcher.Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertGreaterEqual(window.window_layout.contentsMargins().top(), window.titleBar.height())
        window.setMicaEffectEnabled(False)
        window.show()
        self.app.processEvents()
        self.assertTrue(window.titleBar.minBtn.isVisible())
        self.assertTrue(window.titleBar.maxBtn.isVisible())
        self.assertTrue(window.titleBar.closeBtn.isVisible())
        window.check_page.ready = True
        window.show_tasks()
        self.wait_until(lambda: window.task_page.job is None)
        self.assertEqual(steps.current_index, 1)
        self.assertEqual(window.titleBar.titleLabel.text(), window.windowTitle())
        window.show_detail(self.write_task())
        self.assertEqual(steps.current_index, 2)
        window._switch(3)
        window.run_page.busy = True
        window._navigation_state()
        self.assertFalse(window.back_button.isEnabled())
        self.assertFalse(window.navigate(2))
        QTest.mouseClick(steps, launcher.Qt.MouseButton.LeftButton, pos=steps.rect().center())
        self.assertEqual(window.current_index, 3, "步骤指示器不得允许越级跳转")
        self.assertEqual(steps.current_index, 3)
        window.run_page.busy = False
        window._navigation_state()
        self.assertTrue(window.navigate(2))
        self.assertEqual(steps.current_index, 2)
        theme = launcher.qconfig.theme
        self.addCleanup(lambda: launcher.setTheme(theme))
        for target in (launcher.Theme.LIGHT, launcher.Theme.DARK):
            launcher.setTheme(target)
            self.app.processEvents()
            for index in range(4):
                steps.set_current(index)
                image = steps.grab().toImage()
                self.assertFalse(image.isNull())
                self.assertIn(launcher.PAGE_TITLES[index], steps.accessibleName())
            self.assertFalse(window.grab().isNull())

    def test_task_row_border_renders_in_both_themes_and_keeps_clicks(self):
        from PySide6.QtTest import QTest
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        task = self.write_task()
        page.populate([task])
        page.resize(1000, 650)
        page.show()
        self.app.processEvents()
        item = page.list.item(0)
        row = page.list.itemWidget(item)
        self.assertIsInstance(row, launcher.TaskRowFrame)
        self.assertEqual(item.sizeHint().height(), 50)
        self.assertFalse(row.testAttribute(launcher.Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        theme = launcher.qconfig.theme
        self.addCleanup(lambda: launcher.setTheme(theme))
        for target, color in ((launcher.Theme.LIGHT, "#c5cbd3"),
                              (launcher.Theme.DARK, "#62666d")):
            launcher.setTheme(target)
            self.app.processEvents()
            image = row.grab().toImage()
            edge = image.pixelColor(image.width() // 2, 0)
            expected = launcher.QColor(color)
            for actual, wanted in zip(edge.getRgb()[:3], expected.getRgb()[:3]):
                self.assertLessEqual(abs(actual - wanted), 3, "任务条边框应清晰可见并随主题变化")
        selected = []
        page.selected.connect(selected.append)
        QTest.mouseClick(page.list.viewport(), launcher.Qt.MouseButton.LeftButton,
                         pos=page.list.visualItemRect(item).center())
        self.assertEqual(selected, [], "点击卡片空白处不进入详情")
        start = row.findChild(launcher.PrimaryPushButton, "taskStart")
        QTest.mouseClick(start, launcher.Qt.MouseButton.LeftButton)
        self.assertEqual(selected, [task])

    def test_task_row_has_no_leading_decoration_icon(self):
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        page.populate([self.write_task()])
        row = page.list.itemWidget(page.list.item(0))
        self.assertEqual(row.layout().itemAt(0).widget().objectName(), "taskTitle")
        self.assertEqual(row.findChildren(launcher.IconWidget), [], "不保留前置图标或展开箭头")
        self.assertEqual(len(row.findChildren(launcher.PushButton)), 1, "每个卡片仅有开始按钮")
        self.assertEqual(page.list.item(0).sizeHint().height(), 50)

    def test_task_start_enters_detail_without_running(self):
        from PySide6.QtTest import QTest
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        window.check_page.ready = True
        window.show_tasks()
        self.wait_until(lambda: window.task_page.job is None)
        window.show()
        self.app.processEvents()
        row = window.task_page.list.itemWidget(window.task_page.list.item(0))
        start = row.findChild(launcher.PrimaryPushButton, "taskStart")
        self.assertEqual(start.text(), "开始")
        with patch.object(window.run_page, "start") as run:
            QTest.mouseClick(start, launcher.Qt.MouseButton.LeftButton)
            self.assertEqual(window.current_index, 2)
            self.assertEqual(window.detail_page.task.title, self.data["title"])
            run.assert_not_called()
        self.assertFalse(window.run_page.busy)

    def test_task_card_single_line_and_start_button_stays_visible(self):
        from dataclasses import replace
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        task = replace(self.write_task(), title="很长的任务标题" * 30,
                       description="很长的描述" * 100, version="v1.2.3",
                       path=self.workspace / "Task/日常/分类/更细分类/任务/this.task")
        page.populate([task])
        page.resize(760, 620)
        page.show()
        self.app.processEvents()
        row = page.list.itemWidget(page.list.item(0))
        title = row.findChild(launcher.TaskInlineLabel, "taskTitle")
        desc = row.findChild(launcher.TaskInlineLabel, "taskDescription")
        start = row.findChild(launcher.PrimaryPushButton, "taskStart")
        self.assertLessEqual(abs(title.geometry().center().y() - desc.geometry().center().y()), 1)
        self.assertLessEqual(abs(desc.geometry().center().y() - start.geometry().center().y()), 1)
        self.assertTrue(row.rect().contains(start.geometry()), "长标题和描述不能挤掉开始按钮")
        self.assertEqual(title.toolTip(), task.title)
        self.assertEqual(desc.toolTip(), task.description)
        self.assertLessEqual(title.width(), 230)
        self.assertGreater(desc.width(), 30)
        widgets = [row.layout().itemAt(i).widget() for i in range(row.layout().count())]
        widgets = [widget for widget in widgets if widget.isVisible()]
        for left, right in zip(widgets, widgets[1:]):
            self.assertLess(left.geometry().right(), right.geometry().left(),
                            f"{left.objectName()} 不得与 {right.objectName()} 重叠")
        self.assertLessEqual(row.sizeHint().height(), 50)

    def test_checks_pass_stays_on_start_page_until_manual_click(self):
        from PySide6.QtTest import QTest
        results = [launcher.CheckResult(key, True, "通过") for key in
                   ("python", "uv", "bash", "workspace", "windows", "admin")]
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        page = window.check_page
        passed = []
        page.passed.connect(lambda: passed.append(True))
        self.assertFalse(page.continue_button.isEnabled())
        with patch.object(launcher, "inspect_environment", return_value=results):
            page.run_checks()
            self.assertFalse(page.continue_button.isEnabled())
            self.wait_until(lambda: page.job is None)
            self.app.processEvents()
            self.assertTrue(page.ready)
            self.assertEqual(window.current_index, 0, "检查通过不能自动离开启动页")
            self.assertEqual(passed, [])
            self.assertIsNone(window.task_page.job)
            self.assertTrue(page.continue_button.isEnabled())
            self.assertIn("进入任务", page.status.text())
            QTest.mouseClick(page.continue_button, launcher.Qt.MouseButton.LeftButton)
            self.assertEqual(window.current_index, 1)
            self.assertEqual(passed, [True])
            self.wait_until(lambda: window.task_page.job is None)
            window.go_back()
            self.wait_until(lambda: page.job is None)
            self.assertEqual(window.current_index, 0, "返回启动页重新检查后也不自动跳转")
            self.assertEqual(passed, [True])
            self.assertTrue(page.continue_button.isEnabled())

    def test_manual_continue_cannot_bypass_failed_or_busy_checks(self):
        results = [launcher.CheckResult(key, key != "admin", "检查结果") for key in
                   ("python", "uv", "bash", "workspace", "windows", "admin")]
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        page = window.check_page
        with patch.object(launcher, "inspect_environment", return_value=results):
            page.run_checks()
            self.wait_until(lambda: page.job is None)
        self.assertFalse(page.ready)
        self.assertFalse(page.continue_button.isEnabled())
        page.continue_to_tasks()
        self.assertEqual(window.current_index, 0)
        page.ready = True
        page.runner.busy = True
        page._controls()
        self.assertFalse(page.continue_button.isEnabled())
        page.continue_to_tasks()
        self.assertEqual(window.current_index, 0)
        page.runner.busy = False
        page._controls()
        self.assertTrue(page.continue_button.isEnabled())

    def test_stop_key_hint_says_task_defined_with_f12_default(self):
        expected = "停止任务键由任务文件指定，默认 F12"
        for registered in (True, False):
            with self.subTest(registered=registered), \
                 patch.object(launcher.GlobalF9, "register", return_value=registered):
                window = launcher.LauncherWindow(self.workspace, auto_start=False)
                self.addCleanup(window.close)
                self.assertIn(expected, window.hotkey_status.text())
                if registered:
                    self.assertIn("全局 F9 已注册", window.hotkey_status.text())
                else:
                    self.assertIn("仅窗口内 F9 可用", window.hotkey_status.text())
                labels = [label.text() for label in window.run_page.findChildren(launcher.QLabel)]
                self.assertTrue(any(expected in text for text in labels))
                self.assertFalse(window.run_page.busy)

    def test_gui_initialization_and_navigation_gate(self):
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        self.assertEqual(window.stackedWidget.count(), 4)
        self.assertFalse(window.navigate(1))
        self.assertFalse(window.navigate(2))
        window.stackedWidget.setCurrentIndex(3)
        self.assertEqual(window.stackedWidget.currentIndex(), 0)
        window.check_page.ready = True
        window.show_tasks()
        self.assertEqual(window.current_index, 1)
        self.wait_until(lambda: window.task_page.job is None)
        self.assertEqual(window.task_page.list.count(), 1)
        window.show_detail(self.write_task())
        self.assertEqual(window.current_index, 2)
        window.run_page.busy = True
        self.assertFalse(window.navigate(1))
        window.stackedWidget.setCurrentIndex(0)
        self.assertEqual(window.stackedWidget.currentIndex(), 2)
        window.run_page.busy = False
        self.assertTrue(window.navigate(1))
        self.wait_until(lambda: window.task_page.job is None)

    def test_missing_image_then_valid_image_resizes_without_crash(self):
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.set_task(self.write_task())
        self.assertTrue(detail.preview.pixmap.isNull())
        image = QPixmap(160, 90)
        image.fill("green")
        image.save(str(self.folder / "说明.png"))
        detail.set_task(self.write_task())
        detail.resize(1000, 600)
        detail.show()
        self.app.processEvents()
        self.assertFalse(detail.preview.pixmap.isNull())
        detail.resize(1100, 650)
        self.app.processEvents()

    def test_check_cards_group_results_and_follow_theme(self):
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        self.assertEqual(len(page.check_items), 7)
        self.assertEqual(launcher.GIT_URL, "https://git-scm.com/downloads")
        bash_check = page.check_items["bash"]
        self.assertEqual(bash_check.url, launcher.GIT_URL)
        self.assertEqual(len(bash_check.findChildren(launcher.TransparentPushButton, "officialLink")), 1)
        self.assertIs(page.check_items["python"].parentWidget(),
                      page.check_items["workspace"].parentWidget())
        self.assertIs(page.check_items["windows"].parentWidget(),
                      page.check_items["admin"].parentWidget())
        self.assertIsNot(page.check_items["python"].parentWidget(),
                         page.check_items["admin"].parentWidget())
        self.assertIs(page.check_items["seven_zip"], page.zip_check)
        self.assertEqual(page.zip_check.url, launcher.SEVEN_ZIP_URL)
        self.assertIsNot(page.check_items["seven_zip"].parentWidget(),
                         page.check_items["python"].parentWidget())
        page.job_kind = "checks"
        results = [launcher.CheckResult(key, key != "admin", "检查详情 " + key)
                   for key in page.check_items]
        page._result(results)
        self.assertFalse(page.ready)
        theme = launcher.qconfig.theme
        self.addCleanup(lambda: launcher.setTheme(theme))
        styles = []
        for target in (launcher.Theme.LIGHT, launcher.Theme.DARK):
            launcher.setTheme(target)
            self.app.processEvents()
            for result in results:
                check = page.check_items[result.key]
                self.assertEqual(check.detail.text(), result.detail)
                self.assertEqual(check.badge.text(), "已通过" if result.ok else "未通过")
            styles.append(page.check_items["admin"].badge.styleSheet())
        self.assertNotEqual(*styles)
        page.check_items["python"].set_state("checking")
        page._failed("模拟检查异常")
        self.assertEqual(page.check_items["python"].state, "failed")
        self.assertTrue(page.output_toggle.isChecked())

    def test_operation_output_collapses_and_expands_on_commands(self):
        from PySide6.QtTest import QTest
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        page.resize(900, 620)
        page.show()
        self.app.processEvents()
        self.assertTrue(page.console.isHidden())
        with patch.object(page.runner, "start") as start:
            page._start_commands([["fake-command"]])
            start.assert_called_once_with([["fake-command"]], self.workspace)
        self.app.processEvents()
        self.assertFalse(page.console.isHidden())
        self.assertTrue(page.output_toggle.isChecked())
        QTest.mouseClick(page.output_toggle, launcher.Qt.MouseButton.LeftButton)
        self.assertTrue(page.console.isHidden())
        self.assertFalse(page.output_toggle.isChecked())
        page._operation_done(False)

    def test_navigation_is_centered_and_footer_remains_visible(self):
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        self.assertIsInstance(window.back_button, launcher.TransparentPushButton)
        page = window.check_page
        page.rows["workspace"].setText("很长的依赖检查错误和路径 " * 100)
        for width, height in ((900, 620), (1180, 760)):
            window.resize(width, height)
            window.show()
            self.app.processEvents()
            steps_center = window.page_steps.mapTo(window, window.page_steps.rect().center())
            self.assertLessEqual(abs(steps_center.x() - window.rect().center().x()), 2)
            button_rect = page.continue_button.geometry()
            self.assertTrue(page.rect().contains(button_rect))
            self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)

    def test_image_preview_has_no_frame_and_preserves_image_ratio(self):
        class Host(launcher.QWidget):
            def paintEvent(self, event):
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor("#123456"))

        host = Host()
        self.addCleanup(host.close)
        host.resize(640, 360)
        preview = launcher.ImagePreview(host)
        preview.setGeometry(host.rect())
        host.show()
        for size in ((80, 240), (300, 80), (120, 120)):
            with self.subTest(size=size):
                pixmap = QPixmap(*size)
                pixmap.fill("#ff0000")
                path = self.folder / "ratio.png"
                pixmap.save(str(path))
                preview.set_image(path)
                self.app.processEvents()
                image = host.grab().toImage()
                self.assertEqual(image.pixelColor(0, 0).name(), "#123456")
                self.assertEqual(image.pixelColor(320, 1).name(),
                                 "#ff0000" if size[0] <= size[1] else "#123456")
                self.assertEqual(image.pixelColor(320, 180).name(), "#ff0000")
                self.assertEqual(image.pixelColor(1, 180).name(),
                                 "#ff0000" if size[0] > size[1] else "#123456")
        preview.set_image(None)
        self.app.processEvents()
        self.assertEqual(host.grab().toImage().pixelColor(320, 1).name(), "#123456")

    def test_image_preview_opens_zoom_dialog(self):
        path = self.folder / "放大.png"
        pixmap = QPixmap(160, 90)
        pixmap.fill("green")
        pixmap.save(str(path))
        preview = launcher.ImagePreview()
        self.addCleanup(preview.close)
        self.addCleanup(preview._close_dialog)
        preview.set_image(path)
        self.assertEqual(preview.toolTip(), "点击放大查看")
        dialog = preview.open_preview()
        self.assertIsNotNone(dialog)
        self.assertIsInstance(dialog, launcher.ImageDialog)
        self.assertTrue(dialog.isVisible())
        self.assertGreaterEqual(dialog.width(), 360)
        self.assertGreater(dialog.width(), pixmap.width())
        self.assertEqual(preview.dialog, dialog)
        dialog.close()
        self.app.processEvents()
        self.assertIsNone(preview.dialog)
        preview.set_image(None)
        self.assertIsNone(preview.open_preview())
        self.assertEqual(preview.toolTip(), "")

    @unittest.skipUnless(os.name == "nt", "Windows 桌面快捷方式")
    def test_desktop_shortcut_preserves_workspace_and_launcher_arguments(self):
        desktop = Path(self.temp.name) / "Desktop 中文 space"
        executable = Path(sys.executable).resolve()
        for frozen in (False, True):
            for has_icon in (False, True):
                with self.subTest(frozen=frozen, has_icon=has_icon):
                    icon = self.workspace / "icon.ico"
                    if has_icon:
                        icon.write_bytes(b"test icon")
                    elif icon.exists():
                        icon.unlink()
                    with patch("win32com.client.Dispatch") as dispatch, \
                         patch.object(sys, "frozen", frozen, create=True), \
                         patch.object(launcher.QStandardPaths, "writableLocation",
                                      return_value=str(desktop)), \
                         patch.object(launcher, "resource_path", return_value=icon):
                        result = launcher.create_desktop_shortcut(self.workspace)
                    shortcut = dispatch.return_value.CreateShortCut.return_value
                    expected = ["--workspace", str(self.workspace.resolve())]
                    if not frozen:
                        expected.insert(0, str(Path(launcher.__file__).resolve()))
                    self.assertEqual(result, desktop / "Auto Lumin.lnk")
                    dispatch.assert_called_once_with("WScript.Shell")
                    dispatch.return_value.CreateShortCut.assert_called_once_with(str(result))
                    self.assertEqual(shortcut.TargetPath, str(executable))
                    self.assertEqual(shortcut.Arguments, subprocess.list2cmdline(expected))
                    self.assertEqual(shortcut.WorkingDirectory, str(self.workspace.resolve()))
                    self.assertEqual(shortcut.IconLocation,
                                     f"{icon if has_icon else executable},0")
                    shortcut.Save.assert_called_once_with()
                    self.assertFalse(desktop.exists(), "mock 测试不得创建真实快捷方式")

    def test_shortcut_and_group_are_separate_cards(self):
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        shortcut_card = page.shortcut_button.parentWidget()
        group_card = page.group_button.parentWidget()
        self.assertIsNot(shortcut_card, group_card)
        self.assertIn("快捷方式", [label.text() for label in shortcut_card.findChildren(launcher.QLabel)])
        self.assertIn("脚本交流群", [label.text() for label in group_card.findChildren(launcher.QLabel)])
        self.assertNotIn("加群", " ".join(label.text() for label in shortcut_card.findChildren(launcher.QLabel)))
        self.assertTrue(shortcut_card.isVisibleTo(page))
        self.assertTrue(group_card.isVisibleTo(page))

    def test_start_page_shortcut_action_reports_success_and_failure(self):
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        target = Path(self.temp.name) / "Auto Lumin.lnk"
        with patch.object(launcher, "create_desktop_shortcut", return_value=target) as create:
            page.shortcut_button.click()
            create.assert_called_once_with(self.workspace)
            self.assertIn(str(target), page.status.text())
        with patch.object(launcher, "create_desktop_shortcut", side_effect=OSError("拒绝访问")):
            page.shortcut_button.click()
            self.assertIn("创建桌面快捷方式失败", page.status.text())
            self.assertIn("拒绝访问", page.status.text())

    def test_start_page_group_button_copies_exact_group_number(self):
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        clipboard = self.app.clipboard()
        previous = clipboard.text()
        self.addCleanup(lambda: clipboard.setText(previous))
        page.group_button.click()
        self.assertEqual(clipboard.text(), "1108948355")
        self.assertIn("已复制", page.status.text())

    def test_start_page_repository_release_and_background_links(self):
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        links = [
            (page.repository_button, "https://github.com/sueenthear/Auto-Lumin"),
            (page.release_button, "https://github.com/sueenthear/Auto-Lumin/releases/latest"),
            (page.background_button,
             "https://www.bettergi.com/tutorial/rdp.html#%E5%BC%80%E5%A7%8B%E5%AE%89%E8%A3%85"),
        ]
        for control, expected in links:
            with self.subTest(url=expected), \
                 patch.object(launcher.QDesktopServices, "openUrl", return_value=True) as opened:
                control.click()
                opened.assert_called_once()
                self.assertEqual(opened.call_args.args[0].toString(
                    launcher.QUrl.ComponentFormattingOption.FullyEncoded), expected)

    def test_task_modified_at_uses_manifest_mtime_including_invalid_tasks(self):
        timestamp = 1700000000
        os.utime(self.path, (timestamp, timestamp))
        self.assertEqual(launcher.load_task(self.path, self.workspace).modified_at, timestamp)
        self.path.write_text("invalid json", encoding="utf-8")
        os.utime(self.path, (timestamp, timestamp))
        invalid = launcher.load_task(self.path, self.workspace)
        self.assertTrue(invalid.error)
        self.assertEqual(invalid.modified_at, timestamp)
        self.path.unlink()
        missing = launcher.load_task(self.path, self.workspace)
        self.assertTrue(missing.error)
        self.assertEqual(missing.modified_at, 0.0)

    def test_task_sort_switches_between_scan_order_and_newest_manifest(self):
        for name, timestamp in (("a-old", 1700000000), ("b-new", 1700000200),
                                ("c-tie", 1700000200)):
            folder = self.workspace / "Task" / name
            folder.mkdir()
            (folder / "run.py").write_text("pass", encoding="utf-8")
            manifest = folder / "this.task"
            manifest.write_text(json.dumps(dict(self.data, title=name)), encoding="utf-8")
            os.utime(manifest, (timestamp, timestamp))
        os.utime(self.path, (1700000100, 1700000100))
        tasks = launcher.scan_tasks(self.workspace, lambda *args: None, lambda: False)
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        page.populate(tasks)
        def listed():
            return [page.list.item(i).data(launcher.Qt.ItemDataRole.UserRole).path
                    for i in range(page.list.count())]
        self.assertEqual(page.sort_box.currentIndex(), 0)
        self.assertEqual(listed(), [task.path for task in tasks])
        page.search.setText("b-new")
        page.sort_box.setCurrentIndex(1)
        expected = sorted(tasks, key=lambda task: (-task.modified_at, str(task.path).casefold()))
        self.assertEqual(listed(), [task.path for task in expected])
        visible = [page.list.item(i).data(launcher.Qt.ItemDataRole.UserRole).title
                   for i in range(page.list.count()) if not page.list.item(i).isHidden()]
        self.assertEqual(visible, ["b-new"])
        page.populate(tasks)
        self.assertEqual(listed(), [task.path for task in expected], "刷新应保留排序选择")
        page.search.clear()
        page.sort_box.setCurrentIndex(0)
        self.assertEqual(listed(), [task.path for task in tasks])

    def test_task_toolbar_guide_and_folder_actions(self):
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        with patch.object(launcher.QMessageBox, "information") as information:
            page.task_guide_button.click()
            information.assert_called_once_with(page, "任务提示指南",
                "将包含this.task的文件夹移入根目录的Task文件夹中即可，嵌套深度无关，可做分类。")
        with patch.object(launcher.QDesktopServices, "openUrl", return_value=True) as opened:
            page.task_folder_button.click()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), self.workspace / "Task")
        with patch.object(launcher.QDesktopServices, "openUrl", return_value=False):
            page.task_folder_button.click()
            self.assertIn("无法打开目录", page.status.text())

    def test_hidden_log_option_passes_from_detail_and_survives_restart(self):
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        window.check_page.ready = True
        task = self.write_task()
        window.detail_page.set_task(task)
        window.detail_page.suppress_log.setChecked(True)
        window._switch(2)
        with patch.object(launcher, "task_command", return_value=["fake-bash"]), \
             patch.object(window.run_page.process, "start") as start:
            window.run_selected()
            self.assertEqual(window.current_index, 3)
            self.assertTrue(window.run_page.suppress_log)
            window.run_page._stop(0)
            window.detail_page.suppress_log.setChecked(False)
            window.run_page.restart_requested.emit()
            self.assertTrue(window.run_page.suppress_log, "重启沿用本次运行选项")
            self.assertTrue(window.run_page.log_panel.isHidden())
            self.assertEqual(start.call_count, 2)
            window.run_page._stop(0)

    def test_start_and_task_controls_fit_minimum_window_in_both_themes(self):
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        theme = launcher.qconfig.theme
        self.addCleanup(lambda: launcher.setTheme(theme))
        window.resize(900, 620)
        window.show()
        for target in (launcher.Theme.LIGHT, launcher.Theme.DARK):
            launcher.setTheme(target)
            for index in (0, 1):
                window._switch(index)
                page = window.pages[index]
                if index == 0:
                    groups = [(page.shortcut_button,),
                              (page.group_button,),
                              (page.repository_button, page.release_button, page.background_button)]
                else:
                    page.populate([self.write_task()])
                    page.sort_box.setCurrentIndex(1)
                    groups = [(page.task_guide_button, page.task_folder_button, page.refresh_button),
                              (page.search, page.sort_box, page.status)]
                self.app.processEvents()
                with self.subTest(theme=target, page=index):
                    self.assertEqual(window.size(), launcher.QSize(900, 620))
                    for controls in groups:
                        for control in controls:
                            self.assertTrue(control.parentWidget().rect().contains(control.geometry()))
                            if isinstance(control, (launcher.PushButton, launcher.ComboBox)):
                                self.assertGreaterEqual(control.width(), control.sizeHint().width())
                        for left, right in zip(controls, controls[1:]):
                            self.assertLess(left.geometry().right(), right.geometry().left())
                    if index == 0:
                        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
                        self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
                        self.assertTrue(page.rect().contains(page.continue_button.geometry()))
                    self.assertFalse(window.grab().isNull())

    @unittest.skipUnless(os.name == "nt", "Windows ShellExecuteW")
    def test_launcher_relaunches_with_admin_rights_only_when_needed(self):
        with patch.object(launcher, "is_admin", return_value=True), \
             patch.object(launcher.ctypes, "windll") as windll:
            self.assertFalse(launcher.relaunch_as_admin(["--workspace", "x"]))
            windll.shell32.ShellExecuteW.assert_not_called()
        workspace = str(self.workspace)
        for frozen in (False, True):
            with self.subTest(frozen=frozen), \
                 patch.object(launcher, "is_admin", return_value=False), \
                 patch.object(launcher.ctypes, "windll") as windll, \
                 patch.object(sys, "argv", ["--workspace", "ignored"]), \
                 patch.object(sys, "frozen", frozen, create=True):
                windll.shell32.ShellExecuteW.return_value = 33
                self.assertTrue(launcher.relaunch_as_admin(["--workspace", workspace]))
            call = windll.shell32.ShellExecuteW.call_args.args
            self.assertEqual(call[1], "runas")
            self.assertEqual(call[3], subprocess.list2cmdline(["--workspace", workspace]))
            self.assertEqual(call[5], 1)
            if frozen:
                self.assertEqual(call[2], str(sys.executable))
            else:
                self.assertEqual(call[2], str(Path(launcher.__file__).resolve()))
            self.assertFalse(workspace in call[2], call[2])
        with patch.object(launcher, "is_admin", return_value=False), \
             patch.object(launcher.ctypes, "windll") as windll:
            windll.shell32.ShellExecuteW.return_value = 5
            self.assertFalse(launcher.relaunch_as_admin([]), "用户取消 UAC 时原进程应退出")

    def test_main_relaunches_with_admin_and_does_not_open_window_first(self):
        def run(code):
            with patch.object(launcher, "relaunch_as_admin", return_value=code) as relaunch, \
                 patch.object(launcher, "QApplication") as app, \
                 patch.object(launcher, "LauncherWindow") as window:
                app.return_value.exec.return_value = 0
                try:
                    result = launcher.main(["--workspace", str(self.workspace)])
                finally:
                    relaunch.assert_called_once_with(["--workspace", str(self.workspace)])
                return result, app
        result, app = run(True)
        self.assertEqual(result, 0)
        app.assert_not_called()
        result, app = run(False)
        self.assertEqual(result, 0)
        app.assert_called_once()

    def test_window_geometry_ini_lives_in_workspace_and_restores(self):
        self.assertEqual(launcher.launcher_settings_path(self.workspace),
                         self.workspace / "launcher_set.ini")
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        settings_path = self.workspace / "launcher_set.ini"
        self.assertTrue(settings_path.is_file())
        window.resize(1040, 700)
        window.move(73, 61)
        window.show()
        self.app.processEvents()
        expected_size = window.size()
        expected_position = window.pos()
        self.assertTrue(window.close())
        settings = launcher.QSettings(
            str(settings_path), launcher.QSettings.Format.IniFormat)
        self.assertTrue(settings.value("window/geometry"))
        self.assertEqual(settings.value("window/size"), expected_size)
        self.assertEqual(settings.value("window/position"), expected_position)
        restored = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(restored.close)
        self.assertEqual(restored.size(), expected_size)
        self.assertEqual(restored.pos(), expected_position)
        # 切换到其他工作区后必须使用该工作区自己的配置，不能残留旧窗口状态。
        other = Path(self.temp.name) / "other workspace"
        other.mkdir()
        self.assertEqual(launcher.launcher_settings_path(other), other / "launcher_set.ini")
        self.assertNotEqual(launcher.launcher_settings_path(other), settings_path)
        switched = launcher.LauncherWindow(other, auto_start=False, register_hotkey=False)
        self.addCleanup(switched.close)
        self.assertTrue((other / "launcher_set.ini").is_file())
        self.assertEqual(switched.size(), launcher.QSize(1180, 760))

    def test_run_page_can_hide_development_log_panel(self):
        task = self.write_task()
        page = launcher.RunPage(self.workspace)
        self.addCleanup(page.close)
        with patch.object(launcher, "task_command", return_value=["fake-bash"]), \
             patch.object(page.process, "start") as start:
            self.assertTrue(page.start(task, suppress_log=True))
            self.assertTrue(page.suppress_log)
            self.assertTrue(page.log_panel.isHidden())
            self.assertFalse(page.info_panel.isHidden())
            start.assert_called_once_with("fake-bash", [])
            page._route("[2026-10-20 12:34:56.123][来源：do.test]<开发日志>")
            page._route("普通运行信息")
            self.assertNotIn("开发日志", page.log.toPlainText())
            self.assertIn("普通运行信息", page.info.toPlainText())
            page.busy = False
            self.assertTrue(page.start(task, suppress_log=False))
            self.assertFalse(page.suppress_log)
            self.assertFalse(page.log_panel.isHidden())
            page.busy = False

    def test_log_pattern_does_not_route_unstructured_output(self):
        self.assertTrue(launcher.LOG_PATTERN.fullmatch("[2026-10-20 12:34:56.123][来源：do.mouse.click]<中文>"))
        for value in ("normal", "[not time][来源：do.test]<x>",
                      "[2026-10-20 12:34:56][来源：other]<x>", "<b>plain</b>"):
            self.assertFalse(launcher.LOG_PATTERN.fullmatch(value))

    @unittest.skipUnless(os.name == "nt", "Windows HDROP")
    def test_legacy_drop_query_paths(self):
        import ctypes
        from ctypes import wintypes

        class DropFiles(ctypes.Structure):
            _fields_ = [
                ("pFiles", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL),
                ("fWide", wintypes.BOOL),
            ]

        text = "C:\\拖放\\任务包.zip\0C:\\拖放\\另一个.rar\0\0"
        data = text.encode("utf-16-le")
        header = DropFiles()
        header.pFiles = ctypes.sizeof(DropFiles)
        header.fWide = True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
        handle = kernel32.GlobalAlloc(
            0x0042, ctypes.sizeof(DropFiles) + len(data))
        self.assertTrue(handle)
        self.addCleanup(kernel32.GlobalFree, handle)
        address = kernel32.GlobalLock(handle)
        self.assertTrue(address)
        ctypes.memmove(address, ctypes.byref(header), ctypes.sizeof(header))
        ctypes.memmove(address + ctypes.sizeof(header), data, len(data))
        kernel32.GlobalUnlock(handle)
        self.assertEqual(
            launcher.LegacyFileDrop._query_paths(handle),
            [Path("C:\\拖放\\任务包.zip"), Path("C:\\拖放\\另一个.rar")],
        )

    def test_legacy_drop_routes_archives_only_on_task_page(self):
        (self.workspace / "7zr.exe").write_bytes(b"fake")
        window = launcher.LauncherWindow(self.workspace, auto_start=False,
                                         register_hotkey=False)
        self.addCleanup(window.close)
        window.check_page.ready = True
        window.show_tasks()
        self.wait_until(lambda: window.task_page.job is None)
        archive = self.make_package("旧式拖放")
        with patch.object(window.task_page, "choose_package") as choose:
            window._legacy_files_dropped([archive])
            choose.assert_called_once_with(archive)
        with patch.object(window.task_page, "choose_package") as choose:
            window._legacy_files_dropped([self.workspace / "说明.txt"])
            choose.assert_not_called()
        self.assertIn("只支持", window.task_page.import_status.text())

    @unittest.skipUnless(os.name == "nt", "Windows RegisterHotKey")
    def test_real_global_hotkey_register_dispatch_and_unregister(self):
        callbacks = []
        hotkey = launcher.GlobalF9(lambda: callbacks.append("F9"))
        if not hotkey.register():
            if "1409" in hotkey.error:
                self.skipTest("全局 F9 已被其他进程占用（例如启动器正在运行）")
            self.fail(hotkey.error)
        self.addCleanup(hotkey.close)
        from ctypes import wintypes
        message = wintypes.MSG()
        message.message = 0x0312
        message.wParam = hotkey.HOTKEY_ID
        self.assertEqual(hotkey.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)), (True, 0))
        self.assertEqual(callbacks, ["F9"])
        hotkey.close()
        self.assertFalse(hotkey.registered)
        self.assertTrue(hotkey.register(), hotkey.error)

    @unittest.skipUnless(launcher.find_bash(), "需要 Git Bash")
    def test_real_bash_activation_output_exit_and_restart(self):
        # 只在临时目录创建无依赖 venv，不触碰仓库 .venv。
        venv.create(self.workspace / ".venv", with_pip=False)
        script = (
            "import os, sys, time\n"
            "print('ARGS=' + repr(sys.argv[1:]), flush=True)\n"
            "print('ACTIVE=' + os.environ.get('VIRTUAL_ENV', ''), flush=True)\n"
            "print('CWD=' + os.getcwd(), flush=True)\n"
            "print('[2026-10-20 12:34:56.123][来源：do.test]<中文日志>', flush=True)\n"
            "print('<b>不是 HTML</b>', flush=True)\n"
            "print('stderr 中文', file=sys.stderr, flush=True)\n"
            "time.sleep(0.2)\n"
            "for i in range(350): print('line-' + str(i), flush=True)\n"
            "sys.stdout.write('尾行没有换行'); sys.stdout.flush()\n"
            "os._exit(0)\n"
        )
        (self.folder / "run.py").write_text(script, encoding="utf-8")
        window = launcher.LauncherWindow(self.workspace, auto_start=False, register_hotkey=False)
        self.addCleanup(window.close)
        self.addCleanup(window.run_page.cleanup)
        window.check_page.ready = True
        window.show_tasks()
        self.wait_until(lambda: window.task_page.job is None)
        window.show_detail(self.write_task())
        finished = []
        window.run_page.finished.connect(finished.append)
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(1))
        heartbeat.start()
        self.addCleanup(heartbeat.stop)
        window.run_selected()
        self.assertEqual(window.current_index, 3)
        self.assertTrue(window.run_page.busy)
        self.assertFalse(window.back_button.isEnabled())
        self.assertFalse(window.navigate(2))
        window.run_selected()
        self.wait_until(lambda: len(finished) == 1, 20)
        self.assertEqual(finished, [0], window.run_page.info.toPlainText())
        log, info = window.run_page.log.toPlainText(), window.run_page.info.toPlainText()
        self.assertIn("中文日志", log)
        self.assertNotIn("中文日志", info)
        self.assertIn("<b>不是 HTML</b>", info)
        self.assertIn("stderr 中文", info)
        self.assertIn("line-349", info)
        self.assertIn("尾行没有换行", info)
        self.assertIn("ACTIVE=", info)
        self.assertIn("ARGS=['--this-task'", info)
        self.assertIn(str(self.workspace).replace(chr(92), '/').lower(), info.replace(chr(92), '/').lower())
        self.assertGreater(len(ticks), 3, "运行时界面事件循环不能阻塞")
        self.assertTrue(window.run_page.ring.isHidden())
        self.assertFalse(window.run_page.stopped_indicator.isHidden())
        self.assertTrue(window.back_button.isEnabled())
        window.run_selected()
        self.wait_until(lambda: len(finished) == 2, 20)
        self.assertEqual(finished, [0, 0])
        self.assertTrue(window.navigate(2))
        self.assertFalse(window.navigate(0))

    def variable_data(self):
        return dict(self.data, var={
            "value": {"times": 50, "speed": 1.5, "name": "默认", "debug": True},
            "type": {"times": "int", "speed": "float",
                     "name": "string", "debug": "bool"},
            "description": {"times": "执行次数", "speed": "速度倍率",
                            "name": "角色名", "debug": "调试模式"},
        })

    def make_package(self, name="我的任务", *, with_image=True, with_run=True,
                     manifest=None):
        archive_path = self.workspace.parent / f"{name}.zip"
        data = manifest or dict(self.data, title=name, description="包内任务")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(f"{name}/this.task",
                             json.dumps(data, ensure_ascii=False))
            if with_run:
                archive.writestr(f"{name}/run.py", "print('packaged')")
            if with_image:
                archive.writestr(f"{name}/说明.png", b"png")
        return archive_path

    def test_task_variables_are_loaded_for_the_detail_ui(self):
        task = self.write_task(self.variable_data())
        self.assertEqual(task.error, "")
        self.assertEqual([item.name for item in task.variables],
                         ["times", "speed", "name", "debug"])
        self.assertEqual([item.type for item in task.variables],
                         ["int", "float", "string", "bool"])
        self.assertEqual(task.variables[0].description, "执行次数")
        self.assertEqual(task.variables[1].value, 1.5)
        data = self.variable_data()
        data["var"]["value"]["speed"] = 2
        task = self.write_task(data)
        self.assertEqual(task.variables[1].value, 2.0)
        self.assertIsInstance(task.variables[1].value, float)

    def test_invalid_task_variables_report_errors(self):
        cases = (
            {"value": {"x": "1"}, "type": {"x": "int"}},
            {"value": {"x": 1}, "type": {"x": "string"}},
            {"value": {"x": True}, "type": {"x": "int"}},
            {"value": {"x": None}},
            {"value": [], "type": {}},
            {"value": {}, "type": []},
            {"value": {"x": 1}, "description": {"x": 1}},
            {"value": {"x": 1}, "type": {"x": "list"}},
        )
        for var in cases:
            with self.subTest(var=var):
                task = self.write_task(dict(self.data, var=var))
                self.assertTrue(task.error)
                self.assertEqual(task.variables, ())

    def test_detail_page_collapsed_cards_and_variable_editing(self):
        timestamp = 1700000000
        self.write_task(self.variable_data())
        os.utime(self.path, (timestamp, timestamp))
        task = launcher.load_task(self.path, self.workspace)
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.set_task(task)
        self.assertTrue(detail.description_card.expanded)
        self.assertFalse(detail.description_card.body.isHidden())
        for card in (detail.config_card, detail.version_card):
            self.assertFalse(card.expanded)
            self.assertTrue(card.body.isHidden())
        self.assertEqual(set(detail.variable_editors),
                         {"times", "speed", "name", "debug"})
        self.assertIsInstance(detail.variable_editors["times"].editor,
                              launcher.SpinBox)
        self.assertIsInstance(detail.variable_editors["speed"].editor,
                              launcher.DoubleSpinBox)
        self.assertIsInstance(detail.variable_editors["name"].editor,
                              launcher.LineEdit)
        self.assertIsInstance(detail.variable_editors["debug"].editor,
                              launcher.SwitchButton)
        self.assertEqual(detail.fields["built"].text(),
                         launcher.format_build_time(timestamp))
        self.assertNotEqual(detail.fields["built"].text(), "未知")
        detail.variable_editors["times"].editor.setValue(12)
        detail.variable_editors["speed"].editor.setValue(2.25)
        detail.variable_editors["name"].editor.setText("新名字")
        detail.variable_editors["debug"].editor.setChecked(False)
        self.assertTrue(detail.apply_variables())
        stored = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self.assertEqual(stored["var"]["value"], {
            "times": 12, "speed": 2.25, "name": "新名字", "debug": False,
        })
        self.assertEqual(stored["var"]["type"]["times"], "int")
        self.assertEqual(detail.task.variables[0].value, 12)
        detail.config_card.set_expanded(True)
        self.assertTrue(detail.config_card.expanded)
        self.assertFalse(detail.config_card.body.isHidden())
        detail.config_card.set_expanded(False)
        self.assertTrue(detail.config_card.body.isHidden())

    def test_numeric_variable_editors_show_their_values(self):
        task = self.write_task(self.variable_data())
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.resize(900, 620)
        detail.set_task(task)
        detail.config_card.set_expanded(True)
        detail.show()
        self.app.processEvents()
        times = detail.variable_editors["times"].editor
        speed = detail.variable_editors["speed"].editor
        self.assertIsInstance(times, launcher.FluentSpinBox)
        self.assertIsInstance(speed, launcher.FluentDoubleSpinBox)
        self.assertEqual(times.value(), 50)
        self.assertEqual(speed.value(), 1.5)
        self.assertEqual(times.lineEdit().text(), "50")
        self.assertTrue(speed.lineEdit().text())
        self.assertGreaterEqual(times.lineEdit().width(), 40,
                                "times 的数值文本区域不能塌缩")
        self.assertGreaterEqual(speed.lineEdit().width(), 40,
                                "speed 的数值文本区域不能塌缩")
        debug = detail.variable_editors["debug"].editor
        self.assertIsInstance(debug, launcher.SwitchButton)
        self.assertLessEqual(debug.width(), 80)
        self.assertGreaterEqual(
            debug.geometry().right(),
            detail.variable_editors["debug"].width() - 2,
            "开关必须贴住配置行右侧")

    def test_task_config_path_save_and_reset(self):
        task = self.write_task(self.variable_data())
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.set_task(task)
        self.assertIn(str(self.path.resolve()), detail.config_path.text())
        self.assertTrue(detail.save_button.isEnabled())
        self.assertTrue(detail.reset_button.isEnabled())

        detail.variable_editors["times"].editor.setValue(9)
        self.assertTrue(detail.save_variables())
        self.assertIn("已保存", detail.config_status.text())
        stored = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self.assertEqual(stored["var"]["value"]["times"], 9)

        detail.variable_editors["times"].editor.setValue(3)
        self.assertTrue(detail.reset_variables())
        self.assertEqual(detail.variable_editors["times"].editor.value(), 9)
        self.assertIn("已重置", detail.config_status.text())

        detail.set_task(self.write_task(dict(self.data)))
        self.assertIn(str(self.path.resolve()), detail.config_path.text())
        self.assertFalse(detail.save_button.isEnabled())
        self.assertFalse(detail.reset_button.isEnabled())

    def test_variable_save_failure_blocks_launch(self):
        task = self.write_task(self.variable_data())
        detail = launcher.DetailPage()
        self.addCleanup(detail.close)
        detail.set_task(task)
        with patch.object(launcher, "save_task_variables", side_effect=OSError("拒绝访问")):
            self.assertFalse(detail.apply_variables())
        self.assertIn("无法保存任务配置", detail.error.text())
        self.assertIn("拒绝访问", detail.error.text())

    def test_task_command_passes_this_task_argument(self):
        interpreter = launcher.venv_python(self.workspace)
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"fake")
        task = self.write_task(self.variable_data())
        command = launcher.task_command(self.workspace, task, bash="fake-bash")
        self.assertIn("--this-task", command[4])
        self.assertEqual(command[6], str(self.workspace.resolve()))
        self.assertEqual(command[-1], str(self.path.resolve()))

    def test_seven_zip_detection_and_full_install(self):
        self.assertIsNone(launcher.find_7zip(self.workspace))
        for name in reversed(launcher.SEVEN_ZIP_NAMES):
            path = self.workspace / name
            path.write_bytes(b"fake")
            if name == "7z.exe":
                (self.workspace / "7z.dll").write_bytes(b"fake")
            self.assertEqual(launcher.find_7zip(self.workspace), path)
            self.assertEqual(launcher.supports_rar(path), name == "7z.exe")
            path.unlink()
            if name == "7z.exe":
                (self.workspace / "7z.dll").unlink()
        (self.workspace / "7z.exe").write_bytes(b"fake")
        self.assertIsNone(launcher.find_7zip(self.workspace),
                          "缺少 7z.dll 的 7z.exe 不能使用")
        (self.workspace / "7z.dll").write_bytes(b"fake")
        self.assertEqual(launcher.find_7zip(self.workspace), self.workspace / "7z.exe")
        (self.workspace / "7z.exe").unlink()
        (self.workspace / "7z.dll").unlink()
        msi = launcher.SEVEN_ZIP_MSI_MAGIC + b"0" * launcher.SEVEN_ZIP_MSI_MIN_BYTES

        def fake_extract(_msi, destination):
            root = Path(destination) / "Files" / "7-Zip"
            root.mkdir(parents=True)
            (root / "7z.exe").write_bytes(b"fake 7z")
            (root / "7z.dll").write_bytes(b"fake dll")

        with patch.object(launcher, "seven_zip_msi_url",
                          return_value="https://example.test/7z.msi"), \
             patch.object(launcher, "_urlopen_bytes", return_value=msi) as download, \
             patch.object(launcher, "_extract_msi", side_effect=fake_extract) as extract:
            target = launcher.install_7zip(
                self.workspace, lambda *args: None, lambda: False)
        self.assertEqual(target, self.workspace / "7z.exe")
        self.assertTrue(target.is_file())
        self.assertTrue((self.workspace / "7z.dll").is_file())
        self.assertEqual(launcher.find_7zip(self.workspace), target)
        self.assertTrue(launcher.supports_rar(target))
        download.assert_called_once()
        extract.assert_called_once()
        with patch.object(launcher, "seven_zip_msi_url") as msi_url, \
             patch.object(launcher, "_urlopen_bytes") as download:
            self.assertEqual(launcher.install_7zip(
                self.workspace, lambda *args: None, lambda: False), target)
            msi_url.assert_not_called()
            download.assert_not_called()
        (self.workspace / "7z.exe").unlink()
        (self.workspace / "7z.dll").unlink()
        with patch.object(launcher, "seven_zip_msi_url", return_value=None):
            with self.assertRaises(RuntimeError):
                launcher.install_7zip(
                    self.workspace, lambda *args: None, lambda: False)
        with patch.object(launcher, "seven_zip_msi_url",
                          return_value="https://example.test/7z.msi"), \
             patch.object(launcher, "_urlopen_bytes", return_value=b"<html>error</html>"):
            with self.assertRaises(RuntimeError):
                launcher.install_7zip(
                    self.workspace, lambda *args: None, lambda: False)

    def test_seven_zip_msi_url_prefers_current_release(self):
        html = ('<a href="https://github.com/ip7z/7zip/releases/download/26.05/'
                '7z2605-x64.msi">x</a>')
        with patch.object(launcher, "_urlopen_bytes", return_value=html.encode()):
            self.assertEqual(
                launcher.seven_zip_msi_url(),
                "https://github.com/ip7z/7zip/releases/download/26.05/7z2605-x64.msi")
        html = '<a href="a/7z2501-x64.msi">x</a>'
        with patch.object(launcher, "_urlopen_bytes", return_value=html.encode()):
            self.assertEqual(launcher.seven_zip_msi_url(),
                             "https://www.7-zip.org/a/7z2501-x64.msi")

    def test_extract_msi_passes_target_directory(self):
        destination = self.workspace / "target with space"
        with patch.object(launcher.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            launcher._extract_msi(self.workspace / "7z.msi", destination)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "msiexec")
        self.assertIn("/a", command)
        self.assertIn(f"TARGETDIR={destination}", command)
        self.assertTrue(destination.is_dir())

    def test_optional_seven_zip_check_does_not_block_or_confuse_required_checks(self):
        (self.workspace / "7zr.exe").write_bytes(b"fake")
        page = launcher.CheckPage(self.workspace)
        self.addCleanup(page.close)
        required = [launcher.CheckResult(key, True, "通过")
                    for key in launcher.REQUIRED_CHECKS]
        page.job_kind = "checks"
        page._result(required + [launcher.CheckResult("seven_zip", True, "精简版")])
        self.assertTrue(page.ready, "7-Zip 是可选检查")
        self.assertEqual(page.zip_check.state, "passed")
        self.assertFalse(page.install_zip_button.isHidden())
        self.assertIn("完整版", page.install_zip_button.text())

        def install(workspace, _progress, _cancelled):
            target = workspace / "7z.exe"
            target.write_bytes(b"fake")
            (workspace / "7z.dll").write_bytes(b"fake")
            return target

        with patch.object(launcher, "install_7zip", side_effect=install) as installer:
            page.install_zip()
            self.wait_until(lambda: page.job is None)
        installer.assert_called_once()
        self.assertEqual(page.zip_check.state, "passed")
        self.assertTrue(page.install_zip_button.isHidden())
        self.assertTrue((self.workspace / "7z.exe").is_file())

    def test_zip_task_package_is_validated_and_imported(self):
        archive_path = self.make_package()
        package = launcher.inspect_task_package(
            archive_path, None, lambda *args: None, lambda: False)
        self.addCleanup(shutil.rmtree, package.temp, True)
        self.assertEqual(package.error, "")
        self.assertEqual(package.title, "我的任务")
        self.assertEqual(package.name, "我的任务")
        self.assertEqual(package.files, 3)
        target = launcher.import_task_package(
            package, self.workspace, lambda *args: None, lambda: False)
        self.assertEqual(target, self.workspace / "Task" / "未分类" / "我的任务")
        self.assertTrue((target / "this.task").is_file())
        self.assertTrue((target / "run.py").is_file())
        self.assertTrue((target / "说明.png").is_file())
        second = launcher.import_task_package(
            package, self.workspace, lambda *args: None, lambda: False)
        self.assertEqual(second.name, "我的任务 (2)")

    def test_bad_task_packages_report_errors(self):
        def package(name, entries):
            path = self.workspace.parent / f"{name}.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for entry, content in entries.items():
                    archive.writestr(entry, content)
            return path

        manifest = json.dumps(dict(self.data), ensure_ascii=False)
        cases = (
            (package("空包", {"readme.txt": "x"}), "没有 this.task"),
            (package("坏运行", {
                "任务/this.task": json.dumps(dict(self.data, run_target="missing.py"),
                                             ensure_ascii=False),
                "任务/run.py": "",
            }), "找不到 Python 运行文件"),
            (package("缺图", {
                "任务/this.task": manifest, "任务/run.py": "",
            }), "描述图片缺失"),
            (package("多任务", {
                "a/this.task": manifest, "a/run.py": "",
                "b/this.task": manifest, "b/run.py": "",
            }), "多个 this.task"),
            (package("越界", {
                "../evil.txt": "x", "任务/this.task": manifest, "任务/run.py": "",
            }), "越界"),
        )
        for path, message in cases:
            with self.subTest(path=path.name):
                result = launcher.inspect_task_package(
                    path, None, lambda *args: None, lambda: False)
                self.assertIn(message, result.error)
                self.assertIsNone(result.temp)
                self.assertIsNone(result.root)
        missing = launcher.inspect_task_package(
            self.workspace.parent / "不存在.zip", None,
            lambda *args: None, lambda: False)
        self.assertIn("任务包不存在", missing.error)
        seven_path = self.workspace.parent / "包.7z"
        seven_path.write_bytes(b"7z")
        seven = launcher.inspect_task_package(
            seven_path, None, lambda *args: None, lambda: False)
        self.assertIn("未安装 7-Zip", seven.error)
        rar_path = self.workspace.parent / "包.rar"
        rar_path.write_bytes(b"Rar!")
        rar = launcher.inspect_task_package(
            rar_path, self.workspace / "7zr.exe", lambda *args: None, lambda: False)
        self.assertIn("不支持 RAR", rar.error)

    def test_7z_package_uses_workspace_executable(self):
        executable = self.workspace / "7zr.exe"
        executable.write_bytes(b"fake")
        archive_path = self.workspace.parent / "包.7z"
        archive_path.write_bytes(b"7z")

        def fake_run(args, **_kwargs):
            destination = Path(args[3][2:])
            root = destination / "七z任务"
            root.mkdir(parents=True)
            (root / "this.task").write_text(
                json.dumps(dict(self.data), ensure_ascii=False), encoding="utf-8")
            (root / "run.py").write_text("", encoding="utf-8")
            (root / "说明.png").write_bytes(b"png")
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(launcher.subprocess, "run", side_effect=fake_run) as run:
            package = launcher.inspect_task_package(
                archive_path, executable, lambda *args: None, lambda: False)
        self.addCleanup(shutil.rmtree, package.temp, True)
        self.assertEqual(package.error, "")
        self.assertEqual(package.title, "中文任务")
        self.assertEqual(package.files, 3)
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(executable))
        self.assertEqual(command[1:3], ["x", "-y"])
        self.assertTrue(command[3].startswith("-o"))
        self.assertEqual(command[4], str(archive_path))

    def test_rar_package_uses_full_7zip(self):
        executable = self.workspace / "7z.exe"
        executable.write_bytes(b"fake")
        archive_path = self.workspace.parent / "包.rar"
        archive_path.write_bytes(b"Rar!")

        def fake_run(args, **_kwargs):
            destination = Path(args[3][2:])
            root = destination / "rar任务"
            root.mkdir(parents=True)
            (root / "this.task").write_text(
                json.dumps(dict(self.data), ensure_ascii=False), encoding="utf-8")
            (root / "run.py").write_text("", encoding="utf-8")
            (root / "说明.png").write_bytes(b"png")
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(launcher.subprocess, "run", side_effect=fake_run) as run:
            package = launcher.inspect_task_package(
                archive_path, executable, lambda *args: None, lambda: False)
        self.addCleanup(shutil.rmtree, package.temp, True)
        self.assertEqual(package.error, "")
        self.assertEqual(package.title, "中文任务")
        self.assertIn("包.rar", run.call_args.args[0][4])
        rejected = launcher.inspect_task_package(
            archive_path, self.workspace / "7zr.exe",
            lambda *args: None, lambda: False)
        self.assertIn("不支持 RAR", rejected.error)

    def test_run_selected_writes_variable_values_before_launch(self):
        window = launcher.LauncherWindow(self.workspace, auto_start=False,
                                         register_hotkey=False)
        self.addCleanup(window.close)
        window.check_page.ready = True
        task = self.write_task(self.variable_data())
        window.detail_page.set_task(task)
        window.detail_page.variable_editors["times"].editor.setValue(7)
        window._switch(2)
        with patch.object(launcher, "task_command", return_value=["fake-bash"]) as command, \
             patch.object(window.run_page.process, "start"):
            window.run_selected()
            self.assertEqual(window.current_index, 3)
            command.assert_called_once()
            self.assertEqual(window.run_page.task.variables[0].value, 7)
        stored = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self.assertEqual(stored["var"]["value"]["times"], 7)
        self.assertEqual(window.run_page.task.path, self.path)
        window.run_page._stop(0)

    def test_task_page_import_is_hidden_without_seven_zip(self):
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        self.assertFalse(page.import_choose.isEnabled())
        self.assertIn("未安装 7-Zip", page.import_status.text())
        page.choose_package(self.make_package())
        self.assertIsNone(page.package_job)
        self.assertTrue(page.import_confirm.isHidden())
        self.assertFalse((self.workspace / "Task" / "未分类").exists())

    def test_admin_mode_disables_drag_and_points_to_picker(self):
        with patch.object(launcher, "is_admin", return_value=True):
            page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        self.assertFalse(page.acceptDrops())
        self.assertFalse(page.import_card.acceptDrops())
        self.assertIn("管理员", page.import_status.text())
        self.assertIn("选择任务包", page.import_status.text())
        self.assertIsInstance(page.import_choose, launcher.PrimaryPushButton)
        self.assertIn("选择任务包", page.import_title.text())

    @patch.object(launcher, "is_admin", return_value=False)
    def test_drop_card_accepts_archives_and_reports_rar_requirement(self, _is_admin=False):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDropEvent
        (self.workspace / "7zr.exe").write_bytes(b"fake")
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        archive = self.make_package("拖放任务")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(archive))])
        enter = QDragEnterEvent(
            QPoint(5, 5), launcher.Qt.DropAction.CopyAction, mime,
            launcher.Qt.MouseButton.LeftButton,
            launcher.Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(page.import_card, enter)
        self.assertTrue(enter.isAccepted())
        self.assertIn("松开鼠标", page.import_status.text())
        drop = QDropEvent(
            QPointF(5, 5), launcher.Qt.DropAction.CopyAction, mime,
            launcher.Qt.MouseButton.LeftButton,
            launcher.Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(page.import_card, drop)
        self.wait_until(lambda: page.package_job is None)
        self.assertIn("检查通过", page.import_status.text())

        rar = self.workspace.parent / "拖放.rar"
        rar.write_bytes(b"Rar!")
        page.choose_package(rar)
        self.assertIsNone(page.package_job)
        self.assertIn("不支持 RAR", page.import_status.text())

    def test_task_page_imports_valid_package_into_uncategorized(self):
        (self.workspace / "7zr.exe").write_bytes(b"fake")
        page = launcher.TaskPage(self.workspace)
        self.addCleanup(page.close)
        self.assertTrue(page.import_choose.isEnabled())
        page.choose_package(self.make_package("导入任务"))
        self.wait_until(lambda: page.package_job is None)
        self.assertFalse(page.import_confirm.isHidden())
        self.assertIn("检查通过", page.import_status.text())
        page.import_package()
        self.wait_until(lambda: page.package_job is None)
        self.wait_until(lambda: page.job is None)
        target = self.workspace / "Task" / "未分类" / "导入任务"
        self.assertTrue((target / "this.task").is_file())
        self.assertIn("已导入", page.import_status.text())
        titles = [page.list.item(i).data(launcher.Qt.ItemDataRole.UserRole).title
                  for i in range(page.list.count())]
        self.assertIn("导入任务", titles)


if __name__ == "__main__":
    unittest.main()
