"""영상에서 STT 용 16kHz 모노 WAV 를 뽑고 걸린 시간을 출력한다 (08 D-03 1).

사용: python tools/extract_audio.py <영상> --out <wav> [--start-ms N] [--dur-ms N] [--sample-rate 16000] [--meta]
  --meta 를 주면 ffprobe 요약(경로 제외)도 출력한다.
"""
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import analysis, media  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="영상 → 16kHz 모노 WAV")
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start-ms", type=int, default=None)
    ap.add_argument("--dur-ms", type=int, default=None)
    ap.add_argument("--sample-rate", type=int, default=16000)
    ap.add_argument("--meta", action="store_true", help="ffprobe 요약 출력(경로 제외)")
    a = ap.parse_args(argv)

    video = os.path.abspath(a.video)
    out = os.path.abspath(a.out)
    for label, p in (("영상", video), ("출력", out)):
        why = media.check_path_string(p)
        if why:
            print(f"{label} 경로 거부: {why}", file=sys.stderr)
            return 2
    if a.meta:
        t0 = time.perf_counter()
        info = analysis.probe(video)
        print(f"probe {time.perf_counter() - t0:.3f}s:", json.dumps(info, ensure_ascii=False))
    try:
        r = analysis.extract_audio_wav(video, out, sample_rate=a.sample_rate,
                                       start_ms=a.start_ms, dur_ms=a.dur_ms)
    except analysis.AnalysisError as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1
    size = os.path.getsize(out)
    print(f"완료: {r['elapsed_s']:.3f}s, {r['sample_rate']}Hz {r['channels']}ch, "
          f"길이 {r['duration_ms']}ms, {size / 1024 / 1024:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
