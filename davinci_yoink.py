"""DaVinci Yoink: paste a video link, and it lands on your DaVinci Resolve timeline.

Workspace > Scripts > DaVinci Yoink. Paste a URL (YouTube or any site yt-dlp supports), pick a quality and
press Yoink. The file downloads to your Yoink folder, goes into a "Yoinked" bin in the Media Pool, and drops onto the
current timeline at the playhead: picture and sound each on the first track that is free there, linked together.

- 1080p H.264 + AAC by default (the smaller side capped at 1080, so vertical videos keep their full resolution).
  "Best quality" takes the source's top stream (often VP9 or AV1 at 4K). "Audio only" saves a WAV.
- Auto-dubbed videos: the ORIGINAL-language audio track is preferred over the dubs.
- Every yoink is logged (time, URL, title, file, quality) in yoink_log.csv inside the download folder.
- Yoinking the same link at the same quality again reuses the clip already in the bin.
- Linux: Resolve does not decode AAC there, so the audio is converted to PCM in a .mov after the download.

Requirements: yt-dlp with its default extras (pip install -U "yt-dlp[default]") and ffmpeg. Node.js or Deno lets
yt-dlp answer YouTube's player challenges. Common install folders (Homebrew, pip --user) are searched even when
Resolve was started from the Dock or Start menu with a short PATH.

Download folder: DAVINCI_YOINK_DIR, else the folder written by `install.py --folder`, else ~/Videos/DaVinci Yoink
(Windows, Linux) or ~/Movies/DaVinci Yoink (macOS).

Headless use from outside Resolve (Resolve Studio running, external scripting set to Local):
    python davinci_yoink.py --headless <url> [--quality 1080|best|audio] [--no-timeline]
Check the setup (and, with Resolve Studio reachable, what Resolve's own Python sees):
    python davinci_yoink.py --doctor

MIT License. See LICENSE.
"""
import csv, datetime, glob, json, os, platform, re, shutil, subprocess, sys

__version__ = "1.1.2"
DEFAULT_DIR = None                     # install.py --folder writes a path here in the installed copy
BIN = "Yoinked"
ORIG = "[format_note*=original]"
FORMATS = {
    "1080": f"bv*[vcodec^=avc1]+ba[ext=m4a]{ORIG}/bv*[vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
    "best": f"bv*+ba[ext=m4a]{ORIG}/bv*+ba/b",
    "audio": f"ba[ext=m4a]{ORIG}/ba[ext=m4a]/ba",
}
SYSTEM = platform.system()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# never a per-user install: from inside an AI desktop app on Windows it lands where Resolve cannot see it
PIP_FIX = 'python -s -m pip install --no-user -U "yt-dlp[default]"'


def _menu_folder():
    """the folder install.py --folder wrote into the menu copy, so headless runs and --doctor use it too"""
    import ast
    try:
        m = re.search(r"^DEFAULT_DIR = ('.*?'|\".*?\")", open(menu_path(), encoding="utf-8").read(), re.M)
        return ast.literal_eval(m.group(1)) if m else None
    except (OSError, ValueError, SyntaxError):
        return None


def out_dir():
    d = os.environ.get("DAVINCI_YOINK_DIR") or DEFAULT_DIR or _menu_folder()
    if d:
        return os.path.abspath(os.path.expanduser(d))
    sub = "Movies" if SYSTEM == "Darwin" else "Videos"
    return os.path.join(os.path.expanduser("~"), sub, "DaVinci Yoink")


