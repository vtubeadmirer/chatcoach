"""세션 잠금 — locks/<session_id>.json 만들기·읽기·검증, 잠그기 화면 요약 (docs/design/03-data-model.md 6-14·8-4·10-4,
06 7-1·7-2·7-3, 08 D-11 7).

입출력 층이다. Qt 를 import 하지 않는다. 잠금 파일은 **store 를 거쳐서만** 쓴다(ProjectStore.lock_session 이
write_lock_exclusive 를 부른다). 이 모듈의 쓰기 함수를 UI 가 직접 부르지 않는다.

잠금이 하는 일
- 세션 하나를 '이 판으로 비교했다' 고 굳힌다. 파일은 한 번만 만든다(이미 있으면 AlreadyLockedError, 덮어쓰지 않는다).
  임시 파일에 다 쓰고 fsync → 하드 링크(없으면 Windows rename)로 '없을 때만' 바꿔 넣는다. 반쯤 쓴 잠금 파일은 생기지 않는다.
- 풀기 기능은 없다(08 D-11 7). 고치려면 새 세션을 연다. 잘못 잠갔으면 다음 세션의 사유 칸에 적는다.
- 내용: session_id·labeler_id·session_kind·잠근 시각·잠근 PC·앱 버전·기준서 버전과 해시·기록 파일별 sha256·크기·줄 수·
  last_seq·라벨 종류별 유효 수·표본 매니페스트 id 와 해시·요약 숫자와 경고 코드(schemas/session-lock.schema.json).
  재라벨 세션은 자기 표본 chat_id 목록과 그 sha256(current_sample, 선택 칸)도 넣는다(14 4-2 의 5번, 08 D-13 2).

검증 (check_files · verify_lock)
- 잠금에 적힌 파일마다 지금 바이트의 sha256 을 비교한다. 다르면 앞 size 바이트의 sha256 을 다시 본다.
  같으면 '잠금 후 추가'(appended, 잠금 뒤 줄이 붙음 → 그 줄들은 뷰에서 뺀다), 다르면 '변조/손상'(tampered).
  잠금에 있는데 없는 파일도 '변조/손상'. 잠금에 없는 같은 세션의 파일은 '잠금 후 생긴 파일'(extra, 뷰에서 뺀다).

블라인드 (06 7-3, 03 12-1)
- 다른 세션의 잠금 파일 내용·기록 파일은 files_to_open(읽는 세션 종류).may_read_other_session(잠김, 그 세션 종류) 가
  허락할 때만 연다. 막히면 디스크에서 열기 전에 LockBlindError. 있는지 없는지(is_locked)는 이름만 본다.

요약 (lock_summary) 은 순수 계산이다(파일·시계 없음). 잠그기 화면(app/ui/lock_dialog.py)이 보이고, 잠금 파일의
summary 칸에도 같은 숫자를 남긴다. 본문·닉네임은 요약·로그·예외 메시지에 넣지 않는다.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .core import phase as ph
from .core import sampling as sp
from .core import visibility as vis
from .core.ids import SESSION_FILE_RE, SESSION_ID_RE
from .core.validate import StrictJsonError, load_json_strict

log = logging.getLogger(__name__)

LOCK_SCHEMA = "chatcoach/session-lock"
LOCK_SCHEMA_VERSION = 1
LOCKS_DIR = "locks"
LOCK_MAX_BYTES = 1 << 20
MANIFEST_MAX_BYTES = 16 << 20
SCHEMA_REL = ("schemas", "session-lock.schema.json")
HASH_CHUNK = 1 << 20

# 잠글 수 있는 세션 종류. 분석 세션은 라벨이 없어 잠그지 않는다(D-09 4).
LOCKABLE_KINDS = (vis.PRIMARY, vis.RELABEL_SAME, vis.RELABEL_SECOND, vis.ADJUDICATION, vis.ORIGIN_REVIEW)
COUNT_KINDS = ("segment", "utterance", "reaction", "continuation", "video_check", "missed_candidate", "origin",
               "adjudication")

# 다시 열 때 보이는 읽기 전용 사유 (08 D-11 7)
LOCKED_REASON = "이 세션은 잠겼습니다. 잠근 세션은 풀지 않습니다 — 고칠 것이 있으면 새 세션을 여세요 (08 D-11 7)"

# 검증 상태
ST_OK = "ok"                 # 잠금 때와 같음
ST_APPENDED = "appended"     # 잠금 뒤 줄이 붙음(앞부분은 같음)
ST_TAMPERED = "tampered"     # 잠금 때 바이트와 다름(변조/손상), 또는 파일 없음
ST_EXTRA = "extra"           # 잠금 뒤 생긴 같은 세션의 다른 PC 파일
STATUS_TEXT = {ST_OK: "정상", ST_APPENDED: "잠금 후 추가", ST_TAMPERED: "변조/손상", ST_EXTRA: "잠금 후 생긴 파일"}

# 요약 경고 코드 → 화면 문구 (session-lock.schema.json summary.warnings 와 같은 목록)
WARNING_TEXT = {
    "no_labels": "라벨이 하나도 없습니다.",
    "no_segments": "구간 라벨이 없습니다.",
    "segments_unmarked": "구간 완료 표시가 없는 저스트채팅 구간이 있습니다.",
    "reactions_unmarked": "반응 완료 표시가 없는 저스트채팅 구간이 있습니다.",
    "samples_missing": "표본을 뽑지 않은 (반응 완료) 저스트채팅 구간이 있습니다.",
    "samples_pending": "판정하지 않은 표본 채팅이 있습니다.",
    "conflicts": "판 연결이 갈라진(충돌) 라벨이 있습니다. 조정 세션에서만 고칠 수 있게 됩니다.",
    "other_machine_files": "다른 PC 에서 쓴 이 세션의 기록 파일도 함께 잠급니다. 그 PC 에서 이 세션을 닫았는지 확인하세요.",
    "load_warnings": "세션을 열 때 경고가 있었습니다(잘린 줄·충돌 사본 등).",
}
WARNING_CODES = tuple(WARNING_TEXT)


class LockError(RuntimeError):
    """잠그기·잠금 읽기가 거부되거나 실패했다. 메시지에 본문을 넣지 않는다."""


class AlreadyLockedError(LockError, FileExistsError):
    """locks/<session_id>.json 이 이미 있다. 잠금은 한 번만 만든다."""


class LockBlindError(LockError, PermissionError):
    """블라인드 규칙상 이 세션이 열 수 없는 다른 세션의 잠금·기록 파일이다(06 7-3)."""


class LockFormatError(LockError, ValueError):
    """잠금 파일이 형식에 맞지 않는다(깨짐·스키마 위반). problems 에 경로와 사유."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("잠금 파일이 형식에 맞지 않습니다: " + "; ".join(self.problems[:5]))


