"""최종 세트(정답 세트) 계산 — 현재 유효 라벨에 조정 기록을 바꿔 끼운다
(docs/design/16-progress-2026-10-21-c7.md 8-1 4번, 06 6-5·6-6, 03 6-12, 08 D-13 2·D-15 7).

하는 일
- 1차 세션(primary)의 현재 유효 라벨이 바탕이다. 재라벨 세션(relabel_same·relabel_second)은 '두 번 같은가' 를 보는 데만 쓴다.
- 조정 기록(adjudication)은 **adjudicate.case_states 와 같은 열쇠**로 잇는다: (target_kind, {(세션, 대상)}).
  표본(반응)은 1차와 재라벨에 모두 있는 채팅마다 adjudicate.Case 를 만들어 case_states 를 그대로 부른다(부분·상위 집합
  대응·stale 판단까지 같다). 구간은 adjudicate.target_key 로 같은 열쇠의 마지막(seq 큰) 기록을 쓴다.
- decision 별 처리
  · final            → 그 값으로 바꾼다(source = adjudication). 고른 쪽 본문과 같은 라벨이 있으면 그 라벨을 쓴다.
                       재라벨에서 두 판정이 달랐던 항목이면 엄격 점수에는 넣지 않는다(06 6-5 '재라벨된 항목은 두 번
                       같은 등급' — 보고서 전용 사유 adjudicated_disagreement). 참고 점수에는 조정 값으로 들어간다.
  · exclude_from_eval→ 세트에서 뺀다(excluded). 계단표에는 adjudicated_exclude 로 센다(06 6-6 15번).
  · keep_as_ambiguous→ 세트에서 빼서 따로 둔다(ambiguous). 점수에 넣지 않고 건수·참고 표로만 보인다.
- 재라벨에서 두 판정이 다르고 조정 기록이 없으면 human_disagreement (06 6-6 16번, 엄격·참고 모두 제외).
- 1차 세션 안에서 같은 채팅에 유효 반응이 둘 이상이거나 판 연결이 충돌이면 conflict_unresolved (06 6-6 7번).
- 출처(origins: chat_id → real·virtual·undeterminable)를 주면 origin_virtual·origin_undeterminable 를 붙인다. 주지 않으면
  '출처 미판정'(origin_unknown) 으로 표시만 한다(06 6-5).

활동 구간 (segment_timeline)
- 1차 구간표를 바탕으로 시각 조각(piece)을 만든다. 조각 상태:
  label(1차 그대로) · adjudicated(조정 값) · human_disagreement(재라벨과 다르고 조정 없음) · excluded · ambiguous.
- 조정 기록 하나가 덮는 시각 = 대상 구간들의 현재 범위의 교집합(불일치가 난 곳. adjudicate.segment_cases 의 건이
  두 구간 판이 함께 덮는 곳에서 생기므로 같다). final 이 시작·끝을 바꿨으면 그 범위와도 겹치는 곳만 바꾼다.
- 재라벨과 다른 조각이 완충(boundary_buffer) 이하로 짧으면 '경계 차이' 로 보고 label 로 둔다(06 5장: 완충 이하로
  어긋난 경계는 조정 목록에 넣지 않는다와 같은 뜻).

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 입력 dict 를 고치지 않는다(바꿔 끼운 레코드는 깊은 사본).
본문·닉네임·근거 한 줄은 결과의 숫자 요약(summary)에 넣지 않는다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import adjudicate as adj
from . import agreement as ag

# 보고서 전용 사유 (export-row 의 excluded_reasons 열거형 밖 — 보고서 계단표에만 쓴다)
R_ADJ_AMBIGUOUS = "adjudicated_ambiguous"         # 조정에서 '진짜 모호함으로 남김'
R_ADJ_DISAGREEMENT = "adjudicated_disagreement"   # 두 번 달랐고 조정 값으로 정함 → 엄격에서만 뺀다
REPORT_ONLY_REASONS = (R_ADJ_AMBIGUOUS, R_ADJ_DISAGREEMENT)

SRC_LABEL = "label"
SRC_ADJ = "adjudication"

# 조각 상태
ST_LABEL = "label"
ST_ADJUDICATED = "adjudicated"
ST_DISAGREE = "human_disagreement"
ST_EXCLUDED = "excluded"
ST_AMBIGUOUS = "ambiguous"
PIECE_STATUSES = (ST_LABEL, ST_ADJUDICATED, ST_DISAGREE, ST_EXCLUDED, ST_AMBIGUOUS)

_DECISION_STATUS = {adj.D_FINAL: ST_ADJUDICATED, adj.D_EXCLUDE: ST_EXCLUDED, adj.D_AMBIGUOUS: ST_AMBIGUOUS}


class FinalSetError(ValueError):
    """입력이 최종 세트를 만들 조건에 맞지 않음."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _body(rec: Mapping | None) -> Mapping:
    if not isinstance(rec, Mapping):
        return {}
    b = rec.get("body")
    return b if isinstance(b, Mapping) else {}