def tool_env():
    """PATH with the usual tool folders added: Resolve started from the Dock, Finder or Start menu often sees only a
    short system PATH, so Homebrew, pip --user and similar installs would be invisible"""
    home = os.path.expanduser("~")
    extra = [os.path.join(home, ".local", "bin"), os.path.join(home, ".deno", "bin")]
    if SYSTEM == "Darwin":
        extra += ["/opt/homebrew/bin", "/usr/local/bin"] + sorted(glob.glob(os.path.join(home, "Library", "Python", "*", "bin")))
        extra += sorted(glob.glob("/Library/Frameworks/Python.framework/Versions/*/bin"), reverse=True)   # python.org
    elif SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        extra += sorted(glob.glob(os.path.join(appdata, "Python", "Python3*", "Scripts")))
        extra += sorted(glob.glob(os.path.join(local, "Programs", "Python", "Python3*")))
        extra += sorted(glob.glob(os.path.join(local, "Programs", "Python", "Python3*", "Scripts")))
        extra += [r"C:\Program Files\nodejs", os.path.join(local, "Microsoft", "WinGet", "Links")]
    else:
        extra += ["/usr/local/bin"]
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    add = [p for p in extra if os.path.isdir(p) and p not in parts]
    # macOS: the added folders go FIRST, so Homebrew's tools win over /usr/bin (whose python3 is a stub that pops the
    # "install command line developer tools" dialog when those tools are missing)
    env["PATH"] = os.pathsep.join(add + parts if SYSTEM == "Darwin" else parts + add)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _apple_dev_tools():
    """True when /usr/bin/python3 is a real interpreter; on macOS it is a stub until the command line tools exist"""
    if SYSTEM != "Darwin":
        return True
    try:
        return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode == 0
    except OSError:
        return False


def ytdlp_cmd(env):
    """yt-dlp as an executable, else as a module of a Python on PATH. Resolve's own interpreter is not used (inside
    Resolve, sys.executable is Resolve itself); the Microsoft Store python stub is skipped"""
    path = env["PATH"]
    override = os.environ.get("DAVINCI_YOINK_YTDLP")
    if override:
        found = shutil.which(override, path=path) or (override if os.path.isfile(override) else None)
        if not found:
            raise RuntimeError(f"DAVINCI_YOINK_YTDLP is set to {override}, which does not exist")
        return [found]
    exe = shutil.which("yt-dlp", path=path)
    if exe:
        return [exe]
    names = ("py", "python", "python3") if SYSTEM == "Windows" else ("python3", "python")
    for name in names:
        p = shutil.which(name, path=path)
        if not p or "WindowsApps" in p or (p.startswith("/usr/bin/") and not _apple_dev_tools()):
            continue
        r = subprocess.run([p, "-c", "import yt_dlp"], capture_output=True, env=env, creationflags=NO_WINDOW)
        if r.returncode == 0:
            return [p, "-m", "yt_dlp"]
    raise RuntimeError("yt-dlp not found: install it with  " + PIP_FIX)


def js_runtime(env):
    for rt in ("deno", "node"):
        if shutil.which(rt, path=env["PATH"]):
            return ["--js-runtimes", rt]
    return []


def private_copy(path):
    """True when a packaged app's private storage holds a copy of this AppData file. Windows redirects AppData writes
    made from inside packaged apps (such as AI desktop apps) into Packages\\<app>\\LocalCache, and other programs never
    see them. From inside that app both the real file and the private one look present, so this is a warning there;
    the doctor's Resolve view (which sees only real files) is the proof either way."""
    if SYSTEM != "Windows" or not path:
        return False
    pkgs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages")
    for base, sub in ((os.environ.get("APPDATA"), "Roaming"), (os.environ.get("LOCALAPPDATA"), "Local")):
        if base and os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(base) + os.sep):
            rel = os.path.relpath(path, base)
            try:
                apps = os.listdir(pkgs)
            except OSError:
                return False
            return any(os.path.exists(os.path.join(pkgs, a, "LocalCache", sub, rel)) for a in apps)
    return False


def menu_path():
    """where install.py puts the copy that Resolve lists under Workspace > Scripts (per user)"""
    home = os.path.expanduser("~")
    if SYSTEM == "Windows":
        base = os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")),
                            "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts")
    elif SYSTEM == "Darwin":
        base = os.path.join(home, "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve",
                            "Fusion", "Scripts")
    else:
        base = os.path.join(home, ".local", "share", "DaVinciResolve", "Fusion", "Scripts")
    return os.path.join(base, "Utility", "DaVinci Yoink.py")


def menu_paths():
    """the per-user copy first, then the system-wide Scripts folder (an install there is listed too)"""
    system = {"Windows": os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design",
                                      "DaVinci Resolve", "Fusion", "Scripts"),
              "Darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts",
              "Linux": "/opt/resolve/Fusion/Scripts"}.get(SYSTEM)
    return [menu_path()] + ([os.path.join(system, "Utility", "DaVinci Yoink.py")] if system else [])


