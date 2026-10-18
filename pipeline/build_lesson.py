#!/usr/bin/env python3
"""
build_lesson.py - turn one short English video into a lesson.json for the player.

Steps
  1. fetch   : download a URL (direct file link -> urllib, web page -> yt-dlp) or use a local file
  2. encode  : ffmpeg -> mp4 (H.264 + AAC, faststart), scaled down until it fits --max-mb
  3. asr     : faster-whisper with word-level timestamps
  4. split   : group words into sentences (by . ? ! and by long pauses / max length)
  5. enrich  : merge Chinese translations + vocabulary from an enrichment JSON
               (written by a human or an LLM; see README). If it doesn't exist yet,
               a template is written so you can fill it in and re-run with --skip-asr.
  6. write   : lesson.json

Usage
  python pipeline/build_lesson.py --input <URL or file> --out . \
      --title "..." --source-url "..." --license "..."
  # then fill in pipeline/work/enrich.json and run again with --skip-asr
"""
import argparse, json, os, re, shutil, subprocess, sys, urllib.request
from pathlib import Path

VIDEO_EXT = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".m4v", ".avi"}


def log(*a):
    print("[build_lesson]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 1. fetch
def parse_ts(x: str) -> float:
    """'29:05' / '1:02:03.5' / '75' -> seconds"""
    sec = 0.0
    for part in x.strip().split(":"):
        sec = sec * 60 + float(part)
    return sec


def fetch(src: str, work: Path, section=None, yt_args=None):
    """Returns (path, already_trimmed)."""
    if os.path.exists(src):
        return Path(src), False
    work.mkdir(parents=True, exist_ok=True)
    path_part = src.split("?")[0]
    ext = os.path.splitext(path_part)[1].lower()
    if ext in VIDEO_EXT:  # direct file link
        dst = work / f"source{ext}"
        if not dst.exists():
            log("downloading", src)
            req = urllib.request.Request(src, headers={"User-Agent": "english-proto/1.0"})
            with urllib.request.urlopen(req) as r, open(dst, "wb") as f:
                shutil.copyfileobj(r, f)
        return dst, False
    # anything else: let yt-dlp handle it (YouTube, Internet Archive, Commons pages, ...)
    log("downloading with yt-dlp", src)
    for old in work.glob("source.*"):
        old.unlink()
    out = work / "source.%(ext)s"
    cmd = [sys.executable, "-m", "yt_dlp", "-f",
           "b[height<=480][vcodec^=avc1]/bv*[height<=480]+ba/b[height<=720]/b",
           "-o", str(out), "--no-playlist", "--merge-output-format", "mp4"]
    if section:  # only download the part we need
        cmd += ["--download-sections", f"*{section[0]}-{section[1]}", "--force-keyframes-at-cuts"]
    cmd += (yt_args or []) + [src]
    log(" ".join(cmd))
    subprocess.run(cmd, check=True)
    for p in work.glob("source.*"):
        return p, bool(section)
    raise SystemExit("yt-dlp produced no file")


def probe_duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


# ---------------------------------------------------------------- 2. encode
def encode(src: Path, dst: Path, max_mb: float, section=None) -> None:
    dur = probe_duration(src)
    trim = []
    if section:  # trim local / direct-link sources here
        trim = ["-ss", str(parse_ts(section[0])), "-to", str(parse_ts(section[1]))]
        dur = parse_ts(section[1]) - parse_ts(section[0])
    for height, crf in [(480, 26), (360, 28), (360, 32), (240, 32)]:
        log(f"encoding {height}p crf{crf}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *trim, "-i", str(src),
                        "-vf", f"scale=-2:'min(ih,{height})'", "-c:v", "libx264", "-preset", "medium",
                        "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "main",
                        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart",
                        str(dst)], check=True)
        mb = dst.stat().st_size / 1e6
        log(f"  -> {mb:.1f} MB, {dur:.0f}s")
        if mb <= max_mb:
            return
    log("warning: still above --max-mb")


# ---------------------------------------------------------------- 3. asr
def transcribe(video: Path, model_size: str, language: str):
    from faster_whisper import WhisperModel
    log(f"loading faster-whisper '{model_size}' (CPU int8)")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # decode with ffmpeg ourselves (avoids PyAV version issues inside faster-whisper)
    import numpy as np
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-f", "s16le",
                          "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(audio, language=language, word_timestamps=True,
                                      vad_filter=True, beam_size=5,
                                      condition_on_previous_text=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)})
        log(f"  {seg.start:6.1f}s {seg.text.strip()[:70]}")
    return words


# ---------------------------------------------------------------- 4. split
ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "u.s.", "e.g.", "i.e.", "vs."}


