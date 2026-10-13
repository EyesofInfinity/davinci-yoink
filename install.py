"""Install DaVinci Yoink: the script into Resolve's Workspace > Scripts menu, yt-dlp into this Python, then a check.
usage: python install.py                               (then restart Resolve or reopen Workspace > Scripts)
       python install.py --folder <download folder>    (the installed copy downloads there)
       python install.py --no-deps                     (copy the script only)
       python install.py --uninstall
Run it with the Python that DaVinci Resolve uses (on Windows and macOS, the python.org install).
"""
import functools, os, platform, subprocess, sys

print = functools.partial(print, flush=True)              # keep order with pip's output

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import davinci_yoink as dy  # noqa: E402


def install_ytdlp():
    """yt-dlp into this Python's own site-packages: never the per-user folder, which an AI desktop app on Windows
    would redirect into its private storage where Resolve cannot see it"""
    print('installing yt-dlp[default] into', sys.executable)
    r = subprocess.run([sys.executable, "-s", "-m", "pip", "install", "--no-user", "--upgrade", "yt-dlp[default]"])
    if r.returncode != 0:
        print("pip could not install yt-dlp here. Alternatives: " + {
            "Darwin": "brew install yt-dlp", "Windows": "winget install yt-dlp.yt-dlp"}.get(
            platform.system(), "pipx install yt-dlp, or your package manager"))


def main():
    dest = dy.menu_path()
    if "--uninstall" in sys.argv:
        if os.path.exists(dest):
            os.remove(dest)
            print("removed", dest)
        else:
            print("not installed:", dest)
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = open(os.path.join(HERE, "davinci_yoink.py"), encoding="utf-8").read()
    if "--folder" in sys.argv:
        folder = os.path.abspath(os.path.expanduser(sys.argv[sys.argv.index("--folder") + 1]))
        line = "DEFAULT_DIR = None"
        if line not in src:
            sys.exit("davinci_yoink.py has no DEFAULT_DIR line to set")
        src = src.replace(line, "DEFAULT_DIR = " + repr(folder), 1)
        dy.DEFAULT_DIR = folder
        print("download folder:", folder)
    if "--folder" not in sys.argv and os.path.exists(dest):          # a reinstall keeps the folder already set
        import ast, re
        m = re.search(r"^DEFAULT_DIR = ('.*?'|\".*?\")", open(dest, encoding="utf-8").read(), re.M)
        if m:
            src = src.replace("DEFAULT_DIR = None", "DEFAULT_DIR = " + m.group(1), 1)
            dy.DEFAULT_DIR = ast.literal_eval(m.group(1))
            print("download folder kept:", dy.DEFAULT_DIR)
    before = dy.menu_version(dest)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(src)
    print(("replaced version " + before + " at " if before else "installed ") + dest)
    if dy.private_copy(dest):
        print("WARNING: that copy landed in a packaged app's private storage, so Resolve will not list it. Start "
              "Resolve once (it creates its Scripts folder), then run this installer again.")
    if "--no-deps" not in sys.argv:
        install_ytdlp()
    rep = dy.check()
    print("\nwhat DaVinci Yoink finds:")
    for k in ("yt-dlp", "ffmpeg", "js", "menu", "folder"):
        print(f"  {k:7} {rep[k] or 'MISSING'}")
    if rep["problems"]:
        print("to do:\n  " + "\n  ".join(rep["problems"]))
    if not rep["js"]:
        print("  recommended: Node.js or Deno, so yt-dlp can answer YouTube's player checks")
    print("\nnext: restart Resolve, open Workspace > Scripts > DaVinci Yoink. "
          "For Resolve's own view: python davinci_yoink.py --doctor")
    sys.exit(1 if rep["problems"] else 0)


if __name__ == "__main__":
    main()
