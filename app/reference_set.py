"""훈련 기준 '원래 반응' 읽기와 훈련 메뉴 켜기 판단 (08 D-18 6·7, 18 6-2·6-5).

기준 (D-18 7)
  (가) 그 프로젝트의 **사람 최종 세트**(app.export.build_final_set, 세션 밖 읽기 — 잠긴 세션만). 원래 반응 =
       set=final · reaction · graded · 등급 direct/likely · eval.lenient(참고 점수에 들어가는 행) (18 6-5 1).
       원래 반응 시각 = reaction_time_ms, 없으면 연결 발화의 시작. 훈련 범위 = 최종 세트 구간 조각(set=final)을 이은 것.
  (나) 최종 세트가 없으면 `labels\\manual-*.csv` 손라벨을 **읽기만** 한다. 머리줄 chat_id,chat_vod_ms,utterance_vod_ms,grade
       (그 밖의 열 — note 등 — 은 읽지 않고 버린다). 원래 반응 시각 = utterance_vod_ms. 훈련 범위 = 처음 채팅 ~ 마지막
       채팅·대답 시각 + 1ms. 파일이 여럿이면 이름 순 첫 파일.
  엔진 판정은 쓰지 않는다(정답이 아님).

시범 프로젝트 (D-18 7)
  - 판별(is_pilot_project): 폴더 이름이 `pilot-*` 이거나, 시범 기록(<데이터>\\pilot\\freeze.json 의 projects[] ·
    setup.json 의 items[])에 그 프로젝트(project_id 또는 폴더)가 적혀 있으면 시범이다.
  - 시범 세션 모두 잠김(pilot_sessions_locked): 06 7-1 봉인 해제 조건(app.seal.unseal_conditions)과 같은 판단 —
    1차·재라벨 세션이 모두 잠김, 1차가 있음, 1차마다 재라벨 계획, 계획마다 잠긴 재라벨 세션, 표본 재현 검증.
    다만 조건을 채운 해제 기록(seal.jsonl 의 ai.unsealed · conditions_met = true, allow_no_relabel 해제 포함)이 있으면 잠긴
    것으로 본다(08 D-27 3 — 앞당겨 연 기록 conditions_met = false 는 아님).
    seal.jsonl·잠금·계획·표본 매니페스트만 열고 **라벨 파일은 열지 않는다**. 판단이 안 되면(오류) 잠기지 않은 것으로 본다.
  - 시범인데 모두 잠기지 않았으면 훈련 메뉴를 끈다. 이때는 최종 세트도 손라벨도 **열지 않는다**.

자리(reader_kind, 24 5-2 의 9)
  - 공개 함수는 reader_kind(부르는 세션 종류, app.core.visibility)를 받는다. 기본 None = 세션 밖(지금까지와 같다).
  - 라벨링(1차·재라벨)·출처 판정 세션, 모르는 종류(= 봉인 AI 결과 ai_sealed 를 열 수 없는 자리, 03 12-1)는 **아무것도 열기
    전에** 막는다: 최종 세트를 만들면 다른 세션 라벨과 seal.jsonl(노출 계산의 해제 기록)을 읽게 되고, 시범 판단도 seal.jsonl
    (해제 기록)·잠금·계획을 읽기 때문이다. 이때 training_availability·load_reference 는 꺼짐(REASON_SEAT), pilot_block_reason 은 REASON_SEAT,
    pilot_sessions_locked 는 (False, [REASON_SEAT]), load_final_set_reference 는 (None, 'seat_blind').
    is_pilot_project 는 이름·시범 기록(freeze.json·setup.json)만 보므로 막지 않는다.
  - 조정·분석 세션은 막지 않는다(지금까지와 같은 길로 읽는다).

본문·닉네임은 읽지 않거나 버린다(chat_id·시각·등급만, D-17 8). 이 모듈은 파일을 쓰지 않는다.
"""
from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from typing import Iterable

from .core import training as tr
from .core.ids import CHAT_ID_RE