def python_path():
    """the interpreter behind this Python (its base install, not a venv; inside Resolve, the Python Resolve loaded)"""
    base = getattr(sys, "base_prefix", sys.prefix)
    return os.path.join(base, "python.exe") if SYSTEM == "Windows" else os.path.join(base, "bin", "python3")


def menu_version(path=None):
    try:
        m = re.search(r'^__version__ = "([^"]+)"', open(path or menu_path(), encoding="utf-8").read(), re.M)
        return m.group(1) if m else "unknown"
    except OSError:
        return None


def check(inside_resolve=False):
    """what DaVinci Yoink can find, the way it finds it at download time. inside_resolve: running in Resolve's own
    Python, which sees only real files, so the private-storage warnings do not apply there."""
    env = tool_env()
    rep = {"version": __version__, "python": sys.version.split()[0], "python_path": python_path(),
           "venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
           "yt-dlp": None, "ffmpeg": None, "js": None, "folder": out_dir(), "menu": None, "problems": [], "warnings": []}
    found = [(p, menu_version(p)) for p in menu_paths()]
    found = [(p, v) for p, v in found if v is not None]
    if not found:
        rep["problems"].append("not installed in Resolve's Scripts menu: python install.py")
    else:
        p, mv = found[0]
        rep["menu"] = f"{p} (version {mv})"
        if mv != __version__:
            rep["problems"].append(f"the menu copy is version {mv} and this one is {__version__}: python install.py")
        if not inside_resolve and private_copy(p):
            rep["warnings"].append(f"a packaged app's private storage holds a copy of {p}; if Resolve does not list "
                                   "DaVinci Yoink, start Resolve once, then run python install.py again")
    try:
        cmd = ytdlp_cmd(env)
        where = cmd[0]
        if cmd[1:2] == ["-m"]:                                  # yt-dlp as a module: locate the module itself
            r = subprocess.run([cmd[0], "-c", "import yt_dlp; print(yt_dlp.__file__)"], capture_output=True,
                               text=True, env=env, creationflags=NO_WINDOW)
            where = r.stdout.strip() or cmd[0]
        rep["yt-dlp"] = where
    except RuntimeError as e:
        rep["problems"].append(str(e))
    rep["ffmpeg"] = shutil.which("ffmpeg", path=env["PATH"])
    if not rep["ffmpeg"]:
        rep["problems"].append("ffmpeg not found: " + {"Windows": "winget install Gyan.FFmpeg",
                                                         "Darwin": "brew install ffmpeg"}.get(SYSTEM, "install ffmpeg"))
    rep["js"] = next((shutil.which(rt, path=env["PATH"]) for rt in ("deno", "node") if shutil.which(rt, path=env["PATH"])), None)
    if not inside_resolve:
        for name in ("yt-dlp", "ffmpeg"):
            if private_copy(rep[name]):
                rep["warnings"].append(f"a packaged app's private storage holds a copy of {name} ({rep[name]}); if "
                                       f"Resolve's view below misses it, reinstall with  {PIP_FIX}")
    return rep