def split_sentences(words, max_words=28, pause=1.2):
    sents, cur = [], []

    def flush():
        if not cur:
            return
        text = "".join(x["w"] for x in cur).strip()
        text = re.sub(r"\s+", " ", text)
        sents.append({"start": cur[0]["s"], "end": cur[-1]["e"], "en": text,
                      "words": [{"w": x["w"].strip(), "s": x["s"], "e": x["e"]} for x in cur]})
        cur.clear()

    for i, w in enumerate(words):
        if cur and w["s"] - cur[-1]["e"] > pause and len(cur) >= 4:
            flush()
        cur.append(w)
        tok = w["w"].strip().lower()
        end_punct = re.search(r"[.?!][\"')\]]*$", tok) and tok not in ABBREV
        if end_punct or len(cur) >= max_words:
            flush()
    flush()
    for i, s in enumerate(sents):
        s["id"] = i + 1
    # small padding so sentence playback doesn't clip the first/last phoneme
    for i, s in enumerate(sents):
        prev_end = sents[i - 1]["end"] if i else 0
        s["start"] = round(max(prev_end, s["start"] - 0.15), 2)
    return [{"id": s["id"], "start": s["start"], "end": s["end"], "en": s["en"], "zh": "",
             "words": s["words"]} for s in sents]


# ---------------------------------------------------------------- 5. enrich
def word_forms(word):
    w = word.lower()
    forms = {w, w + "s", w + "es", w + "ed", w + "d", w + "ing", w + "ly", w + "'s"}
    if w.endswith("e"):
        forms |= {w[:-1] + "ing"}
    if w.endswith("y"):
        forms |= {w[:-1] + "ies", w[:-1] + "ied"}
    return forms


def find_sentence_ids(term, sentences):
    """Return ids of sentences containing the vocab term (single word or phrase)."""
    parts = term.lower().split()
    ids = []
    for s in sentences:
        toks = re.findall(r"[a-z0-9'-]+", s["en"].lower())
        n = len(parts)
        for i in range(len(toks) - n + 1):
            window = toks[i:i + n]
            if window[:-1] == parts[:-1] and window[-1] in word_forms(parts[-1]):
                ids.append(s["id"])
                break
    return ids


def merge_sentences(sentences, groups):
    if not groups:
        return sentences
    by_id = {s["id"]: s for s in sentences}
    drop, out = set(), []
    for g in groups:
        g = sorted(g)
        first = by_id[g[0]]
        for gid in g[1:]:
            s2 = by_id[gid]
            first["en"] = (first["en"].rstrip() + " " + s2["en"].lstrip()).strip()
            first["end"] = s2["end"]
            first["words"] = first.get("words", []) + s2.get("words", [])
            drop.add(gid)
    for s in sentences:
        if s["id"] not in drop:
            out.append(dict(s))
    for i, s in enumerate(out):
        s["id"] = i + 1
    return out


def enrich(sentences, enrich_path: Path):
    if not enrich_path.exists():
        tmpl = {
            "_help": "Fill zh for every sentence (keep ids), and 15-30 vocab items. "
                     "'match' is optional: the exact surface form(s) to highlight if different from 'word'.",
            "translations": {str(s["id"]): {"en": s["en"], "zh": ""} for s in sentences},
            "vocab": [{"word": "", "match": [], "pos": "", "ipa_us": "", "ipa_uk": "",
                       "def_en": "", "def_zh": "", "example": ""}],
        }
        enrich_path.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"wrote enrichment template -> {enrich_path}  (fill it, then re-run with --skip-asr)")
        return sentences, []
    data = json.loads(enrich_path.read_text(encoding="utf-8"))
    # optional sentence merges, using the ids from transcript.json: [[15, 16], [27, 28]]
    # (sentences are renumbered afterwards; translations/fixes use the NEW ids -
    #  run once with an empty translations map to see them in lesson.json)
    sentences = merge_sentences(sentences, data.get("merge", []))
    # optional ASR corrections: [{"from": "Weisslogel", "to": "Weislogel", "ids": [optional]}]
    for fx in data.get("fixes", []):
        for s in sentences:
            if fx.get("ids") and s["id"] not in fx["ids"]:
                continue
            if fx["from"] in s["en"]:
                s["en"] = s["en"].replace(fx["from"], fx["to"])
                for w in s.get("words", []):
                    w["w"] = w["w"].replace(fx["from"], fx["to"])
    tr = data.get("translations", {})
    missing = 0
    for s in sentences:
        t = tr.get(str(s["id"]))
        zh = t.get("zh") if isinstance(t, dict) else t
        if t and isinstance(t, dict) and t.get("en") and t["en"] != s["en"]:
            log(f"  note: sentence {s['id']} text changed since enrichment was written")
        s["zh"] = zh or ""
        missing += not s["zh"]
    if missing:
        log(f"warning: {missing} sentences have no Chinese translation")
    vocab = []
    for v in data.get("vocab", []):
        if not v.get("word"):
            continue
        terms = v.get("match") or [v["word"]]
        ids = sorted({i for t in terms for i in find_sentence_ids(t, sentences)})
        if not ids:
            log(f"  warning: vocab '{v['word']}' not found in transcript")
        item = {k: v.get(k, "") for k in
                ["word", "pos", "ipa_us", "ipa_uk", "def_en", "def_zh", "example"]}
        item["match"] = terms
        item["sentence_ids"] = ids
        vocab.append(item)
    return sentences, vocab


