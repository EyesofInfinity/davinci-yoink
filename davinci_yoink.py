"""DaVinci Yoink: paste a video link, and it lands on your DaVinci Resolve timeline.

Workspace > Scripts > Utility > DaVinci Yoink. Paste a URL (YouTube or any site yt-dlp supports), pick a quality and
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

MIT License. See LICENSE.
"""
import csv, datetime, glob, json, os, platform, re, shutil, subprocess, sys

__version__ = "1.0.2"
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


def out_dir():
    d = os.environ.get("DAVINCI_YOINK_DIR") or DEFAULT_DIR
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
        return [override]
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
    raise RuntimeError('yt-dlp not found: install it with  pip install -U "yt-dlp[default]"')


def js_runtime(env):
    for rt in ("deno", "node"):
        if shutil.which(rt, path=env["PATH"]):
            return ["--js-runtimes", rt]
    return []


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
                             ui.Label({"ID": "Status", "Text": f"ready  ({out_dir()})", "WordWrap": True}),
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


if __name__ == "__main__" and "--headless" in sys.argv:
    _headless(sys.argv[1:])
elif "resolve" in globals() and ("fu" in globals() or "fusion" in globals()):
    gui(resolve, globals().get("fu") or globals().get("fusion"), bmd)  # noqa: F821  (Resolve's script globals)