# ----------------------------------------------------------------------------- 반응(표본·발화 출발)
@dataclass(frozen=True)
class FinalReaction:
    """최종 세트의 반응 한 건.

    record: 이 건의 정답으로 쓰는 라벨 레코드(1차 라벨, 조정에서 고른 쪽 라벨, 또는 1차 레코드에 final 본문을 넣은 사본).
    base_record: 1차 세션의 원 라벨.
    reasons: 06 6-6 의 행 사유 가운데 이 모듈이 정하는 것(human_disagreement·conflict_unresolved·origin_*·adjudicated_exclude).
    report_reasons: 보고서 전용 사유(adjudicated_ambiguous·adjudicated_disagreement).
    """
    chat_id: str
    record: dict = field(compare=False, repr=False)
    base_record: dict = field(compare=False, repr=False)
    source: str = SRC_LABEL
    relabeled: bool = False
    agreed: bool | None = None
    values: tuple[tuple[str, str], ...] = ()        # ((session_id, 비교 값), …) 세션 ID 순
    decision: str | None = None
    adjudication_id: str | None = None
    stale: bool = False
    synthesized: bool = False                       # final 본문과 같은 라벨이 없어 1차 레코드에 본문만 넣었다
    reasons: tuple[str, ...] = ()
    report_reasons: tuple[str, ...] = ()


@dataclass
class FinalSet:
    primary_session_id: str
    relabel_session_ids: list[str]
    reactions: list[FinalReaction]            # 세트에 남는 것
    excluded: list[FinalReaction]             # exclude_from_eval
    ambiguous: list[FinalReaction]            # keep_as_ambiguous
    conflicts: list[dict]                     # [{chat_id, entry, sample_set_id}] 1차 충돌·중복 (라벨 값 없음)
    pieces: list[dict]                        # segment_timeline 결과
    segment_overrides: list[dict]
    origin_known: bool
    summary: dict

    def all_reactions(self) -> list[FinalReaction]:
        return sorted(self.reactions + self.excluded + self.ambiguous, key=lambda r: r.chat_id)


def _by_chat(reactions: Iterable[Mapping]) -> tuple[dict[str, Mapping], dict[str, list[Mapping]]]:
    """chat_id → 레코드, 그리고 같은 chat_id 가 둘 이상인 묶음."""
    seen: dict[str, list[Mapping]] = {}
    for r in reactions or ():
        cid = _body(r).get("chat_id")
        if isinstance(cid, str) and cid:
            seen.setdefault(cid, []).append(r)
    one = {c: rs[0] for c, rs in seen.items() if len(rs) == 1}
    dup = {c: rs for c, rs in seen.items() if len(rs) > 1}
    return one, dup


def _side_id(side: Mapping) -> str:
    sid = side.get("session_id")
    if not isinstance(sid, str) or not sid:
        raise FinalSetError("세션 ID 가 없습니다")
    return sid


def _same_body(a: Mapping, b: Mapping) -> bool:
    return dict(a) == dict(b)


def _sample_set_id(body: Mapping) -> str | None:
    s = body.get("sample_ref")
    return s.get("sample_set_id") if isinstance(s, Mapping) else None