# ---------------------------------------------------------------- main
def update_index(root: Path, lesson_id: str, title: str, make_default: bool):
    idx_path = root / "lessons" / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"lessons": []}
    entry = {"id": lesson_id, "title": title, "path": f"lessons/{lesson_id}/lesson.json"}
    rest = [l for l in idx["lessons"] if l["id"] != lesson_id]
    pos = next((i for i, l in enumerate(idx["lessons"]) if l["id"] == lesson_id), len(rest))
    idx["lessons"] = [entry] + rest if make_default else rest[:pos] + [entry] + rest[pos:]
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"updated {idx_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="video URL (direct file or any yt-dlp page) or local path")
    ap.add_argument("--out", default=".", help="app root folder (the one with index.html)")
    ap.add_argument("--lesson-id", default=None,
                    help="write to lessons/<id>/ and register it in lessons/index.json (recommended). "
                         "Without it: legacy single lesson -> ./lesson.json + media/video.mp4")
    ap.add_argument("--default", action="store_true", help="make this the first (default) lesson in the switcher")
    ap.add_argument("--section", default=None, help="only use START-END of the video, e.g. 29:05-35:10")
    ap.add_argument("--yt-args", default="", help="extra yt-dlp args, e.g. \"--extractor-args youtube:player_client=android\"")
    ap.add_argument("--work", default=None, help="scratch dir (default: <out>/pipeline/work[/<id>])")
    ap.add_argument("--title", default="Untitled lesson")
    ap.add_argument("--source-url", default=None)
    ap.add_argument("--license", default="UNKNOWN - check before sharing")
    ap.add_argument("--model", default="base.en", help="faster-whisper model: tiny.en/base.en/small.en/...")
    ap.add_argument("--language", default="en")
    ap.add_argument("--max-mb", type=float, default=30)
    ap.add_argument("--enrich", default=None, help="enrichment JSON (default: <work>/enrich.json)")
    ap.add_argument("--skip-asr", action="store_true", help="reuse <work>/transcript.json")
    a = ap.parse_args()

    root = Path(a.out).resolve()
    if a.lesson_id:
        ldir = root / "lessons" / a.lesson_id
        work = Path(a.work).resolve() if a.work else root / "pipeline" / "work" / a.lesson_id
        video, video_rel, lesson_path = ldir / "video.mp4", f"lessons/{a.lesson_id}/video.mp4", ldir / "lesson.json"
    else:
        ldir = root / "media"
        work = Path(a.work).resolve() if a.work else root / "pipeline" / "work"
        video, video_rel, lesson_path = ldir / "video.mp4", "media/video.mp4", root / "lesson.json"
    work.mkdir(parents=True, exist_ok=True)
    ldir.mkdir(parents=True, exist_ok=True)
    tpath = work / "transcript.json"
    section = tuple(a.section.split("-", 1)) if a.section else None

    if not (a.skip_asr and tpath.exists() and video.exists()):
        src, trimmed = fetch(a.input, work, section, a.yt_args.split() if a.yt_args else None)
        encode(src, video, a.max_mb, None if trimmed else section)
        words = transcribe(video, a.model, a.language)
        sentences = split_sentences(words)
        tpath.write_text(json.dumps(sentences, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"{len(sentences)} sentences -> {tpath}")
    else:
        sentences = json.loads(tpath.read_text(encoding="utf-8"))
        log(f"reusing {tpath} ({len(sentences)} sentences)")

    sentences, vocab = enrich(sentences, Path(a.enrich) if a.enrich else work / "enrich.json")
    lesson = {
        "id": a.lesson_id or "lesson",
        "title": a.title,
        "source_url": a.source_url or (a.input if a.input.startswith("http") else ""),
        "section": a.section or "",
        "license": a.license,
        "video": video_rel,
        "duration": round(probe_duration(video), 2),
        "sentences": sentences,
        "vocab": vocab,
    }
    lesson_path.write_text(json.dumps(lesson, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"wrote {lesson_path}: {len(sentences)} sentences, {len(vocab)} vocab")
    if a.lesson_id:
        update_index(root, a.lesson_id, a.title, a.default)


if __name__ == "__main__":
    main()
