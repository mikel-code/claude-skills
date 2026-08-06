# /// script
# requires-python = ">=3.10"
# dependencies = ["mlx-whisper"]
# ///
"""Transcribe an audio/video file with mlx-whisper, validate, repair, annotate.

Discipline (mirrors convert-engine §6.6):
  - Explicit language: taken from --language (caller passes yt-dlp metadata).
    If absent, ONE upfront 30 s probe decides it — never per-window auto-detect.
  - Deterministic validation on every transcript (WV1-WV4):
      WV1 consecutive duplicate segments, WV2 5-gram loop ratio,
      WV3 zlib compression ratio, WV4 timestamp coverage/gaps.
  - Repair ladder on failed validation:
      1. large-v3-turbo (default decoding)
      2. condition_on_previous_text=False        (canonical loop fix)
      3. chunked: 10-min ffmpeg segments, spliced
      4. escalate to large-v3
      5. all fail -> <out>.diag.json + exit 1    (needs-attention)
  - Provenance in the output JSON: transcription {model, language, repairs}.

Usage: uv run transcribe.py <media> [--language XX] [--duration SECONDS] [-o out.json]
Exit codes: 0 ok, 1 failed (diag written), 2 usage.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from collections import Counter
from pathlib import Path

import mlx_whisper

TURBO = "mlx-community/whisper-large-v3-turbo"
LARGE = "mlx-community/whisper-large-v3-mlx"

ES_STOP = {"el","la","los","las","de","que","y","en","un","una","es","por","con","no",
           "para","su","al","lo","como","más","pero","sus","le","ya","o","este","sí",
           "porque","esta","entre","cuando","muy","sin","sobre","también","hasta","hay",
           "donde","desde","todo","nos","durante","cada","vez","algo","esa","ese"}
EN_STOP = {"the","be","to","of","and","a","in","that","have","i","it","for","not","on",
           "with","he","as","you","do","at","this","but","his","by","from","they","we",
           "say","her","she","or","an","will","my","one","all","would","there","their",
           "what","so","up","out","if","about","who","get","which","go","me","when"}


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        sys.exit("ffmpeg not found on PATH — required for audio decoding")
    return path


def ffprobe_duration(media: str) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", media], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- validation

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def validate(result: dict, duration: float) -> list[dict]:
    """WV1-WV4 checks. Returns list of failures (empty = pass)."""
    segments = result.get("segments", [])
    failures: list[dict] = []

    # WV1 — consecutive duplicate segments (>=3 identical in a row)
    run, prev = 1, None
    for seg in segments:
        norm = _normalize(seg.get("text", ""))
        if norm and norm == prev:
            run += 1
            if run == 3:
                failures.append({"check": "WV1",
                                 "detail": f"segment repeated 3+ times: {norm[:60]!r}",
                                 "range": [seg.get("start"), seg.get("end")]})
        else:
            run = 1
        prev = norm

    # WV2 — most frequent 5-gram > 20% of tokens in any ~200-token window
    tokens = _normalize(result.get("text", "")).split()
    window = 200
    for i in range(0, max(1, len(tokens) - window + 1), window // 2):
        chunk = tokens[i:i + window]
        if len(chunk) < 25:
            continue
        grams = Counter(tuple(chunk[j:j + 5]) for j in range(len(chunk) - 4))
        top = grams.most_common(1)[0][1] * 5
        if top / len(chunk) > 0.20:
            failures.append({"check": "WV2",
                             "detail": f"5-gram loop ratio {top / len(chunk):.2f} in token window {i}"})
            break

    # WV3 — zlib compression ratio > 2.4 on any ~1500-char chunk
    text = result.get("text", "")
    for i in range(0, max(1, len(text)), 1500):
        chunk = text[i:i + 1500].encode()
        if len(chunk) < 300:
            continue
        ratio = len(chunk) / len(zlib.compress(chunk))
        if ratio > 2.4:
            failures.append({"check": "WV3", "detail": f"compression ratio {ratio:.2f} at char {i}"})
            break

    # WV4 — coverage: transcript must span >=95% of media, no gap > 60 s
    if segments:
        last_end = max(float(s.get("end") or 0) for s in segments)
        if duration > 0 and last_end < 0.95 * duration:
            failures.append({"check": "WV4",
                             "detail": f"transcript ends at {last_end:.0f}s of {duration:.0f}s media",
                             "range": [last_end, duration]})
        prev_end = 0.0
        for seg in segments:
            start = float(seg.get("start") or 0)
            if start - prev_end > 60:
                failures.append({"check": "WV4", "detail": f"gap {start - prev_end:.0f}s",
                                 "range": [prev_end, start]})
            prev_end = max(prev_end, float(seg.get("end") or 0))
    else:
        failures.append({"check": "WV4", "detail": "no segments"})

    return failures


def text_language(result: dict) -> str:
    """es|en|und from transcript stopwords (~200 words). Deterministic sanity check."""
    words = re.findall(r"[a-záéíóúüñ]+", result.get("text", "").lower())[:200]
    if len(words) < 50:
        return "und"
    es = sum(w in ES_STOP for w in words)
    en = sum(w in EN_STOP for w in words)
    if es >= 2 * max(en, 1) and es >= len(words) * 0.15:
        return "es"
    if en >= 2 * max(es, 1) and en >= len(words) * 0.15:
        return "en"
    return "und"


# ------------------------------------------------------------- transcription

def whisper_run(media: str, model: str, language: str, condition: bool) -> dict:
    return mlx_whisper.transcribe(
        media, path_or_hf_repo=model, language=language,
        condition_on_previous_text=condition, verbose=None)


def probe_language(media: str, tmp: Path) -> str:
    """One-shot detection on the first 30 s. Returns '' on probe failure."""
    wav = tmp / "lang-probe.wav"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", media, "-t", "30",
                    "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav)],
                   capture_output=True)
    if not wav.exists():
        return ""
    try:
        return mlx_whisper.transcribe(str(wav), path_or_hf_repo=TURBO,
                                      verbose=None).get("language", "") or ""
    except Exception:
        return ""


def chunked_transcribe(media: str, language: str, tmp: Path) -> dict:
    """10-min ffmpeg segments transcribed independently (condition off), spliced."""
    chunks_dir = tmp / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", media,
                    "-f", "segment", "-segment_time", "600", "-reset_timestamps", "1",
                    "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    str(chunks_dir / "c%03d.wav")], capture_output=True, check=True)
    merged = {"text": "", "segments": [], "language": language}
    offset = 0.0
    for chunk in sorted(chunks_dir.glob("c*.wav")):
        part = whisper_run(str(chunk), TURBO, language, condition=False)
        for seg in part.get("segments", []):
            seg["start"] += offset
            seg["end"] += offset
            merged["segments"].append(seg)
        merged["text"] += (" " if merged["text"] else "") + part.get("text", "").strip()
        offset += ffprobe_duration(str(chunk))
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description="mlx-whisper transcription with validation + repair ladder")
    ap.add_argument("media", help="audio or video file")
    ap.add_argument("--language", default="", help="ISO 639-1 code from source metadata (probed once if absent)")
    ap.add_argument("--duration", type=float, default=0.0, help="media duration in seconds (ffprobe fallback)")
    ap.add_argument("-o", "--output", default="", help="output JSON path (default: <media>.json)")
    args = ap.parse_args()

    media = Path(args.media)
    if not media.exists():
        ap.error(f"no such file: {media}")
    out = Path(args.output) if args.output else media.with_suffix(".json")
    duration = args.duration or ffprobe_duration(str(media))

    def fail(status: str, summary: str) -> None:
        diag = {"schema": 1, "stage": "transcribe", "status": status,
                "input": str(media), "summary": summary}
        out.with_suffix(out.suffix + ".diag.json").write_text(
            json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"FAILED: {summary} — diagnostics: {out}.diag.json", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="yt-transcribe-") as tmpdir:
        tmp = Path(tmpdir)
        language = args.language.split("-")[0].lower() if args.language else ""
        repairs: list[str] = []

        # Language sanity, audio side: whisper trusts language blindly — wrong
        # metadata yields fluent wrong-language output. One probe, never per-window.
        detected = probe_language(str(media), tmp)
        if not language:
            if not detected:
                fail("language_probe_failed", "no language given and 30s probe failed")
            language = detected
            repairs.append("lang-probed")
        elif not detected:
            print(f"language probe failed — proceeding with requested '{language}' (recorded)", file=sys.stderr)
            repairs.append("lang-probe-failed")
        elif detected != language:
            print(f"language sanity: requested '{language}' but audio detects as "
                  f"'{detected}' — transcribing as '{detected}'", file=sys.stderr)
            language = detected
            repairs.append("lang-redetected")

        attempts = [
            ("large-v3-turbo", [],
             lambda: whisper_run(str(media), TURBO, language, condition=True), TURBO),
            ("condition-on-previous-text False", ["condition-off"],
             lambda: whisper_run(str(media), TURBO, language, condition=False), TURBO),
            ("chunked (10-min segments)", ["condition-off", "chunked"],
             lambda: chunked_transcribe(str(media), language, tmp), TURBO),
            ("escalate to large-v3", ["condition-off", "chunked", "large-v3"],
             lambda: whisper_run(str(media), LARGE, language, condition=False), LARGE),
        ]

        result, model_used, failures = None, TURBO, []
        for i, (label, ladder_repairs, run, model) in enumerate(attempts, 1):
            print(f"attempt {i}: {label}", file=sys.stderr)
            try:
                candidate = run()
            except Exception as exc:
                print(f"  error: {exc}", file=sys.stderr)
                continue
            failures = validate(candidate, duration)
            if not failures:
                result, model_used = candidate, model
                repairs = [r for r in repairs if r] + ladder_repairs
                break
            print(f"  validation failed: {json.dumps(failures)}", file=sys.stderr)

        if result is None:
            fail("transcription_failed",
                 f"whisper failed all repair tiers — {json.dumps(failures)}")

        # Language sanity, text side: strong stopword disagreement with the
        # recorded language means the artifact is untrustworthy — flag, don't ship.
        txt_lang = text_language(result)
        if txt_lang != "und" and txt_lang != language:
            fail("language_undetermined",
                 f"transcribed as '{language}' but text reads as '{txt_lang}' — needs human check")

        result["transcription"] = {"model": model_used, "language": language,
                                   "repairs": repairs}
        out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        print(str(out))


if __name__ == "__main__":
    main()