def build_reactions(primary: Mapping, relabels: Sequence[Mapping] = (), adjudications: Iterable[Mapping] = (), *,
                    origins: Mapping[str, str] | None = None, conflict_chains: Iterable[Sequence[Mapping]] = ()) -> dict:
    """반응 최종 세트. side = {session_id, labeler_id, reactions[]} (현재 유효 reaction 레코드).

    conflict_chains: 1차 세션의 판 연결 충돌(resolve.Chain.versions) 가운데 reaction 인 것들. 채팅 하나를 가리키면
    conflicts 에 넣는다. 결과: {reactions, excluded, ambiguous, conflicts, summary}.
    """
    psid = _side_id(primary)
    plab = primary.get("labeler_id")
    pmap, pdup = _by_chat(primary.get("reactions") or ())
    rel = []
    for s in relabels or ():
        sid = _side_id(s)
        if sid == psid:
            raise FinalSetError("재라벨 세션이 1차 세션과 같습니다")
        m, _ = _by_chat(s.get("reactions") or ())
        rel.append((sid, s.get("labeler_id"), m))

    # 1) 1차와 재라벨에 모두 있는 채팅 → adjudicate.Case (조정 화면과 같은 열쇠)
    cases: list[adj.Case] = []
    for cid in sorted(pmap):
        sides = [adj._reaction_side(psid, plab, pmap[cid])]
        for sid, lab, m in rel:
            if cid in m:
                sides.append(adj._reaction_side(sid, lab, m[cid]))
        if len(sides) < 2:
            continue
        vals = {s.value for s in sides}
        kind = adj.C_GRADE if len(vals) > 1 else "agree"
        cases.append(adj.Case(f"reaction:{cid}", adj.TARGET_REACTION, kind, None,
                              tuple(sorted(sides, key=lambda s: s.session_id)), chat_id=cid))
    adj_list = [r for r in adjudications or () if _body(r).get("target_kind") == adj.TARGET_REACTION]
    states = adj.case_states(cases, adj_list)
    case_by_chat = {c.chat_id: c for c in cases}

    keep: list[FinalReaction] = []
    excluded: list[FinalReaction] = []
    ambiguous: list[FinalReaction] = []
    counts = {"stale": 0, "synthesized": 0, "human_disagreement": 0, "adjudicated_final": 0,
              "adjudicated_disagreement": 0}
    used_adj: set[str] = set()
    for cid in sorted(pmap):
        base = pmap[cid]
        reasons: list[str] = []
        report: list[str] = []
        origin = (origins or {}).get(cid) if origins is not None else None
        if origin == "virtual":
            reasons.append("origin_virtual")
        elif origin == "undeterminable":
            reasons.append("origin_undeterminable")
        case = case_by_chat.get(cid)
        if case is None:
            keep.append(FinalReaction(cid, copy.deepcopy(dict(base)), copy.deepcopy(dict(base)),
                                      values=((psid, ag.reaction_value(base)),), reasons=tuple(reasons)))
            continue
        values = tuple((s.session_id, s.value) for s in case.sides)
        agreed = len({v for _, v in values}) == 1
        st = states.get(case.case_id)
        rec = st.record if st is not None else None
        if rec is None:
            if not agreed:
                reasons.append("human_disagreement")
                counts["human_disagreement"] += 1
            keep.append(FinalReaction(cid, copy.deepcopy(dict(base)), copy.deepcopy(dict(base)), relabeled=True,
                                      agreed=agreed, values=values, reasons=tuple(reasons)))
            continue
        used_adj.add(rec.get("id"))
        b = _body(rec)
        dec = b.get("decision")
        stale = bool(st.stale)
        counts["stale"] += int(stale)
        common = dict(relabeled=True, agreed=agreed, values=values, decision=dec,
                      adjudication_id=rec.get("id") if isinstance(rec.get("id"), str) else None, stale=stale)
        if dec == adj.D_FINAL and isinstance(b.get("final"), Mapping):
            final = b["final"]
            pick = None
            for s in sorted(case.sides, key=lambda s: (s.session_id != psid, s.session_id)):
                src = pmap[cid] if s.session_id == psid else next(m[cid] for sid, _, m in rel if sid == s.session_id)
                if _same_body(_body(src), final):
                    pick = src
                    break
            synth = pick is None
            if synth:
                pick = dict(base)
                pick["body"] = copy.deepcopy(dict(final))
                counts["synthesized"] += 1
            if not agreed:
                report.append(R_ADJ_DISAGREEMENT)
                counts["adjudicated_disagreement"] += 1
            counts["adjudicated_final"] += 1
            keep.append(FinalReaction(cid, copy.deepcopy(dict(pick)), copy.deepcopy(dict(base)), source=SRC_ADJ,
                                      synthesized=synth, reasons=tuple(reasons), report_reasons=tuple(report),
                                      **common))
        elif dec == adj.D_EXCLUDE:
            excluded.append(FinalReaction(cid, copy.deepcopy(dict(base)), copy.deepcopy(dict(base)), source=SRC_ADJ,
                                          reasons=tuple(reasons + ["adjudicated_exclude"]), **common))
        elif dec == adj.D_AMBIGUOUS:
            ambiguous.append(FinalReaction(cid, copy.deepcopy(dict(base)), copy.deepcopy(dict(base)), source=SRC_ADJ,
                                           reasons=tuple(reasons), report_reasons=(R_ADJ_AMBIGUOUS,), **common))
        else:
            # 모양이 맞지 않는 조정 기록: 없는 것으로 본다(사람 불일치면 그대로 뺀다)
            if not agreed:
                reasons.append("human_disagreement")
                counts["human_disagreement"] += 1
            keep.append(FinalReaction(cid, copy.deepcopy(dict(base)), copy.deepcopy(dict(base)), relabeled=True,
                                      agreed=agreed, values=values, reasons=tuple(reasons)))

    conflicts: list[dict] = []
    for cid, recs in sorted(pdup.items()):
        last = recs[-1]
        conflicts.append({"chat_id": cid, "entry": _body(last).get("entry"),
                          "sample_set_id": _sample_set_id(_body(last)), "why": "duplicate_chat"})
    for chain in conflict_chains or ():
        vers = [v for v in chain if isinstance(v, Mapping) and v.get("kind") == "reaction"]
        cids = {_body(v).get("chat_id") for v in vers}
        if len(cids) != 1:
            continue
        cid = next(iter(cids))
        if not isinstance(cid, str) or cid in pmap or any(c["chat_id"] == cid for c in conflicts):
            continue
        last = vers[-1]
        conflicts.append({"chat_id": cid, "entry": _body(last).get("entry"),
                          "sample_set_id": _sample_set_id(_body(last)), "why": "chain_conflict"})
    conflicts.sort(key=lambda c: c["chat_id"])

    unused = sum(1 for r in adj_list if r.get("id") not in used_adj)
    summary = {
        "primary": len(pmap), "relabeled": len(cases), "agreed": sum(1 for c in cases if c.kind == "agree"),
        "kept": len(keep), "excluded": len(excluded), "ambiguous": len(ambiguous), "conflicts": len(conflicts),
        "adjudications": len(adj_list), "adjudications_unmatched": unused, **counts,
    }
    return {"reactions": keep, "excluded": excluded, "ambiguous": ambiguous, "conflicts": conflicts,
            "summary": summary}


