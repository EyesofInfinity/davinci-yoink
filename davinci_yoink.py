"""DaVinci Yoink: paste a video link, and it lands on your DaVinci Resolve timeline.

Workspace > Scripts > Utility > DaVinci Yoink. Paste a URL (YouTube or any site yt-dlp supports), pick a quality and
press Yoink. The file downloads to your Yoink folder, goes into a "Yoinked" bin in the Media Pool, and drops onto the
current timeline at the playhead: picture and sound each on the first track that is free there, linked together.

- 1080p H.264 + AAC by default, which Resolve decodes on every platform. "Best quality" takes the source's top
  stream (often VP9 or AV1 at 4K). "Audio only" saves a WAV.
- Auto-dubbed videos: the ORIGINAL-language audio track is preferred over the dubs.
- Every yoink is logged (time, URL, title, file) in yoink_log.csv inside the download folder.
- Yoinking the same link again reuses the clip already in the bin.

Requirements: yt-dlp (pip install -U yt-dlp) and ffmpeg on PATH. Node.js or Deno on PATH lets yt-dlp handle
YouTube's player challenges. Download folder: the DAVINCI_YOINK_DIR environment variable, else
~/Videos/DaVinci Yoink (Windows, Linux) or ~/Movies/DaVinci Yoink (macOS).

Headless use from outside Resolve (Resolve running, external scripting enabled):
    python davinci_yoink.py --headless <url> [--quality 1080|best|audio] [--no-timeline]

MIT License. See LICENSE.
"""
import csv, datetime, os, platform, re, shutil, subprocess, sys

__version__ = "1.0.0"
BIN = "Yoinked"
ORIG = "[format_note*=original]"
FORMATS = {
    "1080": f"bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]{ORIG}/bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
    "best": f"bv*+ba[ext=m4a]{ORIG}/bv*+ba/b",
    "audio": f"ba[ext=m4a]{ORIG}/ba[ext=m4a]/ba",
}


def out_dir():
    d = os.environ.get("DAVINCI_YOINK_DIR")
    if d:
        return d
    sub = "Movies" if platform.system() == "Darwin" else "Videos"
    return os.path.join(os.path.expanduser("~"), sub, "DaVinci Yoink")


def ytdlp_cmd():
    """yt-dlp as an executable on PATH, else as a module of a Python on PATH (Resolve's own interpreter is not used:
    inside Resolve, sys.executable is Resolve itself)"""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    for py in ("python3", "python", "py"):
        p = shutil.which(py)
        if p and subprocess.run([p, "-c", "import yt_dlp"], capture_output=True).returncode == 0:
            return [p, "-m", "yt_dlp"]
    raise RuntimeError("yt-dlp not found: install it with  pip install -U yt-dlp")


def js_runtime():
    for rt in ("deno", "node"):
        if shutil.which(rt):
            return ["--js-runtimes", rt]
    return []