# ---------------------------------------------------------------------------- 경로·이름
def _check_sid(session_id) -> str:
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError("세션 ID 형식이 아닙니다")
    return session_id


def lock_path(project_root: str, session_id: str) -> str:
    """locks/<session_id>.json. 세션 ID 형식이 아니면 ValueError(경로에 들어가는 이름이라)."""
    return os.path.join(project_root, LOCKS_DIR, f"{_check_sid(session_id)}.json")


def is_locked(project_root: str, session_id: str) -> bool:
    """잠금 파일이 있는가. 이름만 본다(내용을 열지 않는다 — 어느 세션에서 불러도 블라인드를 깨지 않는다)."""
    return os.path.isfile(lock_path(project_root, session_id))


def _guard(session_id: str, reader_kind: str, reader_session_id: str | None) -> None:
    """다른 세션의 잠금·기록 파일을 열어도 되는가(06 7-3). 자기 세션이면 늘 된다. 막히면 LockBlindError."""
    if reader_session_id is not None and session_id == reader_session_id:
        return
    scope = vis.files_to_open(reader_kind)
    if not scope.may_read_other_session(True, vis.session_kind_of_id(session_id)):
        raise LockBlindError(f"{reader_kind} 세션은 다른 세션의 잠금·기록 파일을 열지 않습니다(06 7-3)")


# ---------------------------------------------------------------------------- 해시
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_prefix(path: str, size: int) -> str | None:
    """파일 앞 size 바이트의 sha256. 파일이 그보다 짧거나 읽지 못하면 None."""
    h = hashlib.sha256()
    left = size
    try:
        with builtins.open(path, "rb") as f:
            while left > 0:
                chunk = f.read(min(HASH_CHUNK, left))
                if not chunk:
                    return None
                h.update(chunk)
                left -= len(chunk)
    except OSError:
        return None
    return h.hexdigest()


