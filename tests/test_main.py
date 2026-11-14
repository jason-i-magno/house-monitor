import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class MainTest(unittest.TestCase):
    def test_sigterm_shuts_it_down_as_ctrl_c_does(self):
        """As a container is stopped: the collectors stopped and the database closed, at once, not killed later."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "house.sqlite3"
            none = str(Path(tmp) / "none")
            app = subprocess.Popen(
                [sys.executable, "-m", "housemon", "--house", "housemon/example_house.py", "--db", str(db), "--port", "0",
                 "--backfill-hours", "0", "--ecobee", none, "--whisker", none, "--mova", none, "--uplink", none],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            try:
                self.assertTrue(app.stdout.readline().startswith("House monitor on "))
                app.send_signal(signal.SIGTERM)
                self.assertEqual(app.wait(timeout=10), 0, app.stderr.read())
            finally:
                app.kill()
                app.wait()
                app.stdout.close()
                app.stderr.close()
            self.assertTrue(db.exists())
            self.assertFalse(Path(f"{db}-wal").exists())  # SQLite removes it when the last connection closes cleanly


if __name__ == "__main__":
    unittest.main()