def download(url, quality="1080", folder=None, status=print):
    folder = folder or out_dir()
    os.makedirs(folder, exist_ok=True)
    tmpl = os.path.join(folder, "%(title).90B [%(id)s].%(ext)s")
    cmd = ytdlp_cmd() + js_runtime() + ["--no-playlist", "-f", FORMATS.get(quality, FORMATS["1080"]), "-o", tmpl,
                                         "--print", "after_move:filepath", "--print", "after_move:%(title)s",
                                         "--newline", "--no-warnings"]
    cmd += ["-x", "--audio-format", "wav"] if quality == "audio" else ["--merge-output-format", "mp4"]
    cmd.append(url)
    status("downloading ...")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=flags)
    lines = []
    for line in p.stdout:
        line = line.rstrip()
        lines.append(line)
        m = re.search(r"(\d+(?:\.\d+)?)%", line)
        if m:
            status(f"downloading {m.group(1)}%")
    p.wait()
    paths = [l for l in lines if os.path.isfile(l)]
    if p.returncode != 0 or not paths:
        raise RuntimeError("yt-dlp failed: " + " | ".join(lines[-4:]))
    path = paths[-1]
    i = lines.index(path)
    title = lines[i + 1] if i + 1 < len(lines) else os.path.basename(path)
    with open(os.path.join(folder, "yoink_log.csv"), "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([datetime.datetime.now().isoformat(timespec="seconds"), url, title, path, quality])
    return path, title


def _frames(tc, fps):
    h, m, s, f = (int(x) for x in re.split(r"[:;]", tc))
    return ((h * 60 + m) * 60 + s) * int(round(fps)) + f


def place(resolve, path, to_timeline=True, status=print):
    """import into the Yoinked bin and drop on the current timeline at the playhead"""
    project = resolve.GetProjectManager().GetCurrentProject()
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    folder = next((f for f in root.GetSubFolderList() if f.GetName() == BIN), None) or mp.AddSubFolder(root, BIN)
    mp.SetCurrentFolder(folder)
    norm = os.path.normcase(os.path.abspath(path))
    item = next((c for c in (folder.GetClipList() or [])
                 if os.path.normcase(os.path.abspath(c.GetClipProperty("File Path") or "")) == norm), None)
    if item is None:
        items = mp.ImportMedia([path]) or []
        if not items:
            raise RuntimeError(f"Resolve could not import {path}")
        item = items[0]
    tl = project.GetCurrentTimeline()
    if not to_timeline or tl is None:
        status("in the Yoinked bin" + ("" if tl else " (no timeline open)"))
        return item, []
    fps = float(tl.GetSetting("timelineFrameRate") or 24)
    ph = _frames(tl.GetCurrentTimecode(), fps)
    cfps = float(item.GetClipProperty("FPS") or fps)
    nfr = int(float(item.GetClipProperty("Frames") or 0))
    dur = int(round(nfr * fps / cfps)) if nfr else int(fps * 10)

    def free_track(kind):
        for n in range(1, (tl.GetTrackCount(kind) or 0) + 1):
            if not any(it.GetStart() < ph + dur and it.GetEnd() > ph for it in (tl.GetItemListInTrack(kind, n) or [])):
                return n
        tl.AddTrack(kind) if kind == "video" else tl.AddTrack("audio", "stereo")
        return tl.GetTrackCount(kind)

    is_audio = item.GetClipProperty("Type") == "Audio"
    has_audio = is_audio or item.GetClipProperty("Audio Ch") not in (None, "", "0")
    placed, where = [], []
    # picture and sound are laid separately (a single append drops the sound when the matching audio track is
    # missing), each on the first track free at the playhead, then linked
    for kind, mtype in (([] if is_audio else [("video", 1)]) + ([("audio", 2)] if has_audio else [])):
        n = free_track(kind)
        got = mp.AppendToTimeline([{"mediaPoolItem": item, "startFrame": 0, "endFrame": nfr, "trackIndex": n,
                                    "recordFrame": ph, "mediaType": mtype}])
        if not got:
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
    win = disp.AddWindow({"ID": "Yoink", "WindowTitle": "DaVinci Yoink", "Geometry": [300, 300, 580, 170]},
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
    """import DaVinciResolveScript from its standard install location (external scripting)"""
    paths = {
        "Windows": os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design",
                                "DaVinci Resolve", "Support", "Developer", "Scripting", "Modules"),
        "Darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        "Linux": "/opt/resolve/Developer/Scripting/Modules",
    }
    sys.path.append(os.environ.get("RESOLVE_SCRIPT_API_MODULES", paths.get(platform.system(), "")))
    import DaVinciResolveScript
    return DaVinciResolveScript


if __name__ == "__main__" and "--headless" in sys.argv:
    a = sys.argv[1:]
    url = a[a.index("--headless") + 1]
    q = a[a.index("--quality") + 1] if "--quality" in a else "1080"
    path, title = download(url, q)
    print("downloaded:", path)
    r = _scripting_module().scriptapp("Resolve")
    if r is None:
        sys.exit("Resolve is not answering: start it and enable external scripting (Preferences > System > General)")
    place(r, path, "--no-timeline" not in a)
elif "fu" in globals() and "resolve" in globals():
    gui(resolve, fu, bmd)  # noqa: F821  (globals Resolve provides to scripts run from its Scripts menu)
