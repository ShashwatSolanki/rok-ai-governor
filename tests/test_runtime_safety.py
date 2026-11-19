import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from rok_governor.execution_store import ExecutionStore
from rok_governor.smart_monitor import _change_ratio, _frame_signature
from rok_governor.window_capture import save_window_image


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


if __name__ == "__main__":
    unittest.main()
