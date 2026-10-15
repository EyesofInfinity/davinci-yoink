"""The doctor never certifies without Resolve's own view; private-storage copies are warnings, never false failures."""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import davinci_yoink as dy

CLEAN = {"version": dy.__version__, "python": "3.12", "python_path": "py", "venv": False, "yt-dlp": "y", "ffmpeg": "f",
         "js": "n", "folder": "d", "menu": "m", "problems": [], "warnings": []}


def run_doctor(scriptapp, check=CLEAN):
    out = io.StringIO()
    fake_module = mock.Mock(scriptapp=lambda name: scriptapp)
    with mock.patch.object(dy, "check", lambda inside_resolve=False: dict(check)), \
            mock.patch.object(dy, "_scripting_module", lambda: fake_module), \
            mock.patch("time.sleep", lambda s: None), contextlib.redirect_stdout(out):
        try:
            dy._doctor()
        except SystemExit as e:
            return e.code, out.getvalue()
    return None, out.getvalue()


class Doctor(unittest.TestCase):
    def test_unreachable_is_never_ok(self):
        code, text = run_doctor(None)
        self.assertEqual(code, 2)
        self.assertNotIn("OK: ready", text)
        self.assertIn("NOT VERIFIED", text)

    def test_probe_that_never_runs_is_never_ok(self):
        resolve = mock.Mock()
        resolve.Fusion.return_value.RunScript = lambda path: None       # Resolve answers, the probe never writes
        code, text = run_doctor(resolve)
        self.assertEqual(code, 2)
        self.assertNotIn("OK: ready", text)

    def test_ok_only_from_resolves_view(self):
        def run(path):                                                  # stand in for Resolve running the probe
            src = open(path, encoding="utf-8").read()
            out = src.split("os.replace(tmp, ")[1].split(")")[0].strip("'\"")
            json.dump(CLEAN, open(out, "w", encoding="utf-8"))
        resolve = mock.Mock()
        resolve.Fusion.return_value.RunScript = run
        code, text = run_doctor(resolve)
        self.assertEqual(code, 0)
        self.assertIn("OK: ready to yoink", text)

    def test_probe_loads_this_file_by_path(self):
        seen = {}
        resolve = mock.Mock()
        resolve.Fusion.return_value.RunScript = lambda path: seen.setdefault("src", open(path, encoding="utf-8").read())
        run_doctor(resolve)
        self.assertIn("spec_from_file_location", seen["src"])
        self.assertIn(os.path.abspath(dy.__file__), seen["src"].replace("\\\\", "\\"))
        self.assertNotIn("import davinci_yoink", seen["src"])


@unittest.skipUnless(dy.SYSTEM == "Windows", "private storage is a Windows behaviour")
class PrivateStorage(unittest.TestCase):
    def test_stale_twin_is_a_warning_and_silent_inside_resolve(self):
        with tempfile.TemporaryDirectory() as t:
            roaming, local = os.path.join(t, "Roaming"), os.path.join(t, "Local")
            real = os.path.join(roaming, "Python", "Python312", "Scripts", "yt-dlp.exe")
            twin = os.path.join(local, "Packages", "App_x", "LocalCache", "Roaming", "Python", "Python312", "Scripts", "yt-dlp.exe")
            for f in (real, twin):
                os.makedirs(os.path.dirname(f))
                open(f, "w").close()
            with mock.patch.dict(os.environ, {"APPDATA": roaming, "LOCALAPPDATA": local}), \
                    mock.patch.object(dy, "ytdlp_cmd", lambda env: [real]):
                self.assertTrue(dy.private_copy(real))
                shell, inside = dy.check(), dy.check(inside_resolve=True)
            self.assertFalse([p for p in shell["problems"] if "private" in p])
            self.assertTrue([w for w in shell["warnings"] if "private storage" in w])
            self.assertFalse(inside["warnings"])


class Override(unittest.TestCase):
    def test_missing_override_is_reported(self):
        with mock.patch.dict(os.environ, {"DAVINCI_YOINK_YTDLP": os.path.join(tempfile.gettempdir(), "no-such-yt-dlp")}):
            with self.assertRaises(RuntimeError):
                dy.ytdlp_cmd(dy.tool_env())


if __name__ == "__main__":
    unittest.main()