# ----------------------------------------------------------------------------- 활동 구간
class _Segs:
    """세션 하나의 현재 유효 구간 ([시작, 끝), 분류 5종만)."""

    def __init__(self, segments: Iterable[Mapping]):
        out = []
        for r in segments or ():
            b = _body(r)
            s, e, c = b.get("start_ms"), b.get("end_ms"), b.get("category")
            if _is_int(s) and _is_int(e) and e > s and c in ag.CATEGORIES:
                out.append((s, e, r))
        out.sort(key=lambda x: (x[0], x[1]))
        self.segs = out
        self.by_entity = {(r.get("session_id"), r.get("entity_id")): r for _, _, r in out}

    def at(self, t: int) -> Mapping | None:
        for s, e, r in self.segs:
            if s <= t < e:
                return r
            if s > t:
                break
        return None

    def edges(self) -> set[int]:
        return {s for s, _, _ in self.segs} | {e for _, e, _ in self.segs}


def _in_scope(t: int, scope) -> bool:
    if scope is None:
        return True
    return any(int(a) <= t < int(b) for a, b in scope)


def segment_overrides(primary: Mapping, relabels: Sequence[Mapping], adjudications: Iterable[Mapping]) -> tuple[list[dict], dict]:
    """구간 조정 기록 → 덮어쓸 범위 목록. 같은 열쇠(adjudicate.target_key)면 seq 가 큰 기록 하나만.

    결과 항목: {start_ms, end_ms, decision, category, confidence, record, adjudication_id, stale}.
    record = 조각의 출처 레코드(고른 쪽 구간 라벨, 없으면 조정 기록에 final 본문을 넣은 사본).
    """
    lines = {_side_id(primary): _Segs(primary.get("segments") or ())}
    for s in relabels or ():
        lines[_side_id(s)] = _Segs(s.get("segments") or ())
    by_key: dict[tuple, Mapping] = {}
    for r in adjudications or ():
        b = _body(r)
        if b.get("target_kind") != adj.TARGET_SEGMENT:
            continue
        k = adj.target_key(adj.TARGET_SEGMENT, b.get("targets") or ())
        prev = by_key.get(k)
        if prev is None or (r.get("seq") or 0) >= (prev.get("seq") or 0):
            by_key[k] = r
    out: list[dict] = []
    unmatched = stale_n = 0
    for k in sorted(by_key, key=lambda k: sorted((str(a), str(b)) for a, b in k[1])):
        r = by_key[k]
        b = _body(r)
        found = []
        stale = False
        for t in b.get("targets") or ():
            if not isinstance(t, Mapping):
                continue
            line = lines.get(t.get("session_id"))
            cur = line.by_entity.get((t.get("session_id"), t.get("entity_id"))) if line else None
            if cur is None:
                continue
            found.append(cur)
            stale = stale or cur.get("id") != t.get("version_id")
        if len(found) < 2:
            unmatched += 1
            continue
        lo = max(_body(x)["start_ms"] for x in found)
        hi = min(_body(x)["end_ms"] for x in found)
        dec = b.get("decision")
        cat = conf = None
        src: dict = copy.deepcopy(dict(found[0]))
        if dec == adj.D_FINAL:
            final = b.get("final") if isinstance(b.get("final"), Mapping) else None
            if final is None or final.get("category") not in ag.CATEGORIES:
                unmatched += 1
                continue
            fs, fe = final.get("start_ms"), final.get("end_ms")
            if _is_int(fs) and _is_int(fe) and fe > fs:
                lo, hi = max(lo, fs), min(hi, fe)
            cat, conf = final.get("category"), final.get("confidence")
            same = next((x for x in found if _same_body(_body(x), final)), None)
            if same is not None:
                src = copy.deepcopy(dict(same))
            else:
                src = copy.deepcopy(dict(found[0]))
                src["body"] = copy.deepcopy(dict(final))
        elif dec not in (adj.D_EXCLUDE, adj.D_AMBIGUOUS):
            unmatched += 1
            continue
        if hi <= lo:
            unmatched += 1
            continue
        stale_n += int(stale)
        out.append({"start_ms": lo, "end_ms": hi, "decision": dec, "category": cat, "confidence": conf,
                    "record": src, "adjudication_id": r.get("id") if isinstance(r.get("id"), str) else None,
                    "stale": stale})
    out.sort(key=lambda o: (o["start_ms"], o["end_ms"]))
    return out, {"segment_adjudications": len(by_key), "unmatched": unmatched, "stale": stale_n}


