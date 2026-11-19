import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from unittest.mock import patch

from rok_governor.execution_store import ExecutionStore
from rok_governor.smart_monitor import _change_ratio, _frame_signature
from rok_governor.window_capture import save_window_image
from rok_governor.input_controller import post_click
from rok_governor.window_capture import find_child_windows


class ExecutionStoreTests(unittest.TestCase):
    def test_failed_visual_review_pauses_across_store_instances(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "history.sqlite3"
            store = ExecutionStore(database)
            command = {
                "action_type": "inspect_screen",
                "title": "Open map",
                "target": "Map button",
                "input_actions": [{"type": "click", "x": 500, "y": 500}],
                "screen_fingerprint": "frame-a",
            }
            self.assertTrue(store.claim(command))
            store.finish(command, "posted", {
                "verification_criteria": "Map screen is visible",
            })

            saved = store.record_reviews({
                "advice": {
                    "execution_reviews": [{
                        "execution_id": 1,
                        "assessment": "failed",
                        "visual_evidence": "City view is still visible",
                    }],
                },
            })

            self.assertEqual(saved, 1)
            self.assertIn("failed", ExecutionStore(database).automation_pause()["reason"])
            self.assertTrue(ExecutionStore(database).resume_automation())
            self.assertIsNone(ExecutionStore(database).automation_pause())

    def test_existing_failed_review_seeds_pause(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "history.sqlite3"
            with sqlite3.connect(database) as db:
                db.execute("""CREATE TABLE automation_executions (
                    id INTEGER PRIMARY KEY, status TEXT, details_json TEXT, finished_at TEXT
                )""")
                db.execute(
                    "INSERT INTO automation_executions VALUES (?, ?, ?, ?)",
                    (7, "failed", json.dumps({"visual_review": {"evidence": "No screen change"}}), "now"),
                )
            db.close()

            pause = ExecutionStore(database).automation_pause()
            self.assertIn("Execution #7", pause["reason"])
            self.assertIn("No screen change", pause["reason"])


class FrameCaptureTests(unittest.TestCase):
    def test_signature_accepts_memory_image_and_saved_png(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Image.new("RGB", (640, 360), (25, 80, 120))
            path = save_window_image(image, temporary)

            from_memory = _frame_signature(image)
            from_disk = _frame_signature(path)

            self.assertEqual(from_memory.shape, (180, 320))
            np.testing.assert_array_equal(from_memory, from_disk)

    def test_change_ratio_distinguishes_identical_and_different_frames(self):
        base = np.zeros((20, 20), dtype=np.uint8)
        changed = base.copy()
        changed[:, :5] = 255

        self.assertEqual(_change_ratio(base, base.copy()), 0.0)
        self.assertEqual(_change_ratio(base, changed), 0.25)


class InputControllerTests(unittest.TestCase):
    def setUp(self):
        import win32gui

        self.win32gui = win32gui
        self.window_patch = patch(
            "rok_governor.window_capture.find_windows",
            return_value=[{
                "hwnd": 42,
                "title": "RoK",
                "rect": (100, 100, 1100, 800),
                "minimized": False,
            }],
        )
        self.window_patch.start()
        self.addCleanup(self.window_patch.stop)
        for name, value in {
            "IsWindow": True,
            "IsWindowVisible": True,
            "GetClientRect": (0, 0, 1000, 700),
        }.items():
            mocked = patch.object(win32gui, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        screen_to_client = patch.object(
            win32gui, "ScreenToClient",
            side_effect=lambda _hwnd, point: (point[0] - 100, point[1] - 100),
        )
        screen_to_client.start()
        self.addCleanup(screen_to_client.stop)

    def test_foreground_mode_focuses_moves_and_releases_click(self):
        import win32api
        import win32con

        foreground = patch.object(self.win32gui, "GetForegroundWindow", return_value=42)
        set_focus = patch.object(self.win32gui, "SetForegroundWindow")
        cursor = patch.object(win32api, "SetCursorPos")
        cursor_position = patch.object(win32api, "GetCursorPos", return_value=(600, 450))
        mouse = patch.object(win32api, "mouse_event")
        sleeper = patch("rok_governor.input_controller.time.sleep")
        for mocked in (foreground, set_focus, cursor, cursor_position, mouse, sleeper):
            self.addCleanup(mocked.stop)
        set_focus_mock = set_focus.start()
        cursor_mock = cursor.start()
        cursor_position_mock = cursor_position.start()
        mouse_mock = mouse.start()
        foreground.start()
        sleeper.start()

        result = post_click(
            "RoK", x_normalized=500, y_normalized=500,
            screenshot_size=(1000, 700), input_mode="foreground",
        )

        set_focus_mock.assert_called_once_with(42)
        cursor_mock.assert_called_once_with((600, 450))
        cursor_position_mock.assert_called_once_with()
        self.assertEqual(
            [call.args[0] for call in mouse_mock.call_args_list],
            [win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP],
        )
        self.assertEqual(result["delivery"], "sent_to_foreground_window")

    def test_foreground_mode_refuses_click_if_focus_is_not_granted(self):
        with patch.object(self.win32gui, "SetForegroundWindow"), patch.object(
            self.win32gui, "GetForegroundWindow", return_value=99
        ), patch("rok_governor.input_controller.time.sleep"):
            from rok_governor.input_controller import WindowInputError

            with self.assertRaisesRegex(WindowInputError, "foreground input was not sent"):
                post_click(
                    "RoK", x_normalized=500, y_normalized=500,
                    screenshot_size=(1000, 700), input_mode="foreground",
                )

    def test_foreground_mode_refuses_click_if_pointer_did_not_reach_target(self):
        import win32api

        with patch.object(self.win32gui, "SetForegroundWindow"), patch.object(
            self.win32gui, "GetForegroundWindow", return_value=42
        ), patch.object(win32api, "SetCursorPos"), patch.object(
            win32api, "GetCursorPos", return_value=(0, 0)
        ), patch.object(win32api, "mouse_event") as mouse, patch(
            "rok_governor.input_controller.time.sleep"
        ):
            from rok_governor.input_controller import WindowInputError

            with self.assertRaisesRegex(WindowInputError, "pointer to the calibrated click target"):
                post_click(
                    "RoK", x_normalized=500, y_normalized=500,
                    screenshot_size=(1000, 700), input_mode="foreground",
                )
            mouse.assert_not_called()

    def test_background_mode_posts_messages_without_moving_pointer(self):
        import win32api
        import win32con

        post_message = patch.object(self.win32gui, "PostMessage")
        cursor = patch.object(win32api, "SetCursorPos")
        mouse = patch.object(win32api, "mouse_event")
        sleeper = patch("rok_governor.input_controller.time.sleep")
        for mocked in (post_message, cursor, mouse, sleeper):
            self.addCleanup(mocked.stop)
        post_message_mock = post_message.start()
        cursor_mock = cursor.start()
        mouse_mock = mouse.start()
        sleeper.start()

        result = post_click(
            "RoK", x_normalized=500, y_normalized=500,
            screenshot_size=(1000, 700), input_mode="background",
        )

        self.assertEqual(
            [call.args[1] for call in post_message_mock.call_args_list],
            [win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP],
        )
        cursor_mock.assert_not_called()
        mouse_mock.assert_not_called()
        self.assertEqual(result["delivery"], "posted_to_window_not_foregrounded")


class WindowDiagnosticTests(unittest.TestCase):
    def test_child_window_diagnostic_reports_process_id(self):
        import win32gui
        import win32process

        enum_children = patch.object(
            win32gui, "EnumChildWindows",
            side_effect=lambda _parent, callback, extra: callback(43, extra),
        )
        valid_window = patch.object(win32gui, "IsWindow", return_value=True)
        rect = patch.object(win32gui, "GetWindowRect", return_value=(1, 2, 101, 202))
        client_rect = patch.object(win32gui, "GetClientRect", return_value=(0, 0, 100, 200))
        class_name = patch.object(win32gui, "GetClassName", return_value="GameSurface")
        title = patch.object(win32gui, "GetWindowText", return_value="")
        visible = patch.object(win32gui, "IsWindowVisible", return_value=True)
        minimized = patch.object(win32gui, "IsIconic", return_value=False)
        process_id = patch.object(win32process, "GetWindowThreadProcessId", return_value=(11, 22))
        patches = (enum_children, valid_window, rect, client_rect, class_name, title,
                   visible, minimized, process_id)
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)

        children = find_child_windows(42)

        self.assertEqual(children[0]["hwnd"], 43)
        self.assertEqual(children[0]["process_id"], 22)
        self.assertEqual(children[0]["class_name"], "GameSurface")


if __name__ == "__main__":
    unittest.main()
