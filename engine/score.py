"""손라벨 csv ↔ link 결과 json 채점 (08 D-03 4번 '사람 직접 반응 30건과 비교', 09 5-3 의 5번).

지표 이름은 06 4-2·4-3·6장을 따른다.
  - 이진(반응 있음 = direct·likely, 06 4-3): 정밀도·재현율·F1
  - 등급 혼동표 (06 6-4 E1 의 '등급 혼동표'), 정확 일치율(06 4-2)
  - 반응 시각 오차: Δt = 엔진 반응 시각 − 사람 발화 시각. 중앙값·90퍼센타일, ±2초·±5초 안 비율(06 6-3)
  - 사람만 찾은 것·엔진만 찾은 것 (chat_id 만)

입력
  --labels  09 3-2 의 csv. 열: chat_id, chat_vod_ms, utterance_vod_ms, grade, note (UTF-8, BOM 허용, 헤더 공백 허용)
            시각 칸은 ms 정수 또는 '1:02:03.5' 같은 시각 문자열. grade 는 direct/likely/related_only/unclear/unconfirmed
            또는 한글(직접/높음/관련성만/불명확/미확인). note 는 읽지도 출력하지도 않는다.
            '#' 으로 시작하는 행은 메모로 보고 건너뛴다(헤더 앞이어도). 읽지 못한 행의 경고에는
            줄 번호·칸 이름·사유만 적고 칸 값은 적지 않는다(열이 밀리면 note 가 다른 칸에 들어오기 때문).
  --link    engine.link 결과(ai-result v2 초안, v1 도 받음)
  --messages / --project   chat_id 가 messages.jsonl 에 있는지, chat_vod_ms 가 t_ms 와 같은지 확인(경고만)

집합 규칙 (06 6-5·6-6 을 이 시험판 크기에 맞춰 줄인 것)
  - 범위: --start-ms/--end-ms 가 있으면 그 값, 없으면 link 결과의 scope. 채팅 시각(messages t_ms 우선) 기준 [start, end).
    엔진 반응의 시각은 messages t_ms → link chat_t_ms(v2) → 라벨 chat_vod_ms 순으로 찾는다. 범위가 있는데
    시각을 모르는 엔진 반응(v1 link 를 messages 없이 줄 때)은 범위 밖으로 치고 따로 세며 경고한다.
  - --label-time player 인데 --offset-ms 가 없으면 link 의 calibration 을 쓴다. 그것이 없거나(v1)
    single_offset 이 아니면 거부한다.
  - 사람 '불명확' 은 이진·Δt 에서 뺀다(grade_unclear). 혼동표에는 보인다.
  - link reactions 에 없는 라벨 채팅은 'AI 미평가'(ai_unevaluated) 로 빼고 센다.
  - 손라벨 30건은 전수 라벨이 아니다(06 6-4 E2 성격). 그래서 정밀도를 두 가지로 낸다.
      precision              : 범위 안 엔진 '반응 있음' 전부를 분모로(라벨 없는 채팅은 사람 '반응 없음' 으로 가정 — 낮게 나온다)
      precision_labeled_only : 사람이 등급을 매긴 채팅만 분모로
    f1 은 precision(전수 가정)과 recall 로 낸다. 대표값은 recall 이다(06 6-4 E2: 'AI 재현율').

  python -m engine score --labels <csv> --link <json> [--start-ms N --end-ms N] [--messages F | --project P]
                         [--label-time vod|player [--offset-ms N] [--file-start-ms N]] [--out report.json]

--project 를 주면 <P>/labels/nickname-exposure.jsonl(tools/chat_window.py --show-nickname 이 남김)의 건수를
보고서 nickname_exposure 에 낸다(03 14-4 '노출 세션 표시' 의 시험판).

표준 출력·--out 에는 chat_id·시각·등급·숫자만 쓴다. 채팅 본문·닉네임·전사문·note 는 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from app.core.timecode import Calibration, parse_time, video_to_chat_ms

POSITIVE = ("direct", "likely")
HUMAN_GRADES = ("direct", "likely", "related_only", "unclear", "unconfirmed")
ENGINE_GRADES = ("direct", "likely", "related_only", "unclear", "unconfirmed", "not_evaluated")
GRADE_KO = {"direct": "직접", "likely": "높음", "related_only": "관련성만", "unclear": "불명확",
            "unconfirmed": "미확인", "not_evaluated": "미평가"}
GRADE_ALIASES = {
    "direct": "direct", "직접": "direct", "직접반응": "direct", "직접 반응": "direct",
    "likely": "likely", "높음": "likely", "연결가능성높음": "likely", "연결 가능성 높음": "likely",
    "related_only": "related_only", "related": "related_only", "관련성만": "related_only", "관련": "related_only",
    "관련성만 있음": "related_only",
    "unclear": "unclear", "불명확": "unclear",
    "unconfirmed": "unconfirmed", "none": "unconfirmed", "미확인": "unconfirmed", "반응 미확인": "unconfirmed",
    "반응미확인": "unconfirmed", "없음": "unconfirmed",
}
REQUIRED_COLS = ("chat_id", "chat_vod_ms", "utterance_vod_ms", "grade")
CHAT_ID_RE = re.compile(r"^c[0-9a-f]{20}(_[0-9]+)?$")
MAX_LISTED_WARN_IDS = 20


# ── 읽기 ─────────────────────────────────────────────────────────────────

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def parse_ms_cell(s: str) -> int | None:
    """빈 칸 → None. 숫자만 → ms 정수. ':' 나 h/m/s 가 있으면 시각 문자열(parse_time). 그 밖(예 '412.3')은 오류."""
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"-?[0-9]+", s):
        return int(s)
    if ":" in s or re.search(r"[hms]", s, re.I):
        v = parse_time(s)
        if v is not None:
            return v
    # 칸 값은 메시지에 넣지 않는다. 열이 밀린 행이면 note(닉네임이 들어갈 수 있음)가 여기로 온다.
    raise ValueError("형식 오류(ms 정수 또는 1:02:03.5)")


def normalize_grade(s: str) -> str:
    k = " ".join((s or "").strip().lower().split())
    if k in GRADE_ALIASES:
        return GRADE_ALIASES[k]
    k2 = k.replace(" ", "")
    if k2 in GRADE_ALIASES:
        return GRADE_ALIASES[k2]
    # 칸 값은 메시지에 넣지 않는다(위와 같은 이유)
    raise ValueError("모르는 값(direct/likely/related_only/unclear/unconfirmed 또는 한글 등급)")


def _decode_csv(b: bytes, warnings: list[str]) -> str:
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        warnings.append("labels: UTF-8 이 아니어서 cp949(엑셀 한글 기본)로 읽었습니다. UTF-8 로 저장하길 권합니다")
        return b.decode("cp949")


def load_labels(path: str, *, label_time: str = "vod", cal: Calibration | None = None,
                file_start_ms: int = 0) -> tuple[list[dict], list[str], str]:
    """(라벨 목록, 경고, sha256). 라벨 = {chat_id, chat_vod_ms, utterance_vod_ms, grade, row}.

    label_time='player' 면 utterance_vod_ms 칸을 플레이어(영상 파일) 시각으로 보고 보정으로 VOD 시각으로 바꾼다.
    chat_vod_ms 는 늘 messages.jsonl 의 t_ms(채팅 시계)다.
    """
    with open(path, "rb") as f:
        b = f.read()
    warnings: list[str] = []
    text = _decode_csv(b, warnings)
    rd = csv.reader(io.StringIO(text, newline=""), skipinitialspace=True)
    header = None
    labels: list[dict] = []
    seen: set[str] = set()
    bad_rows: list[str] = []
    dup = 0
    for lineno, row in enumerate(rd, 1):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        if cells[0].startswith("#"):      # 메모 행. 헤더 앞에 있어도 건너뛴다
            continue
        if header is None:
            header = [c.lstrip("﻿").strip().lower() for c in cells]
            missing = [c for c in REQUIRED_COLS if c not in header]
            if missing:
                raise ValueError(f"labels: 열이 없습니다 {missing} (필요: {', '.join(REQUIRED_COLS)}, note)")
            continue
        rec = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header)}
        # 오류 메시지에는 줄 번호·칸 이름·사유만 적는다. 칸 값은 적지 않는다(note 가 밀려 들어올 수 있음)
        col = "chat_id"
        try:
            cid = rec["chat_id"]
            if not CHAT_ID_RE.match(cid):
                raise ValueError("형식 오류(c + 16진 20자)")
            col = "grade"
            grade = normalize_grade(rec["grade"])
            col = "chat_vod_ms"
            cv = parse_ms_cell(rec["chat_vod_ms"])
            col = "utterance_vod_ms"
            uv = parse_ms_cell(rec["utterance_vod_ms"])
        except ValueError as e:
            bad_rows.append(f"{lineno}행: {col} 칸 {e}")
            continue
        if uv is not None and label_time == "player":
            uv = video_to_chat_ms(uv, cal or Calibration(), file_start_ms)
        if cid in seen:
            dup += 1
            continue
        seen.add(cid)
        labels.append({"chat_id": cid, "chat_vod_ms": cv, "utterance_vod_ms": uv, "grade": grade, "row": lineno})
    if header is None:
        raise ValueError("labels: 빈 파일입니다")
    if bad_rows:
        warnings.append(f"labels: 읽지 못해 뺀 행 {len(bad_rows)}개 — " + "; ".join(bad_rows[:10]))
    if dup:
        warnings.append(f"labels: 같은 chat_id 가 또 나온 행 {dup}개(첫 행만 씀, 06 6-2)")
    return labels, warnings, _sha256_bytes(b)


def _read_json(path: str) -> tuple[dict, bytes]:
    with open(path, "rb") as f:
        b = f.read()
    raw = b[3:] if b.startswith(b"\xef\xbb\xbf") else b

    def _no_const(name):
        raise ValueError(f"JSON 에 {name} 은 쓸 수 없습니다")
    return json.loads(raw.decode("utf-8"), parse_constant=_no_const), b


def load_link(path: str) -> tuple[dict, list[str], str]:
    doc, b = _read_json(path)
    w: list[str] = []
    if doc.get("schema") != "chatcoach/ai-result":
        raise ValueError("link: chatcoach/ai-result 가 아닙니다")
    if doc.get("schema_version") not in (1, 2):
        raise ValueError(f"link: schema_version {doc.get('schema_version')} 은 모릅니다")
    if doc.get("time_axis", "vod_ms") != "vod_ms":
        raise ValueError("link: time_axis 가 vod_ms 가 아닙니다")
    if not isinstance(doc.get("reactions"), list):
        raise ValueError("link: reactions 가 없습니다")
    cal = doc.get("calibration") or {}
    if cal.get("needs_anchor_calibration") or cal.get("status") == "unverified":
        w.append("link: 보정이 확인되지 않은 결과입니다(calibration unverified). Δt·창 판정이 흔들릴 수 있습니다")
    return doc, w, _sha256_bytes(b)


def load_message_index(path: str) -> dict[str, dict]:
    """chat_id → {t_ms, kind}. 본문은 읽어 들이되 남기지 않는다."""
    import app.jsonl as jsonl   # engine 은 app.core.*·app.jsonl 만 쓴다(test_layers)
    res = jsonl.read(path)
    if not res.ok:
        raise ValueError(f"{os.path.basename(path)}: 중간에 깨진 줄 {len(res.corrupt_lines)}개")
    out: dict[str, dict] = {}
    for r in res.records:
        cid = r.get("chat_id")
        if cid and cid not in out:
            out[cid] = {"t_ms": r.get("t_ms"), "kind": r.get("kind")}
    return out


# ── 계산 ─────────────────────────────────────────────────────────────────

def percentile(xs: Sequence[float], q: float) -> float | None:
    """선형 보간 분위수(numpy 기본 'linear' 와 같음). q 는 0~100."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def _engine_grade(r: Mapping | None) -> str:
    if r is None:
        return "not_evaluated"
    if r.get("result") == "unconfirmed" or r.get("grade") is None:
        return "unconfirmed"
    return r["grade"]