def file_entry(name: str, data: bytes) -> dict:
    """기록 파일 하나의 잠금 항목 — 디스크에서 한 번 읽은 같은 바이트로 크기·해시·줄 수·last_seq 를 낸다.

    줄 수 = LF 로 끝난 줄 수. last_seq = 마지막 온전한 줄의 seq(읽지 못하면 줄 수). 잘린 마지막 조각은 세지 않는다.
    """
    m = SESSION_FILE_RE.match(name)
    if not m:
        raise ValueError("기록 파일 이름 규칙에 맞지 않습니다")
    lines = data.count(b"\n")
    last_seq = lines
    end = data.rfind(b"\n")
    if end > 0:
        start = data.rfind(b"\n", 0, end) + 1
        try:
            seq = json.loads(data[start:end].decode("utf-8-sig" if start == 0 else "utf-8")).get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
                last_seq = seq
        except (ValueError, AttributeError):
            pass
    return {"name": name, "machine4": m.group(2), "size": len(data), "sha256": sha256_bytes(data),
            "lines": lines, "last_seq": last_seq}


# ---------------------------------------------------------------------------- 표본 매니페스트 (자기 세션 것)
def session_manifests(samples_dir: str, session_id: str) -> list[tuple[dict, str]]:
    """samples/ 에서 source_segment.session_id 가 이 세션인 매니페스트 → [(매니페스트, 파일 sha256)] (sample_set_id 순).

    부르는 쪽이 visibility(samples 읽기 가능)를 확인한다. 읽지 못한 파일은 경고 로그(이름·사유 이름)만 남기고 건너뛴다.
    """
    out: list[tuple[dict, str]] = []
    try:
        names = sorted(os.listdir(samples_dir))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("S-") and name.endswith(".json")):
            continue
        try:
            with builtins.open(os.path.join(samples_dir, name), "rb") as f:
                data = f.read(MANIFEST_MAX_BYTES + 1)
            m = load_json_strict(data, MANIFEST_MAX_BYTES)
            if m.get("schema") != sp.SCHEMA:
                raise ValueError("schema")
            sp.parse_sample_set_id(m.get("sample_set_id"))
        except (OSError, ValueError, StrictJsonError, sp.SamplingError) as e:
            log.warning("표본 매니페스트를 읽지 못했습니다: %s (%s)", name, type(e).__name__)
            continue
        if (m.get("source_segment") or {}).get("session_id") == session_id:
            out.append((m, sha256_bytes(data)))
    return out


# ---------------------------------------------------------------------------- 요약 (순수)
@dataclass(frozen=True)
class SampleProgress:
    sample_set_id: str
    done: int
    total: int
    base: int
    extra: int

    @property
    def pending(self) -> int:
        return max(0, self.total - self.done)


@dataclass(frozen=True)
class LockSummary:
    """잠그기 전 요약. 숫자와 경고 코드뿐이다(본문 없음)."""
    session_id: str
    session_kind: str
    segments: int
    jc_segments: int
    reactions: int
    label_counts: dict
    status_counts: dict
    samples: tuple = ()                 # SampleProgress …
    segments_unmarked: int = 0
    reactions_unmarked: int = 0
    samples_missing: int = 0
    files: int = 0
    warnings: tuple = ()                # WARNING_CODES 중
    blockers: tuple = ()                # 잠글 수 없는 까닭(문구). 비어 있으면 잠글 수 있다

    @property
    def samples_judged(self) -> int:
        return sum(s.done for s in self.samples)

    @property
    def samples_total(self) -> int:
        return sum(s.total for s in self.samples)

    @property
    def can_lock(self) -> bool:
        return not self.blockers

    def warning_texts(self) -> list[str]:
        out = []
        for w in self.warnings:
            t = WARNING_TEXT[w]
            if w == "segments_unmarked":
                t = f"구간 완료 표시가 없는 저스트채팅 구간이 {self.segments_unmarked}개 있습니다."
            elif w == "reactions_unmarked":
                t = f"반응 완료 표시가 없는 저스트채팅 구간이 {self.reactions_unmarked}개 있습니다."
            elif w == "samples_missing":
                t = f"표본을 뽑지 않은 (반응 완료) 저스트채팅 구간이 {self.samples_missing}개 있습니다."
            elif w == "samples_pending":
                t = f"판정하지 않은 표본 채팅이 {self.samples_total - self.samples_judged}건 있습니다."
            elif w == "conflicts":
                t = f"판 연결이 갈라진(충돌) 라벨이 {self.status_counts.get('conflict', 0)}건 있습니다. 잠근 뒤에는 조정 세션에서만 다룹니다."
            out.append(t)
        return out

    def to_lock_summary(self) -> dict:
        """잠금 파일 summary 칸."""
        return {"segments": self.segments, "reactions": self.reactions, "samples_judged": self.samples_judged,
                "samples_total": self.samples_total, "warnings": list(self.warnings)}


