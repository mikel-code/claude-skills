# /// script
# requires-python = ">=3.10"
# dependencies = ["yt-dlp"]
# ///
"""Transcribe a video URL to an OKF-frontmatter markdown file.

Strategy (network-frugal, deterministic):
  1. Probe metadata only (no download).
  2. If captions exist, download ONLY the caption track (json3) — never the video.
     Preference: manual subs in the requested/original language > any manual >
     auto captions original track > auto in requested language.
  3. If no usable captions, download bestaudio only (smallest download with full
     audio quality; tiny-video fallback for sites without audio-only formats)
     and hand it to transcribe.py (mlx-whisper + validation + repair ladder).

Usage:
    uv run yt_transcript.py <url> [options]
    uv run yt_transcript.py <url> --probe          # metadata + caption availability, no download
Options:
    -o FILE          output markdown path (default: <output-dir>/<date>-<slug>.md)
    --output-dir DIR directory for the default filename (default: cwd)
    --lang XX        preferred transcript language (default: video's original language)
    --no-fallback    captions only — exit 3 instead of downloading audio
    --force-whisper  skip captions, transcribe the audio even if captions exist
    --keep-audio     keep the downloaded media file next to the markdown
Exit codes: 0 ok, 1 error, 2 usage, 3 no captions and fallback disabled/failed.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp

SCRIPT_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------------ helpers

def slugify(title: str, max_len: int = 60) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9áéíóúüñ\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:max_len].rstrip("-") or "video"


def format_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def iso_date(upload_date: str) -> str:
    if upload_date and len(upload_date) == 8:
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return ""


# ------------------------------------------------------------ caption logic

def caption_langs(meta: dict) -> dict:
    subs = {k: v for k, v in (meta.get("subtitles") or {}).items() if k != "live_chat"}
    autos = meta.get("automatic_captions") or {}
    return {"manual": sorted(subs), "auto": sorted(autos)}


def pick_caption(meta: dict, prefer: str) -> tuple[str, str] | None:
    """Return (kind, lang-key) — kind is 'manual'|'auto' — or None."""
    subs = {k: v for k, v in (meta.get("subtitles") or {}).items() if k != "live_chat"}
    autos = meta.get("automatic_captions") or {}
    original = (meta.get("language") or "").split("-")[0]
    if not original:
        orig_track = next((k for k in autos if k.endswith("-orig")), "")
        original = orig_track[:-5].split("-")[0] if orig_track else ""

    def match(tracks: dict, lang: str) -> str | None:
        if not lang:
            return None
        if lang in tracks:
            return lang
        return next((k for k in sorted(tracks) if k.split("-")[0] == lang), None)

    for lang in filter(None, [prefer, original, "en", "es"]):
        key = match(subs, lang)
        if key:
            return ("manual", key)
    if subs:
        return ("manual", sorted(subs)[0])

    # Auto captions: the original ASR track is trustworthy; translated tracks
    # only when the user explicitly asked for that language.
    for lang in filter(None, [prefer, original]):
        if f"{lang}-orig" in autos:
            return ("auto", f"{lang}-orig")
        key = match(autos, lang)
        if key and (lang == prefer or lang == original):
            return ("auto", key)
    return None


def parse_json3(data: dict) -> list[dict]:
    segments = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        segments.append({"start": event.get("tStartMs", 0) / 1000.0,
                         "end": (event.get("tStartMs", 0) + event.get("dDurationMs", 0)) / 1000.0,
                         "text": re.sub(r"\s+", " ", text)})
    return segments


def group_into_paragraphs(segments: list[dict], target_words: int = 50) -> list[dict]:
    paragraphs, buf, start, wc = [], [], None, 0
    for seg in segments:
        if start is None:
            start = seg["start"]
        buf.append(seg["text"])
        wc += len(seg["text"].split())
        if wc >= target_words:
            paragraphs.append({"start": start, "text": " ".join(buf).strip()})
            buf, start, wc = [], None, 0
    if buf:
        paragraphs.append({"start": start, "text": " ".join(buf).strip()})
    return paragraphs


# ---------------------------------------------------------------- downloads

def extract_metadata(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        meta = ydl.extract_info(url, download=False)
    if not meta:
        sys.exit(f"could not extract metadata for {url}")
    if meta.get("_type") == "playlist":
        sys.exit("this is a playlist/channel URL — pass a single video URL")
    return meta


def download_captions(url: str, lang_key: str, kind: str, tmp: Path) -> list[dict] | None:
    opts = {
        "skip_download": True,
        "writesubtitles": kind == "manual",
        "writeautomaticsub": kind == "auto",
        "subtitleslangs": [lang_key],
        "subtitlesformat": "json3",
        "outtmpl": str(tmp / "captions.%(ext)s"),
        "quiet": True, "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    sub_file = next(tmp.glob("captions*.json3"), None)
    if not sub_file:
        return None
    return parse_json3(json.loads(sub_file.read_text(encoding="utf-8")))


def download_audio(url: str, tmp: Path) -> Path:
    opts = {
        # bestaudio = full audio quality, no video payload; the height-capped
        # fallbacks keep the download small on sites without audio-only formats.
        "format": "bestaudio/best[height<=360]/worst[height>=240]/best",
        "outtmpl": str(tmp / "media.%(ext)s"),
        "quiet": True, "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    media = next((p for p in sorted(tmp.iterdir())
                  if p.stem == "media" and not p.name.endswith(".part")), None)
    if not media:
        sys.exit("audio download produced no file")
    return media


def run_whisper(media: Path, language: str, duration: float) -> dict:
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found on PATH — required to run the whisper fallback")
    out_json = media.with_suffix(".transcript.json")
    cmd = [uv, "run", "--quiet", str(SCRIPT_DIR / "transcribe.py"), str(media),
           "-o", str(out_json), "--duration", str(duration)]
    if language:
        cmd += ["--language", language]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        diag = out_json.with_suffix(out_json.suffix + ".diag.json")
        if diag.exists():
            summary = json.loads(diag.read_text()).get("summary", "")
            sys.exit(f"transcription failed: {summary}")
        sys.exit(3)
    return json.loads(out_json.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- output

def okf_frontmatter(meta: dict, source: str, language: str,
                    transcription: dict | None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = meta.get("webpage_url") or meta.get("original_url") or ""
    desc = next((line.strip() for line in (meta.get("description") or "").splitlines()
                 if line.strip()), meta.get("title", ""))[:200]
    generated_by = ("machine:youtube-captions" if source.startswith("captions")
                    else f"machine:mlx-whisper/{(transcription or {}).get('model', '').split('/')[-1]}")

    lines = ["---",
             "type: Video Transcript",
             f"title: {yaml_str(meta.get('title', ''))}",
             f"description: {yaml_str(desc)}",
             f"resource: {url}",
             'tags: ["video", "transcript", "youtube"]'
             if "youtube" in (meta.get("extractor", "") or "").lower()
             else 'tags: ["video", "transcript"]',
             "generated:",
             f"  by: {generated_by}",
             f"  at: {now}",
             "sources:",
             f"  - resource: {url}",
             "status: stable",
             "video:"]
    if meta.get("channel") or meta.get("uploader"):
        lines.append(f"  channel: {yaml_str(meta.get('channel') or meta.get('uploader'))}")
    published = iso_date(meta.get("upload_date", ""))
    if published:
        lines.append(f"  published: {published}")
    if meta.get("duration"):
        lines.append(f"  duration: {yaml_str(format_duration(meta['duration']))}")
    lines.append(f"  video_id: {meta.get('id', '')}")
    if language:
        lines.append(f"  language: {language}")
    lines.append(f"  transcript_source: {source}")
    if transcription:
        lines.append("  transcription:")
        lines.append(f"    model: {transcription.get('model', '')}")
        repairs = transcription.get("repairs") or []
        lines.append(f"    repairs: [{', '.join(yaml_str(r) for r in repairs)}]")
    lines.append("---")
    return "\n".join(lines) + "\n"


def write_markdown(path: Path, meta: dict, paragraphs: list[dict], source: str,
                   language: str, transcription: dict | None) -> None:
    md = okf_frontmatter(meta, source, language, transcription)
    md += f"\n# {meta.get('title', '')}\n\n## Transcript\n\n"
    for p in paragraphs:
        md += f"**[{format_ts(p['start'])}]** {p['text']}\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


# --------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="Video URL -> OKF markdown transcript")
    ap.add_argument("url", help="video URL (YouTube or any yt-dlp-supported site)")
    ap.add_argument("--probe", action="store_true", help="print metadata + caption availability, no download")
    ap.add_argument("-o", "--output", default="", help="output markdown path")
    ap.add_argument("--output-dir", default=".", help="directory for default filename")
    ap.add_argument("--lang", default="", help="preferred transcript language (ISO 639-1)")
    ap.add_argument("--no-fallback", action="store_true", help="captions only; exit 3 if none")
    ap.add_argument("--force-whisper", action="store_true", help="transcribe audio even if captions exist")
    ap.add_argument("--keep-audio", action="store_true", help="keep the downloaded media file")
    args = ap.parse_args()

    try:
        meta = extract_metadata(args.url)
    except yt_dlp.utils.DownloadError as exc:
        sys.exit(f"metadata extraction failed: {exc}")

    prefer = args.lang.split("-")[0].lower() if args.lang else ""
    choice = pick_caption(meta, prefer)

    if args.probe:
        print(json.dumps({
            "id": meta.get("id"), "title": meta.get("title"),
            "channel": meta.get("channel") or meta.get("uploader"),
            "published": iso_date(meta.get("upload_date", "")),
            "duration": format_duration(meta.get("duration") or 0),
            "language": meta.get("language"),
            "captions": caption_langs(meta),
            "would_use": {"kind": choice[0], "lang": choice[1]} if choice else "whisper-fallback",
        }, ensure_ascii=False, indent=2))
        return

    published = iso_date(meta.get("upload_date", ""))
    basename = f"{published + '-' if published else ''}{slugify(meta.get('title', ''))}"
    out_path = Path(args.output) if args.output else Path(args.output_dir) / f"{basename}.md"

    with tempfile.TemporaryDirectory(prefix="yt-transcript-") as tmpdir:
        tmp = Path(tmpdir)
        paragraphs, source, language, transcription = None, "", "", None

        if choice and not args.force_whisper:
            kind, lang_key = choice
            print(f"captions: {kind} track '{lang_key}' — downloading subtitles only", file=sys.stderr)
            segments = download_captions(args.url, lang_key, kind, tmp)
            if segments:
                paragraphs = group_into_paragraphs(segments)
                source = f"captions-{kind}"
                language = lang_key.replace("-orig", "").split("-")[0]
            else:
                print("caption download returned nothing — falling back", file=sys.stderr)

        if paragraphs is None:
            if args.no_fallback:
                print("no usable captions and --no-fallback given", file=sys.stderr)
                sys.exit(3)
            language = prefer or (meta.get("language") or "").split("-")[0]
            print(f"no captions — downloading audio for whisper"
                  f"{' (language: ' + language + ')' if language else ''}", file=sys.stderr)
            media = download_audio(args.url, tmp)
            size_mb = media.stat().st_size / 1e6
            print(f"downloaded {media.name} ({size_mb:.1f} MB) — transcribing", file=sys.stderr)
            result = run_whisper(media, language, float(meta.get("duration") or 0))
            transcription = result.get("transcription", {})
            language = transcription.get("language", language)
            paragraphs = group_into_paragraphs(result.get("segments", []))
            source = "whisper"
            if args.keep_audio:
                kept = out_path.parent / f"{basename}{media.suffix}"
                shutil.copy2(media, kept)
                print(f"audio kept: {kept}", file=sys.stderr)

        write_markdown(out_path, meta, paragraphs, source, language, transcription)
        print(str(out_path))


if __name__ == "__main__":
    main()