def score(labels: Sequence[dict], link_doc: Mapping, *, start_ms: int | None = None, end_ms: int | None = None,
          messages: Mapping[str, dict] | None = None) -> dict:
    """순수 계산. 보고서 dict 를 돌려준다(입력 해시·경고는 run() 이 더한다)."""
    warnings: list[str] = []
    reactions: dict[str, dict] = {}
    dup_r = 0
    for r in link_doc["reactions"]:
        cid = r.get("chat_id")
        if cid in reactions:
            dup_r += 1
            continue
        reactions[cid] = r
    if dup_r:
        warnings.append(f"link: 같은 chat_id 의 반응 줄 {dup_r}개(첫 줄만 씀)")

    scope = link_doc.get("scope") or {}
    s0 = start_ms if start_ms is not None else scope.get("start_ms")
    s1 = end_ms if end_ms is not None else scope.get("end_ms")
    scope_source = "cli" if (start_ms is not None or end_ms is not None) else ("link" if scope else "none")

    def chat_time(cid: str, fallback: int | None) -> int | None:
        if messages is not None and cid in messages and isinstance(messages[cid].get("t_ms"), int):
            return messages[cid]["t_ms"]
        return fallback

    def in_scope(t: int | None) -> bool:
        if t is None:
            return True
        return (s0 is None or t >= s0) and (s1 is None or t < s1)

    has_scope = s0 is not None or s1 is not None
    lab_t = {lb["chat_id"]: lb["chat_vod_ms"] for lb in labels}

    def engine_time(cid: str, r: Mapping) -> int | None:
        """엔진 반응의 채팅 시각: messages t_ms → link chat_t_ms(v2) → 라벨 chat_vod_ms. 모르면 None."""
        t = chat_time(cid, r.get("chat_t_ms"))
        if t is None:
            t = lab_t.get(cid)
        return t

    engine_no_time: set[str] = set()

    def engine_in_scope(cid: str, r: Mapping) -> bool:
        """범위가 있는데 시각을 모르면 범위 밖으로 치고 따로 센다(v1 link 에는 chat_t_ms 가 없다)."""
        t = engine_time(cid, r)
        if t is None:
            if has_scope and cid not in labeled_ids:   # 범위 안에 넣은 라벨 채팅이면 라벨 쪽 판정을 따른다
                engine_no_time.add(cid)
                return False
            return True
        return in_scope(t)

    # messages 확인(경고만)
    msg_check = None
    if messages is not None:
        missing = [lb["chat_id"] for lb in labels if lb["chat_id"] not in messages]
        mism = [lb["chat_id"] for lb in labels if lb["chat_id"] in messages and lb["chat_vod_ms"] is not None
                and messages[lb["chat_id"]].get("t_ms") != lb["chat_vod_ms"]]
        non_text = [lb["chat_id"] for lb in labels if lb["chat_id"] in messages
                    and messages[lb["chat_id"]].get("kind") != "text"]
        msg_check = {"labels_not_in_messages": missing, "chat_vod_ms_mismatch": mism, "non_text_message": non_text}
        if missing:
            warnings.append(f"labels: messages.jsonl 에 없는 chat_id {len(missing)}개 — 복사 실수인지 확인: "
                            + ", ".join(missing[:MAX_LISTED_WARN_IDS]))
        if mism:
            warnings.append(f"labels: chat_vod_ms 가 messages 의 t_ms 와 다른 행 {len(mism)}개(범위 판정은 t_ms 를 씀): "
                            + ", ".join(mism[:MAX_LISTED_WARN_IDS]))
        if non_text:
            warnings.append(f"labels: text 가 아닌 채팅(후원·구독 등) {len(non_text)}개 — 엔진은 text 만 판정한다")
    else:
        warnings.append("messages.jsonl 을 주지 않아 chat_id 존재 확인을 건너뜀(--messages 또는 --project)")

    # 범위 안 라벨
    in_labels, out_of_scope, no_time = [], [], 0
    for lb in labels:
        t = chat_time(lb["chat_id"], lb["chat_vod_ms"])
        if t is None:
            no_time += 1
        (in_labels if in_scope(t) else out_of_scope).append(lb)
    if no_time:
        warnings.append(f"labels: 채팅 시각을 몰라 범위 판정 없이 넣은 라벨 {no_time}개")
    if out_of_scope:
        warnings.append(f"labels: 범위 밖이라 뺀 라벨 {len(out_of_scope)}개")

    # 혼동표 (사람 행 × 엔진 열)
    conf = {h: {e: 0 for e in ENGINE_GRADES} for h in HUMAN_GRADES}
    for lb in in_labels:
        conf[lb["grade"]][_engine_grade(reactions.get(lb["chat_id"]))] += 1
    both = [(lb["grade"], _engine_grade(reactions.get(lb["chat_id"]))) for lb in in_labels]
    comparable = [(h, e) for h, e in both if e != "not_evaluated" and h != "unclear" and e != "unclear"]
    exact = sum(h == e for h, e in comparable)

    # 이진
    labeled_ids = {lb["chat_id"] for lb in in_labels}
    excluded = {"ai_unevaluated": [], "grade_unclear": []}
    h_pos, h_neg = [], []
    for lb in in_labels:
        cid = lb["chat_id"]
        if cid not in reactions:
            excluded["ai_unevaluated"].append(cid)
        elif lb["grade"] == "unclear":
            excluded["grade_unclear"].append(cid)
        elif lb["grade"] in POSITIVE:
            h_pos.append(lb)
        else:
            h_neg.append(lb)
    unclear_ids = set(excluded["grade_unclear"])
    e_pos_ids = []
    for cid, r in reactions.items():
        if _engine_grade(r) not in POSITIVE:
            continue
        if not engine_in_scope(cid, r):
            continue
        e_pos_ids.append(cid)
    e_pos = set(e_pos_ids)
    n_link_in_scope = sum(1 for cid, r in reactions.items() if engine_in_scope(cid, r))
    n_pos_no_time = sum(1 for cid in engine_no_time if _engine_grade(reactions[cid]) in POSITIVE)
    if engine_no_time:
        warnings.append(f"link: 채팅 시각을 몰라 범위 판정을 못 해 뺀 엔진 반응 {len(engine_no_time)}개"
                        f"(그중 반응 있음 {n_pos_no_time}개)"
                        " (v1 link 에는 chat_t_ms 가 없다. --messages 또는 --project 를 주면 판정한다)")
    tp = [lb for lb in h_pos if lb["chat_id"] in e_pos]
    fn = [lb for lb in h_pos if lb["chat_id"] not in e_pos]
    fp_labeled = [lb["chat_id"] for lb in h_neg if lb["chat_id"] in e_pos]
    fp_unlabeled = [cid for cid in e_pos_ids if cid not in labeled_ids]
    e_on_unclear = [cid for cid in e_pos_ids if cid in unclear_ids]
    n_tp, n_fn, n_fpl, n_fpu = len(tp), len(fn), len(fp_labeled), len(fp_unlabeled)
    precision = _ratio(n_tp, n_tp + n_fpl + n_fpu)
    precision_lab = _ratio(n_tp, n_tp + n_fpl)
    recall = _ratio(n_tp, n_tp + n_fn)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else (
        0.0 if precision is not None and recall is not None else None)
    tn_labeled = sum(1 for lb in h_neg if lb["chat_id"] not in e_pos)

    # Δt
    dts, missing_time = [], 0
    for lb in tp:
        et = reactions[lb["chat_id"]].get("reaction_time_ms")
        if et is None or lb["utterance_vod_ms"] is None:
            missing_time += 1
            continue
        dts.append(int(et) - int(lb["utterance_vod_ms"]))
    adts = [abs(d) for d in dts]
    tstats = {
        "definition": "engine_reaction_time_ms - human_utterance_vod_ms (둘 다 '반응 있음' 인 채팅, VOD ms)",
        "n": len(dts),
        "missing_time": missing_time,
        "median_ms": percentile(dts, 50),
        "p90_ms": percentile(dts, 90),
        "abs_median_ms": percentile(adts, 50),
        "abs_p90_ms": percentile(adts, 90),
        "within_2s": _ratio(sum(a <= 2000 for a in adts), len(adts)),
        "within_5s": _ratio(sum(a <= 5000 for a in adts), len(adts)),
        "mean_ms": round(sum(dts) / len(dts), 1) if dts else None,
    }

    def by_time(ids: Iterable[str], fb: Mapping[str, int | None]) -> list[str]:
        return sorted(ids, key=lambda c: (chat_time(c, fb.get(c)) if chat_time(c, fb.get(c)) is not None else -1, c))

    eng_t = {cid: engine_time(cid, r) for cid, r in reactions.items()}
    return {
        "scope": {"start_ms": s0, "end_ms": s1, "source": scope_source},
        "counts": {
            "labels_total": len(labels), "labels_in_scope": len(in_labels), "labels_out_of_scope": len(out_of_scope),
            "link_reactions": len(reactions),
            "link_reactions_in_scope": n_link_in_scope,
            "link_reactions_no_time": len(engine_no_time),
            "engine_positive_no_time": n_pos_no_time,
            "human_positive": len(h_pos), "human_negative": len(h_neg),
            "engine_positive_in_scope": len(e_pos_ids),
            "excluded": {k: len(v) for k, v in excluded.items()},
            "engine_positive_on_human_unclear": len(e_on_unclear),
        },
        "binary": {
            "positive": list(POSITIVE),
            "tp": n_tp, "fn": n_fn, "fp_labeled": n_fpl, "fp_unlabeled": n_fpu, "tn_labeled": tn_labeled,
            "precision": precision, "precision_labeled_only": precision_lab, "recall": recall, "f1": f1,
            "note": "손라벨은 전수가 아니다(06 6-4 E2). precision 은 라벨 없는 채팅을 '반응 없음' 으로 가정한 하한에 가깝다",
        },
        "grade_confusion": {"rows": "human", "cols": "engine", "table": conf},
        "grade_exact_agreement": {"n": len(comparable), "exact": exact, "rate": _ratio(exact, len(comparable)),
                                  "note": "둘 다 평가했고 어느 쪽도 불명확이 아닌 라벨만"},
        "reaction_time_error": tstats,
        "lists": {
            "human_only": by_time([lb["chat_id"] for lb in fn], lab_t),
            "engine_only": by_time(fp_labeled + fp_unlabeled, eng_t),
            "engine_only_labeled_negative": by_time(fp_labeled, eng_t),
            "ai_unevaluated": by_time(excluded["ai_unevaluated"], lab_t),
            "human_unclear": by_time(excluded["grade_unclear"], lab_t),
        },
        "messages_check": msg_check,
        "warnings": warnings,
    }