def lock_summary(view, *, session_id: str, session_kind: str, marks: Iterable[Mapping] = (),
                 manifests: Iterable[Mapping] = (), files: int = 1, load_warnings: bool = False,
                 blockers: Sequence[str] = ()) -> LockSummary:
    """잠그기 화면의 요약을 계산한다(순수).

    view: app.core.resolve.EffectiveView (이 세션 기록만으로 만든 것). marks: 이 세션의 phase.mark 줄들.
    manifests: 이 세션 구간의 표본 매니페스트들(계열마다 가장 큰 sample_rev 만 센다). files: 이 세션 기록 파일 수.
    """
    label_counts = {k: len(view.items(k, session_id)) for k in COUNT_KINDS}
    status = view.count_by_status()
    status_counts = {k: int(status.get(k, 0)) for k in ("active", "retracted", "conflict", "invalid")}
    segs = view.items("segment", session_id)
    reactions = view.items("reaction", session_id)
    pm = ph.PhaseMarks(marks)
    jc = ph.just_chatting_segments(segs)
    seg_un = sum(1 for s in jc if not pm.done("segments", s["entity_id"]))
    react_un = sum(1 for s in jc if pm.done("segments", s["entity_id"]) and not pm.done("reactions", s["entity_id"]))
    latest = list(sp.latest_of([m for m in manifests if isinstance(m, Mapping)]).values())
    samples = []
    for m in sorted(latest, key=lambda x: x["sample_set_id"]):
        states, _ = sp.item_states(m, reactions)
        p = sp.progress(m, states)
        samples.append(SampleProgress(m["sample_set_id"], p.done, p.total, p.base, p.extra))
    drawn = {(m.get("source_segment") or {}).get("entity_id") for m in latest}
    targets = ph.sample_targets(segs, pm)
    missing = sum(1 for s in targets if s["entity_id"] not in drawn) if session_kind == vis.PRIMARY else 0
    w: list[str] = []
    if sum(label_counts.values()) == 0:
        w.append("no_labels")
    if not segs and session_kind in vis.LABELING_SESSIONS:
        w.append("no_segments")
    if seg_un:
        w.append("segments_unmarked")
    if react_un:
        w.append("reactions_unmarked")
    if missing:
        w.append("samples_missing")
    if any(s.pending for s in samples):
        w.append("samples_pending")
    if status_counts["conflict"]:
        w.append("conflicts")
    if files > 1:
        w.append("other_machine_files")
    if load_warnings:
        w.append("load_warnings")
    return LockSummary(session_id, session_kind, len(segs), len(jc), len(reactions), label_counts, status_counts,
                       tuple(samples), seg_un, react_un, missing, files, tuple(w), tuple(blockers))


# ---------------------------------------------------------------------------- 잠금 문서
# ---------------------------------------------------------------------------- 현재 표본 (재라벨 세션, 14 4-2 의 5번)
# 재라벨 세션은 samples/ 에 매니페스트를 쓰지 않고 블록 표본을 메모리에서 만든다(workbench.prepare_relabel_sample).
# 잠글 때 그 표본(판정 대상이었던 chat_id 전부)을 잠금 파일에 남겨, 보고서가 D-12 2 의 '현재 표본' 을 구간표 근사
# 대신 이것으로 본다. chat_id 만 — 본문·라벨 내용은 없다.
CURRENT_SAMPLE_KINDS = (vis.RELABEL_SAME, vis.RELABEL_SECOND)
CURRENT_SAMPLE_MAX = 50_000


def current_sample_doc(chat_ids: Iterable[str]) -> dict:
    """chat_id 모음 → 잠금 파일 current_sample 칸 {chat_ids_sha256, count, chat_ids(정렬·중복 없음)}.
    해시는 sampling.ids_sha256(정렬해 '\\n' 으로 이은 UTF-8, 06 2-7 과 같은 식). chat_id 형식이 아니면 ValueError."""
    from .core.ids import CHAT_ID_RE
    ids = sorted(set(chat_ids))
    for c in ids:
        if not isinstance(c, str) or not CHAT_ID_RE.match(c):
            raise ValueError("chat_id 형식이 아닌 값이 있습니다")
    if len(ids) > CURRENT_SAMPLE_MAX:
        raise ValueError(f"현재 표본이 {CURRENT_SAMPLE_MAX}건을 넘습니다")
    return {"chat_ids_sha256": sp.ids_sha256(ids), "count": len(ids), "chat_ids": ids}


