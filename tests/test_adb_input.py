import subprocess
import unittest
from unittest.mock import patch

from rok_governor.adb_input import _run_adb


class AdbRecoveryTests(unittest.TestCase):
    def test_offline_transport_is_reconnected_and_original_command_retried(self):
        command = ["adb", "exec-out", "screencap", "-p"]
        offline = subprocess.CalledProcessError(
            1, command, stderr=b"error: device offline"
        )
        reconnect = subprocess.CompletedProcess(
            ["adb", "reconnect", "offline"], 0, stdout=b"", stderr=b""
        )
        screenshot = subprocess.CompletedProcess(command, 0, stdout=b"png", stderr=b"")

        with patch(
            "rok_governor.adb_input.subprocess.run",
            side_effect=[offline, reconnect, screenshot],
        ) as run, patch("rok_governor.adb_input.time.sleep"):
            result = _run_adb(command, timeout=10)

        self.assertEqual(result.stdout, b"png")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[1].args[0], ["adb", "reconnect", "offline"])

    def test_non_connection_adb_errors_are_not_retried(self):
        command = ["adb", "exec-out", "screencap", "-p"]
        failure = subprocess.CalledProcessError(1, command, stderr=b"unknown command")
        with patch("rok_governor.adb_input.subprocess.run", side_effect=failure) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                _run_adb(command, timeout=10)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
