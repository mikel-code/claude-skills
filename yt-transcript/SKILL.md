---
name: yt-transcript
description: "Transcribe a video URL (YouTube or any yt-dlp-supported site) to a markdown file with Open Knowledge Format frontmatter. Downloads only subtitles when captions exist; otherwise downloads audio-only and transcribes locally with mlx-whisper (validated, with a repair ladder). Use when the user asks to transcribe a video, get a YouTube transcript, or turn a video into notes/markdown."
license: MIT
compatibility: Requires Python 3.10+ with uv and ffmpeg; the no-captions fallback additionally requires Apple Silicon (mlx-whisper)
---

# yt-transcript

Deterministic, script-driven video transcription. The scripts do all the work —
do not hand-roll yt-dlp or whisper invocations.

## Strategy (network-frugal)

1. **Probe first** — metadata only, no download.
2. **Captions if available** — downloads only the subtitle track (json3 on
   YouTube, WebVTT on other sites), never the video. Manual subs are preferred
   over auto captions; auto captions use
   the original ASR track, never a translated one (unless the user asked for a
   specific language with `--lang`).
3. **Whisper fallback** — when no usable captions exist, downloads `bestaudio`
   only (full audio quality, no video payload; a small-video fallback covers
   sites without audio-only formats) and transcribes locally with mlx-whisper
   `large-v3-turbo`. Explicit language from the video metadata (one 30 s probe
   if absent — never per-window auto-detect), deterministic validation
   (repetition loops, n-gram loops, zlib compression ratio, timestamp
   coverage), and a repair ladder:
   condition-on-previous-text off → chunked 10-min splice → escalate to
   large-v3 → needs-attention (`.diag.json` + nonzero exit, nothing written).

## Commands

All via `uv run` (dependencies resolve automatically from inline metadata):

```bash
# What would happen — metadata + caption availability, no download
uv run <skill-dir>/scripts/yt_transcript.py "<url>" --probe

# Transcribe to markdown (default filename: <published>-<slug>.md in cwd)
uv run <skill-dir>/scripts/yt_transcript.py "<url>" -o ~/Documents/talk.md

# Options
#   --lang es          prefer a caption/transcription language
#   --output-dir DIR   directory for the default filename
#   --no-fallback      captions only; exit 3 if none
#   --force-whisper    ignore captions, transcribe the audio
#   --keep-audio       keep the downloaded media next to the markdown
```

`scripts/transcribe.py` is also usable standalone on any local audio/video
file: `uv run <skill-dir>/scripts/transcribe.py <media> [--language es] [-o out.json]`.

Run `--probe` first when the user hasn't specified expectations — it shows
whether the fast caption path is available or a whisper run (model download,
minutes of compute) is coming, and lets you tell the user before committing.

## Output

One markdown file per video: OKF v0.2 frontmatter (`type: Video Transcript`,
title/description/resource/tags, `generated.by` = `machine:youtube-captions`
or `machine:mlx-whisper/<model>`, `sources`, plus a custom `video:` block with
channel/published/duration/language/`transcript_source` and — for whisper runs —
the transcription provenance `{model, repairs}`), then the transcript as
timestamped ~50-word paragraphs:

```markdown
**[0:03:12]** paragraph text …
```

## Notes

- Whisper runs are compute-heavy (minutes for long videos) and hold ~2-4 GB of
  RAM; warn the user before transcribing anything long.
- A `<out>.transcript.json.diag.json` after a failed run holds the machine-readable
  failure summary — surface its `summary` field to the user, don't retry blindly.
- Playlists/channels are refused by design — pass one video URL at a time.
- Transcripts of private/unlisted content are the user's data: default the
  output outside any public repo working directory.