def _doctor():
    """print what this Python finds, then ask Resolve's own Python (Resolve Studio, external scripting on)"""
    import tempfile, time
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # paths with any characters print when piped
    me = os.path.abspath(__file__)
    rep = check()
    print(f"DaVinci Yoink {rep['version']} (this shell, Python {rep['python']}: {rep['python_path']}"
          f"{', a virtual environment' if rep['venv'] else ''})")
    for k in ("yt-dlp", "ffmpeg", "js", "menu", "folder"):
        print(f"  {k:7} {rep[k] or 'MISSING'}")
    for w in rep["warnings"]:
        print("  warning: " + w)
    try:
        r = _scripting_module().scriptapp("Resolve")
    except Exception:
        r = None
    res = None
    if r is None:
        print("Resolve: not reachable (it needs Resolve Studio open with external scripting set to Local).")
    else:
        tmp = tempfile.gettempdir()
        out = os.path.join(tmp, "davinci_yoink_doctor.json")
        probe = os.path.join(tmp, "davinci_yoink_doctor.py")
        if os.path.exists(out):
            os.remove(out)
        with open(probe, "w", encoding="utf-8") as f:              # load this very file by path, write atomically
            f.write("import importlib.util, json, os\n"
                    f"spec = importlib.util.spec_from_file_location('davinci_yoink_probe', {me!r})\n"
                    "d = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(d)\n"
                    f"tmp = {out!r} + '.part'\n"
                    "json.dump(d.check(inside_resolve=True), open(tmp, 'w', encoding='utf-8'))\n"
                    f"os.replace(tmp, {out!r})\n")
        r.Fusion().RunScript(probe)
        for _ in range(75):
            if os.path.exists(out):
                try:
                    with open(out, encoding="utf-8") as fh:
                        res = json.load(fh)
                    break
                except ValueError:
                    pass
            time.sleep(0.2)
        for f in (probe, out, out + ".part"):                    # the probe only reads; leave nothing behind
            try:
                os.remove(f)
            except OSError:
                pass
        if res:
            print(f"Resolve's own Python {res['python']} ({res['python_path']}) sees:")
            for k in ("yt-dlp", "ffmpeg", "js", "menu"):
                print(f"  {k:7} {res[k] or 'MISSING'}")
            if os.path.normcase(res["python_path"]) != os.path.normcase(rep["python_path"]):
                print(f"note: Resolve runs a different Python than this shell; if anything is missing, run "
                      f"install.py with {res['python_path']}")
        else:
            print("Resolve: answered but did not run the probe (its Python could not load DaVinci Yoink).")
    if res is None:                                           # never certify without Resolve's own view
        print("PROBLEMS (this shell):\n  " + "\n  ".join(rep["problems"]) if rep["problems"] else
              "this shell finds everything.")
        print("NOT VERIFIED from Resolve's side: open Workspace > Scripts > DaVinci Yoink; its status line reads "
              "'ready' when everything is found.")
        sys.exit(2)
    problems = res["problems"]
    print("OK: ready to yoink" if not problems else "PROBLEMS:\n  " + "\n  ".join(problems))
    sys.exit(1 if problems else 0)


def download(url, quality="1080", folder=None, status=print):
    quality = quality if quality in FORMATS else "1080"
    folder = folder or out_dir()
    os.makedirs(folder, exist_ok=True)
    env = tool_env()
    if not shutil.which("ffmpeg", path=env["PATH"]):
        raise RuntimeError("ffmpeg not found: install it and put it on your PATH")
    tag = "" if quality == "1080" else f" {quality}"            # each quality keeps its own file
    cmd = ytdlp_cmd(env) + js_runtime(env) + [
        "--encoding", "utf-8", "--no-playlist", "-I", "1", "-f", FORMATS[quality],
        "-P", folder, "-o", f"%(title).90B [%(id)s]{tag}.%(ext)s",
        "--print", "after_move:%(filepath)j", "--print", "after_move:%(title)j",
        "--progress", "--newline", "--progress-template", "download:YOINK %(progress._percent_str)s"]
    if quality == "1080":
        cmd += ["-S", "res:1080"]                              # the SMALLER side capped: vertical videos stay sharp
    cmd += ["-x", "--audio-format", "wav"] if quality == "audio" else ["--merge-output-format", "mp4"]
    cmd += ["--", url]
    status("downloading ...")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=env, creationflags=NO_WINDOW)
    lines, printed = [], []
    for line in p.stdout:
        line = line.rstrip()
        lines.append(line)
        if line.startswith("YOINK"):
            status("downloading " + line[5:].strip())
        elif line.startswith('"'):
            try:
                printed.append(json.loads(line))
            except ValueError:
                pass
    p.wait()
    path = next((x for x in printed if isinstance(x, str) and os.path.isfile(x)), None)
    if p.returncode != 0 or not path:
        tail = [l for l in lines if not l.startswith("YOINK")][-4:]
        raise RuntimeError("yt-dlp failed: " + " | ".join(tail))
    title = next((x for x in printed if isinstance(x, str) and x != path), os.path.basename(path))
    if SYSTEM == "Linux" and quality != "audio":               # Resolve on Linux does not decode AAC
        mov = os.path.splitext(path)[0] + ".mov"
        if not os.path.exists(mov):
            status("converting the audio to PCM for Resolve on Linux ...")
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", path, "-c:v", "copy",
                            "-c:a", "pcm_s16le", mov], check=True, env=env)
        path = mov
    log = os.path.join(folder, "yoink_log.csv")
    new = not os.path.exists(log)
    with open(log, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "url", "title", "file", "quality"])
        w.writerow([datetime.datetime.now().isoformat(timespec="seconds"), url, title, path, quality])
    return path, title


