#!/usr/bin/env python3
"""台本JSONから音声を作り、タイミング情報を書き込む（GitHub Actions用）。

使い方: python3 tools/build_episode.py episodes/<YYYY-MM-DD>-ja.json

- 文ごとに edge-tts で mp3 を作り、ffprobe で長さを測る
- 文の間に短い無音（文間0.35秒／パート間0.7秒）を挟んで連結し、
  audio/<日付>-ja.mp3 を作る
- 台本に lines_timed / segments / seconds / made_at を書き戻し、
  episodes.json の先頭にこの回を登録する
- 声は差し替え可能：環境変数 TTS_VOICE（既定 ja-JP-NanamiNeural）
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile

VOICE = os.environ.get("TTS_VOICE", "ja-JP-NanamiNeural")
GAP_LINE = 0.35
GAP_PART = 0.7


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def make_silence(path, seconds):
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=24000:cl=mono", "-t", str(seconds),
         "-codec:a", "libmp3lame", "-b:a", "48k", path])


def tts(text, path):
    run(["edge-tts", "--voice", VOICE, "--text", text,
         "--write-media", path])


def main():
    ep_path = sys.argv[1]
    with open(ep_path, encoding="utf-8") as f:
        ep = json.load(f)
    date = ep["date"]
    lang = ep.get("lang", "ja")

    # (part名, segment kind, segment index, say, show) の並びを作る
    lines = []
    for ln in ep.get("opening", []):
        lines.append(("opening", "opening", 0, ln))
    for i, item in enumerate(ep.get("news", [])):
        for ln in item.get("lines", []):
            lines.append(("news", "news", i, ln))
    for ln in ep.get("closing", []):
        lines.append(("closing", "closing", 0, ln))
    if not lines:
        sys.exit("no spoken lines")

    tmp = tempfile.mkdtemp()
    pieces = []          # 連結する mp3 の並び
    timed = []           # lines_timed
    segments = []        # segments
    t = 0.0
    prev_key = None
    seg_start = 0.0
    for n, (part, kind, idx, ln) in enumerate(lines):
        key = (kind, idx)
        if prev_key is not None:
            gap = GAP_LINE if key == prev_key else GAP_PART
            sil = os.path.join(tmp, f"gap{n}.mp3")
            make_silence(sil, gap)
            pieces.append(sil)
            t += gap
        if key != prev_key:
            if prev_key is not None:
                segments.append({"kind": prev_key[0], "index": prev_key[1],
                                 "start": round(seg_start, 3),
                                 "end": round(timed[-1]["end"], 3)})
            seg_start = t
            prev_key = key
        p = os.path.join(tmp, f"line{n}.mp3")
        tts(ln["say"], p)
        d = duration(p)
        pieces.append(p)
        timed.append({"start": round(t, 3), "end": round(t + d, 3),
                      "show": ln["show"], "part": part})
        t += d
    segments.append({"kind": prev_key[0], "index": prev_key[1],
                     "start": round(seg_start, 3),
                     "end": round(timed[-1]["end"], 3)})

    os.makedirs("audio", exist_ok=True)
    audio_path = f"audio/{date}-{lang}.mp3"
    listfile = os.path.join(tmp, "list.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in pieces:
            f.write(f"file '{p}'\n")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", listfile, "-codec:a", "libmp3lame", "-b:a", "64k",
         audio_path])

    ep["lines_timed"] = timed
    ep["segments"] = segments
    ep["seconds"] = round(t, 2)
    ep["made_at"] = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))).isoformat()
    with open(ep_path, "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False, indent=1)

    # episodes.json：同日分を除いて先頭に追加
    idx_path = "episodes.json"
    idx = []
    if os.path.exists(idx_path):
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
    idx = [e for e in idx if e.get("date") != date]
    idx.insert(0, {"date": date,
                   lang: {"title": ep["title"], "seconds": ep["seconds"]}})
    idx.sort(key=lambda e: e["date"], reverse=True)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    print(f"built {audio_path}: {ep['seconds']}s, {len(timed)} lines")


if __name__ == "__main__":
    main()
