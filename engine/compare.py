"""두 asr.json 의 문자 단위 차이 (D-04 의 두 모델 비교용). 숫자만 낸다. 문장은 출력하지 않는다.

char_diff_ratio = 편집 거리(문자, 공백 제거) / 기준(a) 글자 수.
정답지가 아니라 '두 모델이 서로 얼마나 다른가' 다. 어느 쪽이 맞는지는 사람 손채점이 필요하다.
"""
from __future__ import annotations

import json


def _chars(d: dict) -> str:
    return "".join("".join((s.get("text") or "").split()) for s in d.get("segments") or [])


def edit_distance(a: str, b: str) -> int:
    """레벤슈타인 거리 (한 줄 DP, O(len(a)*len(b)))."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def compare(a: dict, b: dict) -> dict:
    ca, cb = _chars(a), _chars(b)
    dist = edit_distance(ca, cb)
    return {
        "a_model": (a.get("model") or {}).get("name"),
        "b_model": (b.get("model") or {}).get("name"),
        "a_chars": len(ca),
        "b_chars": len(cb),
        "edit_distance": dist,
        "char_diff_ratio": round(dist / len(ca), 4) if ca else None,
        "a_segments": len(a.get("segments") or []),
        "b_segments": len(b.get("segments") or []),
    }


def compare_files(path_a: str, path_b: str) -> dict:
    with open(path_a, encoding="utf-8") as f:
        a = json.load(f)
    with open(path_b, encoding="utf-8") as f:
        b = json.load(f)
    return compare(a, b)