def _current_sample_problems(cs) -> list[str]:
    """current_sample 칸의 규칙(스키마 밖): 정렬·중복 없음, count = 길이, 해시 = 목록."""
    if not isinstance(cs, Mapping):
        return ["$.current_sample: object 가 아닙니다"]
    ids = cs.get("chat_ids")
    if not isinstance(ids, list) or not all(isinstance(c, str) for c in ids):
        return ["$.current_sample.chat_ids: 문자열 목록이 아닙니다"]
    p = []
    if any(ids[i] >= ids[i + 1] for i in range(len(ids) - 1)):
        p.append("$.current_sample.chat_ids: 정렬되어 있지 않거나 겹칩니다")
    if cs.get("count") != len(ids):
        p.append("$.current_sample.count: 목록 길이와 다릅니다")
    if cs.get("chat_ids_sha256") != sp.ids_sha256(ids):
        p.append("$.current_sample.chat_ids_sha256: 목록과 맞지 않습니다")
    return p


def lock_current_sample(doc: Mapping) -> list[str] | None:
    """잠금 문서의 current_sample → chat_id 목록. 칸이 없으면(1차·앞 판 잠금) None. 맞지 않으면 ValueError(사유)."""
    if not isinstance(doc, Mapping) or doc.get("current_sample") is None:
        return None
    p = _current_sample_problems(doc["current_sample"])
    if p:
        raise ValueError("; ".join(p))
    return list(doc["current_sample"]["chat_ids"])


def build_lock_doc(*, session_id: str, project_id: str, labeler_id: str, session_kind: str, locked_at: str,
                   locked_on_machine4: str, app_version: str, guide: Mapping, relabel_plan_id: str | None,
                   files: Sequence[Mapping], summary: LockSummary,
                   sample_manifests: Sequence[tuple[str, str]] = (),
                   sample_verify: Sequence[Mapping] = (),
                   current_sample: Iterable[str] | None = None) -> dict:
    """잠금 파일 내용(순수). sample_manifests = [(sample_set_id, sha256)]. 키 순서는 사람이 읽기 쉬운 순서.

    current_sample: 재라벨 세션의 자기 표본 chat_id 모음(14 4-2 의 5번). 주면 current_sample 칸(current_sample_doc)을
    더한다. 재라벨이 아닌 세션 종류에 주면 ValueError(1차의 표본은 samples/ 매니페스트가 맡는다). None 이면 칸이 없다.
    """
    doc = _lock_doc_base(session_id=session_id, project_id=project_id, labeler_id=labeler_id,
                         session_kind=session_kind, locked_at=locked_at, locked_on_machine4=locked_on_machine4,
                         app_version=app_version, guide=guide, relabel_plan_id=relabel_plan_id, files=files,
                         summary=summary, sample_manifests=sample_manifests, sample_verify=sample_verify)
    if current_sample is not None:
        if session_kind not in CURRENT_SAMPLE_KINDS:
            raise ValueError("현재 표본 목록은 재라벨 세션 잠금에만 넣습니다")
        doc["current_sample"] = current_sample_doc(current_sample)
    return doc


def _lock_doc_base(*, session_id: str, project_id: str, labeler_id: str, session_kind: str, locked_at: str,
                   locked_on_machine4: str, app_version: str, guide: Mapping, relabel_plan_id: str | None,
                   files: Sequence[Mapping], summary: LockSummary,
                   sample_manifests: Sequence[tuple[str, str]],
                   sample_verify: Sequence[Mapping]) -> dict:
    return {
        "schema": LOCK_SCHEMA,
        "schema_version": LOCK_SCHEMA_VERSION,
        "session_id": session_id,
        "project_id": project_id,
        "labeler_id": labeler_id,
        "session_kind": session_kind,
        "locked_at": locked_at,
        "locked_by": labeler_id,
        "locked_on_machine4": locked_on_machine4,
        "app_version": app_version,
        "guide": {"guide_version": guide.get("guide_version"), "guide_doc_sha256": guide.get("guide_doc_sha256"),
                  "thresholds_sha256": guide.get("thresholds_sha256")},
        "relabel_plan_id": relabel_plan_id,
        "files": [dict(f) for f in sorted(files, key=lambda f: f["name"])],
        "label_counts": dict(summary.label_counts),
        "status_counts": dict(summary.status_counts),
        "sample_manifests": [{"sample_set_id": i, "sha256": h} for i, h in sorted(sample_manifests)],
        "sample_verify": [{"sample_set_id": v["sample_set_id"], "ok": bool(v["ok"]), "problems": int(v["problems"])}
                          for v in sample_verify],
        "summary": summary.to_lock_summary(),
    }