MANUAL_PREFIX = "manual-"
MANUAL_SUFFIX = ".csv"
MANUAL_REQUIRED = ("chat_id", "chat_vod_ms", "utterance_vod_ms", "grade")
MANUAL_MAX_BYTES = 4 * 1024 * 1024
PILOT_PREFIX = "pilot-"
PILOT_META_MAX_BYTES = 4 * 1024 * 1024

REASON_NO_REFERENCE = "라벨이 잠긴 구간이 없습니다"
REASON_PILOT_LOCKED = "시범 세션이 모두 잠기기 전에는 훈련을 열지 않습니다 (D-18 7)"
REASON_NO_PROJECT = "프로젝트를 열지 못했습니다"
REASON_SEAT = "이 세션에서는 훈련 기준(다른 세션 라벨·봉인 기록)을 열지 않습니다 (06 7-3)"
SEAT_BLIND = "seat_blind"


def seat_blind(reader_kind: str | None) -> bool:
    """이 자리가 훈련 기준 읽기를 막아야 하는가. None(세션 밖)은 아니다. 봉인 AI 결과(ai_sealed)를 열 수 없는 자리 —
    라벨링(1차·재라벨)·출처 판정·모르는 종류 — 는 막는다(03 12-1). 파일을 열지 않는다."""
    if reader_kind is None:
        return False
    from .core import visibility as vis
    return vis.files_to_open(reader_kind).access("ai_sealed") == vis.NONE


@dataclass
class ReferenceSet:
    """훈련 기준 '원래 반응'. reactions 는 chat_id 하나에 한 건(core.training.normalize_reference)."""
    source: str                                   # final_set | manual_csv
    reactions: list[tr.RefReaction]
    ranges: list[tuple[int, int]]                 # 훈련 범위 [(시작, 끝)] 시작 순
    sha256: str
    file_name: str | None = None                  # manual_csv 일 때 파일 이름(폴더 없이)
    counts: dict = field(default_factory=dict)    # 숫자만: rows·used·skipped_*·duplicates
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.reactions)

    @property
    def first_range(self) -> tuple[int, int] | None:
        return self.ranges[0] if self.ranges else None

    def log_reference(self) -> dict:
        """훈련 기록 training.start 의 reference 칸."""
        return {"source": self.source, "sha256": self.sha256, "count": self.count, "file": self.file_name}


@dataclass
class Availability:
    enabled: bool
    reason: str | None                    # 꺼졌을 때 보일 한 줄
    pilot: bool = False
    reference: ReferenceSet | None = None


# ----------------------------------------------------------------------------- 시범 판별
def _root(project_dir: str) -> str:
    root = os.path.abspath(project_dir)
    if os.path.basename(root).lower() == "project.json":
        root = os.path.dirname(root)
    return root


