"""Add a server-TTS voice-over to a recorded demo video.

  python scripts/video_narrate.py --url http://127.0.0.1:18610 --video runs/video/full.mp4 --cues runs/video/cues.json --out runs/video/narrated.mp4

Cues JSON: [{"start": 0.0, "end": 6.5, "text": "..."}]. Text is synthesized through the stand's /api/tts with the demo login;
each cue is sped up only if it does not fit its window. The video stream is copied, the voice is AAC.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx


def ffprobe_duration(path: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], text=True).strip())


def synthesize(base: str, account: str, pin: str, text: str, out: Path) -> None:
    c = httpx.Client(base_url=base.rstrip("/"), timeout=90, follow_redirects=True)
    c.post("/api/login/demo", json={"account": account, "pin": pin, "consent": True}).raise_for_status()
    r = c.post("/api/tts", json={"text": text, "lang": "ru"})
    r.raise_for_status()
    out.write_bytes(r.content)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:18610")
    ap.add_argument("--video", required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--account", default="selftaught")
    ap.add_argument("--pin", default="3002")
    a = ap.parse_args()
    video = Path(a.video)
    cues = json.loads(Path(a.cues).read_text(encoding="utf-8"))
    dur = ffprobe_duration(video)
    with tempfile.TemporaryDirectory(prefix="dm-narration-") as td:
        tmp = Path(td)
        inputs = ["-i", str(video)]
        filters = [f"anullsrc=r=48000:cl=mono:d={dur:.3f}[base]"]
        mix = ["base"]
        for i, cue in enumerate(cues, 1):
            start, end, text = float(cue["start"]), float(cue["end"]), cue["text"].strip()
            if not text or end <= start or start >= dur:
                continue
            wav = tmp / f"cue{i}.wav"
            synthesize(a.url, a.account, a.pin, text, wav)
            sd = ffprobe_duration(wav)
            window = min(end, dur) - start
            factor = max(1.0, min(1.35, sd / max(0.1, window * 0.92)))
            inputs += ["-i", str(wav)]
            filters.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=mono,atempo={factor:.3f},adelay={int(start * 1000)}[a{i}]")
            mix.append(f"a{i}")
        if len(mix) == 1:
            print("no cues synthesized", file=sys.stderr)
            return 2
        filters.append("".join(f"[{x}]" for x in mix) + f"amix=inputs={len(mix)}:normalize=0:dropout_transition=0,atrim=0:{dur:.3f}[aout]")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(filters),
               "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", a.out]
        subprocess.run(cmd, check=True)
    print(a.out, f"{dur:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
