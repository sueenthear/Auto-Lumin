"""识图内存回归；使用合成图和模拟 WGC，不捕获真实窗口。"""
import gc
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

import cv2
import numpy as np

from script_core.facade import AutomationCore
from script_core.image_manager.capture import WindowCapture
from script_core.image_manager.matcher import TemplateMatcher


def out_of_memory():
    return cv2.error("OpenCV(5.0.0) error: (-4:Insufficient memory) "
                     "Failed to allocate 34620024 bytes in function 'cv::OutOfMemoryError'")


class CaptureMemoryTests(unittest.TestCase):
    def setUp(self):
        # Client area is (width, height); the WGC frame is 1px larger on each side.
        self.size = [4, 3]
        self.state = SimpleNamespace(timeout=False, closed=False, start_error=False,
                                     fail_start_numbers=set(), started=threading.Event())
        self.sessions = []
        state = self.state
        sessions = self.sessions

        class FakeWindow:
            hwnd = 0x10

            @property
            def client_size(self):
                return tuple(self_size)

            def client_rect_in_window_capture(self):
                width, height = self_size
                return (1, 1, 1 + width, 1 + height)

        self_size = self.size
        self.window = FakeWindow()

        class NativeSession:
            # 模拟原生对象对 bound method 的不可见强引用，不依赖循环 GC。
            handles = {}

            def __init__(self, owner):
                self.key = id(self)
                self.handles[self.key] = (owner.frame_handler, owner.closed_handler)

            def __del__(self):
                self.handles.pop(self.key, None)

        class FakeControl:
            def __init__(self):
                self.stopped = False
                self.waited = False

            def stop(self):
                self.stopped = True

            def wait(self):
                assert self.stopped, "必须先停止会话再等待线程结束"
                self.waited = True

        class FakeCapture:
            def __init__(self, **kwargs):
                self.frame_handler = self.closed_handler = None
                self.control = FakeControl()
                self.capture = None
                self.delivered = 0

            def event(self, handler):
                if handler.__name__ == "on_frame_arrived":
                    self.frame_handler = handler
                else:
                    self.closed_handler = handler
                return handler

            def deliver(self, force=False):
                if state.closed:
                    self.closed_handler()
                    return
                if state.timeout:
                    return
                if force or self.delivered == 0:  # a live window pushes frames
                    self.delivered += 1
                    width, height = self_size
                    buffer = np.arange((height + 2) * (width + 2) * 4, dtype=np.uint8)
                    buffer = buffer.reshape(height + 2, width + 2, 4)
                    self.frame_handler(SimpleNamespace(frame_buffer=buffer), self.control)

            def start_free_threaded(self):
                if state.start_error or len(sessions) in state.fail_start_numbers:
                    state.started.set()
                    raise RuntimeError("start failed")
                # 登记发生在原生会话启动时，和 windows-capture 的真实时序一致。
                self.capture = NativeSession(self)
                state.started.set()
                self.deliver()
                return self.control

        def factory(**kwargs):
            session = FakeCapture(**kwargs)
            self.sessions.append(session)
            return session

        self.patch = patch.dict(sys.modules, {"windows_capture": SimpleNamespace(WindowsCapture=factory)})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(NativeSession.handles.clear)

    def test_one_session_serves_many_screenshots_and_close_releases_it(self):
        capture = WindowCapture(self.window)
        enabled = gc.isenabled()
        gc.disable()
        try:
            self.assertEqual(capture.screenshot().shape, (3, 4, 4))  # opens the session
            for _ in range(40):
                self.sessions[-1].deliver(force=True)  # a new frame keeps arriving
                image = capture.screenshot()
                self.assertEqual(image.shape, (3, 4, 4))
                self.assertTrue(image.flags.owndata, "截图不得保留整个窗口的大数组")
                self.assertTrue(image.flags.c_contiguous)
        finally:
            if enabled:
                gc.enable()
        self.assertEqual(len(self.sessions), 1, "反复识图不得每次新建 WGC 会话")
        self.assertTrue(capture.session_active)
        capture.close()
        self.assertFalse(capture.session_active)
        self.assertTrue(self.sessions[-1].control.waited, "close 必须停止并等待会话线程")
        self.assertIsNone(self.sessions[-1].frame_handler, "必须断开原生回调引用")
        self.assertIsNone(self.sessions[-1].closed_handler)
        self.assertIsNone(self.sessions[-1].capture)

    def test_static_window_reuses_last_frame_without_stalling(self):
        capture = WindowCapture(self.window)
        self.addCleanup(capture.close)
        self.assertIsNotNone(capture.screenshot())  # first frame arrives at start
        start = time.monotonic()
        for _ in range(5):  # no new frames now: must not wait the full timeout
            self.assertEqual(capture.screenshot(timeout=5).shape, (3, 4, 4))
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(len(self.sessions), 1)

    def test_resized_window_rebuilds_the_session(self):
        capture = WindowCapture(self.window)
        self.addCleanup(capture.close)
        self.assertEqual(capture.screenshot().shape, (3, 4, 4))
        self.size[:] = [8, 6]  # the task resized the window
        self.assertEqual(capture.screenshot(timeout=1).shape, (6, 8, 4))
        self.assertEqual(len(self.sessions), 2, "尺寸变化必须重建会话")
        self.assertTrue(self.sessions[0].control.waited)
        self.assertIsNone(self.sessions[0].capture, "旧会话必须释放原生引用")

    def test_resize_replacement_start_failure_stops_retired_session(self):
        capture = WindowCapture(self.window)
        self.assertEqual(capture.screenshot().shape, (3, 4, 4))
        old = self.sessions[0]
        self.size[:] = [8, 6]
        self.state.fail_start_numbers.add(2)
        with self.assertRaisesRegex(RuntimeError, "start failed"):
            capture.screenshot()
        self.assertTrue(old.control.waited, "replacement start failure must stop retired session")
        self.assertIsNone(old.capture, "retired native handle must be released")
        self.assertIsNone(old.frame_handler)
        self.assertFalse(capture.session_active)

    def test_close_discards_error_from_retired_session_before_restart(self):
        capture = WindowCapture(self.window)
        self.assertEqual(capture.screenshot().shape, (3, 4, 4))
        old = self.sessions[0]
        with capture._condition:
            capture._error = RuntimeError("error from old WGC session")
        capture.close()
        self.assertIsNone(capture._error)
        self.assertTrue(old.control.waited)
        # The next request creates a fresh session; the retired error must not
        # be observed by the new generation.
        self.assertEqual(capture.screenshot().shape, (3, 4, 4))
        self.assertEqual(len(self.sessions), 2)
        self.assertTrue(capture.session_active)
        capture.close()

    def test_close_wakes_screenshot_waiting_for_first_frame(self):
        capture = WindowCapture(self.window)
        self.state.timeout = True  # start WGC but suppress all frame callbacks
        outcome = []
        finished = threading.Event()

        def take_screenshot():
            try:
                capture.screenshot(timeout=5)
            except BaseException as exc:
                outcome.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=take_screenshot)
        worker.start()
        self.assertTrue(self.state.started.wait(1), "screenshot should start the capture session")
        # Let screenshot enter Condition.wait before close detaches/notifies.
        time.sleep(0.03)
        started = time.monotonic()
        capture.close()
        worker.join(1)
        self.assertTrue(finished.is_set(), "close must wake the first-frame waiter")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], RuntimeError)
        self.assertIn("closed or replaced", str(outcome[0]))
        self.assertFalse(capture.session_active)
        self.assertTrue(self.sessions[0].control.waited)
        self.assertIsNone(self.sessions[0].capture)
        self.assertIsNone(self.sessions[0].frame_handler)

    def test_capture_error_timeout_closed_and_start_failure_release_session(self):
        scenarios = (("timeout", TimeoutError), ("closed", RuntimeError),
                     ("start_error", RuntimeError))
        for name, expected in scenarios:
            with self.subTest(name=name):
                self.sessions.clear()
                setattr(self.state, name, True)
                try:
                    with self.assertRaises(expected):
                        WindowCapture(self.window).screenshot(timeout=0.05)
                    session = self.sessions[-1]
                    self.assertIsNone(session.frame_handler)
                    self.assertIsNone(session.closed_handler)
                    self.assertIsNone(session.capture)
                    if not name == "start_error":
                        self.assertTrue(session.control.waited)
                finally:
                    setattr(self.state, name, False)

    def test_invalid_crop_fails_and_releases_session(self):
        self.window.client_rect_in_window_capture = lambda: (0, 0, 30, 30)
        with self.assertRaisesRegex(RuntimeError, "WGC capture failed"):
            WindowCapture(self.window).screenshot()
        self.assertIsNone(self.sessions[-1].frame_handler)
        self.assertIsNone(self.sessions[-1].capture)


class MatchMemoryTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(513)
        self.source = self.rng.integers(0, 256, (90, 120, 3), dtype=np.uint8)
        self.template = self.source[38:49, 47:60].copy()

    def test_transient_opencv_memory_error_recovers_without_changing_match(self):
        expected = TemplateMatcher.find(self.source, self.template)
        original = cv2.matchTemplate
        first = True
        def match(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise out_of_memory()
            return original(*args, **kwargs)
        with patch.object(cv2, "matchTemplate", side_effect=match):
            actual = TemplateMatcher.find(self.source, self.template)
        self.assertEqual(actual.center, expected.center)
        self.assertAlmostEqual(actual.score, expected.score, places=4)

    def test_persistent_memory_error_is_bounded_and_diagnostic(self):
        with patch.object(cv2, "matchTemplate", side_effect=out_of_memory()) as match:
            with self.assertRaisesRegex(MemoryError, "template matching"):
                TemplateMatcher.find(self.source, self.template)
        self.assertLessEqual(match.call_count, 2, "低内存恢复失败后不能无界重试")

    def test_non_memory_cv_error_is_not_hidden_or_retried(self):
        error = cv2.error("OpenCV (-215:Assertion failed) type == template.type()")
        with patch.object(cv2, "matchTemplate", side_effect=error) as match:
            with self.assertRaises(cv2.error) as raised:
                TemplateMatcher.find(self.source, self.template)
        self.assertIs(raised.exception, error)
        self.assertEqual(match.call_count, 1)

    def test_python_memory_error_recovers(self):
        original = TemplateMatcher._to_bgr
        counter = 0
        def convert(image):
            nonlocal counter
            counter += 1
            if counter == 1:
                raise MemoryError("allocation failed")
            return original(image)
        with patch.object(TemplateMatcher, "_to_bgr", side_effect=convert):
            result = TemplateMatcher.find(self.source, self.template)
        self.assertEqual(result.center, (53, 43))

    def test_tiled_recovery_visits_boundaries_and_preserves_all_methods(self):
        original = cv2.matchTemplate
        for method in (cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED, cv2.TM_SQDIFF_NORMED):
            for channels in (1, 3, 4):
                with self.subTest(method=method, channels=channels):
                    shape = (56, 69) if channels == 1 else (56, 69, channels)
                    source = self.rng.integers(0, 256, shape, dtype=np.uint8)
                    # Cross both tile boundaries; right/bottom are partial tiles.
                    needle = source[15:26, 15:28].copy()
                    if channels == 4:
                        needle[:, :, 3] = 255
                        needle[0:3, 0:4, 3] = 0
                    expected = TemplateMatcher.find(source, needle, method=method)
                    calls = []
                    def match(haystack, template, *args, **kwargs):
                        calls.append(haystack.shape)
                        if len(calls) == 1:
                            raise out_of_memory()
                        self.assertLessEqual(haystack.shape[0], 16 + needle.shape[0] - 1)
                        self.assertLessEqual(haystack.shape[1], 16 + needle.shape[1] - 1)
                        return original(haystack, template, *args, **kwargs)
                    with patch.object(TemplateMatcher, "_TILE_EDGE", 16), \
                         patch.object(cv2, "matchTemplate", side_effect=match), \
                         self.assertWarns(RuntimeWarning):
                        actual = TemplateMatcher.find(source, needle, method=method)
                    self.assertEqual(actual.center, expected.center)
                    self.assertAlmostEqual(actual.score, expected.score, places=4)
                    self.assertGreater(len(calls), 5)

    def test_recovery_finds_last_candidate_and_respects_threshold(self):
        original = cv2.matchTemplate
        self.template = self.source[-11:, -13:].copy()
        def match(source, template, *args, **kwargs):
            if source.shape == self.source.shape:
                raise out_of_memory()
            return original(source, template, *args, **kwargs)
        with patch.object(TemplateMatcher, "_TILE_EDGE", 16), \
             patch.object(cv2, "matchTemplate", side_effect=match), \
             self.assertWarns(RuntimeWarning):
            result = TemplateMatcher.find(self.source, self.template)
        self.assertEqual(result.center, (113, 84))
        unrelated = self.rng.integers(0, 256, self.template.shape, dtype=np.uint8)
        with patch.object(TemplateMatcher, "_TILE_EDGE", 16), \
             patch.object(cv2, "matchTemplate", side_effect=match), \
             self.assertWarns(RuntimeWarning):
            self.assertIsNone(TemplateMatcher.find(self.source, unrelated, threshold=0.999))

    def test_match_results_and_converted_buffers_not_retained(self):
        refs = []
        original = cv2.matchTemplate
        def match(*args, **kwargs):
            result = original(*args, **kwargs)
            refs.append(weakref.ref(result))
            return result
        with patch.object(cv2, "matchTemplate", side_effect=match):
            for _ in range(60):
                self.assertIsNotNone(TemplateMatcher.find(self.source, self.template))
                self.assertIsNone(refs[-1](), "不得持有上一轮匹配得分矩阵")

    def test_failed_conversion_buffer_is_released_before_retry(self):
        original = TemplateMatcher._to_bgr
        refs = []
        def convert(image):
            result = original(image)
            if result is not image:
                refs.append(weakref.ref(result))
            return result
        first = True
        original_match = cv2.matchTemplate
        def match(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise out_of_memory()
            return original_match(*args, **kwargs)
        def collect():
            self.assertTrue(refs)
            self.assertTrue(all(ref() is None for ref in refs),
                            "首次失败的 traceback 不得在恢复时持有转换后的大数组")
        source = cv2.cvtColor(self.source, cv2.COLOR_BGR2BGRA)
        # Mock.call_args would itself retain the converted source array passed
        # to matchTemplate. Plain replacement functions model real ownership.
        with patch.object(TemplateMatcher, "_to_bgr", new=staticmethod(convert)), \
             patch.object(cv2, "matchTemplate", new=match), \
             patch("script_core.image_manager.matcher.gc.collect", new=collect), \
             self.assertWarns(RuntimeWarning):
            self.assertIsNotNone(TemplateMatcher.find(source, self.template))

    def test_facade_uses_selected_method_once_and_preserves_roi(self):
        core = SimpleNamespace(image=SimpleNamespace(screenshot=Mock(return_value=self.source)),
                               input=Mock(), window=SimpleNamespace(hwnd=1))
        with patch("script_core.facade.Interrupt"):
            do = AutomationCore(core)
        for way in ("ccoeff", "sqdiff", "ccorr"):
            do.match.set.way(way)
            with patch.object(cv2, "matchTemplate", wraps=cv2.matchTemplate) as match:
                self.assertEqual(do.match.pos(self.template, roi=(40, 30, 50, 40)), (53, 43))
                self.assertEqual(match.call_count, 1, "非默认算法不得先做一次默认匹配")
                self.assertEqual(match.call_args.args[2], do.match._way)

    def test_invalid_image_and_transparent_template_still_raise(self):
        invalid = [np.empty((0, 1, 3), np.uint8), np.zeros((4,), np.uint8),
                   np.zeros((4, 4, 2), np.uint8)]
        for image in invalid:
            with self.subTest(shape=image.shape), self.assertRaises(ValueError):
                TemplateMatcher.find(image, self.template)
        with self.assertRaisesRegex(ValueError, "fully transparent"):
            TemplateMatcher.find(self.source, np.zeros((3, 3, 4), dtype=np.uint8))
        self.assertIsNone(TemplateMatcher.find(self.template, self.source))
        with self.assertRaises(ValueError):
            TemplateMatcher.find(self.source, self.template, threshold=1.1)

    def test_facade_exposes_image_close_and_session_reuse(self):
        from script_core.image_manager import ImageManager
        manager = ImageManager.__new__(ImageManager)
        manager.capture = Mock()
        manager.close()
        manager.capture.close.assert_called_once_with()
        image = SimpleNamespace(screenshot=Mock(return_value=self.source), close=Mock())
        core = SimpleNamespace(image=image, input=Mock(), window=SimpleNamespace(hwnd=1))
        with patch("script_core.facade.Interrupt"):
            do = AutomationCore(core)
        do.image.close()
        image.close.assert_called_once_with()

    def test_exist_loop_recovers_from_memory_pressure_without_task_changes(self):
        core = SimpleNamespace(image=SimpleNamespace(screenshot=Mock(return_value=self.source)),
                               input=Mock(), window=SimpleNamespace(hwnd=1))
        with patch("script_core.facade.Interrupt"):
            do = AutomationCore(core)
        original = cv2.matchTemplate
        first = True
        def match(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise out_of_memory()
            return original(*args, **kwargs)
        with patch.object(cv2, "matchTemplate", side_effect=match):
            for _ in range(20):
                self.assertTrue(do.match.exist(self.template))
        self.assertEqual(core.image.screenshot.call_count, 20)


if __name__ == "__main__":
    unittest.main()
