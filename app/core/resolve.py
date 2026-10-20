"""현재 유효 라벨 계산 (docs/design/03-data-model.md 10장·11장, 05 S-10).

라벨 기록(labels/*.jsonl 의 줄 dict 들) → '지금 유효한 판' 뷰.

규칙 (03 10-1, 11장)
- 고치기 = 같은 entity_id 에 supersedes = 현재 판 id 인 새 줄. 지우기 = retract 줄. 되살리기 = retract 를 supersedes 하는 새 판.
- supersedes 는 **같은 세션 · 같은 entity_id · 같은 종류**(retract 제외)의 줄만 가리킬 수 있다.
  어기는 줄은 판 연결에서 빼고 problems 에 invalid_ref 로 남긴다. 그 줄을 가리키는 줄도 연달아 무효가 된다.
- 판 연결은 (session_id, entity_id) 마다 하나다. 뿌리 1개 · 순환 없음 · 갈라짐 없음이어야 머리를 정한다.
  - 한 판을 둘 이상이 supersedes 하거나 뿌리가 둘 이상 → fork. 자동으로 고르지 않는다(상태 conflict).
  - 순환 → cycle. 방문 집합과 깊이 상한(10,000)으로 찾아 멈춘다(상태 conflict).
- 머리가 retract 면 철회됨(retracted), 아니면 그 머리가 현재 유효 판(active).
- 순서는 벽시계가 아니라 (session_id, seq) 다. 같은 세션의 PC 두 대 파일은 seq 가 겹칠 수 있어 id 로 한 번 더 정렬한다.
  판 연결 자체는 supersedes 로만 정하므로 이 순서는 출력 순서에만 쓴다.
- 같은 id 가 다른 내용으로 두 번 → duplicate_id. 그 id 의 줄은 모두 빼고 그 대상은 conflict 로 둔다.
  내용까지 같으면(같은 파일을 두 번 넣음 등) 하나로 친다.

입력은 dict 의 묶음, 또는 app.jsonl.read 결과(ReadResult: .records·.truncated_tail·.corrupt_lines)를 그대로 받는다.
ReadResult 여럿을 담은 목록도 받는다. 잘린 마지막 줄·중간 깨진 줄은 problems 에 알림으로만 남긴다
(잘린 줄은 이미 jsonl.read 가 격리했으므로 나머지는 정상으로 계산한다).

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 입력 dict 를 고치지 않는다.
본문(body)·닉네임은 problems 에 넣지 않는다. id·종류·사유만 담는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

# 판 연결을 만드는 라벨 종류 (label-event.schema.json). 점이 있는 운영 기록은 연결에 들어가지 않는다.
LABEL_KINDS = frozenset({
    "segment", "utterance", "reaction", "continuation", "video_check",
    "missed_candidate", "origin", "adjudication", "retract",
})
RETRACT = "retract"
MAX_DEPTH = 10_000

# 상태
ACTIVE = "active"          # 머리가 일반 판 → 현재 유효
RETRACTED = "retracted"    # 머리가 retract
CONFLICT = "conflict"      # fork·cycle·duplicate_id·too_deep → 유효 판 없음, 사람이 조정
INVALID = "invalid"        # 이 줄 자체가 무효(판 연결에서 빠짐)
UNKNOWN = "unknown"        # 모르는 id

# 문제 코드
P_INVALID_REF = "invalid_ref"
P_CYCLE = "cycle"
P_FORK = "fork"
P_DUPLICATE_ID = "duplicate_id"
P_MALFORMED = "malformed"
P_TOO_DEEP = "too_deep"
P_TRUNCATED_TAIL = "truncated_tail"
P_CORRUPT_LINE = "corrupt_line"

# invalid_ref 의 사유
R_OTHER_SESSION = "other_session"
R_OTHER_ENTITY = "other_entity"
R_OTHER_KIND = "other_kind"
R_MISSING = "missing"                    # 가리킨 id 가 입력에 없음(다른 세션 파일·잘린 줄 등)
R_TARGET_INVALID = "target_invalid"      # 가리킨 줄이 무효라 같이 빠짐
R_RETRACT_OF_RETRACT = "retract_of_retract"
R_RETRACT_WITHOUT_TARGET = "retract_without_target"
R_SELF = "self"                          # 자기 자신을 supersedes


@dataclass(frozen=True)
class Problem:
    code: str
    session_id: str | None = None
    entity_id: str | None = None
    label_ids: tuple[str, ...] = ()
    reason: str | None = None
    detail: dict | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"code": self.code}
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.entity_id is not None:
            d["entity_id"] = self.entity_id
        if self.label_ids:
            d["label_ids"] = list(self.label_ids)
        if self.reason is not None:
            d["reason"] = self.reason
        if self.detail:
            d["detail"] = dict(self.detail)
        return d


@dataclass
class Chain:
    """(session_id, entity_id) 하나의 판 연결."""
    session_id: str
    entity_id: str
    kind: str | None                 # 대상 종류(retract 아닌 판의 kind). 판이 모두 retract 면 None
    status: str                      # ACTIVE · RETRACTED · CONFLICT
    versions: list[dict]             # 유효한 줄. 정상이면 뿌리→머리 순, conflict 면 (seq, id) 순
    head: dict | None = None         # ACTIVE·RETRACTED 일 때 머리 줄

    @property
    def effective(self) -> dict | None:
        return self.head if self.status == ACTIVE else None


def _order_key(rec: Mapping) -> tuple:
    seq = rec.get("seq")
    return (str(rec.get("session_id") or ""), seq if isinstance(seq, int) else 0, str(rec.get("id") or ""))


def _iter_input(records: Any, notes: list[Problem]) -> Iterator[dict]:
    """dict 묶음 · ReadResult · ReadResult 목록을 평평하게 편다."""
    if hasattr(records, "records") and not isinstance(records, Mapping):
        if getattr(records, "truncated_tail", False):
            notes.append(Problem(P_TRUNCATED_TAIL))
        for ln in getattr(records, "corrupt_lines", None) or ():
            notes.append(Problem(P_CORRUPT_LINE, detail={"line": int(ln)}))
        yield from _iter_input(records.records, notes)
        return
    if isinstance(records, Mapping):
        yield records  # type: ignore[misc]
        return
    for r in records:
        if isinstance(r, Mapping):
            yield r  # type: ignore[misc]
        elif hasattr(r, "records"):
            yield from _iter_input(r, notes)
        else:
            notes.append(Problem(P_MALFORMED, reason="not_object"))


def _malformed_reason(r: Mapping) -> str | None:
    for k in ("id", "session_id", "entity_id"):
        v = r.get(k)
        if not isinstance(v, str) or not v:
            return f"missing_{k}"
    if "supersedes" not in r:
        return "missing_supersedes"
    sup = r.get("supersedes")
    if sup is not None and (not isinstance(sup, str) or not sup):
        return "bad_supersedes"
    if r.get("kind") == RETRACT and sup is None:
        return R_RETRACT_WITHOUT_TARGET
    return None


class EffectiveView:
    """resolve() 결과. 입력 dict 는 고치지 않고 그대로 가리킨다(호출하는 쪽도 고치지 말 것)."""

    def __init__(self, chains: dict[tuple[str, str], Chain], line_chain: dict[str, tuple[str, str]],
                 invalid: dict[str, str], problems: list[Problem], other_records: list[dict],
                 known_ids: set[str]):
        self._chains = chains
        self._line_chain = line_chain          # 줄 id → 연결 키 (유효한 줄만)
        self._invalid = invalid                # 무효 줄 id → 사유 코드
        self._known = known_ids
        self.problems: list[Problem] = problems
        self.other_records: list[dict] = other_records   # session.open 같은 운영 기록 (seq 순)

    # ----------------------------------------------------------------- 판 id(label_id)로 묻기
    def chain_of(self, label_id: str) -> Chain | None:
        key = self._line_chain.get(label_id)
        return self._chains.get(key) if key else None

    def effective(self, label_id: str) -> dict | None:
        """label_id(어느 판이든)가 속한 대상의 현재 유효 판. 철회·충돌·무효·모름이면 None."""
        c = self.chain_of(label_id)
        return c.effective if c else None

    def history(self, label_id: str) -> list[dict]:
        """label_id 가 속한 대상의 모든 유효 판 (정상이면 뿌리→머리, 충돌이면 seq 순). 무효 줄은 빠진다."""
        c = self.chain_of(label_id)
        return list(c.versions) if c else []

    def status(self, label_id: str) -> str:
        if label_id in self._invalid:
            return INVALID
        c = self.chain_of(label_id)
        if c is not None:
            return c.status
        return INVALID if label_id in self._known else UNKNOWN

    def is_head(self, label_id: str) -> bool:
        """store 가 새 판을 쓸 때: supersedes 로 가리킬 수 있는 '현재 머리' 인가 (10-1)."""
        c = self.chain_of(label_id)
        return bool(c and c.head is not None and c.head.get("id") == label_id)

    # ----------------------------------------------------------------- 대상(entity_id)으로 묻기
    def chain(self, entity_id: str, session_id: str | None = None) -> Chain | None:
        """세션을 주지 않으면 그 entity_id 의 연결이 하나뿐일 때만 돌려준다."""
        if session_id is not None:
            return self._chains.get((session_id, entity_id))
        found = [c for (s, e), c in self._chains.items() if e == entity_id]
        return found[0] if len(found) == 1 else None

    def effective_entity(self, entity_id: str, session_id: str | None = None) -> dict | None:
        c = self.chain(entity_id, session_id)
        return c.effective if c else None

    # ----------------------------------------------------------------- 전체
    def chains(self) -> list[Chain]:
        return [self._chains[k] for k in sorted(self._chains)]

    def items(self, kind: str | None = None, session_id: str | None = None) -> list[dict]:
        """현재 유효 판 전부 ((session_id, seq, id) 순). kind·session_id 로 거를 수 있다."""
        out = [c.head for c in self._chains.values()
               if c.status == ACTIVE and c.head is not None
               and (kind is None or c.kind == kind) and (session_id is None or c.session_id == session_id)]
        return sorted(out, key=_order_key)

    def conflicts(self) -> list[Chain]:
        return [c for c in self.chains() if c.status == CONFLICT]

    @property
    def invalid_ids(self) -> dict[str, str]:
        return dict(self._invalid)

    def count_by_status(self) -> dict[str, int]:
        out = {ACTIVE: 0, RETRACTED: 0, CONFLICT: 0}
        for c in self._chains.values():
            out[c.status] = out.get(c.status, 0) + 1
        out[INVALID] = len(self._invalid)
        return out

    def to_dict(self) -> dict:
        """골든 비교용 요약(본문 없음). 순서가 정해져 있다."""
        return {
            "effective": {f"{c.session_id}/{c.entity_id}": (c.head or {}).get("id")
                          for c in self.chains() if c.status == ACTIVE},
            "status": {f"{c.session_id}/{c.entity_id}": c.status for c in self.chains()},
            "invalid": dict(sorted(self._invalid.items())),
            "problems": [p.to_dict() for p in self.problems],
        }


def resolve(records: Any) -> EffectiveView:
    """라벨 줄 묶음 → EffectiveView (02 3-3 계약). 같은 입력이면 같은 결과다(순서 무관)."""
    problems: list[Problem] = []
    notes: list[Problem] = []
    raw = list(_iter_input(records, notes))

    labels: list[Mapping] = []
    others: list[dict] = []
    for r in raw:
        if r.get("kind") in LABEL_KINDS:
            labels.append(r)
        elif isinstance(r.get("kind"), str) and "." in r["kind"]:
            others.append(r)  # type: ignore[arg-type]
        else:
            problems.append(Problem(P_MALFORMED, session_id=_s(r.get("session_id")),
                                    label_ids=_ids(r), reason="unknown_kind"))
    labels.sort(key=_order_key)
    others.sort(key=_order_key)

    invalid: dict[str, str] = {}
    known: set[str] = set()

    # 1) 모양 검사
    shaped: list[Mapping] = []
    for r in labels:
        if isinstance(r.get("id"), str):
            known.add(r["id"])
        why = _malformed_reason(r)
        if why:
            if why == R_RETRACT_WITHOUT_TARGET:
                problems.append(Problem(P_INVALID_REF, session_id=_s(r.get("session_id")),
                                        entity_id=_s(r.get("entity_id")), label_ids=_ids(r), reason=why))
            else:
                problems.append(Problem(P_MALFORMED, session_id=_s(r.get("session_id")),
                                        entity_id=_s(r.get("entity_id")), label_ids=_ids(r), reason=why))
            if isinstance(r.get("id"), str):
                invalid[r["id"]] = P_INVALID_REF if why == R_RETRACT_WITHOUT_TARGET else P_MALFORMED
            continue
        shaped.append(r)

    # 2) 같은 id: 내용이 같으면 하나로, 다르면 duplicate_id (모두 빼고 그 대상은 conflict)
    by_id: dict[str, Mapping] = {}
    dup_ids: set[str] = set()
    for r in shaped:
        i = r["id"]
        if i in by_id:
            if by_id[i] != r:
                dup_ids.add(i)
            continue
        by_id[i] = r
    forced_conflict: set[tuple[str, str]] = set()
    for i in sorted(dup_ids):
        r = by_id.pop(i)
        problems.append(Problem(P_DUPLICATE_ID, session_id=r["session_id"], entity_id=r["entity_id"], label_ids=(i,)))
        invalid[i] = P_DUPLICATE_ID
        forced_conflict.add((r["session_id"], r["entity_id"]))

    # 3) supersedes 검사 — 가리킨 줄이 무효면 이 줄도 무효(연달아). 위로 거슬러 올라가 한 번씩만 정한다.
    def base_kind(rec: Mapping) -> str | None:
        """retract 를 건너 거슬러 올라가 대상 종류를 찾는다. 순환·끊김이면 None."""
        seen = set()
        cur: Mapping | None = rec
        while cur is not None and cur["id"] not in seen:
            if cur["kind"] != RETRACT:
                return cur["kind"]
            seen.add(cur["id"])
            cur = by_id.get(cur["supersedes"]) if cur["supersedes"] else None
        return None

    def check_basic(rec: Mapping) -> str | None:
        sup = rec["supersedes"]
        if sup is None:
            return None
        if sup == rec["id"]:
            return R_SELF
        tgt = by_id.get(sup)
        if tgt is None:
            return R_TARGET_INVALID if sup in invalid else R_MISSING
        if tgt["session_id"] != rec["session_id"]:
            return R_OTHER_SESSION
        if tgt["entity_id"] != rec["entity_id"]:
            return R_OTHER_ENTITY
        if rec["kind"] == RETRACT:
            if tgt["kind"] == RETRACT:
                return R_RETRACT_OF_RETRACT
        else:
            tk = tgt["kind"] if tgt["kind"] != RETRACT else base_kind(tgt)
            if tk is not None and tk != rec["kind"]:
                return R_OTHER_KIND
        return None

    decided: dict[str, str | None] = {}     # id → None(유효) 또는 무효 사유
    for i in sorted(by_id, key=lambda k: _order_key(by_id[k])):
        path: list[str] = []
        onpath: set[str] = set()
        cur = i
        top: str | None = None
        while True:
            if cur in decided:
                top = decided[cur]
                break
            if cur in onpath:          # 순환: 여기서는 유효로 두고 4) 에서 cycle 로 잡는다
                top = None
                break
            rec = by_id[cur]
            why = check_basic(rec)
            if why:
                decided[cur] = why
                top = why
                break
            path.append(cur)
            onpath.add(cur)
            if rec["supersedes"] is None:
                top = None
                break
            cur = rec["supersedes"]
        for node in reversed(path):
            if top is not None:
                top = R_TARGET_INVALID
            decided[node] = top

    valid: dict[str, Mapping] = {i: by_id[i] for i, why in decided.items() if why is None}
    for i in sorted((i for i, why in decided.items() if why), key=lambda k: _order_key(by_id[k])):
        r = by_id[i]
        invalid[i] = P_INVALID_REF
        problems.append(Problem(P_INVALID_REF, session_id=r["session_id"], entity_id=r["entity_id"],
                                label_ids=(i,), reason=decided[i],
                                detail={"supersedes": r["supersedes"]}))

    # 4) (session_id, entity_id) 로 묶어 판 연결
    groups: dict[tuple[str, str], list[Mapping]] = {}
    for r in valid.values():
        groups.setdefault((r["session_id"], r["entity_id"]), []).append(r)
    for key in forced_conflict:
        groups.setdefault(key, [])

    chains: dict[tuple[str, str], Chain] = {}
    line_chain: dict[str, tuple[str, str]] = {}
    for key in sorted(groups):
        recs = sorted(groups[key], key=_order_key)
        sid, eid = key
        kinds = sorted({r["kind"] for r in recs if r["kind"] != RETRACT})
        ekind = kinds[0] if len(kinds) == 1 else None
        for r in recs:
            line_chain[r["id"]] = key
        chain_problems: list[Problem] = []

        roots = [r for r in recs if r["supersedes"] is None]
        children: dict[str, list[Mapping]] = {}
        for r in recs:
            if r["supersedes"] is not None:
                children.setdefault(r["supersedes"], []).append(r)

        if len(roots) > 1:
            chain_problems.append(Problem(P_FORK, session_id=sid, entity_id=eid,
                                          label_ids=tuple(r["id"] for r in roots), reason="multiple_roots"))
        for parent in sorted(children, key=lambda p: _order_key(valid[p]) if p in valid else ("", 0, p)):
            kids = children[parent]
            if len(kids) > 1:
                chain_problems.append(Problem(P_FORK, session_id=sid, entity_id=eid,
                                              label_ids=tuple(k["id"] for k in sorted(kids, key=_order_key)),
                                              reason="same_parent", detail={"supersedes": parent}))

        # 뿌리에서 닿지 않는 줄 = 순환 위에 있거나 순환에 매달린 줄
        reach: set[str] = set()
        stack = [r["id"] for r in roots]
        steps = 0
        while stack and steps <= MAX_DEPTH * 2:
            steps += 1
            cur = stack.pop()
            if cur in reach:
                continue
            reach.add(cur)
            stack.extend(k["id"] for k in children.get(cur, ()))
        unreached = [r for r in recs if r["id"] not in reach]
        if unreached:
            cyc = _find_cycle(unreached[0], valid)
            chain_problems.append(Problem(P_CYCLE, session_id=sid, entity_id=eid,
                                          label_ids=tuple(cyc) if cyc else tuple(r["id"] for r in unreached)))

        head: Mapping | None = None
        ordered: list[Mapping] = recs
        if not chain_problems and key not in forced_conflict and recs:
            # 정상: 뿌리 하나에서 자식 하나씩 따라간다
            path = [roots[0]]
            while len(path) <= MAX_DEPTH:
                kids = children.get(path[-1]["id"])
                if not kids:
                    break
                path.append(kids[0])
            if len(path) > MAX_DEPTH:
                chain_problems.append(Problem(P_TOO_DEEP, session_id=sid, entity_id=eid,
                                              detail={"limit": MAX_DEPTH}))
            else:
                ordered = path
                head = path[-1]

        problems.extend(chain_problems)
        if head is None:
            status = CONFLICT
        elif head["kind"] == RETRACT:
            status = RETRACTED
        else:
            status = ACTIVE
        chains[key] = Chain(sid, eid, ekind, status, list(ordered), head)

    problems.extend(notes)
    problems.sort(key=_problem_order)
    return EffectiveView(chains, line_chain, invalid, problems, others, known)


def _find_cycle(start: Mapping, valid: Mapping[str, Mapping]) -> list[str]:
    """start 에서 supersedes 를 따라가 만나는 순환의 id 목록(가장 앞 seq 부터)."""
    seen: dict[str, int] = {}
    path: list[str] = []
    cur: Mapping | None = start
    for _ in range(MAX_DEPTH):
        if cur is None:
            return []
        i = cur["id"]
        if i in seen:
            cyc = path[seen[i]:]
            k = min(range(len(cyc)), key=lambda n: _order_key(valid[cyc[n]]))
            return cyc[k:] + cyc[:k]
        seen[i] = len(path)
        path.append(i)
        sup = cur["supersedes"]
        cur = valid.get(sup) if sup else None
    return []


_PROBLEM_RANK = {P_TRUNCATED_TAIL: 0, P_CORRUPT_LINE: 1, P_MALFORMED: 2, P_DUPLICATE_ID: 3,
                 P_INVALID_REF: 4, P_FORK: 5, P_CYCLE: 6, P_TOO_DEEP: 7}


def _problem_order(p: Problem) -> tuple:
    return (_PROBLEM_RANK.get(p.code, 99), p.session_id or "", p.entity_id or "", p.label_ids,
            p.reason or "", (p.detail or {}).get("line", 0))


def _s(v) -> str | None:
    return v if isinstance(v, str) else None


def _ids(r: Mapping) -> tuple[str, ...]:
    return (r["id"],) if isinstance(r.get("id"), str) else ()


__all__ = [
    "resolve", "EffectiveView", "Chain", "Problem", "LABEL_KINDS", "MAX_DEPTH",
    "ACTIVE", "RETRACTED", "CONFLICT", "INVALID", "UNKNOWN",
    "P_INVALID_REF", "P_CYCLE", "P_FORK", "P_DUPLICATE_ID", "P_MALFORMED", "P_TOO_DEEP",
    "P_TRUNCATED_TAIL", "P_CORRUPT_LINE",
]