def segment_timeline(primary: Mapping, relabels: Sequence[Mapping] = (), adjudications: Iterable[Mapping] = (), *,
                     buffer_ms: int = ag.DEFAULT_BUFFER_MS) -> tuple[list[dict], list[dict], dict]:
    """최종 구간 조각 (pieces, overrides, summary).

    piece = {start_ms, end_ms, category, confidence, status, record, relabeled, adjudication_id}.
    side 의 scope 는 그 세션이 라벨을 만들 수 있는 범위 [(시작, 끝)] (None = 전체). 재라벨은 자기 범위 안에서만 견준다.
    """
    P = _Segs(primary.get("segments") or ())
    R = [(_Segs(s.get("segments") or ()), s.get("scope")) for s in relabels or ()]
    overrides, osum = segment_overrides(primary, relabels, adjudications)
    edges = set(P.edges())
    for line, scope in R:
        edges |= line.edges()
        for a, b in scope or ():
            edges |= {int(a), int(b)}
    for o in overrides:
        edges |= {o["start_ms"], o["end_ms"]}
    cuts = sorted(edges)
    raw: list[dict] = []
    for p, q in zip(cuts, cuts[1:]):
        if q <= p:
            continue
        m = p + (q - p) // 2
        base = P.at(m)
        if base is None:
            continue
        bb = _body(base)
        piece = {"start_ms": p, "end_ms": q, "category": bb.get("category"), "confidence": bb.get("confidence"),
                 "status": ST_LABEL, "record": base, "relabeled": False, "adjudication_id": None}
        relabeled = False
        differ = False
        for line, scope in R:
            if not _in_scope(m, scope):
                continue
            other = line.at(m)
            if other is None:
                continue
            relabeled = True
            differ = differ or _body(other).get("category") != bb.get("category")
        piece["relabeled"] = relabeled
        ov = None
        for o in overrides:
            if o["start_ms"] <= m < o["end_ms"]:
                ov = o
        if ov is not None:
            piece["status"] = _DECISION_STATUS[ov["decision"]]
            piece["adjudication_id"] = ov["adjudication_id"]
            if ov["decision"] == adj.D_FINAL:
                piece.update(category=ov["category"], confidence=ov["confidence"], record=ov["record"])
        elif differ:
            piece["status"] = ST_DISAGREE
        raw.append(piece)
    pieces = _merge(raw)
    # 완충 이하로 짧은 불일치 조각 = 경계 차이 → 1차 값 그대로 (06 5장)
    for pc in pieces:
        if pc["status"] == ST_DISAGREE and pc["end_ms"] - pc["start_ms"] <= buffer_ms:
            pc["status"] = ST_LABEL
            pc["within_buffer_shift"] = True
    pieces = _merge(pieces)
    out = [{**pc, "record": copy.deepcopy(dict(pc["record"]))} for pc in pieces]
    secs = {s: 0 for s in PIECE_STATUSES}
    for pc in out:
        secs[pc["status"]] += pc["end_ms"] - pc["start_ms"]
    summary = {**osum, "pieces": len(out), "ms_by_status": secs}
    return out, overrides, summary