# ── 출력 ─────────────────────────────────────────────────────────────────

def _fmt(v, pct=False) -> str:
    if v is None:
        return "-"
    if pct:
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:.0f}" if abs(v) >= 100 else f"{v:.4g}"
    return str(v)


def format_report(rep: Mapping) -> str:
    L: list[str] = []
    sc, c, b, t = rep["scope"], rep["counts"], rep["binary"], rep["reaction_time_error"]
    if sc.get("start_ms") is None and sc.get("end_ms") is None:
        # 범위 없이 채점하면 파일 전체의 엔진 판정이 분모에 들어간다(2026-10-22 link-v03 측정 고침)
        L.append(f"범위: 없음 — 파일 전체를 채점함 ({sc['source']}). 구간 채점이면 --start-ms/--end-ms 를 주세요")
    else:
        L.append(f"범위: {_fmt(sc['start_ms'])} ~ {_fmt(sc['end_ms'])} ms ({sc['source']})")
    L.append(f"라벨 {c['labels_in_scope']}/{c['labels_total']}건 (사람 반응 있음 {c['human_positive']}, 없음 {c['human_negative']}, "
             f"불명확 제외 {c['excluded']['grade_unclear']}, AI 미평가 제외 {c['excluded']['ai_unevaluated']})")
    L.append(f"엔진 반응 있음(범위 안) {c['engine_positive_in_scope']}건 / 엔진 판정 {c['link_reactions_in_scope']}건")
    L.append("")
    L.append("[이진: 반응 있음 = direct·likely]")
    L.append(f"  TP {b['tp']}  FN {b['fn']}  FP(라벨 음성) {b['fp_labeled']}  FP(라벨 없음) {b['fp_unlabeled']}  TN(라벨) {b['tn_labeled']}")
    L.append(f"  재현율 {_fmt(b['recall'], True)}   정밀도 {_fmt(b['precision'], True)} (라벨만 {_fmt(b['precision_labeled_only'], True)})"
             f"   F1 {_fmt(b['f1'])}")
    L.append("")
    L.append("[등급 혼동표: 행 = 사람, 열 = 엔진]")
    cols = list(ENGINE_GRADES)
    w = 9
    L.append("  " + "사람\\엔진".ljust(w) + "".join(GRADE_KO[e].rjust(w) for e in cols) + "합".rjust(w))
    tab = rep["grade_confusion"]["table"]
    for h in HUMAN_GRADES:
        row = tab[h]
        L.append("  " + GRADE_KO[h].ljust(w) + "".join(str(row[e]).rjust(w) for e in cols) + str(sum(row.values())).rjust(w))
    ga = rep["grade_exact_agreement"]
    L.append(f"  등급 정확 일치 {ga['exact']}/{ga['n']} = {_fmt(ga['rate'], True)}")
    L.append("")
    L.append("[반응 시각 오차 Δt = 엔진 − 사람]")
    L.append(f"  n {t['n']} (시각 빠짐 {t['missing_time']})  중앙값 {_fmt(t['median_ms'])}ms  90분위 {_fmt(t['p90_ms'])}ms  "
             f"|Δt| 중앙값 {_fmt(t['abs_median_ms'])}ms  |Δt| 90분위 {_fmt(t['abs_p90_ms'])}ms  "
             f"±2초 {_fmt(t['within_2s'], True)}  ±5초 {_fmt(t['within_5s'], True)}")
    L.append("")
    ls = rep["lists"]
    L.append(f"[사람만 찾은 것 {len(ls['human_only'])}건]")
    L.extend("  " + x for x in ls["human_only"])
    L.append(f"[엔진만 찾은 것 {len(ls['engine_only'])}건 (그중 사람이 반응 없음으로 매긴 것 {len(ls['engine_only_labeled_negative'])})]")
    L.extend("  " + x for x in ls["engine_only"])
    ex = rep.get("nickname_exposure")
    L.append("")
    if ex is None:
        L.append("[원 닉네임 노출 기록] 확인 안 함(--project 를 줄 때만 본다)")
    else:
        L.append(f"[원 닉네임 노출 기록] {ex['sessions_total']}건 (채점 범위와 겹침 {ex['sessions_overlapping_scope']}건)")
    if rep["warnings"]:
        L.append("")
        L.append("[경고]")
        L.extend("  - " + x for x in rep["warnings"])
    return "\n".join(L)


