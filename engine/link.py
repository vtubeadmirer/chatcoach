"""채팅 ↔ 발화 연결 시험판 link-v0 (08 D-03 3번: 증거 7종 자동 탐지 + 4-2 규칙 등급).

입력
  - chat/messages.jsonl (가명 채팅. text 만 판정한다)
  - chat/protected/identities.jsonl (선택. 수집 PC 로컬의 가림 전 닉네임 — 닉네임 호명 증거에만 쓰고 결과에 넣지 않는다)
  - derived/asr.json (engine.asr 결과, 영상 파일 시각)
  - 보정: project.json 의 calibration(있으면) 또는 명령줄 --offset-ms · --file-start-ms (기본 0, '앵커로 보정 필요')
출력
  ai-result 형식 schema_version 2 초안(측정 문서 2장에 정의). schemas/ai-result.schema.json(v1)은 고치지 않았다.
  to_v1() 로 v1 스키마에 맞는 부분만 뽑을 수 있다.

실행(저장소 루트에서. 무거운 의존성이 없어 앱 .venv 로도 돈다)
  python -m engine.link --project <폴더> --out <결과.json> [--start-ms 0] [--end-ms 300000] [--review-csv <csv>]

결과·표준 출력에 채팅 본문·닉네임·전사문을 넣지 않는다. 근거 한 줄은 시각과 증거 이름만 쓴다.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from app.core import rules
from app.core.rules import EVIDENCE_KEYS, RuleConfig
from app.core.timecode import Calibration, calibration_from_json, chat_to_video_ms, fmt_display, video_to_chat_ms

from . import features as F

ENGINE_NAME = "chatcoach-link"
ENGINE_VERSION = "0.1.0"
RESULT_SCHEMA_VERSION = 2          # 초안. 스키마 파일은 v1 그대로(측정 문서 2장)

EVIDENCE_LABEL = {
    "nickname_called": "닉네임 호명", "text_repeated": "문구 되풀이", "direct_reference": "직접 지칭",
    "answer_form": "답의 형태", "unique_content": "고유 내용", "within_time_window": "시간 창",
    "same_topic": "같은 주제",
}
STRENGTH_LABEL = {"strong": "강", "medium": "중", "weak": "약", "gate": "관문"}
GRADE_ORDER = {"direct": 0, "likely": 1, "unclear": 2, "related_only": 3, None: 9}


@dataclass(frozen=True)
class LinkConfig:
    """엔진 시작값. rule 은 안내서 시작값(guide-config), 나머지는 안내서에 숫자가 없어 엔진이 정한 값이다."""
    rule: RuleConfig = rules.V0_1
    similar_window_ms_default: int = 20000   # guide-config similar_chat_window_sec 가 null(미정)일 때 엔진이 쓰는 값
    similar_min: float = 0.5                 # 비슷한 채팅: bigram Dice 하한
    on_topic_lookback_ms: int = 60000        # '이미 그 주제를 말하던 중' 을 볼 채팅 앞 시간
    # 2026-10-21 v0.2: 채팅 내용어 가운데 이 비율(약 3분의 1) 이상을 앞 발화가 이미 말했을 때만 '이미 그 주제' 로 본다.
    # 이전(0 에 해당)에는 낱말 하나만 겹쳐도 켜져, 대화가 이어지는 동안 거의 늘 '높음' 을 막았다.
    on_topic_min_ratio: float = 0.33
    prior_lookback_ms: int = 120000          # '채팅에만 있던 내용' 을 확인할 채팅 앞 시간(missed.prior_handling_window_sec 와 같은 값)
    repeat_min_chars: int = 4
    repeat_min_containment: float = 0.6
    repeat_min_run_ratio: float = 0.6
    # 2026-10-21 v0.2: STT 조각은 2초 안팎이라 긴 채팅을 읽으면 여러 조각에 걸친다. 후보 조각 시작부터
    # merge_follow_ms 안에 시작하는 뒤 조각을 이어 붙인 글로 문구 되풀이·고유 내용을 본다(0 이면 조각 하나만).
    merge_follow_ms: int = 6000
    repeat_abs_run_chars: int | None = 7       # features.text_repeated 의 2번 조건 (None 이면 끔)
    repeat_partial_run_chars: int | None = 5   # 3번 조건 (None 이면 끔)
    repeat_partial_run_ratio: float = 0.5
    repeat_partial_min_containment: float = 0.5
    # 강한 증거가 이 채팅에만 있으면(비슷한 채팅들은 같은 발화와 되풀이가 아님) 비슷한 채팅이 있어도 직접으로 본다
    strong_unique_resolves_similar: bool = True
    same_topic_min_dice: float = 0.2
    nick_max_ratio: float = 0.3
    direct_reference_needs_link: bool = True  # 직접 지칭 표현 + 채팅과 내용 연결이 있어야 증거로 본다
    certain_margin: float = 0.5
    probably_margin: float = 0.25

    @property
    def similar_window_ms(self) -> int:
        w = self.rule.similar_chat_window_ms
        return self.similar_window_ms_default if w is None else w

    def to_json(self) -> dict:
        d = asdict(self)
        d["rule"] = {"time_window_ms": self.rule.time_window_ms, "direct_min_strong": self.rule.direct_min_strong,
                     "likely_min_medium": self.rule.likely_min_medium,
                     "similar_chat_window_ms": self.rule.similar_chat_window_ms,
                     "evidence_strength": dict(self.rule.evidence_strength)}
        return d

    def sha256(self) -> str:
        b = json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(b.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Utterance:
    seg_id: int
    start_ms: int      # 영상 파일 시각
    end_ms: int
    text: str


@dataclass(frozen=True)
class Chat:
    chat_id: str
    t_ms: int          # VOD 시각
    viewer_id: str
    text: str


@dataclass
class Inputs:
    chats: list[Chat]
    utterances: list[Utterance]
    nicknames: dict[str, list[str]] | None      # viewer_id → 가림 전 닉네임들. None = 목록 없음(호명은 판단 불가)
    calibration: Calibration
    file_start_in_vod_ms: int = 0
    calibration_source: str = "default"
    video_no: str | None = None
    messages_sha256: str | None = None
    asr_meta: dict = field(default_factory=dict)
    skipped_kinds: dict = field(default_factory=dict)


# ── 읽기 ─────────────────────────────────────────────────────────────────

def _reject_const(name):
    raise ValueError(f"JSON 에 {name} 은 쓸 수 없습니다")


def _read_json(path: str) -> dict:
    with open(path, "rb") as f:
        b = f.read()
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    return json.loads(b.decode("utf-8"), parse_constant=_reject_const)


def _read_jsonl(path: str) -> list[dict]:
    import app.jsonl as jsonl   # engine 은 app.core.*·app.jsonl 만 쓴다(test_layers)
    res = jsonl.read(path)
    if not res.ok:
        raise ValueError(f"{os.path.basename(path)}: 중간에 깨진 줄 {len(res.corrupt_lines)}개")
    return res.records


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def load_messages(path: str) -> tuple[list[Chat], dict]:
    """text 채팅만 Chat 으로. t_ms 가 null 인 것과 text 가 아닌 것은 종류별 개수만 센다."""
    chats, skipped = [], {}
    for r in _read_jsonl(path):
        k = r.get("kind")
        if k != "text" or r.get("t_ms") is None:
            key = k if k != "text" else "text_invalid_time"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        chats.append(Chat(r["chat_id"], int(r["t_ms"]), r.get("viewer_id", "anon"), r.get("text", "")))
    chats.sort(key=lambda c: (c.t_ms, c.chat_id))
    return chats, skipped


def load_identities(path: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in _read_jsonl(path):
        names = [n["nickname"] for n in r.get("nicknames", []) if n.get("nickname")]
        out[r["viewer_id"]] = names
    return out


def load_asr(path: str) -> tuple[list[Utterance], dict]:
    d = _read_json(path)
    if d.get("format") != "chatcoach.asr":
        raise ValueError("asr.json 형식이 아닙니다")
    utts = [Utterance(int(s.get("id", i)), int(s["start_ms"]), int(s["end_ms"]), s.get("text", ""))
            for i, s in enumerate(d.get("segments", []))]
    utts.sort(key=lambda u: (u.start_ms, u.seg_id))
    meta = {"model": (d.get("model") or {}).get("name"), "vad": (d.get("settings") or {}).get("vad"),
            "duration_ms": (d.get("audio") or {}).get("duration_ms"), "sha256": _sha256_file(path),
            "segments": len(utts)}
    return utts, meta


def load_project(project_dir: str, *, use_identities: bool = True, offset_ms: int | None = None,
                 file_start_ms: int | None = None, asr_path: str | None = None) -> Inputs:
    """프로젝트 폴더에서 입력을 모은다. project.json 이 있으면 활성 보정을 쓰고, 명령줄 값이 있으면 그것이 이긴다."""
    msg_path = os.path.join(project_dir, "chat", "messages.jsonl")
    chats, skipped = load_messages(msg_path)
    utts, asr_meta = load_asr(asr_path or os.path.join(project_dir, "derived", "asr.json"))
    nick = None
    idp = os.path.join(project_dir, "chat", "protected", "identities.jsonl")
    if use_identities and os.path.isfile(idp):
        nick = load_identities(idp)

    cal, src, video_no = Calibration(), "default", None
    pj_cal = None
    pj = os.path.join(project_dir, "project.json")
    if os.path.isfile(pj):
        p = _read_json(pj)
        video_no = (p.get("broadcast") or {}).get("video_no")
        if p.get("calibration"):
            pj_cal = p["calibration"]
            cal, src = calibration_from_json(pj_cal), "project.json"
    if video_no is None:
        chat_dir = os.path.join(project_dir, "chat")
        for n in sorted(os.listdir(chat_dir)):
            if n.startswith("fetch-") and n.endswith(".json"):
                video_no = _read_json(os.path.join(chat_dir, n)).get("video_no")
                break
    fs = 0
    if offset_ms is None and file_start_ms is not None and pj_cal is not None:
        # project.json 의 offset_ms 는 '오프셋 - 클립 시작' 을 합친 값이다(timecode.calibration_to_json).
        # 클립 시작만 따로 주면 그 값을 되돌려 넣어 읽어야 클립 시작을 두 번 빼지 않는다.
        fs = file_start_ms
        cal = calibration_from_json(pj_cal, file_start_in_vod_ms=fs)
        src = "project.json+cli_file_start"
    elif offset_ms is not None or file_start_ms is not None:
        cal = replace(cal, offset_ms=offset_ms if offset_ms is not None else cal.offset_ms,
                      method="single_offset", status="unverified")
        fs = file_start_ms or 0
        src = "cli"
    return Inputs(chats=chats, utterances=utts, nicknames=nick, calibration=cal, file_start_in_vod_ms=fs,
                  calibration_source=src, video_no=video_no, messages_sha256=_sha256_file(msg_path),
                  asr_meta=asr_meta, skipped_kinds=skipped)


# ── 증거 계산 ────────────────────────────────────────────────────────────

def repeated_in(chat_text: str, utt_text: str, cfg: LinkConfig) -> bool:
    """LinkConfig 의 시작값으로 features.text_repeated 를 부른다(엔진 안 한 곳에서만 값을 넘긴다)."""
    return F.text_repeated(chat_text, utt_text, min_chars=cfg.repeat_min_chars,
                           min_containment=cfg.repeat_min_containment, min_run_ratio=cfg.repeat_min_run_ratio,
                           abs_run_chars=cfg.repeat_abs_run_chars, partial_run_chars=cfg.repeat_partial_run_chars,
                           partial_run_ratio=cfg.repeat_partial_run_ratio,
                           partial_min_containment=cfg.repeat_partial_min_containment)


def evidence_for(chat: Chat, utt: Utterance, chat_video_ms: int, *, nicknames: Sequence[str] | None,
                 prior_texts: Sequence[str], cfg: LinkConfig, utt_vod_ms: int,
                 merged_text: str | None = None) -> dict:
    """채팅–발화 짝 하나의 증거 7항목. 값은 {'present': bool|None, 'at_ms': VOD ms|None}.

    merged_text: 이 조각과 바로 뒤 조각들을 이어 붙인 글(문구 되풀이·고유 내용·내용어 공유에 쓴다). 없으면 조각 글.
    답의 형태·직접 지칭·호명은 첫 조각(반응을 시작한 말)으로 본다.
    """
    c, u = chat.text, utt.text
    um = merged_text if merged_text else u
    topic = F.same_topic(c, u, min_dice=cfg.same_topic_min_dice)
    repeated = repeated_in(c, um, cfg)
    if repeated and um is not u and not repeated_in(c, u, cfg):
        # 이어 붙인 글로만 되풀이가 보이면, 겹친 덩어리가 이 조각 안에서 시작할 때만 이 조각의 증거로 센다.
        # 그러지 않으면 실제로 읽기 몇 초 앞 조각이 반응 시각으로 잡힌다(2026-10-21 개발용 측정).
        repeated = F.run_starts_in_head(c, um, u)
    answer = F.answer_form(c, u)
    terms = F.direct_reference_terms(u)
    # 직접 지칭 표현은 발화만 보면 어느 채팅인지 모른다 → 내용어 공유·되풀이·답의 형태 중 하나가 함께 있어야 센다
    linked = repeated or answer or bool(F.shared_content(c, um))
    direct_ref = bool(terms) and (not cfg.direct_reference_needs_link or linked)
    nick = F.nickname_called(nicknames, u, max_ratio=cfg.nick_max_ratio)
    vals = {
        "nickname_called": nick,
        "text_repeated": repeated,
        "direct_reference": direct_ref,
        "answer_form": answer,
        "unique_content": bool(F.unique_content(c, um, prior_texts)),
        "within_time_window": rules.within_time_window(chat_video_ms, utt.start_ms, cfg.rule),
        "same_topic": topic,
    }
    return {k: {"present": v, "at_ms": utt_vod_ms if v else None} for k, v in vals.items()}


def _pick_grade(gc: rules.GradeConditions, *, strong_unique: bool = False) -> str | None:
    """엔진의 등급 선택(앱은 고르지 않는다). 직접(비슷한 채팅이 있으면 호명이거나 되풀이가 이 채팅에만 맞을 때만)
    > (동점·비슷한 채팅 → 불명확) > 높음 > 관련성만."""
    if gc.result == "unconfirmed":
        return None
    best = gc.best()
    if best is None or best.tier is None:
        return None
    if best.tier == "direct":
        # 비슷한 채팅이 같은 시간대에 있으면 문구 되풀이·직접 지칭은 어느 채팅인지 가르지 못한다(예시 4).
        # 닉네임 호명만 채팅 하나를 가리킨다.
        if ("similar_chat_nearby" in gc.reasons and "nickname_called" not in best.present
                and not strong_unique):
            return "unclear"
        return "direct"
    if gc.tied:
        return "unclear"
    if gc.similar_blocked:
        # 스트리머가 이미 그 주제를 말하던 중이면 비슷한 채팅이 있어도 '관련성만'(안내서 4-4 예시 3).
        # 증거가 더 많은 후보가 더 낮은 판정(불명확)을 받는 역전을 막는다.
        if "already_on_topic" in best.blocked:
            return "related_only"
        return "unclear"
    return best.tier


def _reaction_candidate(grade: str | None, gc: rules.GradeConditions) -> rules.CandidateGrade | None:
    """반응 시각을 줄 후보. 직접이면 직접 후보 가운데 가장 앞(첫 반응, 안내서 4-3), 아니면 best()."""
    best = gc.best() if grade else None
    if grade == "direct":
        directs = [c for c in gc.per_candidate if c.tier == "direct"]
        if directs:
            return min(directs, key=lambda c: c.index)
    return best


def _confidence(grade: str | None, gc: rules.GradeConditions) -> tuple[str | None, float | None]:
    """확신도 = 1·2위 후보 점수 차 / 1위 점수. 직접만 '확실함' 이 될 수 있다(엔진 시작값)."""
    if grade is None:
        return None, None
    scores = sorted((c.score for c in gc.per_candidate), reverse=True)
    s1 = scores[0]
    s2 = scores[1] if len(scores) > 1 else 0.0
    margin = round((s1 - s2) / s1, 4) if s1 > 0 else 0.0
    if grade == "unclear":
        return "probably", margin          # 가릴 수 없다는 판정 자체는 분명하다(안내서 5장 '불명확 / 확실함' 과 같은 취지)
    if margin >= 0.5 and grade == "direct":
        return "certain", margin
    if margin >= 0.25:
        return "probably", margin
    return "undeterminable", margin


def link(inp: Inputs, cfg: LinkConfig = LinkConfig(), *, start_ms: int | None = None,
         end_ms: int | None = None) -> tuple[list[dict], dict]:
    """채팅마다 반응 초안 1줄. (reactions, stats) 를 돌려준다. 순수 계산(입력은 Inputs 로 받는다)."""
    cal, fs = inp.calibration, inp.file_start_in_vod_ms
    utts = inp.utterances
    u_starts = [u.start_ms for u in utts]
    max_len = max((u.end_ms - u.start_ms for u in utts), default=0)
    chats = inp.chats
    c_times = [c.t_ms for c in chats]
    ngr = [F.char_ngrams(c.text) for c in chats]
    low = [F.is_low_content(c.text) for c in chats]
    audio_end = inp.asr_meta.get("duration_ms")
    W = cfg.rule.time_window_ms
    sw = cfg.similar_window_ms

    reactions: list[dict] = []
    st = {"chats_in_scope": 0, "chats_low_content": 0, "window_utterances_total": 0, "candidates_total": 0,
          "chats_with_window_utterance": 0, "chats_with_candidate": 0, "window_truncated_by_audio_end": 0,
          "nickname_unknown": 0, "similar_chat_flagged": 0, "on_topic_flagged": 0,
          "evidence_present": {k: 0 for k in EVIDENCE_KEYS}}
    for ci, ch in enumerate(chats):
        if start_ms is not None and ch.t_ms < start_ms:
            continue
        if end_ms is not None and ch.t_ms >= end_ms:
            continue
        st["chats_in_scope"] += 1
        if low[ci]:
            st["chats_low_content"] += 1
        v = chat_to_video_ms(ch.t_ms, cal, fs)
        if audio_end is not None and v + W > audio_end:
            st["window_truncated_by_audio_end"] += 1
        # 시간 창 안 발화(시작 시각 기준 [v, v+W])
        lo = bisect.bisect_left(u_starts, v)
        hi = bisect.bisect_right(u_starts, v + W)
        window = utts[lo:hi]
        # 채팅 앞 발화: 고유 내용 확인용 / 이미 그 주제 확인용
        p0 = bisect.bisect_left(u_starts, v - cfg.prior_lookback_ms - max_len)
        prior = [u for u in utts[p0:lo] if u.end_ms > v - cfg.prior_lookback_ms]
        recent = [u for u in prior if u.start_ms >= v - cfg.on_topic_lookback_ms]
        # '이미 그 주제' 는 약한 증거(same_topic)보다 엄하게: 채팅의 내용어를 앞 발화가 이미 말했을 때만
        ctoks = F.content_tokens(ch.text)
        if ctoks and recent:
            spoken = "".join(F.compact(u.text) for u in recent)
            hit = sum(1 for t in ctoks if t in spoken)
            on_topic = hit > 0 and hit / len(ctoks) >= cfg.on_topic_min_ratio
        else:
            on_topic = False
        # 같은 시간대 비슷한 다른 시청자 채팅
        similar_ids: list[str] = []
        similar_idx: list[int] = []
        if not low[ci]:
            a = bisect.bisect_left(c_times, ch.t_ms - sw)
            b = bisect.bisect_right(c_times, ch.t_ms + sw)
            for j in range(a, b):
                if j == ci or low[j]:
                    continue
                o = chats[j]
                if o.viewer_id == ch.viewer_id and o.viewer_id != "anon":
                    continue
                if F.dice(ngr[ci], ngr[j]) >= cfg.similar_min:
                    similar_ids.append(o.chat_id)
                    similar_idx.append(j)
        no_similar = not similar_ids
        nick_list = None if inp.nicknames is None else inp.nicknames.get(ch.viewer_id, [])
        if nick_list is None:
            st["nickname_unknown"] += 1

        cands, cand_meta = [], []
        prior_texts = [u.text for u in prior]
        for k_off, u in enumerate(window):
            uv = video_to_chat_ms(u.start_ms, cal, fs)
            merged = u.text
            if cfg.merge_follow_ms > 0:
                parts, j = [u.text], lo + k_off + 1
                while j < len(utts) and utts[j].start_ms <= u.start_ms + cfg.merge_follow_ms:
                    parts.append(utts[j].text)
                    j += 1
                merged = " ".join(parts)
            ev = evidence_for(ch, u, v, nicknames=nick_list, prior_texts=prior_texts, cfg=cfg, utt_vod_ms=uv,
                              merged_text=merged)
            # 시간 창 말고 다른 증거가 하나도 없는 발화는 후보로 올리지 않는다(시간 근접만으로 연결하지 않음)
            if not any(ev[k]["present"] for k in EVIDENCE_KEYS if k != "within_time_window"):
                continue
            cands.append({"evidence": ev})
            cand_meta.append((u, uv, merged))
        st["window_utterances_total"] += len(window)
        st["candidates_total"] += len(cands)
        st["chats_with_window_utterance"] += bool(window)
        st["chats_with_candidate"] += bool(cands)
        if similar_ids:
            st["similar_chat_flagged"] += 1
        if on_topic:
            st["on_topic_flagged"] += 1

        gc = rules.grade_conditions(cands, cfg.rule, no_similar_chat_nearby=no_similar,
                                    streamer_already_on_topic=on_topic)
        # 강한 증거(문구 되풀이)가 이 채팅에만 있는가: 비슷한 채팅들이 같은 발화(이어 붙인 글)와 되풀이가 아니면
        # 그 발화는 이 채팅을 읽은 것으로 가를 수 있다. 같은 질문이 둘 다 되풀이되면(안내서 예시 4) 여전히 불명확.
        strong_unique = False
        gb = gc.best()
        if (cfg.strong_unique_resolves_similar and similar_idx and gb is not None and gb.tier == "direct"
                and "text_repeated" in gb.present):
            bmerged = cand_meta[gb.index][2]
            strong_unique = not any(repeated_in(chats[j].text, bmerged, cfg) for j in similar_idx)
        grade = _pick_grade(gc, strong_unique=strong_unique)
        conf, margin = _confidence(grade, gc)
        best = gc.best() if grade else None
        cand_out = []
        for cg, (u, uv, _m), cd in zip(gc.per_candidate, cand_meta, cands):
            for k in EVIDENCE_KEYS:
                if cd["evidence"][k]["present"]:
                    st["evidence_present"][k] += 1
            cand_out.append({
                "asr_segment_id": u.seg_id,
                "utterance_start_ms": uv,
                "utterance_end_ms": video_to_chat_ms(u.end_ms, cal, fs),
                "tier": cg.tier, "score": cg.score, "strong": cg.strong, "medium": cg.medium, "weak": cg.weak,
                "evidence": cd["evidence"],
            })
        react = _reaction_candidate(grade, gc)
        if best is not None:
            bu, buv, _bm = cand_meta[best.index]
            rationale = _rationale(buv, best, cfg.rule, grade, gc)
            # 반응 시각은 첫 반응 후보(직접일 때 가장 앞 직접 후보), 점수·근거는 점수 최댓값 후보
            ru, ruv, _rm = cand_meta[react.index]
            r_time, u_start, u_end = ruv, ruv, video_to_chat_ms(ru.end_ms, cal, fs)
            score = best.score
        else:
            rationale = (f"{fmt_display(ch.t_ms)} 채팅 뒤 {W // 1000}초 안 발화 {len(window)}개, "
                         "시간 외 증거 없음 → 반응 미확인")
            r_time = u_start = u_end = None
            score = None
        reactions.append({
            "chat_id": ch.chat_id,
            "result": gc.result,
            "grade": grade,
            "reaction_time_ms": r_time,
            "utterance_start_ms": u_start,
            "utterance_end_ms": u_end,
            "score": score,
            "chat_t_ms": ch.t_ms,
            "eligible_grades": list(gc.eligible_grades),
            "confidence": conf,
            "confidence_margin": margin,
            "unclear_cause": gc.unclear_cause if grade == "unclear" else None,
            "no_similar_chat_nearby": no_similar,
            "similar_chat_ids": similar_ids,
            "streamer_already_on_topic": on_topic,
            "nickname_evidence_available": nick_list is not None,
            "window_utterances": len(window),
            "linked_candidate": react.index if react is not None else None,
            "candidates": cand_out,
            "reasons": list(gc.reasons),
            "rationale": rationale,
        })
    return reactions, st


def _rationale(at_vod_ms: int, cg: rules.CandidateGrade, rc: RuleConfig, grade: str | None,
               gc: rules.GradeConditions) -> str:
    parts = [f"{EVIDENCE_LABEL[k]}({STRENGTH_LABEL[rc.evidence_strength[k]]})" for k in cg.present]
    tail = ""
    if grade == "unclear":
        tail = " · 후보 여럿" if gc.tied else " · 비슷한 채팅 있음"
    elif cg.blocked:
        tail = " · 높음 막힘(" + ",".join(cg.blocked) + ")"
    return f"{fmt_display(at_vod_ms)} 발화: " + "·".join(parts) + tail


# ── 결과 문서 ────────────────────────────────────────────────────────────

def build_result(inp: Inputs, reactions: list[dict], stats: dict, cfg: LinkConfig, *, start_ms, end_ms,
                 elapsed_s: float, created_at: str, run_id: str) -> dict:
    cal = inp.calibration
    grades = {}
    for r in reactions:
        g = r["grade"] or ("unconfirmed" if r["result"] == "unconfirmed" else "none")
        grades[g] = grades.get(g, 0) + 1
    confs = {}
    for r in reactions:
        if r["confidence"]:
            confs[r["confidence"]] = confs.get(r["confidence"], 0) + 1
    stats = dict(stats, grade_counts=grades, confidence_counts=confs, elapsed_s=round(elapsed_s, 3),
                 skipped_non_text=inp.skipped_kinds)
    return {
        "schema": "chatcoach/ai-result",
        "schema_version": RESULT_SCHEMA_VERSION,
        "x_draft": "link-v0 — docs/measurements/2026-10-20-link-v0.md 2장 (스키마 파일은 v1)",
        "run_id": run_id,
        "created_at": created_at,
        "engine": {
            "name": ENGINE_NAME, "version": ENGINE_VERSION,
            "components": {"vad": "silero (faster-whisper)" if inp.asr_meta.get("vad") else None,
                           "asr": inp.asr_meta.get("model"), "ocr": None, "other": "features-v0 rules-v0.1"},
            "config_sha256": cfg.sha256(), "preregistered": False,
        },
        "target": {"platform": "chzzk", "video_no": inp.video_no or "0", "chat_id_algorithm": "sl-chat-id-v1",
                   "messages_sha256": inp.messages_sha256},
        "time_axis": "vod_ms",
        "media": None,
        "calibration": {
            "source": inp.calibration_source, "offset_ms": cal.offset_ms, "method": cal.method,
            "file_start_in_vod_ms": inp.file_start_in_vod_ms, "status": cal.status,
            "needs_anchor_calibration": cal.status == "unverified",
        },
        "scope": {"start_ms": start_ms, "end_ms": end_ms, "asr_duration_ms": inp.asr_meta.get("duration_ms")},
        "inputs": {"asr_sha256": inp.asr_meta.get("sha256"), "asr_segments": inp.asr_meta.get("segments"),
                   "identities_used": inp.nicknames is not None},
        "config": cfg.to_json(),
        # guide-config 가 null(미정)인 값을 엔진이 대신 정해 쓴 것. 이 값에 기댄 similar_chat_ids·'불명확' 은 엔진 가정이다.
        "assumptions": {
            "similar_chat_window_ms": cfg.similar_window_ms,
            "similar_chat_window_source": ("engine_default" if cfg.rule.similar_chat_window_ms is None
                                           else "guide_config"),
        },
        "stats": stats,
        "reactions": reactions,
    }


V1_REACTION_KEYS = ("chat_id", "result", "grade", "reaction_time_ms", "utterance_start_ms", "utterance_end_ms", "score")
V1_TOP_KEYS = ("schema", "run_id", "created_at", "engine", "target", "time_axis")


def to_v1(doc: dict) -> dict:
    """schema_version 1(schemas/ai-result.schema.json)에 맞는 부분만 뽑는다."""
    out = {k: doc[k] for k in V1_TOP_KEYS}
    out["schema_version"] = 1
    out["reactions"] = [{k: r[k] for k in V1_REACTION_KEYS} for r in doc["reactions"]]
    return out


def validate_result(doc: dict) -> list[str]:
    """v2 초안의 손 검사(정식 스키마 없음). 빈 목록이면 통과."""
    import re
    p: list[str] = []
    cid = re.compile(r"^c[0-9a-f]{20}(_[0-9]+)?$")
    for k in ("schema", "schema_version", "run_id", "created_at", "engine", "target", "time_axis", "reactions"):
        if k not in doc:
            p.append(f"$.{k} 없음")
    if p:
        return p
    if doc["schema"] != "chatcoach/ai-result" or doc["schema_version"] != RESULT_SCHEMA_VERSION:
        p.append("schema/schema_version")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", doc["run_id"]):
        p.append("run_id 형식")
    if doc["time_axis"] != "vod_ms":
        p.append("time_axis")
    for i, r in enumerate(doc["reactions"]):
        w = f"$.reactions[{i}]"
        if not cid.match(r.get("chat_id", "")):
            p.append(f"{w}.chat_id")
        if r["result"] not in ("graded", "unconfirmed"):
            p.append(f"{w}.result")
        if r["result"] == "unconfirmed" and (r["grade"] is not None or r["candidates"] or r["reaction_time_ms"] is not None):
            p.append(f"{w}: unconfirmed 인데 등급·후보·시각이 있음")
        if r["result"] == "graded" and not r["candidates"]:
            p.append(f"{w}: graded 인데 후보 없음")
        if r["grade"] not in (None, *rules.GRADES):
            p.append(f"{w}.grade")
        if r["grade"] in ("direct", "likely") and r["reaction_time_ms"] is None:
            p.append(f"{w}: 직접·높음인데 반응 시각 없음")
        if r["grade"] == "direct":
            lc = r["candidates"][r["linked_candidate"]]
            if lc["strong"] < 1:
                p.append(f"{w}: 강한 증거 없는 직접 반응")
        if r["grade"] == "unclear" and not r.get("unclear_cause"):
            p.append(f"{w}: unclear_cause 없음")
        if r.get("confidence") not in (None, "certain", "probably", "undeterminable"):
            p.append(f"{w}.confidence")
        for j, c in enumerate(r["candidates"]):
            if set(c["evidence"]) != set(EVIDENCE_KEYS):
                p.append(f"{w}.candidates[{j}].evidence 키")
            for k, e in c["evidence"].items():
                if e["present"] not in (True, False, None):
                    p.append(f"{w}.candidates[{j}].evidence.{k}.present")
            for k in ("utterance_start_ms", "utterance_end_ms"):
                if isinstance(c[k], bool) or not isinstance(c[k], int):
                    p.append(f"{w}.candidates[{j}].{k} 정수 아님")
    return p


def write_json(doc: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.part-{os.getpid()}"
    data = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def review_rows(reactions: Sequence[dict], top: int = 20) -> list[dict]:
    """사람 확인용 상위 N건. 등급(직접→높음→불명확→관련성만) → 점수 내림차순 → 채팅 시각."""
    graded = [r for r in reactions if r["grade"]]
    graded.sort(key=lambda r: (GRADE_ORDER[r["grade"]], -(r["score"] or 0), r["chat_t_ms"]))
    rows = []
    for r in graded[:top]:
        c = r["candidates"][r["linked_candidate"]]
        keys = [k for k in EVIDENCE_KEYS if c["evidence"][k]["present"]]
        rows.append({"chat_id": r["chat_id"], "chat_vod": fmt_display(r["chat_t_ms"]),
                     "chat_vod_ms": r["chat_t_ms"], "utterance_vod": fmt_display(r["utterance_start_ms"]),
                     "utterance_vod_ms": r["utterance_start_ms"], "grade": r["grade"],
                     "confidence": r["confidence"], "score": r["score"], "evidence": ";".join(keys)})
    return rows


def write_review_csv(rows: Sequence[dict], path: str) -> None:
    cols = ["chat_id", "chat_vod", "chat_vod_ms", "utterance_vod", "utterance_vod_ms", "grade", "confidence",
            "score", "evidence"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run(project_dir: str, out_path: str, *, start_ms=None, end_ms=None, use_identities=True, offset_ms=None,
        file_start_ms=None, asr_path=None, review_csv=None, top=20, cfg: LinkConfig = LinkConfig(),
        v1_out=None) -> dict:
    t0 = time.perf_counter()
    inp = load_project(project_dir, use_identities=use_identities, offset_ms=offset_ms,
                       file_start_ms=file_start_ms, asr_path=asr_path)
    t_load = time.perf_counter() - t0
    t1 = time.perf_counter()
    reactions, stats = link(inp, cfg, start_ms=start_ms, end_ms=end_ms)
    t_link = time.perf_counter() - t1
    now = datetime.now().astimezone()
    run_id = f"link-v0-{now.strftime('%Y%m%dT%H%M%S')}"
    doc = build_result(inp, reactions, stats, cfg, start_ms=start_ms, end_ms=end_ms,
                       elapsed_s=t_link, created_at=now.isoformat(timespec="seconds"), run_id=run_id)
    doc["stats"]["load_s"] = round(t_load, 3)
    probs = validate_result(doc)
    if probs:
        raise ValueError(f"결과 검사 실패 {len(probs)}건: {probs[:5]}")
    write_json(doc, out_path)
    if v1_out:
        write_json(to_v1(doc), v1_out)
    if review_csv:
        write_review_csv(review_rows(reactions, top), review_csv)
    return doc


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine.link", description="채팅 ↔ 발화 연결 시험판(link-v0)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--asr", help="asr.json 경로(기본 <project>/derived/asr.json)")
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    ap.add_argument("--offset-ms", type=int, help="보정 오프셋(영상 = VOD − 클립 시작 + 오프셋). 주면 project.json 보다 앞선다")
    ap.add_argument("--file-start-ms", type=int, help="영상 파일이 VOD 몇 ms 에서 시작하는지(클립)")
    ap.add_argument("--no-identities", action="store_true", help="닉네임 목록을 쓰지 않음(호명은 판단 불가)")
    ap.add_argument("--review-csv", help="사람 확인용 상위 N건 CSV(저장소 밖에만 둘 것)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--v1-out", help="schema_version 1 부분만 따로 저장")
    ns = ap.parse_args(argv)
    try:
        doc = run(ns.project, ns.out, start_ms=ns.start_ms, end_ms=ns.end_ms, use_identities=not ns.no_identities,
                  offset_ms=ns.offset_ms, file_start_ms=ns.file_start_ms, asr_path=ns.asr,
                  review_csv=ns.review_csv, top=ns.top, v1_out=ns.v1_out)
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:300]}, ensure_ascii=False))
        return 1
    print(json.dumps({"out": ns.out, "calibration": doc["calibration"], "stats": doc["stats"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