def _merge(pieces: Sequence[dict]) -> list[dict]:
    out: list[dict] = []
    for pc in pieces:
        if out:
            last = out[-1]
            if (last["end_ms"] == pc["start_ms"] and last["status"] == pc["status"]
                    and last["category"] == pc["category"] and last["confidence"] == pc["confidence"]
                    and last["record"] is pc["record"] and last["relabeled"] == pc["relabeled"]
                    and last["adjudication_id"] == pc["adjudication_id"]
                    and last.get("within_buffer_shift") == pc.get("within_buffer_shift")):
                last["end_ms"] = pc["end_ms"]
                continue
        out.append(dict(pc))
    return out


# ----------------------------------------------------------------------------- 묶음
def build_final_set(primary: Mapping, relabels: Sequence[Mapping] = (), adjudications: Iterable[Mapping] = (), *,
                    origins: Mapping[str, str] | None = None, conflict_chains: Iterable[Sequence[Mapping]] = (),
                    buffer_ms: int = ag.DEFAULT_BUFFER_MS) -> FinalSet:
    """1차 + 재라벨 + 조정 기록 → FinalSet. 같은 입력이면 같은 결과다.

    primary·relabels: {session_id, labeler_id, scope, segments[], reactions[]} — 각 세션의 현재 유효 레코드
    (resolve.EffectiveView.items(kind, session_id)). adjudications: 조정 세션의 현재 유효 adjudication 레코드.
    """
    adjs = list(adjudications or ())
    rx = build_reactions(primary, relabels, adjs, origins=origins, conflict_chains=conflict_chains)
    pieces, overrides, ssum = segment_timeline(primary, relabels, adjs, buffer_ms=buffer_ms)
    summary = {"reactions": rx["summary"], "segments": ssum, "origin_known": origins is not None}
    return FinalSet(_side_id(primary), [_side_id(s) for s in relabels or ()], rx["reactions"], rx["excluded"],
                    rx["ambiguous"], rx["conflicts"], pieces, overrides, origins is not None, summary)


def merge_origins(origin_values: Iterable[Mapping[str, str]]) -> dict[str, str]:
    """출처 판정 세션 여럿의 값 → chat_id 마다 하나. 세션끼리 다르면 'undeterminable'(판별 불가로 본다)."""
    out: dict[str, str] = {}
    seen: dict[str, set] = {}
    for m in origin_values or ():
        for cid, v in (m or {}).items():
            if v in ("real", "virtual", "undeterminable"):
                seen.setdefault(cid, set()).add(v)
    for cid, vs in seen.items():
        out[cid] = next(iter(vs)) if len(vs) == 1 else "undeterminable"
    return out


__all__ = [
    "FinalSetError", "FinalReaction", "FinalSet", "build_final_set", "build_reactions", "segment_timeline",
    "segment_overrides", "merge_origins", "R_ADJ_AMBIGUOUS", "R_ADJ_DISAGREEMENT", "REPORT_ONLY_REASONS",
    "SRC_LABEL", "SRC_ADJ", "ST_LABEL", "ST_ADJUDICATED", "ST_DISAGREE", "ST_EXCLUDED", "ST_AMBIGUOUS",
    "PIECE_STATUSES",
]