def _frames(tc, fps, drop=False):
    """timecode -> absolute frame count; drop-frame timecodes (29.97 / 59.94 DF) use the drop-frame formula"""
    h, m, s, f = (int(x) for x in re.split(r"[:;.]", tc))
    r = int(round(fps))
    if drop or ";" in tc:
        d = 2 * (r // 30)                                      # frames dropped per minute: 2 at 29.97, 4 at 59.94
        total_min = 60 * h + m
        return ((h * 3600 + m * 60 + s) * r + f) - d * (total_min - total_min // 10)
    return ((h * 60 + m) * 60 + s) * r + f


def _num(x, default):
    try:
        return float(str(x).split()[0])
    except (ValueError, IndexError):
        return default


def place(resolve, path, to_timeline=True, status=print):
    """import into the Yoinked bin and drop on the current timeline at the playhead"""
    project = resolve.GetProjectManager().GetCurrentProject()
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    prev_folder = mp.GetCurrentFolder()
    folder = next((f for f in root.GetSubFolderList() if f.GetName() == BIN), None) or mp.AddSubFolder(root, BIN)
    mp.SetCurrentFolder(folder)
    try:
        norm = os.path.normcase(os.path.abspath(path))
        item = next((c for c in (folder.GetClipList() or [])
                     if os.path.normcase(os.path.abspath(c.GetClipProperty("File Path") or "")) == norm), None)
        if item is None:
            items = mp.ImportMedia([path]) or []
            if not items:
                raise RuntimeError(f"Resolve could not import {path}")
            item = items[0]
    finally:
        if prev_folder:
            mp.SetCurrentFolder(prev_folder)
    tl = project.GetCurrentTimeline()
    if not to_timeline or tl is None:
        status("in the Yoinked bin" + ("" if tl else " (no timeline open)"))
        return item, []
    fps = _num(tl.GetSetting("timelineFrameRate"), 24.0)
    drop = str(tl.GetSetting("timelineDropFrameTimecode")) == "1"
    ph = _frames(tl.GetCurrentTimecode(), fps, drop)
    cfps = _num(item.GetClipProperty("FPS"), fps)
    nfr = int(_num(item.GetClipProperty("Frames"), 0))
    dur = int(round(nfr * fps / cfps)) if nfr else int(fps * 10)

    def free_track(kind):
        for n in range(1, (tl.GetTrackCount(kind) or 0) + 1):
            if tl.GetIsTrackLocked(kind, n):
                continue
            if not any(it.GetStart() < ph + dur and it.GetEnd() > ph for it in (tl.GetItemListInTrack(kind, n) or [])):
                return n
        ok = tl.AddTrack(kind) if kind == "video" else tl.AddTrack("audio", "stereo")
        if not ok:
            raise RuntimeError(f"could not add a {kind} track")
        return tl.GetTrackCount(kind)

    is_audio = item.GetClipProperty("Type") == "Audio"
    has_audio = is_audio or item.GetClipProperty("Audio Ch") not in (None, "", "0")
    placed, where = [], []
    # picture and sound are laid separately (a single append drops the sound when the matching audio track is
    # missing), each on the first free unlocked track at the playhead, then linked. endFrame is exclusive.
    for kind, mtype in (([] if is_audio else [("video", 1)]) + ([("audio", 2)] if has_audio else [])):
        n = free_track(kind)
        info = {"mediaPoolItem": item, "trackIndex": n, "recordFrame": ph, "mediaType": mtype}
        if nfr:
            info.update({"startFrame": 0, "endFrame": nfr})
        got = mp.AppendToTimeline([info])
        if not got:
            if placed:
                tl.DeleteClips(placed, False)                    # never leave half a clip behind
            raise RuntimeError(f"AppendToTimeline refused the {kind}")
        placed += got
        where.append(f"{kind[0].upper()}{n}")
    if len(placed) > 1:
        tl.SetClipsLinked(placed, True)
    status(f"on the timeline at the playhead: {' + '.join(where)}")
    return item, placed


def gui(resolve, fu, bmd):
    problems = [p for p in check(inside_resolve=True)["problems"] if "Scripts menu" not in p]   # it is running
    first = f"ready  ({out_dir()})" if not problems else "not ready: " + "  |  ".join(problems)
    ui = fu.UIManager
    disp = bmd.UIDispatcher(ui)
    win = disp.AddWindow({"ID": "Yoink", "WindowTitle": "DaVinci Yoink", "Geometry": [300, 300, 600, 170]},
                         ui.VGroup([
                             ui.HGroup([ui.Label({"Text": "URL", "Weight": 0}),
                                        ui.LineEdit({"ID": "Url", "PlaceholderText": "paste a video link"})]),
                             ui.HGroup([ui.ComboBox({"ID": "Quality"}),
                                        ui.CheckBox({"ID": "Tl", "Text": "Drop on the timeline at the playhead",
                                                     "Checked": True}),
                                        ui.Button({"ID": "Go", "Text": "Yoink", "Weight": 0})]),
                             ui.Label({"ID": "Status", "Text": first, "WordWrap": True}),
                         ]))
    it = win.GetItems()
    for q in ("1080p H.264 (plays everywhere)", "Best quality (often 4K VP9/AV1)", "Audio only (WAV)"):
        it["Quality"].AddItem(q)

    def status(s):
        it["Status"].Text = s

    def go(ev):
        url = it["Url"].Text.strip()
        if not url:
            status("paste a link first")
            return
        q = ("1080", "best", "audio")[it["Quality"].CurrentIndex]
        try:
            path, title = download(url, q, status=status)
            place(resolve, path, bool(it["Tl"].Checked), status=status)
            status(f"yoinked: {title}")
            it["Url"].Text = ""
        except Exception as e:
            status(f"failed: {e}")

    win.On.Go.Clicked = go
    win.On.Url.ReturnPressed = go
    win.On.Yoink.Close = lambda ev: disp.ExitLoop()
    win.Show()
    disp.RunLoop()
    win.Hide()


def _scripting_module():
    """import DaVinciResolveScript for external scripting (RESOLVE_SCRIPT_API, else the standard install location)"""
    paths = {
        "Windows": os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design",
                                "DaVinci Resolve", "Support", "Developer", "Scripting"),
        "Darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        "Linux": "/opt/resolve/Developer/Scripting",
    }
    base = os.environ.get("RESOLVE_SCRIPT_API", paths.get(SYSTEM, ""))
    sys.path.append(os.path.join(base, "Modules"))
    import DaVinciResolveScript
    return DaVinciResolveScript


def _headless(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="davinci_yoink.py")
    ap.add_argument("--headless", required=True, metavar="URL")
    ap.add_argument("--quality", choices=sorted(FORMATS), default="1080")
    ap.add_argument("--no-timeline", action="store_true")
    a = ap.parse_args(argv)
    path, title = download(a.headless, a.quality)
    print("downloaded:", path)
    r = _scripting_module().scriptapp("Resolve")
    if r is None:
        sys.exit("Resolve is not answering: start Resolve Studio and set Preferences > System > General > "
                 "External scripting using: Local")
    place(r, path, not a.no_timeline)


if __name__ == "__main__" and "--doctor" in sys.argv:
    _doctor()
elif __name__ == "__main__" and "--headless" in sys.argv:
    _headless(sys.argv[1:])
elif "resolve" in globals() and ("fu" in globals() or "fusion" in globals()):
    gui(resolve, globals().get("fu") or globals().get("fusion"), bmd)  # noqa: F821  (Resolve's script globals)
