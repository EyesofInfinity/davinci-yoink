"""Tool discovery under a short GUI PATH, simulated per platform so it runs anywhere: python -m unittest discover tests"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import davinci_yoink as dy

HOME = "/Users/someone"
DOCK_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"          # what Resolve sees when started from the Dock or Finder


class MacDiscovery(unittest.TestCase):
    """files: the fake file system's executables; dirs: its folders; dev_tools: xcode-select -p succeeds"""

    def run_case(self, files, dirs, dev_tools, has_module=()):
        calls = []

        def which(name, path=None):
            for d in (path or "").split(":"):
                if f"{d}/{name}" in files:
                    return f"{d}/{name}"
            return None

        def run(cmd, **kw):
            calls.append(cmd)
            ok = (cmd[0] == "/usr/bin/xcode-select" and dev_tools) or (cmd[0] in has_module)
            return mock.Mock(returncode=0 if ok else 1)

        def isdir(p):
            return p in dirs

        with mock.patch.object(dy, "SYSTEM", "Darwin"), mock.patch.object(dy.os, "pathsep", ":"), \
                mock.patch.dict(dy.os.environ, {"PATH": DOCK_PATH}, clear=False), \
                mock.patch.object(dy.os.path, "expanduser", lambda p: p.replace("~", HOME)), \
                mock.patch.object(dy.os.path, "join", lambda *a: "/".join(a)), \
                mock.patch.object(dy.os.path, "isdir", isdir), \
                mock.patch.object(dy.glob, "glob", lambda pat: [d for d in sorted(dirs) if d.startswith(HOME + "/Library/Python/")]), \
                mock.patch.object(dy.shutil, "which", which), mock.patch.object(dy.subprocess, "run", run):
            env = dy.tool_env()
            try:
                cmd = dy.ytdlp_cmd(env)
            except RuntimeError as e:
                cmd = e
            ffmpeg = which("ffmpeg", env["PATH"])
        return env["PATH"].split(":"), cmd, ffmpeg, calls

    def test_homebrew_tools_found_and_first(self):
        path, cmd, ffmpeg, calls = self.run_case(
            files={"/opt/homebrew/bin/yt-dlp", "/opt/homebrew/bin/ffmpeg"},
            dirs={"/opt/homebrew/bin", "/usr/local/bin"}, dev_tools=False)
        self.assertEqual(cmd, ["/opt/homebrew/bin/yt-dlp"])
        self.assertEqual(ffmpeg, "/opt/homebrew/bin/ffmpeg")
        self.assertLess(path.index("/opt/homebrew/bin"), path.index("/usr/bin"))
        self.assertEqual(calls, [])                         # nothing probed, no dialog possible

    def test_intel_homebrew_and_pip_user(self):
        bindir = HOME + "/Library/Python/3.12/bin"
        path, cmd, ffmpeg, _ = self.run_case(
            files={bindir + "/yt-dlp", "/usr/local/bin/ffmpeg"},
            dirs={"/usr/local/bin", bindir}, dev_tools=True)
        self.assertEqual(cmd, [bindir + "/yt-dlp"])
        self.assertEqual(ffmpeg, "/usr/local/bin/ffmpeg")

    def test_module_only_prefers_homebrew_python(self):
        path, cmd, _, calls = self.run_case(
            files={"/opt/homebrew/bin/python3", "/usr/bin/python3"},
            dirs={"/opt/homebrew/bin"}, dev_tools=True, has_module={"/opt/homebrew/bin/python3"})
        self.assertEqual(cmd, ["/opt/homebrew/bin/python3", "-m", "yt_dlp"])
        self.assertNotIn("/usr/bin/python3", [c[0] for c in calls])

    def test_no_dev_tools_never_touches_the_stub(self):
        path, cmd, _, calls = self.run_case(files={"/usr/bin/python3"}, dirs=set(), dev_tools=False)
        self.assertIsInstance(cmd, RuntimeError)            # the friendly "yt-dlp not found" message
        self.assertIn("yt-dlp not found", str(cmd))
        self.assertNotIn("/usr/bin/python3", [c[0] for c in calls])

    def test_dev_tools_present_system_python_allowed(self):
        path, cmd, _, _ = self.run_case(files={"/usr/bin/python3"}, dirs=set(), dev_tools=True,
                                        has_module={"/usr/bin/python3"})
        self.assertEqual(cmd, ["/usr/bin/python3", "-m", "yt_dlp"])


if __name__ == "__main__":
    unittest.main()