EXPOSURE_LOG_REL = os.path.join("labels", "nickname-exposure.jsonl")   # tools/chat_window.py 가 쓴다


def load_exposure_summary(path: str, start_ms: int | None, end_ms: int | None) -> dict:
    """원 닉네임 노출 기록(chat_window --show-nickname) 요약. 파일이 없으면 0건. 숫자만 돌려준다."""
    out = {"log": EXPOSURE_LOG_REL, "log_present": os.path.isfile(path), "sessions_total": 0,
           "sessions_overlapping_scope": 0, "corrupt_lines": 0}
    if not out["log_present"]:
        return out
    import app.jsonl as jsonl
    res = jsonl.read(path)
    out["corrupt_lines"] = len(res.corrupt_lines)
    for r in res.records:
        if r.get("event") != "view.exposed" or r.get("what") != "nickname_raw":
            continue
        out["sessions_total"] += 1
        a, b = r.get("window_start_ms"), r.get("window_end_ms")
        if not (isinstance(a, int) and isinstance(b, int)):
            out["sessions_overlapping_scope"] += 1        # 범위를 모르면 겹친다고 본다
        elif (end_ms is None or a < end_ms) and (start_ms is None or b >= start_ms):
            out["sessions_overlapping_scope"] += 1
    return out


def write_json(doc: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.part-{os.getpid()}"
    data = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run(labels_path: str, link_path: str, *, start_ms=None, end_ms=None, messages_path=None,
        label_time: str = "vod", offset_ms: int | None = None, file_start_ms: int | None = None,
        out_path: str | None = None, exposure_log_path: str | None = None) -> dict:
    link_doc, lw, link_sha = load_link(link_path)
    lcal = link_doc.get("calibration") or {}
    if label_time == "player":
        # v1 link 에는 calibration 이 없다(to_v1 이 뺀다). 이때 0 으로 바꾸면 Δt 가 오프셋만큼 치우친다.
        if offset_ms is None and not isinstance(lcal.get("offset_ms"), int):
            raise ValueError("--label-time player: link 결과에 calibration.offset_ms 가 없습니다(v1?). --offset-ms 를 주세요")
        # 이 도구는 단일 오프셋만 쓴다. anchors 를 쓰는 구간별 보정이면 offset_ms 만으로는 틀린다.
        if offset_ms is None and lcal.get("method", "single_offset") != "single_offset":
            raise ValueError(f"--label-time player: link 결과의 보정 방식 {lcal.get('method')!r} 은 단일 오프셋이 아닙니다."
                             " --offset-ms 를 주거나 utterance_vod_ms 를 VOD 시각으로 적어 --label-time vod 로 돌리세요")
    cal = Calibration(offset_ms=int(offset_ms if offset_ms is not None else (lcal.get("offset_ms") or 0)))
    fs = int(file_start_ms if file_start_ms is not None else (lcal.get("file_start_in_vod_ms") or 0))
    labels, labw, lab_sha = load_labels(labels_path, label_time=label_time, cal=cal, file_start_ms=fs)
    messages = None
    msg_sha = None
    if messages_path:
        messages = load_message_index(messages_path)
        msg_sha = _sha256_file(messages_path)
    rep = score(labels, link_doc, start_ms=start_ms, end_ms=end_ms, messages=messages)
    extra = []
    tgt = (link_doc.get("target") or {}).get("messages_sha256")
    if msg_sha and tgt and tgt != msg_sha:
        extra.append("link 결과의 messages_sha256 이 지금 messages.jsonl 과 다릅니다(채팅 집합이 바뀌었을 수 있음, 06 6-2)")
    exposure = None
    if exposure_log_path:
        exposure = load_exposure_summary(exposure_log_path, rep["scope"]["start_ms"], rep["scope"]["end_ms"])
        if exposure["sessions_total"]:
            extra.append(f"원 닉네임 노출 기록 {exposure['sessions_total']}건(채점 범위와 겹침 "
                         f"{exposure['sessions_overlapping_scope']}건) — 손라벨 중 원 닉네임을 봤을 수 있다(03 14-4, 06)")
    rep["warnings"] = lw + labw + extra + rep["warnings"]
    rep["nickname_exposure"] = exposure
    rep = {
        "format": "chatcoach.score",
        "format_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": {"labels_sha256": lab_sha, "link_sha256": link_sha, "link_run_id": link_doc.get("run_id"),
                   "link_engine": link_doc.get("engine", {}).get("name"),
                   "link_engine_version": link_doc.get("engine", {}).get("version"),
                   "link_config_sha256": link_doc.get("engine", {}).get("config_sha256"),
                   "messages_sha256": msg_sha,
                   "label_time": label_time,
                   "label_time_conversion": ({"offset_ms": cal.offset_ms, "file_start_in_vod_ms": fs}
                                             if label_time == "player" else None)},
        **rep,
    }
    if out_path:
        write_json(rep, out_path)
    return rep


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine score", description="손라벨 csv ↔ link 결과 채점")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--link", required=True)
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--messages", help="messages.jsonl (chat_id 존재 확인)")
    g.add_argument("--project", help="프로젝트 폴더(<P>/chat/messages.jsonl 을 씀)")
    ap.add_argument("--label-time", choices=("vod", "player"), default="vod",
                    help="utterance_vod_ms 칸의 시계. player 면 보정으로 VOD 시각으로 바꾼다(기본 vod)")
    ap.add_argument("--offset-ms", type=int, help="--label-time player 에서 쓸 오프셋(기본: link 결과의 값)")
    ap.add_argument("--file-start-ms", type=int, help="--label-time player 에서 클립 시작(기본: link 결과의 값)")
    ap.add_argument("--out", help="보고서 json (저장소 밖에 둘 것)")
    ns = ap.parse_args(argv)
    mp = ns.messages or (os.path.join(ns.project, "chat", "messages.jsonl") if ns.project else None)
    try:
        rep = run(ns.labels, ns.link, start_ms=ns.start_ms, end_ms=ns.end_ms, messages_path=mp,
                  label_time=ns.label_time, offset_ms=ns.offset_ms, file_start_ms=ns.file_start_ms, out_path=ns.out,
                  exposure_log_path=os.path.join(ns.project, EXPOSURE_LOG_REL) if ns.project else None)
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:300]}, ensure_ascii=False))
        return 1
    print(format_report(rep))
    if ns.out:
        print(f"\n보고서: {ns.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
