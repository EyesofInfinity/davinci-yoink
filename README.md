# DaVinci Yoink

Paste a video link, and it lands on your DaVinci Resolve timeline.

Premiere Pro has extensions that take a YouTube link and drop the video straight into a sequence. DaVinci Yoink does the same for DaVinci Resolve: one small script in Resolve's Scripts menu.

## What it does

1. You paste a URL (YouTube or any of the many sites [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports) and pick a quality.
2. It downloads the video to your Yoink folder.
3. It imports the file into a **Yoinked** bin in the Media Pool.
4. It drops the clip onto the current timeline **at the playhead**. Picture and sound each go on the first track that is free there, linked together. A new track is added if every track is busy.

Also:

- **1080p H.264 + AAC by default.** The smaller side is capped at 1080, so vertical videos keep their full resolution. *Best quality* takes the source's top stream (often 4K VP9 or AV1). *Audio only* saves a WAV.
- **Original-language audio** on auto-dubbed videos: the original track is preferred over the dubs.
- **A log of every yoink** (time, URL, title, file, quality) in `yoink_log.csv`, so you always know where a clip came from.
- **No duplicates:** yoinking the same link at the same quality again reuses the clip already in the bin. Each quality keeps its own file.
- **Playlist links** take the first video only.
- **Linux:** Resolve does not decode AAC there, so the audio is converted to PCM in a `.mov` after the download. The free Linux version of Resolve does not decode H.264 either; use Studio or *Best quality*.

## Requirements

- DaVinci Resolve with Python scripting available (the Scripts-menu tool works in Resolve; headless use needs Resolve Studio)
- Python 3.8+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) with its default extras: `pip install -U "yt-dlp[default]"`
- [ffmpeg](https://ffmpeg.org/)
- Recommended for YouTube: [Node.js](https://nodejs.org/) or [Deno](https://deno.com/). yt-dlp uses one of them for YouTube's player checks.

Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`), `pip --user` and the usual Windows Python folders are searched automatically, so tools are found even when Resolve was started from the Dock or Start menu with a short `PATH`. To point at a specific yt-dlp, set `DAVINCI_YOINK_YTDLP`.

## Install

```
git clone https://github.com/EyesofInfinity/davinci-yoink
cd davinci-yoink
python install.py
```

This copies the script into Resolve's `Fusion/Scripts/Utility` folder for your user. Restart Resolve if it's open, then open **Workspace > Scripts > DaVinci Yoink**.

To remove it: `python install.py --uninstall`

## Where files go

The `DAVINCI_YOINK_DIR` environment variable, else the folder set at install (`python install.py --folder <path>`), otherwise:

- Windows, Linux: `~/Videos/DaVinci Yoink`
- macOS: `~/Movies/DaVinci Yoink`

## Headless use

With Resolve Studio running and external scripting set to Local (Preferences > System > General > External scripting using):

```
python davinci_yoink.py --headless "<url>" [--quality 1080|best|audio] [--no-timeline]
```

## When a download breaks

Sites change often, and yt-dlp updates to keep up. Most failures are fixed by:

```
pip install -U "yt-dlp[default]"
```

## License

MIT. See [LICENSE](LICENSE).