_schema_cache: dict[str, object] = {}


def load_lock_schema(path: str | None = None):
    """schemas/session-lock.schema.json 해석기(store.LabelSchema 와 같은 손검사기)."""
    from . import paths
    from .store import LabelSchema
    p = path or os.path.join(paths.code_dir(), *SCHEMA_REL)
    s = _schema_cache.get(p)
    if s is None:
        with builtins.open(p, "rb") as f:
            s = _schema_cache[p] = LabelSchema(load_json_strict(f.read(LOCK_MAX_BYTES + 1), LOCK_MAX_BYTES))
    return s


def check_lock_doc(doc, schema=None) -> list[str]:
    """스키마와 몇 가지 규칙(파일 이름이 이 세션 것, 라벨러가 세션 ID 앞부분)을 본다. 문제 목록(빈 목록이면 통과)."""
    sch = schema or load_lock_schema()
    problems = list(sch.check(doc))
    if problems or not isinstance(doc, dict):
        return problems
    sid = doc["session_id"]
    m = SESSION_ID_RE.match(sid)
    if m.group(1) != doc["labeler_id"]:
        problems.append("$.labeler_id: 세션 ID 의 라벨러와 다릅니다")
    if vis.session_kind_of_id(sid) != doc["session_kind"]:
        problems.append("$.session_kind: 세션 ID 의 종류 코드와 다릅니다")
    names = [f["name"] for f in doc["files"]]
    if len(set(names)) != len(names):
        problems.append("$.files: 같은 파일이 두 번 있습니다")
    for i, f in enumerate(doc["files"]):
        fm = SESSION_FILE_RE.match(f["name"])
        if fm.group(1) != sid or fm.group(2) != f["machine4"]:
            problems.append(f"$.files[{i}]: 이 세션의 기록 파일 이름이 아닙니다")
    if "current_sample" in doc:
        if doc["session_kind"] not in CURRENT_SAMPLE_KINDS:
            problems.append("$.current_sample: 재라벨 세션 잠금에만 있습니다")
        problems += _current_sample_problems(doc["current_sample"])
    return problems


