"""Copy DaVinci Yoink into DaVinci Resolve's Scripts > Utility menu for the current user.
usage: python install.py                       (then restart Resolve or reopen Workspace > Scripts)
       python install.py --folder <download folder>   (the installed copy downloads there)
       python install.py --uninstall
"""
import os, platform, shutil, sys

NAME = "DaVinci Yoink.py"


def utility_dir():
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Windows":
        base = os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")),
                            "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts")
    elif system == "Darwin":
        base = os.path.join(home, "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve",
                            "Fusion", "Scripts")
    else:
        base = os.path.join(home, ".local", "share", "DaVinciResolve", "Fusion", "Scripts")
    return os.path.join(base, "Utility")


def main():
    dest = os.path.join(utility_dir(), NAME)
    if "--uninstall" in sys.argv:
        if os.path.exists(dest):
            os.remove(dest)
            print("removed", dest)
        else:
            print("not installed:", dest)
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "davinci_yoink.py"), encoding="utf-8").read()
    if "--folder" in sys.argv:
        folder = os.path.abspath(os.path.expanduser(sys.argv[sys.argv.index("--folder") + 1]))
        line = "DEFAULT_DIR = None"
        if line not in src:
            sys.exit("davinci_yoink.py has no DEFAULT_DIR line to set")
        src = src.replace(line, "DEFAULT_DIR = " + repr(folder), 1)
        print("download folder:", folder)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(src)
    print("installed", dest)
    if not shutil.which("yt-dlp"):
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            print('next: install yt-dlp with  pip install -U "yt-dlp[default]"')
    if not shutil.which("ffmpeg"):
        print("next: put ffmpeg on your PATH (yt-dlp needs it to merge video and audio)")


if __name__ == "__main__":
    main()