def default_pilot_dir(project_dir: str) -> str:
    """<projects 폴더의 부모>\\pilot (tools/pilot_setup·pilot_freeze 의 <데이터>\\pilot 과 같은 자리)."""
    return os.path.join(os.path.dirname(os.path.dirname(_root(project_dir))), "pilot")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, "rb") as f:
            raw = f.read(PILOT_META_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > PILOT_META_MAX_BYTES:
        return None
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _project_id(root: str) -> str | None:
    doc = _read_json(os.path.join(root, "project.json"))
    pid = doc.get("project_id") if doc else None
    return pid if isinstance(pid, str) else None


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _listed(entries: Iterable, root: str, pid: str | None) -> bool:
    name = os.path.basename(root).lower()
    for e in entries or ():
        if isinstance(e, str):
            if e.lower() == name or (pid and e == pid) or _same_dir(e, root):
                return True
        elif isinstance(e, dict):
            if pid and e.get("project_id") == pid:
                return True
            for k in ("project_root", "path"):
                v = e.get(k)
                if isinstance(v, str) and _same_dir(v, root):
                    return True
            for k in ("slug", "name"):
                v = e.get(k)
                if isinstance(v, str) and v.lower() == name:
                    return True
    return False


def is_pilot_project(project_dir: str, *, pilot_dir: str | None = None) -> bool:
    """시범 프로젝트인가. 폴더 이름 pilot-* 이거나 시범 기록(freeze.json projects[] · setup.json items[])에 적힘."""
    root = _root(project_dir)
    if os.path.basename(root).lower().startswith(PILOT_PREFIX):
        return True
    pdir = pilot_dir if pilot_dir is not None else default_pilot_dir(root)
    pid = _project_id(root)
    freeze = _read_json(os.path.join(pdir, "freeze.json"))
    if freeze and _listed(freeze.get("projects") or (), root, pid):
        return True
    setup = _read_json(os.path.join(pdir, "setup.json"))
    if setup and _listed(setup.get("items") or (), root, pid):
        return True
    return False


def pilot_sessions_locked(project_dir: str, *, reader_kind: str | None = None) -> tuple[bool, list[str]]:
    """시범 세션이 모두 잠겼나 — (잠김, 아닌 까닭 목록). 조건을 채운 해제 기록(seal.conditions_met_unseal_exists — ai.unsealed
    · conditions_met = true, 플래그 해제 allow_no_relabel 포함)이 있으면 (True, []) (08 D-27 3), 없으면 seal.unseal_conditions
    (06 7-1)와 같은 판단. seal.jsonl·잠금·계획·표본 매니페스트만 연다. 오류면 (False, [까닭]). 막는 자리(seat_blind)면 열기
    전에 (False, [REASON_SEAT])."""
    if seat_blind(reader_kind):
        return False, [REASON_SEAT]
    from . import seal
    root = _root(project_dir)
    try:
        if seal.conditions_met_unseal_exists(root):
            return True, []
        cond = seal.unseal_conditions(root)
    except Exception as e:  # noqa: BLE001 — 판단이 안 되면 잠기지 않은 것으로 (블라인드 쪽으로 안전하게)
        return False, [f"잠금 상태를 확인하지 못했습니다 ({type(e).__name__})"]
    return bool(cond.met), list(cond.reasons)


def pilot_block_reason(project_dir: str, *, pilot_dir: str | None = None,
                       reader_kind: str | None = None) -> str | None:
    """시범 프로젝트이고 시범 세션이 모두 잠기지 않았으면 그 까닭 한 줄. 아니면 None.
    막는 자리(seat_blind)면 시범이든 아니든 아무것도 열기 전에 REASON_SEAT."""
    if seat_blind(reader_kind):
        return REASON_SEAT
    if not is_pilot_project(project_dir, pilot_dir=pilot_dir):
        return None
    ok, _ = pilot_sessions_locked(project_dir)
    return None if ok else REASON_PILOT_LOCKED


# ----------------------------------------------------------------------------- (가) 사람 최종 세트
def _merge_ranges(rs: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(rs):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def reference_from_final_export(fx) -> ReferenceSet | None:
    """export.build_final_set 결과(FinalSetExport 또는 {items, segment_items} dict) → ReferenceSet. 원래 반응이 0 이면 None."""
    items = fx.get("items") if isinstance(fx, dict) else getattr(fx, "items", None)
    seg_items = fx.get("segment_items") if isinstance(fx, dict) else getattr(fx, "segment_items", None)
    refs: list[tr.RefReaction] = []
    counts = {"rows": 0, "used": 0, "skipped_grade": 0, "skipped_eval": 0, "skipped_shape": 0}
    for it in items or ():
        if not isinstance(it, dict) or it.get("set") != "final":
            continue
        row = it.get("row") or {}
        if row.get("label_kind") != "reaction":
            continue
        counts["rows"] += 1
        if row.get("result") != "graded" or row.get("grade") not in tr.REFERENCE_GRADES:
            counts["skipped_grade"] += 1
            continue
        if not (row.get("eval") or {}).get("lenient"):
            counts["skipped_eval"] += 1
            continue
        cid = row.get("chat_id")
        if not isinstance(cid, str) or not CHAT_ID_RE.match(cid):
            counts["skipped_shape"] += 1
            continue
        ct = row.get("chat_t_ms") if _is_int(row.get("chat_t_ms")) else None
        rt = row.get("reaction_time_ms") if _is_int(row.get("reaction_time_ms")) else None
        if rt is None:
            linked = row.get("linked_utterance_id")
            u = next((u for u in row.get("utterances") or () if isinstance(u, dict)
                      and u.get("utterance_id") == linked), None) if linked else None
            if u is not None and _is_int(u.get("start_ms")):
                rt = u["start_ms"]
        refs.append(tr.RefReaction(cid, ct, rt, row.get("grade")))
    if not refs:
        return None
    norm, dup = tr.normalize_reference(refs)
    counts["used"] = len(norm)
    counts["duplicates"] = dup
    ranges = []
    for s in seg_items or ():
        if not isinstance(s, dict) or s.get("set") != "final":
            continue
        pc = s.get("piece") or {}
        a, b = pc.get("start_ms"), pc.get("end_ms")
        if _is_int(a) and _is_int(b) and b > a:
            ranges.append((a, b))
    ranges = _merge_ranges(ranges)
    warns = []
    if not ranges:
        ranges = _span_of(norm)
        warns.append("최종 세트에 구간 조각이 없어 처음·마지막 반응으로 범위를 잡았습니다")
    return ReferenceSet(tr.REF_FINAL_SET, norm, ranges, tr.reference_sha256(tr.REF_FINAL_SET, norm),
                        None, counts, warns)


def _span_of(refs: Iterable[tr.RefReaction]) -> list[tuple[int, int]]:
    ts = [t for r in refs for t in (r.chat_vod_ms, r.reaction_vod_ms) if _is_int(t)]
    cs = [r.chat_vod_ms for r in refs if _is_int(r.chat_vod_ms)]
    if not cs:
        return []
    return [(min(cs), max(ts) + 1)]


def load_final_set_reference(project_dir: str, *,
                             reader_kind: str | None = None) -> tuple[ReferenceSet | None, str | None]:
    """(가) 사람 최종 세트. (기준 또는 None, 없을 때 까닭 이름). 잠긴 세션만 읽는다(export.build_final_set 규칙).
    막는 자리(seat_blind)면 열기 전에 (None, SEAT_BLIND) — 최종 세트는 다른 세션 라벨과 seal.jsonl 을 읽는다."""
    if seat_blind(reader_kind):
        return None, SEAT_BLIND
    from . import export
    try:
        fx = export.build_final_set(_root(project_dir))
    except Exception as e:  # noqa: BLE001 — 잠기지 않음·1차 없음·형식 오류 모두 '최종 세트 없음'
        return None, type(e).__name__
    ref = reference_from_final_export(fx)
    return ref, (None if ref is not None else "empty")


# ----------------------------------------------------------------------------- (나) 손라벨 CSV
def manual_csv_files(project_dir: str) -> list[str]:
    """labels\\manual-*.csv 이름 순 (폴더 없이 이름만)."""
    d = os.path.join(_root(project_dir), "labels")
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(n for n in names if n.startswith(MANUAL_PREFIX) and n.lower().endswith(MANUAL_SUFFIX)
                  and os.path.isfile(os.path.join(d, n)))


def _int_cell(v) -> int | None:
    v = (v or "").strip()
    if not v or not (v.isdigit() and v.isascii()):
        return None
    return int(v)


def read_manual_csv(path: str) -> ReferenceSet | None:
    """손라벨 CSV 하나를 읽는다(읽기만). 필요한 열만 쓰고 나머지 열(note 등)은 버린다. 원래 반응이 0 이면 None.
    머리줄에 필요한 열이 없으면 ValueError."""
    with open(path, "rb") as f:
        raw = f.read(MANUAL_MAX_BYTES + 1)
    if len(raw) > MANUAL_MAX_BYTES:
        raise ValueError("손라벨 파일이 너무 큽니다")
    text = raw.decode("utf-8-sig")
    rd = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip() for h in (rd.fieldnames or [])]
    missing = [c for c in MANUAL_REQUIRED if c not in header]
    if missing:
        raise ValueError("손라벨 머리줄에 열이 없습니다: " + ", ".join(missing))
    refs: list[tr.RefReaction] = []
    counts = {"rows": 0, "used": 0, "skipped_grade": 0, "skipped_shape": 0}
    for row in rd:
        row = {(k or "").strip(): v for k, v in row.items() if k is not None}
        counts["rows"] += 1
        grade = (row.get("grade") or "").strip()
        if grade not in tr.REFERENCE_GRADES:
            counts["skipped_grade"] += 1
            continue
        cid = (row.get("chat_id") or "").strip()
        ct = _int_cell(row.get("chat_vod_ms"))
        if not CHAT_ID_RE.match(cid) or ct is None:
            counts["skipped_shape"] += 1
            continue
        refs.append(tr.RefReaction(cid, ct, _int_cell(row.get("utterance_vod_ms")), grade))
    if not refs:
        return None
    norm, dup = tr.normalize_reference(refs)
    counts["used"] = len(norm)
    counts["duplicates"] = dup
    name = os.path.basename(path)
    return ReferenceSet(tr.REF_MANUAL_CSV, norm, _span_of(norm), tr.reference_sha256(tr.REF_MANUAL_CSV, norm),
                        name, counts, [])


def load_manual_reference(project_dir: str) -> ReferenceSet | None:
    """(나) labels\\manual-*.csv — 이름 순으로 읽을 수 있는 첫 파일."""
    d = os.path.join(_root(project_dir), "labels")
    for n in manual_csv_files(project_dir):
        try:
            ref = read_manual_csv(os.path.join(d, n))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if ref is not None:
            return ref
    return None


# ----------------------------------------------------------------------------- 묶음
def load_reference(project_dir: str, *, pilot_dir: str | None = None,
                   reader_kind: str | None = None) -> tuple[ReferenceSet | None, str | None]:
    """훈련 기준. (기준, None) 또는 (None, 꺼진 까닭 한 줄). 시범이고 모두 잠기지 않았으면 아무 라벨도 열지 않는다.
    막는 자리(seat_blind — 라벨링·출처 판정 세션)면 아무것도 열기 전에 (None, REASON_SEAT)."""
    if seat_blind(reader_kind):
        return None, REASON_SEAT
    root = _root(project_dir)
    if not os.path.isfile(os.path.join(root, "project.json")):
        return None, REASON_NO_PROJECT
    why = pilot_block_reason(root, pilot_dir=pilot_dir)
    if why:
        return None, why
    ref, _ = load_final_set_reference(root)
    if ref is None:
        ref = load_manual_reference(root)
    if ref is None or not ref.ranges:
        return None, REASON_NO_REFERENCE
    return ref, None


def training_availability(project_dir: str, *, pilot_dir: str | None = None,
                          reader_kind: str | None = None) -> Availability:
    """메뉴 '훈련 → 스트리머 훈련' 을 켤지. 꺼지면 까닭 한 줄(18 6-2).
    막는 자리(seat_blind)면 project.json·시범 기록·라벨·seal.jsonl 을 하나도 열지 않고 꺼짐(REASON_SEAT)."""
    if seat_blind(reader_kind):
        return Availability(False, REASON_SEAT, False, None)
    root = _root(project_dir)
    pilot = os.path.isfile(os.path.join(root, "project.json")) and is_pilot_project(root, pilot_dir=pilot_dir)
    ref, why = load_reference(root, pilot_dir=pilot_dir)
    return Availability(ref is not None, why, pilot, ref)


__all__ = [
    "ReferenceSet", "Availability", "is_pilot_project", "pilot_sessions_locked", "pilot_block_reason",
    "default_pilot_dir", "reference_from_final_export", "load_final_set_reference", "manual_csv_files",
    "read_manual_csv", "load_manual_reference", "load_reference", "training_availability",
    "REASON_NO_REFERENCE", "REASON_PILOT_LOCKED", "REASON_NO_PROJECT", "MANUAL_REQUIRED",
    "REASON_SEAT", "SEAT_BLIND", "seat_blind",
]