def dump_lock(doc: Mapping) -> bytes:
    """UTF-8(BOM 없음)·LF·들여쓰기 2·끝 개행. NaN 금지."""
    return (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _fsync_dir(d: str) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_new_or_remove(path: str, data: bytes, exists_error: Exception) -> None:
    """하드 링크·rename 을 못 쓸 때의 대체 길: 'xb' 로 새로 만들어 쓰고, 쓰다가 실패하면 만든 파일을 지운다
    (반쯤 쓴 잠금·계획 파일이 남아 세션이 영구히 '깨진 잠금' 이 되지 않게). 이미 있으면 exists_error."""
    try:
        f = builtins.open(path, "xb")
    except FileExistsError:
        raise exists_error from None
    try:
        with f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            log.warning("반쯤 쓴 파일을 지우지 못했습니다: %s", os.path.basename(path))
        raise


def write_lock_exclusive(path: str, data: bytes) -> None:
    """없을 때만 원자적으로 만든다. 이미 있으면 AlreadyLockedError(덮어쓰지 않는다).

    임시 파일에 다 쓰고 fsync → os.link(임시, 최종). 링크는 최종 이름이 있으면 실패하므로 '없을 때만' 이 원자적이다.
    하드 링크를 못 쓰는 파일 시스템이면 Windows 는 os.rename(대상이 있으면 FileExistsError), 그 밖은 'xb' 로 열어 쓰고
    쓰다가 실패하면 그 파일을 지운다(_write_new_or_remove).
    **store 만 부른다**(ProjectStore.lock_session).
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    if os.path.exists(path):
        raise AlreadyLockedError("이미 잠긴 세션입니다")
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with builtins.open(tmp, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise AlreadyLockedError("이미 잠긴 세션입니다") from None
        except (OSError, NotImplementedError, AttributeError):
            if os.name == "nt":
                try:
                    os.rename(tmp, path)
                except FileExistsError:
                    raise AlreadyLockedError("이미 잠긴 세션입니다") from None
            else:
                _write_new_or_remove(path, data, AlreadyLockedError("이미 잠긴 세션입니다"))
        _fsync_dir(d)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------- 읽기·검증
def _read_lock_unguarded(project_root: str, session_id: str, schema=None) -> dict:
    p = lock_path(project_root, session_id)
    try:
        with builtins.open(p, "rb") as f:
            data = f.read(LOCK_MAX_BYTES + 1)
        doc = load_json_strict(data, LOCK_MAX_BYTES)
    except FileNotFoundError:
        raise
    except (OSError, StrictJsonError, ValueError) as e:
        raise LockFormatError([f"읽지 못했습니다 ({type(e).__name__})"]) from None
    problems = check_lock_doc(doc, schema)
    if not problems and doc["session_id"] != session_id:
        problems.append("$.session_id: 파일 이름과 다릅니다")
    if problems:
        raise LockFormatError(problems)
    return doc


def read_lock(project_root: str, session_id: str, *, reader_kind: str, reader_session_id: str | None = None,
              schema=None) -> dict:
    """잠금 파일을 읽고 검사한다. 블라인드상 열 수 없으면 디스크에서 열기 전에 LockBlindError.
    없으면 FileNotFoundError, 깨졌거나 형식이 틀리면 LockFormatError."""
    _check_sid(session_id)
    _guard(session_id, reader_kind, reader_session_id)
    return _read_lock_unguarded(project_root, session_id, schema)


@dataclass(frozen=True)
class FileCheck:
    name: str
    status: str                 # ST_OK·ST_APPENDED·ST_TAMPERED·ST_EXTRA
    last_seq: int | None        # 잠금의 last_seq (extra 는 None)
    detail: str = ""            # 'missing' · 'shorter' · 'unreadable' 등 (본문 없음)


@dataclass(frozen=True)
class LockCheck:
    """잠금 검증 결과. ok = 모든 파일이 잠금 때와 같음."""
    session_id: str
    files: tuple = ()           # FileCheck …

    @property
    def ok(self) -> bool:
        return all(f.status == ST_OK for f in self.files)

    @property
    def tampered(self) -> bool:
        return any(f.status == ST_TAMPERED for f in self.files)

    def by_status(self, status: str) -> list[FileCheck]:
        return [f for f in self.files if f.status == status]

    def messages(self) -> list[str]:
        out = []
        for f in self.files:
            if f.status == ST_TAMPERED:
                why = {"missing": "파일이 없습니다", "unreadable": "읽지 못했습니다"}.get(f.detail, "잠근 때와 내용이 다릅니다")
                out.append(f"{f.name}: 변조/손상 — {why}")
            elif f.status == ST_APPENDED:
                out.append(f"{f.name}: 잠금 후 추가된 줄이 있어 뷰에서 뺍니다(seq > {f.last_seq})")
            elif f.status == ST_EXTRA:
                out.append(f"{f.name}: 잠금 뒤 생긴 이 세션의 기록 파일이라 읽지 않습니다")
        return out


def check_files(labels_dir: str, doc: Mapping, present_names: Iterable[str]) -> LockCheck:
    """잠금 문서와 labels/ 의 이 세션 파일들을 비교한다. present_names = 지금 있는 이 세션의 기록 파일 이름.

    부르는 쪽이 블라인드(그 세션 파일을 열어도 되는가)를 확인한다. store 는 자기 세션에만 쓴다.
    """
    locked = {f["name"]: f for f in doc["files"]}
    present = set(present_names)
    out: list[FileCheck] = []
    for name in sorted(locked):
        f = locked[name]
        p = os.path.join(labels_dir, name)
        if name not in present or not os.path.isfile(p):
            out.append(FileCheck(name, ST_TAMPERED, f["last_seq"], "missing"))
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            out.append(FileCheck(name, ST_TAMPERED, f["last_seq"], "unreadable"))
            continue
        if size < f["size"]:
            out.append(FileCheck(name, ST_TAMPERED, f["last_seq"], "shorter"))
            continue
        head = _sha256_prefix(p, f["size"])
        if head is None:
            out.append(FileCheck(name, ST_TAMPERED, f["last_seq"], "unreadable"))
        elif head != f["sha256"]:
            out.append(FileCheck(name, ST_TAMPERED, f["last_seq"], "changed"))
        elif size == f["size"]:
            out.append(FileCheck(name, ST_OK, f["last_seq"]))
        else:
            out.append(FileCheck(name, ST_APPENDED, f["last_seq"], "appended"))
    for name in sorted(present - set(locked)):
        out.append(FileCheck(name, ST_EXTRA, None))
    return LockCheck(doc["session_id"], tuple(out))


def session_file_names(labels_dir: str, session_id: str) -> list[str]:
    """labels/ 에서 이 세션의 기록 파일 이름(규칙에 맞는 것)만. 다른 세션 파일은 이름만 보고 건너뛴다."""
    try:
        names = sorted(os.listdir(labels_dir))
    except OSError:
        return []
    out = []
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if m and m.group(1) == session_id:
            out.append(n)
    return out


@dataclass(frozen=True)
class LockedRecords:
    """다른 세션의 잠긴 기록(잠금 때 바이트까지만). records 는 (파일 이름, seq) 순, seq ≤ 잠금 last_seq 줄만."""
    session_id: str
    lock: dict
    check: LockCheck
    records: tuple = ()
    skipped_files: int = 0            # 변조/손상이라 읽지 않은 파일 수
    problems: tuple = ()              # 깨진 줄 등 (본문 없음)


def _parse_locked_bytes(data: bytes) -> tuple[list[dict], list[str]]:
    """잠긴 바이트 → JSON 줄 목록. 깨진 줄은 건너뛰고 몇 번째인지만 남긴다(본문 없음)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    out: list[dict] = []
    probs: list[str] = []
    for i, raw in enumerate(data.split(b"\n"), start=1):
        raw = raw.rstrip(b"\r")
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            probs.append(f"{i}번째 줄을 읽지 못했습니다")
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out, probs


def read_locked_records(project_root: str, session_id: str, *, reader_kind: str,
                        reader_session_id: str | None = None, schema=None) -> LockedRecords:
    """다른 세션의 **잠긴** 기록을 읽는다(분석 세션이 잠긴 1차 라벨을 볼 때, 08 D-09 5).

    블라인드: read_lock 과 같은 _guard — 재라벨·두 번째 사람·1차 세션이 남의 세션을 부르면 디스크를 열기 전에
    LockBlindError. 잠금이 없으면 FileNotFoundError(잠기지 않은 세션은 열지 않는다). 잠금에 적힌 파일만, 잠금 때 size
    바이트까지만 읽고 seq > last_seq 줄은 뺀다(잠금 후 추가). 변조/손상 파일은 읽지 않고 skipped_files 로 센다.
    """
    doc = read_lock(project_root, session_id, reader_kind=reader_kind, reader_session_id=reader_session_id,
                    schema=schema)
    labels = os.path.join(project_root, "labels")
    chk = check_files(labels, doc, session_file_names(labels, session_id))
    by = {f.name: f for f in chk.files}
    recs: list[dict] = []
    probs: list[str] = []
    skipped = 0
    for ent in sorted(doc["files"], key=lambda f: f["name"]):
        fc = by.get(ent["name"])
        if fc is None or fc.status not in (ST_OK, ST_APPENDED):
            skipped += 1
            continue
        try:
            with builtins.open(os.path.join(labels, ent["name"]), "rb") as f:
                data = f.read(ent["size"])
        except OSError as e:
            skipped += 1
            probs.append(f"{ent['name']}: 읽지 못했습니다 ({type(e).__name__})")
            continue
        if sha256_bytes(data) != ent["sha256"]:
            skipped += 1                        # check_files 와 읽기 사이에 바뀜
            continue
        rows, bad = _parse_locked_bytes(data)
        probs += [f"{ent['name']}: {b}" for b in bad]
        last = ent["last_seq"]
        recs += [r for r in rows if r.get("session_id") == session_id
                 and isinstance(r.get("seq"), int) and not isinstance(r.get("seq"), bool) and r["seq"] <= last]
    return LockedRecords(session_id, doc, chk, tuple(recs), skipped, tuple(probs))


def verify_lock(project_root: str, session_id: str, *, reader_kind: str, reader_session_id: str | None = None,
                schema=None) -> LockCheck:
    """잠긴 세션의 기록 파일이 잠금 때와 같은지 본다. 해시가 다르면 '변조/손상' 또는 '잠금 후 추가'.

    블라인드: 다른 세션이면 reader_kind 가 그 세션 파일을 열 수 있을 때만(조정 = 잠긴 세션, 분석 = 잠긴 1차).
    재라벨·두 번째 사람 세션은 앞 사람 것을 부르면 디스크를 열기 전에 LockBlindError.
    잠금이 없으면 FileNotFoundError, 잠금 파일이 깨졌으면 LockFormatError.
    """
    doc = read_lock(project_root, session_id, reader_kind=reader_kind, reader_session_id=reader_session_id,
                    schema=schema)
    labels = os.path.join(project_root, "labels")
    return check_files(labels, doc, session_file_names(labels, session_id))
