"""AI 결과 봉인·해제 (docs/design/06-evaluation-and-agreement.md 7-1·7-2, 03 6-14 'AI 봉인 기록'·17장, 08 D-15 7·8).

입출력 층이다. Qt 를 import 하지 않는다. label-event 스키마와 프로젝트 폴더는 건드리지 않는다 — 기록은 모두
`<앱>\\data\\ai\\<project_id>\\` 아래 새 파일이다(D-15 7).

폴더 (<ai 루트> = 프로젝트가 `…\\projects\\<slug>` 에 있으면 그 옆 `…\\ai`, 아니면 `<데이터>\\ai`. 보고서 폴더와 같은 규칙)
  <ai 루트>\\<project_id>\\sealed\\<sha256>.bin   받은 바이트 그대로(봉인 원본)
  <ai 루트>\\<project_id>\\seal.jsonl             추가 전용 기록 — ai.sealed · ai.unsealed
  <ai 루트>\\<project_id>\\runs\\<run_id>.json     해제한 결과(검사·정규화한 사본, 스키마 1판 모양)
  <ai 루트>\\<project_id>\\topics\\<run_id>.json   해제한 주제 흐름(topic-flow, 짝 봉인이 있을 때만)
  <ai 루트>\\<project_id>\\words\\<run_id>.json    해제한 시각별 전사 낱말(transcript-words, 짝 봉인이 있을 때만)
  <ai 루트>\\<project_id>\\vectors\\<run_id>.json  해제한 창별 주제 벡터(topic-vectors, 짝 봉인이 있을 때만)

짝 봉인 세 가지 — 모두 **같은 규칙**(_PairSpec 한 표, 23 5-2 의 2·10):
| 짝 | 봉인 함수 | 기록 종류 | 출처 | 해제 폴더 · 읽기 | 검사 |
|---|---|---|---|---|---|
| 주제 흐름(D-17 5) | seal_topics_bytes·_file | topics.sealed | engine_topics·file | topics\\ · read_topics | app.core.topics |
| 시각별 전사 낱말(D-20 3) | seal_words_bytes·_file | words.sealed | engine_words·file | words\\ · read_words | app.core.words_doc |
| 창별 주제 벡터(22 7-1 의 3) | seal_vectors_bytes·_file | vectors.sealed | engine_vectors·file | vectors\\ · read_vectors | vectors_check |
- 짝은 ai-result 봉인 하나(pair = 그 sha256)에만 붙는다. 짝이 없거나 이미 풀렸으면 받지 않는다(풀린 결과에 나중에 붙이면
  봉인 없이 보이게 되므로). 따로 풀지 않는다 — 짝 ai-result 를 풀 때 **같은 unseal 안에서 한 번에** 풀고, ai.unsealed 기록의
  topics·words·vectors 칸(+ <짝>_error)과 pairs 목록에 해시·코드만 남긴다. '봉인됨 n건' 은 ai-result 만 센다.
- 전사 낱말·벡터는 전사문과 같은 등급이다(벡터는 어느 정도 거꾸로 풀 수 있다) — 검사 결과·기록·오류에 낱말·수를 넣지 않는다
  (해시·크기·코드만). 시범 프로젝트는 이 길로만 만든다(tools/engine_to_ai.py --from-project … --words --vectors --seal).
- 짝 하나가 깨져도 판정 해제는 막지 않는다 — 그 짝만 풀지 않고 코드(<짝>_unreadable·_bin_mismatch·_json·_format)를 남긴다.
- 벡터 검사(vectors_check)는 앱 쪽 규칙이다(앱은 엔진을 import 하지 않는다, D-06): 칸 모양·target·벡터 해시·
  app.core.viewer_score.vectors_from_doc 이 모든 창을 읽는지. 엄한 검사(정규화 길이 등)는 engine.topic_vectors.validate 몫.
- 짝 보충(attach_pairs, D-23 3): 판정만 봉인된 것에 짝을 나중에 붙인다(판정을 다시 봉인하지 않는다 — 사전 봉인 여부 그대로).
  먼저 모두 검사하고 하나라도 거부되면 아무것도 붙이지 않는다. 이미 있는 짝은 덮어쓰지 않는다('present'). 붙인 짝 기록에는
  attached = true · after_primary_lock 칸이 더해진다(SealEntry.pairs_attached). 명령줄은 tools/ai_seal.py attach-pairs.

원인 코드 (24 5-2 의 5): SealError 마다 .code(SEAL_ERROR_CODES — dup_run_id·sealed_mismatch·messages_missing 등). 메시지(str)는
사람이 읽는 문장 그대로 두고(화면에 그대로 보인다), 도구·요약은 error_code(예외) 로 'unseal:<코드>' 를 적는다.
해제 조건이 안 된 사유도 코드가 있다(UnsealConditions.codes — reasons 와 같은 차례, UNSEAL_REASON_CODES). 계획 파일이 깨져 막힌
것은 'plan_unreadable'(+ unreadable_plans 에 계획 ID)이고 '잠긴 재라벨 세션이 없음'(relabel_not_locked)과 가른다.

seal.jsonl 잠금 (형식 검증 2026-10-23 에서 찾은 경쟁): seal.jsonl 을 읽어 확인하고 쓰는 곳(seal_bytes·짝 봉인·attach_pairs·
unseal·_append)은 모두 <ai 루트>\\<project_id>\\.unseal.lock(O_EXCL 로 만드는 배타 잠금 파일)을 잡고 한다. 앱과
tools/ai_seal.py·pilot_unseal_day --go 가 같은 봉인을 함께 풀어도 ai.unsealed 는 한 줄이다 — 늦은 쪽은 잠금을 얻은 뒤 기록을
다시 읽고, 차례로 두 번 부른 것과 똑같이 AlreadyUnsealedError 를 낸다. 같은 스레드 안에서는 다시 잡지 않는다(attach_pairs →
짝 봉인 → _append). SEAL_LOCK_TIMEOUT_S 를 기다려도 못 얻으면 SealError(seal_busy, 아무것도 쓰지 않음). SEAL_LOCK_STALE_S 보다
오래된 잠금 파일은 비정상 종료로 남은 것으로 보고 지운다. 폴더가 아직 없으면 기록도 없으므로 잠그지 않는다(폴더를 만들지 않는다).

봉인 (seal_bytes · seal_file)
- 언제든 받는다. 받을 때 06 6-2 검사를 **들어온 바이트로** 돌려 '유효 / 거부 코드' 만 남긴다. 내용은 화면·로그·기록에
  내지 않고 해석 결과(run_id·엔진 이름·판정 수)도 남기지 않는다(06 7-2). 기록: 해시·크기·시각·사람·출처 종류·검사 결과.
- 같은 바이트를 다시 받으면 새 줄을 쓰지 않는다(봉인은 한 번).

봉인 해제 (unseal) — **봉인 파일을 여는 곳은 이 함수 하나다.** 해제 전에 내용을 읽는 공개 함수는 없다.
- 06 7-1 조건: 라벨링 세션(1차·재라벨)이 모두 잠김, 1차마다 재라벨 계획이 있음, 계획된 재라벨이 모두 잠김,
  표본·계획 재현 검증 통과 — 뒤 둘은 app.agreement_report.comparison_gate 가 본다(잠금·계획·매니페스트만 열고
  라벨 파일은 열지 않는다).
- 조건 전에 열려면 사유(early_reason)가 있어야 하고, 기록에 conditions_met = false 와 사유가 남는다(03 6-14).
- 기록 ai.unsealed: run_id, 그 시점의 잠금 해시 목록 locks[](session_id·잠금 파일 sha256·locked_at), conditions_met,
  reason, 사전 봉인 여부, 보정 판(media_ms 였을 때), 경고 코드, 뺀 chat_id 건수, runs 파일 sha256,
  **engine_version**(해제한 결과의 engine.version — 분석 화면 '판 A 기본' 이 읽는다, 23 5-2 의 10),
  **pairs**[](짝마다 kind·sha256·file_sha256·error — 짝이 없으면 빈 목록), 짝이 있을 때만 topics·words·vectors 칸.
  seal.jsonl 형식은 하위 호환이다(RECORD_V 1 그대로, 칸만 더함): 옛 기록은 engine_version 없음 = '판 모름', pairs 없음.

해제 전 읽기: read_run·read_topics·read_words·read_vectors 는 ai.unsealed 기록이 없으면 파일을 열기 전에
SealNotUnsealedError(= SealBlindError — 해제 전에는 아무 자리도 열지 않는다)를 낸다.

사전 봉인 (06 7-2): 봉인 시각 < 가장 이른 1차 세션 잠금 시각이면 '사전 봉인'. 저장하지 않고 seal.jsonl 과 잠금 파일의
시각에서 계산한다(03 6-14). 잠긴 1차가 아직 없으면 사전 봉인이다(앞으로의 잠금보다 앞선다).

자리(reader_kind) 검사 (03 12-1 visibility.files_to_open 의 ai_sealed): 라벨링·재라벨·출처 판정 세션은 이 모듈의
어느 함수도 부르지 못한다(SealBlindError, 파일을 열기 전에). 받기·상태 보기는 세션 밖(None)과 조정 세션, 해제는 세션 밖과
조정 세션만. 분석 세션(UNSEALED_ONLY)은 상태 보기(seal_status: '봉인됨 n건')와 해제된 결과 읽기(read_run·read_topics·
read_words·read_vectors)만 된다 —
나란히 보기(D-15 7)를 위해서다. 봉인 파일을 여는 unseal 은 그대로 막힌다.

봉인은 암호화가 아니다(06 7-4). 탐색기로 파일을 여는 것은 막지 못한다 — 운영 규칙으로 막는다.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

from . import ai_results as air
from . import jsonl, paths
from .core import visibility as vis
from .core.ids import SESSION_FILE_RE, SESSION_ID_RE
from .core.validate import StrictJsonError, load_json_strict
from .project import atomic_write, open_project

SEAL_FILE = "seal.jsonl"
SEALED_DIR = "sealed"
RUNS_DIR = "runs"
RECORD_V = 1
K_SEALED = "ai.sealed"
K_UNSEALED = "ai.unsealed"
K_TOPICS_SEALED = "topics.sealed"               # 주제 흐름 짝 봉인 (D-17 5)
TOPICS_DIR = "topics"
SOURCE_KINDS = ("engine_link", "file")          # 출처 종류: 엔진 link 를 바로 봉인 · 받은 파일
TOPICS_SOURCE_KINDS = ("engine_topics", "file")  # 주제 흐름 출처: 엔진을 바로 봉인 · 받은 파일
TOPICS_MAX_BYTES = 4 << 20
K_WORDS_SEALED = "words.sealed"                 # 시각별 전사 낱말 짝 봉인 (D-20 3, 22 5-2 의 7)
WORDS_DIR = "words"
WORDS_SOURCE_KINDS = ("engine_words", "file")   # 전사 낱말 출처: 엔진을 바로 봉인 · 받은 파일
WORDS_MAX_BYTES = 16 << 20                      # app.viewer_flow.WORDS_MAX_BYTES 와 같다
K_VECTORS_SEALED = "vectors.sealed"             # 창별 주제 벡터 짝 봉인 (23 5-2 의 2)
VECTORS_DIR = "vectors"
VECTORS_SOURCE_KINDS = ("engine_vectors", "file")   # 주제 벡터 출처: 엔진을 바로 봉인 · 받은 파일
VECTORS_MAX_BYTES = 64 << 20                    # app.viewer_flow.VECTORS_MAX_BYTES 와 같다
# topic-vectors 문서의 칸(engine.topic_vectors.TOP_KEYS 와 같다 — 앱은 엔진을 import 하지 않으므로 글자로 둔다)
VECTORS_TOP_KEYS = ("schema", "schema_version", "run_id", "created_at", "engine", "model", "target", "time_axis",
                    "calibration", "window_ms", "span_ms", "encoding", "dim", "range", "stats", "vectors_sha256",
                    "windows")
VECTORS_TARGET_KEYS = ("project_id", "video_no", "asr_sha256")
VECTORS_WINDOW_KEYS = ("start_ms", "end_ms", "v")
LOCK_MAX_BYTES = 4 << 20
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
PROJECT_ID_RE = re.compile(r"\A[0-9a-f-]{36}\Z")
LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
REASON_MAX = 500
SEAL_LOCK_FILE = ".unseal.lock"                 # <ai 루트>\<project_id>\ 아래 — seal.jsonl 확인·쓰기 잠금
SEAL_LOCK_TIMEOUT_S = 30.0                      # 다른 프로세스가 쥔 잠금을 이만큼 기다린다(넘으면 seal_busy)
SEAL_LOCK_STALE_S = 300.0                       # 이보다 오래된 잠금 파일은 비정상 종료로 남은 것으로 보고 지운다
SEAL_LOCK_POLL_S = 0.05

__all__ = [
    "SealError", "SealBlindError", "SealNotUnsealedError", "SealBlockedError", "AlreadyUnsealedError",
    "SealEntry", "SealStatus", "UnsealConditions", "UnsealResult",
    "default_ai_root", "project_ai_dir", "seal_bytes", "seal_file", "seal_status", "unseal_conditions",
    "unseal", "read_run", "unseal_events", "SOURCE_KINDS", "PAIR_KINDS",
    "seal_topics_bytes", "seal_topics_file", "read_topics", "topics_check", "TOPICS_SOURCE_KINDS",
    "seal_words_bytes", "seal_words_file", "read_words", "words_check", "WORDS_SOURCE_KINDS",
    "seal_vectors_bytes", "seal_vectors_file", "read_vectors", "vectors_check", "VECTORS_SOURCE_KINDS",
    "SEAL_ERROR_CODES", "error_code", "attach_pairs", "AttachResult", "ATTACH_SOURCES", "UNSEAL_REASON_CODES",
    "SEAL_LOCK_FILE",
]


#: SealError 의 원인 코드 (24 5-2 의 5). 메시지(str)는 사람이 읽는 문장 그대로 두고, 기계가 읽는 원인은 .code 에 둔다 —
#: 해제 당일 요약·도구 출력은 'unseal:<코드>' 로 적는다(error_code). 코드에는 내용·경로·ID 가 없다.
SEAL_ERROR_CODES = (
    "seal_error",              # 코드를 주지 않은 SealError(옛 호출)
    "reader_blind",            # 이 자리(세션 종류)는 data\ai 를 열지 않는다 (SealBlindError)
    "not_unsealed",            # 해제 기록이 없는 run 을 읽으려 함 (SealNotUnsealedError)
    "conditions_unmet",        # 06 7-1 해제 조건 안 됨 (SealBlockedError)
    "already_unsealed",        # 이미 해제한 봉인 (AlreadyUnsealedError)
    "project_id_format", "ai_dir_in_project", "ai_dir_in_repo", "project_json", "bad_by",
    "seal_jsonl_corrupt",      # seal.jsonl 깨진 줄·끝 줄 잘림 — 새 기록을 쓰지 않는다
    "lock_unreadable", "lock_session_mismatch",
    "source_kind", "too_large", "sha_format", "reason_length",
    "pair_sha_format",         # 짝 sha256 형식이 아님
    "pair_missing",            # 짝 판정 봉인이 없음
    "pair_unsealed",           # 짝 판정이 이미 해제됨(붙이지 않음)
    "pair_not_valid",          # 짝 판정 봉인이 받을 때 거부된 것(붙이지 않음, attach_pairs)
    "pair_kind",               # 모르는 짝 종류(attach_pairs)
    "no_seal_record",          # 봉인 기록이 없는 해시
    "rejected_at_seal",        # 받을 때 거부된 결과는 해제하지 않음
    "messages_missing",        # 프로젝트 채팅 파일이 없거나 읽을 줄이 없음
    "sealed_unreadable",       # 봉인 파일(.bin)을 읽지 못함
    "sealed_mismatch",         # 봉인 파일이 기록과 다름(바뀌었거나 손상)
    "recheck_rejected",        # 지금 프로젝트와 대조하면 거부됨
    "all_dropped",             # 판정이 모두 이 프로젝트에 없는 chat_id
    "run_id_format",
    "dup_run_id",              # 같은 run_id 의 다른 결과가 이미 해제됨
    "unsealed_mismatch",       # 해제한 파일(runs·짝)이 해제 기록의 해시와 다름
    "seal_busy",               # 다른 프로세스가 seal.jsonl 잠금(.unseal.lock)을 쥐고 있어 기다리다 멈춤 — 아무것도 쓰지 않음
)

#: 해제 조건(06 7-1)이 안 된 사유 코드 — UnsealConditions.codes 에 reasons 와 같은 차례로 둔다. 해제 당일 요약
#: (tools/pilot_unseal_day)이 이 코드를 그대로 적는다. 코드에는 세션·계획 ID 가 없다(계획 ID 는 unreadable_plans 에 따로).
UNSEAL_REASON_CODES = (
    "unlocked_sessions",       # 잠기지 않은 라벨링 세션이 있음
    "no_primary",              # 1차 세션이 없음
    "no_relabel_plan",         # 재라벨 계획이 없는 1차 세션이 있음
    "relabel_not_locked",      # 계획은 읽히는데 그 계획의 잠긴 재라벨 세션이 아직 없음
    "plan_unreadable",         # 계획 파일이 깨졌거나 형식이 아님(남은 계획이든 잠긴 재라벨 세션의 계획이든) — 파일을 본다
    "sample_verify",           # 표본·계획 재현 검증이 통과하지 않음
    "other",                   # 그 밖의 비교 조건 오류(잠금 파일 불일치 등)
)


class SealError(RuntimeError):
    """봉인·해제를 할 수 없다. 메시지에 내용·경로를 넣지 않는다(해시·코드·건수만). .code = 원인 코드(SEAL_ERROR_CODES)."""
    code = "seal_error"

    def __init__(self, msg: str = "", *, code: str | None = None):
        super().__init__(msg)
        if code:
            self.code = code


class SealBlindError(SealError, PermissionError):
    """이 자리(세션 종류)는 data\\ai 를 열지 않는다(06 7-3). 파일을 열기 전에."""
    code = "reader_blind"


class SealNotUnsealedError(SealBlindError):
    """해제 기록(ai.unsealed)이 없는 run 을 읽으려 했다 — 해제 전에는 어느 자리에서도 runs·topics·words·vectors 를
    열지 않는다(파일을 열기 전에). SealBlindError 이므로 '블라인드라 못 봄' 으로 다루면 된다."""
    code = "not_unsealed"


class SealBlockedError(SealError):
    """06 7-1 해제 조건이 되지 않았다. reasons 에 사유 문장(세션·계획 ID 와 건수만), reason_codes 에 같은 차례의 사유 코드
    (UNSEAL_REASON_CODES — 주지 않았으면 빈 목록)."""
    code = "conditions_unmet"

    def __init__(self, reasons: list[str], reason_codes: list[str] | None = None):
        self.reasons = list(reasons)
        self.reason_codes = list(reason_codes or [])
        super().__init__("봉인 해제 조건(06 7-1)이 되지 않았습니다: " + "; ".join(self.reasons))


class AlreadyUnsealedError(SealError):
    """이미 해제한 봉인이다."""
    code = "already_unsealed"


def error_code(exc: BaseException) -> str:
    """예외 → 원인 코드. SealError 면 .code, 아니면 예외 이름(OSError 등). 해제 당일 요약의 'unseal:<코드>' 에 쓴다."""
    c = getattr(exc, "code", None) if isinstance(exc, SealError) else None
    return c if isinstance(c, str) and c else type(exc).__name__


@dataclass
class SealEntry:
    """seal.jsonl 의 ai.sealed 한 줄 + 해제 여부. 내용을 해석한 값은 run_id(해제 뒤에만) 하나뿐이다."""
    sha256: str
    size: int
    sealed_at: str
    sealed_by: str
    source: str
    check: str                                   # valid · rejected
    reject_reasons: list[str]
    presealed: bool | None = None                # 사전 봉인 여부(계산값)
    unsealed_at: str | None = None
    run_id: str | None = None                    # 해제한 뒤에만
    conditions_met: bool | None = None           # 해제 때 06 7-1 조건이 됐는지
    new: bool = False                            # 이번 호출에서 새로 봉인했는가
    topics_sha256: str | None = None             # 짝으로 봉인한 주제 흐름(검사 통과한 마지막 것, D-17 5)
    words_sha256: str | None = None              # 짝으로 봉인한 시각별 전사 낱말(검사 통과한 마지막 것, D-20 3)
    vectors_sha256: str | None = None            # 짝으로 봉인한 창별 주제 벡터(검사 통과한 마지막 것, 23 5-2 의 2)
    engine_version: str | None = None            # 해제 기록의 엔진 판(해제 뒤에만, 옛 기록은 None = 판 모름)
    pairs_attached: list[str] = field(default_factory=list)   # 짝 보충(attach_pairs)으로 나중에 붙인 짝 종류(D-23 3)


@dataclass
class SealStatus:
    project_id: str
    entries: list[SealEntry]
    first_primary_lock_at: str | None
    problems: list[str] = field(default_factory=list)     # seal.jsonl 의 깨진 줄 등(줄 번호만)

    @property
    def sealed_count(self) -> int:
        """아직 해제하지 않은 봉인 수 — 화면은 해제 전에 '봉인됨 n건' 만 보인다(D-15 7)."""
        return sum(1 for e in self.entries if e.unsealed_at is None)

    @property
    def unsealed_count(self) -> int:
        return sum(1 for e in self.entries if e.unsealed_at is not None)


@dataclass
class UnsealConditions:
    met: bool
    reasons: list[str]                           # 안 된 사유(사람이 읽는 문장 — 세션·계획 ID 와 건수만)
    warnings: list[str]                          # comparison_gate 경고 등
    labeling_sessions: list[str]                 # 본 라벨링 세션 ID
    codes: list[str] = field(default_factory=list)              # reasons 와 같은 차례의 사유 코드(UNSEAL_REASON_CODES)
    unreadable_plans: list[str] = field(default_factory=list)   # 해제를 막은 읽지 못한 계획 ID(plan_unreadable 의 대상)


@dataclass
class UnsealResult:
    sha256: str
    run_id: str
    run_path: str
    conditions_met: bool
    presealed: bool
    record: dict


# ----------------------------------------------------------------------------- 자리·경로
def _guard(reader_kind: str | None, *, unseal: bool, unsealed_view: bool = False) -> None:
    """unsealed_view: 건수 보기(seal_status)·해제된 결과 읽기(read_run) — 분석 세션(UNSEALED_ONLY)도 된다."""
    if reader_kind is None:
        return
    access = vis.files_to_open(reader_kind).access("ai_sealed")
    if unsealed_view and access == vis.UNSEALED_ONLY:
        return
    if unseal:
        if reader_kind == vis.ADJUDICATION:
            return
    else:
        if access in (vis.SEAL_ONLY, vis.READ):
            return
    raise SealBlindError("이 세션에서는 AI 결과 봉인 폴더를 열지 않습니다(06 7-3)")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _under(p: str, root: str) -> bool:
    p, r = _norm(p), _norm(root).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


def _in_git_tree(path: str) -> bool:
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def default_ai_root(project_root: str) -> str:
    """기본 ai 루트: 프로젝트가 '…\\projects\\<slug>' 에 있으면 그 옆 '…\\ai', 아니면 <데이터>\\ai (보고서 폴더와 같은 규칙)."""
    root = os.path.abspath(project_root)
    parent = os.path.dirname(root)
    if os.path.basename(parent).lower() == "projects":
        return os.path.join(os.path.dirname(parent), "ai")
    return os.path.join(paths.data_root(), "ai")


def project_ai_dir(project_root: str, project_id: str, ai_root: str | None = None) -> str:
    """<ai 루트>\\<project_id>. 프로젝트 폴더·저장소(git 작업 트리) 안이면 SealError."""
    if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
        raise SealError("project_id 형식이 아닙니다", code="project_id_format")
    base = os.path.abspath(ai_root or default_ai_root(project_root))
    if _under(base, project_root):
        raise SealError("AI 결과 폴더가 프로젝트 폴더 안입니다(06 7-2: 프로젝트 밖)", code="ai_dir_in_project")
    if _under(base, paths.code_dir()) or _in_git_tree(base):
        raise SealError("AI 결과 폴더가 저장소 안입니다", code="ai_dir_in_repo")
    return os.path.join(base, project_id)


def _project(project_root: str):
    p = open_project(project_root)
    if p.data is None or not p.project_id:
        raise SealError("project.json 을 읽지 못했습니다", code="project_json")
    return p


def _now_iso(now: datetime | None) -> str:
    n = now or datetime.now().astimezone()
    if n.tzinfo is None:
        n = n.astimezone()
    return n.isoformat(timespec="milliseconds")


def _parse_dt(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def _app_version(v: str | None) -> str:
    if v:
        return v
    from . import __version__
    return __version__


def _check_by(by) -> str:
    if not isinstance(by, str) or not LABELER_RE.match(by):
        raise SealError("by 는 라벨러 ID(예: L01) 여야 합니다", code="bad_by")
    return by


# ----------------------------------------------------------------------------- seal.jsonl 잠금
_held = threading.local()          # 이 스레드가 지금 쥔 잠금의 폴더(정규화 경로) — 같은 스레드는 다시 잡지 않는다


def _lock_acquire(path: str, token: bytes) -> None:
    """path 를 O_EXCL 로 만든다(없을 때만 — 두 프로세스·두 스레드 가운데 하나만 된다). 이미 있으면 SEAL_LOCK_POLL_S 마다
    다시 해 보고, SEAL_LOCK_STALE_S 보다 오래된 파일은 지운다. SEAL_LOCK_TIMEOUT_S 를 넘기면 SealError(seal_busy)."""
    deadline = time.monotonic() + SEAL_LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > SEAL_LOCK_STALE_S:
                    os.remove(path)                  # 비정상 종료로 남은 잠금 — 지우고 곧바로 다시
                    continue
            except OSError:
                pass                                 # 그 사이 풀렸다 — 다시
        except PermissionError:
            pass                                     # Windows: 다른 쪽이 막 지우는 중(삭제 대기) — 곧 다시
        else:
            try:
                os.write(fd, token)
            except BaseException:
                os.close(fd)
                _lock_remove(path)
                raise
            os.close(fd)
            return
        if time.monotonic() >= deadline:
            raise SealError(f"다른 곳(앱·도구)이 봉인 기록을 쓰는 중이라 {SEAL_LOCK_TIMEOUT_S:.0f}초 기다린 뒤 멈췄습니다 "
                            "— 아무것도 쓰지 않았습니다. 잠시 뒤 다시 하세요", code="seal_busy")
        time.sleep(SEAL_LOCK_POLL_S)


def _lock_remove(path: str) -> None:
    """잠금 파일을 지운다. Windows 에서 보안 프로그램이 잠깐 쥐고 있으면(PermissionError) 2초까지 다시 해 본다."""
    for _ in range(40):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(SEAL_LOCK_POLL_S)
        except OSError:
            return


def _lock_release(path: str, token: bytes) -> None:
    """내 잠금일 때만 지운다(오래 쥐어 남이 낡은 잠금으로 보고 지우고 새로 잡았으면 그것은 두고 간다)."""
    try:
        with builtins.open(path, "rb") as f:
            mine = f.read(len(token) + 1) == token
    except OSError:
        return
    if mine:
        _lock_remove(path)


@contextmanager
def _seal_lock(d: str):
    """<ai 루트>\\<project_id>(= d) 의 seal.jsonl 확인·쓰기 잠금(머리말 'seal.jsonl 잠금'). 같은 스레드 안에서 겹쳐 부르면
    바깥 것 하나만 잡는다. d 가 없으면 기록도 없어 지킬 것이 없으므로 잡지 않는다(폴더를 만들지 않는다 — 거절된 호출이
    아무것도 남기지 않게)."""
    held = getattr(_held, "dirs", None)
    if held is None:
        held = _held.dirs = set()
    key = _norm(d)
    if key in held or not os.path.isdir(d):
        yield
        return
    path = os.path.join(d, SEAL_LOCK_FILE)
    token = f"{os.getpid()}-{uuid.uuid4().hex}\n".encode("ascii")
    _lock_acquire(path, token)
    held.add(key)
    try:
        yield
    finally:
        held.discard(key)
        _lock_release(path, token)


# ----------------------------------------------------------------------------- seal.jsonl
def _read_records(d: str) -> tuple[list[dict], list[str]]:
    p = os.path.join(d, SEAL_FILE)
    if not os.path.exists(p):
        return [], []
    res = jsonl.read(p)
    probs = [f"seal.jsonl {n}번째 줄이 깨졌습니다" for n in res.corrupt_lines]
    if res.truncated_tail:
        probs.append("seal.jsonl 마지막 줄이 잘렸습니다")
    return res.records, probs


def _append(d: str, rec: dict) -> dict:
    """한 줄 더 쓴다. 잠금 안에서(부르는 쪽이 이미 쥐었으면 그대로) — seq 를 읽고 쓰는 사이에 다른 쓰기가 끼지 않게."""
    with _seal_lock(d):
        recs, probs = _read_records(d)
        if probs:
            raise SealError("seal.jsonl 이 깨져 새 기록을 쓰지 않습니다: " + "; ".join(probs[:3]), code="seal_jsonl_corrupt")
        seq = max((r.get("seq", 0) for r in recs if isinstance(r.get("seq"), int)), default=0) + 1
        full = {"v": RECORD_V, "seq": seq, **rec}
        jsonl.append(os.path.join(d, SEAL_FILE), full)
        return full


def _sealed_record(recs: Iterable[dict], sha: str) -> dict | None:
    for r in recs:
        if r.get("kind") == K_SEALED and r.get("sha256") == sha:
            return r
    return None


def _unsealed_record(recs: Iterable[dict], sha: str) -> dict | None:
    for r in recs:
        if r.get("kind") == K_UNSEALED and r.get("sha256") == sha:
            return r
    return None


# ----------------------------------------------------------------------------- 잠금 (읽기만)
def _lock_docs(project_root: str) -> list[tuple[str, str, dict]]:
    """locks/<session_id>.json → [(session_id, 파일 sha256, 내용)]. 라벨 파일은 열지 않는다. 깨진 잠금은 SealError."""
    d = os.path.join(project_root, "locks")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json") or not SESSION_ID_RE.match(n[:-5]):
            continue
        sid = n[:-5]
        try:
            with builtins.open(os.path.join(d, n), "rb") as f:
                raw = f.read(LOCK_MAX_BYTES + 1)
            doc = load_json_strict(raw, LOCK_MAX_BYTES)
        except (OSError, StrictJsonError):
            raise SealError(f"{sid} 의 잠금 파일을 읽지 못했습니다", code="lock_unreadable") from None
        if doc.get("session_id") != sid:
            raise SealError(f"{sid} 의 잠금 파일 session_id 가 이름과 다릅니다", code="lock_session_mismatch")
        out.append((sid, hashlib.sha256(raw).hexdigest(), doc))
    return out


def _first_primary_lock_at(locks) -> str | None:
    best: tuple[datetime, str] | None = None
    for sid, _h, doc in locks:
        if vis.session_kind_of_id(sid) != vis.PRIMARY:
            continue
        d = _parse_dt(doc.get("locked_at"))
        if d is not None and (best is None or d < best[0]):
            best = (d, doc["locked_at"])
    return best[1] if best else None


def _is_presealed(sealed_at: str, first_primary_lock_at: str | None) -> bool:
    if first_primary_lock_at is None:
        return True
    a, b = _parse_dt(sealed_at), _parse_dt(first_primary_lock_at)
    return a is not None and b is not None and a < b


# ----------------------------------------------------------------------------- 봉인
def seal_bytes(project_root: str, data: bytes, *, source: str, by: str, now: datetime | None = None,
               ai_root: str | None = None, app_version: str | None = None,
               reader_kind: str | None = None) -> SealEntry:
    """받은 바이트를 봉인한다. 검사는 이 바이트로만 하고(봉인 파일을 다시 열지 않는다) 코드만 남긴다.

    크기 상한을 넘으면 봉인하지 않고 SealError. 거부된 파일도 봉인은 한다(check = rejected — 해제는 거부)."""
    _guard(reader_kind, unseal=False)
    if source not in SOURCE_KINDS:
        raise SealError(f"출처 종류는 {SOURCE_KINDS} 가운데 하나여야 합니다", code="source_kind")
    _check_by(by)
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 는 bytes 여야 합니다")
    data = bytes(data)
    if len(data) > air.MAX_BYTES:
        raise SealError(f"크기 상한({air.MAX_BYTES} 바이트)을 넘어 봉인하지 않습니다", code="too_large")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    sha = hashlib.sha256(data).hexdigest()
    os.makedirs(os.path.join(d, SEALED_DIR), exist_ok=True)
    with _seal_lock(d):                 # '이미 봉인됨' 확인과 쓰기 사이에 다른 프로세스가 같은 바이트를 봉인하지 않게
        recs, _ = _read_records(d)
        old = _sealed_record(recs, sha)
        binp = os.path.join(d, SEALED_DIR, f"{sha}.bin")
        if not os.path.exists(binp):
            atomic_write(binp, data)
        if old is not None:
            return _entry(old, recs, _first_primary_lock_at(_lock_docs(project_root)))
        chk = air.check_bytes(data, proj.data)
        rec = _append(d, {
            "kind": K_SEALED, "at": _now_iso(now), "by": by, "app_version": _app_version(app_version),
            "sha256": sha, "size": len(data), "source": source, "check": chk.check,
            "reject_reasons": list(chk.rejects),
        })
    e = _entry(rec, [], _first_primary_lock_at(_lock_docs(project_root)))
    e.new = True
    return e


def seal_file(project_root: str, path: str, *, source: str = "file", by: str, now: datetime | None = None,
              ai_root: str | None = None, app_version: str | None = None,
              reader_kind: str | None = None) -> SealEntry:
    """받은 파일(봉인 전 원본)을 읽어 seal_bytes. 원본을 지우는 것은 부르는 쪽 몫이다."""
    _guard(reader_kind, unseal=False)
    with builtins.open(path, "rb") as f:
        data = f.read(air.MAX_BYTES + 1)
    return seal_bytes(project_root, data, source=source, by=by, now=now, ai_root=ai_root,
                      app_version=app_version, reader_kind=reader_kind)


def _entry(rec: Mapping, recs: Iterable[dict], first_lock: str | None) -> SealEntry:
    e = SealEntry(sha256=rec["sha256"], size=rec.get("size", 0), sealed_at=rec.get("at", ""),
                  sealed_by=rec.get("by", ""), source=rec.get("source", ""), check=rec.get("check", ""),
                  reject_reasons=list(rec.get("reject_reasons") or []))
    e.presealed = _is_presealed(e.sealed_at, first_lock)
    recs = list(recs)
    u = _unsealed_record(recs, e.sha256)
    if u is not None:
        e.unsealed_at, e.run_id, e.conditions_met = u.get("at"), u.get("run_id"), u.get("conditions_met")
        ev = u.get("engine_version")
        e.engine_version = ev if isinstance(ev, str) and ev else None
    for spec in _PAIRS:
        p = _pair_record(recs, e.sha256, spec.kind)
        setattr(e, f"{spec.name}_sha256", p.get("sha256") if p is not None else None)
        if p is not None and p.get("attached") is True:
            e.pairs_attached.append(spec.name)
    return e


def _pair_record(recs: Iterable[dict], pair: str, kind: str) -> dict | None:
    """pair(ai-result sha256)에 짝지은 kind 봉인 가운데 검사를 통과한 마지막 것."""
    best = None
    for r in recs:
        if r.get("kind") == kind and r.get("pair") == pair and r.get("check") == "valid" \
                and isinstance(r.get("sha256"), str):
            best = r
    return best


# ----------------------------------------------------------------------------- 주제 흐름 짝 봉인 (D-17 5)
def topics_check(data: bytes, project_data: Mapping | None) -> tuple[str, list[str]]:
    """주제 흐름 바이트 검사 → ('valid'|'rejected', 거부 코드). 내용(키워드·시각)은 돌려주지 않는다."""
    from .core import topics as T
    try:
        doc = load_json_strict(bytes(data), TOPICS_MAX_BYTES)
    except StrictJsonError:
        return "rejected", ["topics_json"]
    if T.validate(doc):
        return "rejected", ["topics_format"]
    codes: list[str] = []
    want = str(((project_data or {}).get("broadcast") or {}).get("video_no") or "") or None
    got = (doc.get("target") or {}).get("video_no")
    if want is not None and got is not None and str(got) != want:
        codes.append("topics_video_mismatch")
    return ("rejected" if codes else "valid"), codes


def seal_topics_bytes(project_root: str, data: bytes, *, pair_sha256: str, by: str, source: str = "file",
                      now: datetime | None = None, ai_root: str | None = None, app_version: str | None = None,
                      reader_kind: str | None = None) -> SealEntry:
    """주제 흐름을 ai-result 봉인 하나(pair_sha256)의 짝으로 봉인한다. 짝이 없거나 이미 풀렸으면 SealError
    (풀린 결과에 나중에 붙이면 봉인 없이 보이게 되므로). 같은 바이트를 다시 받으면 새 줄을 쓰지 않는다.
    돌려주는 SealEntry 의 run_id·unsealed_at 은 비어 있고, sha256·size·check 만 뜻이 있다."""
    return _seal_pair_bytes(_SPEC_TOPICS, project_root, data, pair_sha256=pair_sha256, by=by, source=source,
                            now=now, ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


def seal_topics_file(project_root: str, path: str, *, pair_sha256: str, by: str, now: datetime | None = None,
                     ai_root: str | None = None, app_version: str | None = None,
                     reader_kind: str | None = None) -> SealEntry:
    """받은 주제 흐름 파일을 읽어 seal_topics_bytes(source='file'). 원본을 지우는 것은 부르는 쪽 몫이다."""
    return _seal_pair_file(_SPEC_TOPICS, project_root, path, pair_sha256=pair_sha256, by=by, now=now,
                           ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


def _topics_problems(doc) -> list:
    from .core import topics as T
    return list(T.validate(doc) or [])


# ----------------------------------------------------------------------------- 시각별 전사 낱말 짝 봉인 (D-20 3)
def words_check(data: bytes, project_data: Mapping | None) -> tuple[str, list[str]]:
    """시각별 전사 낱말 바이트 검사(app.core.words_doc — 엔진과 같은 규칙) → ('valid'|'rejected', 거부 코드).
    내용(낱말·시각)은 돌려주지 않는다. 방송 번호·project_id 가 이 프로젝트와 다르면 거부한다."""
    from .core import words_doc as WD
    try:
        doc = load_json_strict(bytes(data), WORDS_MAX_BYTES)
    except StrictJsonError:
        return "rejected", ["words_json"]
    if WD.validate(doc):
        return "rejected", ["words_format"]
    codes: list[str] = []
    pd = project_data or {}
    tgt = doc.get("target") or {}
    want = str((pd.get("broadcast") or {}).get("video_no") or "") or None
    got = tgt.get("video_no")
    if want is not None and got is not None and str(got) != want:
        codes.append("words_video_mismatch")
    want_pid = pd.get("project_id") if isinstance(pd.get("project_id"), str) else None
    got_pid = tgt.get("project_id")
    if want_pid is not None and got_pid is not None and got_pid != want_pid:
        codes.append("words_project_mismatch")
    return ("rejected" if codes else "valid"), codes


def seal_words_bytes(project_root: str, data: bytes, *, pair_sha256: str, by: str, source: str = "file",
                     now: datetime | None = None, ai_root: str | None = None, app_version: str | None = None,
                     reader_kind: str | None = None) -> SealEntry:
    """시각별 전사 낱말을 ai-result 봉인 하나(pair_sha256)의 짝으로 봉인한다(seal_topics_bytes 와 같은 규칙과 인자).
    짝이 없거나 이미 풀렸으면 SealError(풀린 결과에 나중에 붙이면 봉인 없이 보이게 되므로). 같은 바이트를 다시 받으면
    새 줄을 쓰지 않는다. 돌려주는 SealEntry 의 run_id·unsealed_at 은 비어 있고, sha256·size·check 만 뜻이 있다."""
    return _seal_pair_bytes(_SPEC_WORDS, project_root, data, pair_sha256=pair_sha256, by=by, source=source,
                            now=now, ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


def seal_words_file(project_root: str, path: str, *, pair_sha256: str, by: str, now: datetime | None = None,
                    ai_root: str | None = None, app_version: str | None = None,
                    reader_kind: str | None = None) -> SealEntry:
    """받은 전사 낱말 파일을 읽어 seal_words_bytes(source='file'). 원본을 지우는 것은 부르는 쪽 몫이다."""
    return _seal_pair_file(_SPEC_WORDS, project_root, path, pair_sha256=pair_sha256, by=by, now=now,
                           ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


def _words_problems(doc) -> list:
    from .core import words_doc as WD
    return list(WD.validate(doc) or [])


# ----------------------------------------------------------------------------- 창별 주제 벡터 짝 봉인 (23 5-2 의 2)
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _vectors_hash(windows) -> str:
    """engine.topic_vectors.vectors_sha256 과 같은 식: [[start_ms, end_ms, v], …] 의 공백 없는 ASCII JSON 의 sha256."""
    rows = [[w["start_ms"], w["end_ms"], w["v"]] for w in windows]
    b = json.dumps(rows, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(b.encode("ascii")).hexdigest()


def _vectors_problems(doc) -> list[str]:
    """topic-vectors 문서의 앱 쪽 검사 → 문제 목록(값·수는 넣지 않는다). 칸 모양(모르는 칸 금지 — 창 글이 끼어들지 않게),
    schema·판·시각 축, target 칸, 창마다 칸·시각 순서, 벡터 해시, viewer_score.vectors_from_doc 이 모든 창을 읽는지."""
    from .core import viewer_score as VS
    if not isinstance(doc, Mapping):
        return ["not_object"]
    p: list[str] = []
    if set(doc) != set(VECTORS_TOP_KEYS):
        p.append("top_keys")
        return p
    if doc["schema"] != VS.TOPIC_VECTORS_SCHEMA or doc["schema_version"] != VS.TOPIC_VECTORS_SCHEMA_VERSION:
        p.append("schema")
    if doc["time_axis"] != "vod_ms":
        p.append("time_axis")
    tg = doc["target"]
    if not isinstance(tg, Mapping) or set(tg) != set(VECTORS_TARGET_KEYS) \
            or not (tg.get("asr_sha256") is None or (isinstance(tg.get("asr_sha256"), str)
                                                     and SHA256_RE.match(tg["asr_sha256"]))):
        p.append("target")
    if not isinstance(doc["calibration"], Mapping) or not isinstance(doc["engine"], Mapping) \
            or not isinstance(doc["model"], Mapping) or not isinstance(doc["stats"], Mapping):
        p.append("meta")
    W, enc, dim = doc["window_ms"], doc["encoding"], doc["dim"]
    if not _is_int(W) or W <= 0 or enc not in VS.VEC_ENCODINGS or not _is_int(dim) or not 0 <= dim <= VS.VEC_MAX_DIM:
        p.append("shape")
    wins = doc["windows"]
    if not isinstance(wins, list):
        p.append("windows")
        return p
    if p:
        return p
    prev = -1
    for w in wins:
        if not isinstance(w, Mapping) or set(w) != set(VECTORS_WINDOW_KEYS) or not _is_int(w["start_ms"]) \
                or not _is_int(w["end_ms"]) or w["start_ms"] < 0 or w["start_ms"] % W \
                or w["end_ms"] != w["start_ms"] + W or w["start_ms"] <= prev:
            p.append("window_rows")
            return p
        prev = w["start_ms"]
    vs = doc["vectors_sha256"]
    try:
        h = _vectors_hash(wins)
    except (TypeError, ValueError):
        h = None
    if not isinstance(vs, str) or h != vs:
        p.append("vectors_sha256")
    if len(VS.vectors_from_doc(doc)) != len(wins):
        p.append("vectors_decode")
    return p


def vectors_check(data: bytes, project_data: Mapping | None) -> tuple[str, list[str]]:
    """창별 주제 벡터 바이트 검사 → ('valid'|'rejected', 거부 코드: vectors_json · vectors_format ·
    vectors_video_mismatch · vectors_project_mismatch). 내용(수·시각)은 돌려주지 않는다(words_check 와 같은 규칙)."""
    try:
        doc = load_json_strict(bytes(data), VECTORS_MAX_BYTES)
    except StrictJsonError:
        return "rejected", ["vectors_json"]
    if _vectors_problems(doc):
        return "rejected", ["vectors_format"]
    codes: list[str] = []
    pd = project_data or {}
    tgt = doc.get("target") or {}
    want = str((pd.get("broadcast") or {}).get("video_no") or "") or None
    got = tgt.get("video_no")
    if want is not None and got is not None and str(got) != want:
        codes.append("vectors_video_mismatch")
    want_pid = pd.get("project_id") if isinstance(pd.get("project_id"), str) else None
    got_pid = tgt.get("project_id")
    if want_pid is not None and got_pid is not None and got_pid != want_pid:
        codes.append("vectors_project_mismatch")
    return ("rejected" if codes else "valid"), codes


def seal_vectors_bytes(project_root: str, data: bytes, *, pair_sha256: str, by: str, source: str = "file",
                       now: datetime | None = None, ai_root: str | None = None, app_version: str | None = None,
                       reader_kind: str | None = None) -> SealEntry:
    """창별 주제 벡터를 ai-result 봉인 하나(pair_sha256)의 짝으로 봉인한다(seal_words_bytes 와 같은 규칙과 인자 —
    tools/engine_to_ai --vectors --seal 이 찾는 이름). 짝이 없거나 이미 풀렸으면 SealError. 같은 바이트를 다시 받으면
    새 줄을 쓰지 않는다. 돌려주는 SealEntry 의 run_id·unsealed_at 은 비어 있고, sha256·size·check 만 뜻이 있다."""
    return _seal_pair_bytes(_SPEC_VECTORS, project_root, data, pair_sha256=pair_sha256, by=by, source=source,
                            now=now, ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


def seal_vectors_file(project_root: str, path: str, *, pair_sha256: str, by: str, now: datetime | None = None,
                      ai_root: str | None = None, app_version: str | None = None,
                      reader_kind: str | None = None) -> SealEntry:
    """받은 주제 벡터 파일을 읽어 seal_vectors_bytes(source='file'). 원본을 지우는 것은 부르는 쪽 몫이다."""
    return _seal_pair_file(_SPEC_VECTORS, project_root, path, pair_sha256=pair_sha256, by=by, now=now,
                           ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


# ----------------------------------------------------------------------------- 짝 봉인 공통
@dataclass(frozen=True)
class _PairSpec:
    """짝 하나의 규칙. 크기 상한·검사 함수는 이름으로 두고 부를 때 이 모듈에서 찾는다(시험이 상한을 바꿀 수 있게)."""
    name: str                  # 기록 칸·해제 폴더 기준 이름·문제 코드 머리: topics · words · vectors
    kind: str                  # seal.jsonl 기록 종류
    folder: str                # 해제 폴더
    sources: tuple[str, ...]
    label: str                 # 메시지의 이름
    obj: str                   # 메시지의 '…을/를'
    max_name: str              # 크기 상한 상수 이름
    check_name: str            # 받을 때 검사 함수 이름 (data, project_data) → (check, codes)
    problems_name: str         # 풀 때 형식 검사 함수 이름 (doc) → 문제 목록

    @property
    def max_bytes(self) -> int:
        return globals()[self.max_name]

    def check(self, data: bytes, project_data):
        return globals()[self.check_name](data, project_data)

    def problems(self, doc) -> list:
        return globals()[self.problems_name](doc)


_SPEC_TOPICS = _PairSpec("topics", K_TOPICS_SEALED, TOPICS_DIR, TOPICS_SOURCE_KINDS, "주제 흐름", "주제 흐름을",
                         "TOPICS_MAX_BYTES", "topics_check", "_topics_problems")
_SPEC_WORDS = _PairSpec("words", K_WORDS_SEALED, WORDS_DIR, WORDS_SOURCE_KINDS, "전사 낱말", "전사 낱말을",
                        "WORDS_MAX_BYTES", "words_check", "_words_problems")
_SPEC_VECTORS = _PairSpec("vectors", K_VECTORS_SEALED, VECTORS_DIR, VECTORS_SOURCE_KINDS, "주제 벡터", "주제 벡터를",
                          "VECTORS_MAX_BYTES", "vectors_check", "_vectors_problems")
_PAIRS = (_SPEC_TOPICS, _SPEC_WORDS, _SPEC_VECTORS)      # unseal 이 푸는 순서
_PAIR_BY_NAME = {s.name: s for s in _PAIRS}
PAIR_KINDS = tuple(s.name for s in _PAIRS)              # ("topics", "words", "vectors")


def _seal_pair_bytes(spec: _PairSpec, project_root: str, data: bytes, *, pair_sha256: str, by: str, source: str,
                     now: datetime | None, ai_root: str | None, app_version: str | None,
                     reader_kind: str | None, extra: Mapping | None = None) -> SealEntry:
    """extra: 새 기록에 더할 칸(attach_pairs 의 attached·after_primary_lock). 같은 바이트가 이미 있으면 쓰지 않는다."""
    _guard(reader_kind, unseal=False)
    if source not in spec.sources:
        raise SealError(f"{spec.label} 출처 종류는 {spec.sources} 가운데 하나여야 합니다", code="source_kind")
    _check_by(by)
    if not isinstance(pair_sha256, str) or not SHA256_RE.match(pair_sha256):
        raise SealError("짝 sha256 형식이 아닙니다", code="pair_sha_format")
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 는 bytes 여야 합니다")
    data = bytes(data)
    limit = spec.max_bytes
    if len(data) > limit:
        raise SealError(f"{spec.label} 크기 상한({limit} 바이트)을 넘어 봉인하지 않습니다", code="too_large")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    with _seal_lock(d):                 # '짝이 아직 안 풀림' 확인과 쓰기 사이에 unseal 이 끼지 않게(풀린 뒤 붙는 짝이 없게)
        recs, probs = _read_records(d)
        if probs:
            raise SealError("seal.jsonl 이 깨져 새 기록을 쓰지 않습니다: " + "; ".join(probs[:3]),
                            code="seal_jsonl_corrupt")
        if _sealed_record(recs, pair_sha256) is None:
            raise SealError("짝 AI 결과 봉인이 없습니다 — 판정 결과를 먼저 봉인하세요", code="pair_missing")
        if _unsealed_record(recs, pair_sha256) is not None:
            raise SealError(f"짝 AI 결과가 이미 해제되어 {spec.obj} 붙이지 않습니다", code="pair_unsealed")
        sha = hashlib.sha256(data).hexdigest()
        old = [r for r in recs if r.get("kind") == spec.kind and r.get("sha256") == sha and r.get("pair") == pair_sha256]
        os.makedirs(os.path.join(d, SEALED_DIR), exist_ok=True)
        binp = os.path.join(d, SEALED_DIR, f"{sha}.bin")
        if not os.path.exists(binp):
            atomic_write(binp, data)
        if old:
            r = old[-1]
            return SealEntry(sha256=sha, size=r.get("size", 0), sealed_at=r.get("at", ""), sealed_by=r.get("by", ""),
                             source=r.get("source", ""), check=r.get("check", ""),
                             reject_reasons=list(r.get("reject_reasons") or []))
        chk, codes = spec.check(data, proj.data)
        rec = _append(d, {
            "kind": spec.kind, "at": _now_iso(now), "by": by, "app_version": _app_version(app_version),
            "sha256": sha, "size": len(data), "source": source, "pair": pair_sha256, "check": chk,
            "reject_reasons": codes, **dict(extra or {}),
        })
    e = SealEntry(sha256=sha, size=len(data), sealed_at=rec["at"], sealed_by=by, source=source, check=chk,
                  reject_reasons=codes)
    e.new = True
    return e


def _seal_pair_file(spec: _PairSpec, project_root: str, path: str, *, pair_sha256: str, by: str,
                    now: datetime | None, ai_root: str | None, app_version: str | None,
                    reader_kind: str | None) -> SealEntry:
    _guard(reader_kind, unseal=False)
    with builtins.open(path, "rb") as f:
        data = f.read(spec.max_bytes + 1)
    return _seal_pair_bytes(spec, project_root, data, pair_sha256=pair_sha256, by=by, source="file", now=now,
                            ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


# ----------------------------------------------------------------------------- 짝 보충 (D-23 3, 24 5-2 의 1)
ATTACH_SOURCES = ("engine", "file")      # 짝 보충 출처: 엔진을 이 자리에서 메모리로 돌림 · 받은 파일


@dataclass
class AttachResult:
    """attach_pairs 한 번의 결과. 내용(낱말·수·키워드)은 없다 — 해시·크기·검사·코드만."""
    pair_sha256: str                             # 짝을 붙인 판정 봉인
    rows: list[dict]                             # 짝마다 {kind, status, sha256, size, check, reject_reasons}
    presealed: bool                              # 판정 봉인의 사전 봉인 여부(짝 보충으로 바뀌지 않는다)
    after_primary_lock: bool                     # 이번 보충이 첫 1차 잠금 뒤인가(기록에도 남는다)

    @property
    def ok(self) -> bool:
        """모든 짝이 붙었거나(attached) 이미 있었다(present)."""
        return all(r["status"] in ("attached", "present") for r in self.rows)

    @property
    def attached(self) -> list[str]:
        return [r["kind"] for r in self.rows if r["status"] == "attached"]


def attach_pairs(project_root: str, pair_sha256: str, docs: Mapping[str, bytes], *, by: str,
                 source: str = "file", now: datetime | None = None, ai_root: str | None = None,
                 app_version: str | None = None, reader_kind: str | None = None) -> AttachResult:
    """**이미 있는 판정 봉인**(pair_sha256)에 짝(주제 흐름·전사 낱말·주제 벡터)을 나중에 붙인다(D-23 3: 실제 시범 봉인에
    짝이 없으면 잠긴 뒤·해제 전에 엔진으로 만들어 붙이고, 판정을 다시 봉인하지 않는다 — 다시 봉인하면 '사전 봉인' 이 아니게
    된다). 판정 봉인은 건드리지 않으므로 사전 봉인 여부는 그대로다.

    docs: {짝 종류(PAIR_KINDS): 바이트}. source: 'engine'(→ 기록의 출처 engine_<짝>) 또는 'file'.
    규칙
    - 판정 봉인이 없으면(pair_missing)·받을 때 거부됐으면(pair_not_valid)·이미 풀렸으면(pair_unsealed) SealError —
      아무것도 쓰지 않는다.
    - 짝마다: 검사를 통과한 같은 종류 짝이 이미 있으면 붙이지 않고 'present'(덮어쓰지 않는다 — 봉인은 한 번).
    - **먼저 모두 검사한다.** 하나라도 거부되면(크기·형식·방송 번호·project_id) 아무 짝도 붙이지 않고 그 짝을 'rejected'
      (나머지는 'skipped') 로 돌려준다 — 반쯤 붙은 상태가 생기지 않게. 거부 코드만 남고 내용은 남지 않는다.
    - 붙인 짝의 기록(<짝>.sealed)에는 보통 칸에 더해 attached = true, after_primary_lock(첫 1차 잠금 뒤인가)이 남는다.
      seal.jsonl 은 하위 호환이다(칸만 더함). 해제·읽기는 보통 짝과 같다(unseal 이 같은 해제에서 한 번에 푼다)."""
    _guard(reader_kind, unseal=False)
    if source not in ATTACH_SOURCES:
        raise SealError(f"짝 보충 출처는 {ATTACH_SOURCES} 가운데 하나여야 합니다", code="source_kind")
    _check_by(by)
    if not isinstance(pair_sha256, str) or not SHA256_RE.match(pair_sha256):
        raise SealError("짝 sha256 형식이 아닙니다", code="pair_sha_format")
    unknown = [k for k in docs if k not in _PAIR_BY_NAME]
    if unknown or not docs:
        raise SealError(f"짝 종류는 {PAIR_KINDS} 가운데 하나 이상이어야 합니다", code="pair_kind")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    # 확인(판정 봉인·안 풀림·이미 있는 짝)부터 짝을 모두 붙일 때까지 한 잠금 안에서 — 그 사이에 unseal 이 끼면 풀린 결과에
    # 짝이 붙는다. 안의 _seal_pair_bytes·_append 는 같은 스레드라 다시 잡지 않는다.
    with _seal_lock(d):
        return _attach_pairs_locked(proj, d, project_root, pair_sha256, docs, by=by, source=source, now=now,
                                    ai_root=ai_root, app_version=app_version, reader_kind=reader_kind)


def _attach_pairs_locked(proj, d: str, project_root: str, pair_sha256: str, docs: Mapping[str, bytes], *, by: str,
                         source: str, now: datetime | None, ai_root: str | None, app_version: str | None,
                         reader_kind: str | None) -> AttachResult:
    """attach_pairs 의 본체(seal.jsonl 잠금 안에서만 부른다)."""
    recs, probs = _read_records(d)
    if probs:
        raise SealError("seal.jsonl 이 깨져 새 기록을 쓰지 않습니다: " + "; ".join(probs[:3]), code="seal_jsonl_corrupt")
    srec = _sealed_record(recs, pair_sha256)
    if srec is None:
        raise SealError("짝 AI 결과 봉인이 없습니다 — 판정 결과를 먼저 봉인하세요", code="pair_missing")
    if srec.get("check") != "valid":
        raise SealError("받을 때 거부된 판정 봉인에는 짝을 붙이지 않습니다", code="pair_not_valid")
    if _unsealed_record(recs, pair_sha256) is not None:
        raise SealError("짝 AI 결과가 이미 해제되어 짝을 붙이지 않습니다", code="pair_unsealed")
    first = _first_primary_lock_at(_lock_docs(project_root))
    at = _now_iso(now)
    after = first is not None and not _is_presealed(at, first)
    presealed = _is_presealed(srec.get("at", ""), first)
    rows: list[dict] = []
    todo: list[tuple[_PairSpec, bytes]] = []
    for spec in _PAIRS:                                   # 늘 같은 순서(topics → words → vectors)
        if spec.name not in docs:
            continue
        data = docs[spec.name]
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("짝 자료는 bytes 여야 합니다")
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        row = {"kind": spec.name, "status": "", "sha256": sha, "size": len(data), "check": None, "reject_reasons": []}
        rows.append(row)
        have = _pair_record(recs, pair_sha256, spec.kind)
        if have is not None:
            row.update(status="present", sha256=have["sha256"], size=have.get("size", 0), check="valid")
            continue
        if len(data) > spec.max_bytes:
            row.update(status="rejected", check="rejected", reject_reasons=[f"{spec.name}_too_large"])
            continue
        chk, codes = spec.check(data, proj.data)
        row.update(check=chk, reject_reasons=list(codes))
        if chk != "valid":
            row["status"] = "rejected"
            continue
        todo.append((spec, data))
    if any(r["status"] == "rejected" for r in rows):
        for r in rows:
            if r["status"] == "":
                r["status"] = "skipped"
        return AttachResult(pair_sha256, rows, presealed, after)
    by_kind = {r["kind"]: r for r in rows}
    for spec, data in todo:
        src = spec.sources[0] if source == "engine" else "file"
        e = _seal_pair_bytes(spec, project_root, data, pair_sha256=pair_sha256, by=by, source=src, now=now,
                             ai_root=ai_root, app_version=app_version, reader_kind=reader_kind,
                             extra={"attached": True, "after_primary_lock": after})
        r = by_kind[spec.name]
        r.update(status="attached" if e.check == "valid" else "rejected", check=e.check,
                 reject_reasons=list(e.reject_reasons))
    return AttachResult(pair_sha256, rows, presealed, after)


def _unseal_pair(spec: _PairSpec, d: str, recs: list[dict], pair: str,
                 run_id: str) -> tuple[dict | None, str | None, str | None]:
    """짝 하나를 연다(unseal 안에서만 부른다). → (기록 칸 {sha256, file_sha256} 또는 None, 문제 코드 또는 None,
    짝 봉인 sha256 또는 None = 짝 없음). 짝 문제로 판정 해제를 막지 않는다 — 코드만 남기고 그 짝은 풀지 않는다."""
    t = _pair_record(recs, pair, spec.kind)
    if t is None:
        return None, None, None
    psha = t["sha256"]
    limit = spec.max_bytes
    try:
        with builtins.open(os.path.join(d, SEALED_DIR, f"{psha}.bin"), "rb") as f:
            raw = f.read(limit + 1)
    except OSError:
        return None, f"{spec.name}_unreadable", psha
    if hashlib.sha256(raw).hexdigest() != psha or len(raw) != t.get("size"):
        return None, f"{spec.name}_bin_mismatch", psha
    try:
        doc = load_json_strict(raw, limit)
    except StrictJsonError:
        return None, f"{spec.name}_json", psha
    if spec.problems(doc):
        return None, f"{spec.name}_format", psha
    out = (json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")
    os.makedirs(os.path.join(d, spec.folder), exist_ok=True)
    atomic_write(os.path.join(d, spec.folder, f"{run_id}.json"), out)
    return {"sha256": psha, "file_sha256": hashlib.sha256(out).hexdigest()}, None, psha


def _unsealed_or_blind(recs: Iterable[dict], run_id: str) -> dict:
    """run_id 의 마지막 ai.unsealed 기록. 없으면(해제 전) 파일을 열기 전에 SealNotUnsealedError."""
    u = [r for r in recs if r.get("kind") == K_UNSEALED and r.get("run_id") == run_id]
    if not u:
        raise SealNotUnsealedError("해제 기록이 없는 run_id 입니다(해제 전에는 열지 않습니다)")   # code not_unsealed
    return u[-1]


def _read_pair(spec: _PairSpec, project_root: str, run_id: str, *, ai_root: str | None,
               reader_kind: str | None) -> dict | None:
    _guard(reader_kind, unseal=True, unsealed_view=True)
    if not isinstance(run_id, str) or not air.RUN_ID_RE.match(run_id):
        raise SealError("run_id 형식이 아닙니다", code="run_id_format")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, _ = _read_records(d)
    u = _unsealed_or_blind(recs, run_id)
    t = u.get(spec.name)
    if not isinstance(t, Mapping) or not isinstance(t.get("file_sha256"), str):
        return None
    limit = spec.max_bytes
    with builtins.open(os.path.join(d, spec.folder, f"{run_id}.json"), "rb") as f:
        raw = f.read(limit + 1)
    if hashlib.sha256(raw).hexdigest() != t["file_sha256"]:
        raise SealError(f"해제한 {spec.label} 파일이 기록과 다릅니다", code="unsealed_mismatch")
    return load_json_strict(raw, limit)


def seal_status(project_root: str, *, ai_root: str | None = None, reader_kind: str | None = None) -> SealStatus:
    """seal.jsonl 과 잠금 파일만 읽는다. 봉인 파일(.bin)·runs 는 열지 않는다. 분석 세션도 된다(건수만 보인다)."""
    _guard(reader_kind, unseal=False, unsealed_view=True)
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, probs = _read_records(d)
    first = _first_primary_lock_at(_lock_docs(project_root))
    entries = [_entry(r, recs, first) for r in recs if r.get("kind") == K_SEALED and isinstance(r.get("sha256"), str)]
    return SealStatus(project_id=proj.project_id, entries=entries, first_primary_lock_at=first, problems=probs)


def unseal_events(project_root: str, *, ai_root: str | None = None,
                  reader_kind: str | None = None) -> tuple[list[dict], list[str]]:
    """seal.jsonl 의 ai.unsealed 줄 → ([{run_id, at, sha256, conditions_met, engine_version}], 깨진 줄 문제). 노출 계산
    (app.core.exposure 규칙 2)에 쓴다. seal.jsonl 만 읽는다(봉인 파일·runs 는 열지 않는다). 자리 규칙은 seal_status 와 같다.
    폴더가 없으면 ([], [])."""
    _guard(reader_kind, unseal=False, unsealed_view=True)
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, probs = _read_records(d)
    out = [{"run_id": r.get("run_id"), "at": r.get("at"), "sha256": r.get("sha256"),
            "conditions_met": r.get("conditions_met"),
            "engine_version": r.get("engine_version") if isinstance(r.get("engine_version"), str) else None}
           for r in recs if r.get("kind") == K_UNSEALED]
    return out, probs


# ----------------------------------------------------------------------------- 해제 조건
def _labeling_session_ids(project_root: str, locks) -> list[str]:
    """labels/ 의 파일 이름과 locks/ 이름에서 라벨링 세션(1차·재라벨) ID 를 얻는다. 내용은 열지 않는다."""
    out = {sid for sid, _h, _d in locks}
    try:
        names = os.listdir(os.path.join(project_root, "labels"))
    except OSError:
        names = []
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if m:
            out.add(m.group(1))
    return sorted(s for s in out if vis.session_kind_of_id(s) in vis.LABELING_SESSIONS)


def unseal_conditions(project_root: str, project_data: Mapping | None = None, *,
                      reader_kind: str | None = None) -> UnsealConditions:
    """06 7-1 해제 조건. 잠금·계획·표본 매니페스트만 연다(라벨 파일·봉인 파일은 열지 않는다).
    사유마다 codes 에 코드(UNSEAL_REASON_CODES)를 같은 차례로 둔다. 계획 파일이 깨진 것(plan_unreadable, ID 는
    unreadable_plans)은 '잠긴 재라벨 세션이 없음'(relabel_not_locked)과 따로 적는다 — 막는 판정(met)은 그대로다."""
    _guard(reader_kind, unseal=True)
    from . import agreement_report as ar
    from . import relabel as relabel_io
    root = os.path.abspath(project_root)
    data = project_data if project_data is not None else _project(root).data
    locks = _lock_docs(root)
    locked = {sid for sid, _h, _d in locks}
    sids = _labeling_session_ids(root, locks)
    reasons: list[str] = []
    codes: list[str] = []
    warnings: list[str] = []
    unreadable_plans: list[str] = []

    def unmet(code: str, text: str) -> None:
        codes.append(code)
        reasons.append(text)
    unlocked = [s for s in sids if s not in locked]
    if unlocked:
        unmet("unlocked_sessions", "잠기지 않은 라벨링 세션 " + ", ".join(unlocked))
    primaries = [s for s in sids if vis.session_kind_of_id(s) == vis.PRIMARY]
    if not primaries:
        unmet("no_primary", "1차 세션이 없습니다")
    plans = relabel_io.list_plans(root, include_cancelled=True)
    planned = {p.source_session_id for p in plans if p.source_session_id}
    no_plan = [s for s in primaries if s not in planned]
    if no_plan:
        unmet("no_relabel_plan", "재라벨 계획이 없는 1차 세션 " + ", ".join(no_plan))
    locked_sids = [s for s in sids if s in locked]
    if locked_sids:
        try:
            warnings += ar.comparison_gate(root, data, locked_sids)
        except ar.RelabelPendingError as e:
            # pending_relabel_plans 는 읽지 못한 계획(원 세션을 모름)도 늘 '아직' 으로 센다 — 사유는 둘로 가른다
            broken = {p.plan_id for p in plans if p.status == relabel_io.STATUS_UNREADABLE}
            wait = [p for p in e.plan_ids if p not in broken]
            bad = [p for p in e.plan_ids if p in broken]
            if wait:
                unmet("relabel_not_locked", "잠긴 재라벨 세션이 없는 계획 " + ", ".join(wait))
            if bad:
                unmet("plan_unreadable", "읽지 못한 재라벨 계획 " + ", ".join(bad) + " (계획 파일이 깨졌거나 형식이 아닙니다)")
                unreadable_plans += bad
        except ar.PlanUnreadableError as e:       # 잠긴 재라벨 세션의 계획 파일을 읽지 못함(SampleVerifyError 의 하나)
            unmet("plan_unreadable", str(e))
            unreadable_plans += [p for p in e.plan_ids if p not in unreadable_plans]
        except ar.SampleVerifyError as e:
            unmet("sample_verify", str(e))
        except ar.ReportError as e:
            unmet("other", str(e))
    return UnsealConditions(met=not reasons, reasons=reasons, warnings=warnings, labeling_sessions=sids, codes=codes,
                            unreadable_plans=unreadable_plans)


# ----------------------------------------------------------------------------- 해제 (봉인 파일을 여는 유일한 곳)
def unseal(project_root: str, sha256: str, *, by: str, now: datetime | None = None, ai_root: str | None = None,
           early_reason: str | None = None, app_version: str | None = None,
           reader_kind: str | None = None) -> UnsealResult:
    """봉인 하나를 푼다. 06 7-1 조건이 되지 않으면 SealBlockedError — early_reason(사유)을 주면 conditions_met=false 로
    기록하고 연다. 순서: 자리 검사 → 기록 확인 → 조건 → **seal.jsonl 잠금 → 기록 다시 확인**(그 사이 다른 프로세스가 풀었으면
    AlreadyUnsealedError — 차례로 두 번 부른 것과 같다) → 봉인 파일 열기(sha256 대조) → 다시 검사(채팅 ID 대조) →
    정규화 → runs\\<run_id>.json 쓰기 → 짝(주제 흐름·전사 낱말·주제 벡터)을 한 번에 풀기 → ai.unsealed 기록
    (engine_version·pairs 포함) → 잠금 풀기. 조건(오래 걸릴 수 있음)은 잠금 밖에서 본다."""
    _guard(reader_kind, unseal=True)
    if not isinstance(sha256, str) or not SHA256_RE.match(sha256):
        raise SealError("sha256 형식이 아닙니다", code="sha_format")
    _check_by(by)
    if early_reason is not None:
        early_reason = early_reason.strip()
        if not early_reason or len(early_reason) > REASON_MAX:
            raise SealError(f"앞당겨 여는 사유는 1~{REASON_MAX}자여야 합니다", code="reason_length")
    root = os.path.abspath(project_root)
    proj = _project(root)
    d = project_ai_dir(root, proj.project_id, ai_root)
    recs, probs = _read_records(d)
    if probs:
        raise SealError("seal.jsonl 이 깨져 해제하지 않습니다: " + "; ".join(probs[:3]), code="seal_jsonl_corrupt")
    srec = _sealed_record(recs, sha256)
    if srec is None:
        raise SealError("봉인 기록이 없는 해시입니다", code="no_seal_record")
    if _unsealed_record(recs, sha256) is not None:
        raise AlreadyUnsealedError("이미 해제한 봉인입니다")
    if srec.get("check") != "valid":
        raise SealError("받을 때 거부된 결과는 해제하지 않습니다: " + ",".join(srec.get("reject_reasons") or []),
                        code="rejected_at_seal")
    cond = unseal_conditions(root, proj.data, reader_kind=reader_kind)
    if not cond.met and early_reason is None:
        raise SealBlockedError(cond.reasons, cond.codes)
    # 채팅 ID 대조가 안 되면(채팅 파일이 없거나 한 줄도 읽지 못함) 정규화가 판정을 모두 '없는 chat_id' 로 빼고,
    # 해제는 한 번뿐이라 되돌릴 수 없다 — 기록을 쓰기 전에, 봉인 파일을 열기도 전에 멈춘다.
    chat_ids = air.project_chat_ids(root)
    if not chat_ids:
        raise SealError("프로젝트 채팅 파일(chat/messages.jsonl)이 없거나 읽을 수 있는 줄이 없어 해제하지 않습니다 "
                        "— 채팅 파일을 되살린 뒤 다시 하세요", code="messages_missing")
    # ---- 여기서부터 ai.unsealed 를 쓸 때까지 seal.jsonl 잠금을 쥔다. 위 확인은 잠금 밖이라 그 사이 다른 프로세스(앱·
    # tools/ai_seal.py·pilot_unseal_day --go)가 같은 봉인을 풀었을 수 있다 — 기록을 다시 읽어 확인한다(늦은 쪽은 봉인 파일을
    # 열지도 않는다). 짝·run_id 확인도 새로 읽은 기록으로 한다(그 사이 붙은 짝은 attach_pairs 가 같은 잠금 안에서 썼다).
    with _seal_lock(d):
        recs, probs = _read_records(d)
        if probs:
            raise SealError("seal.jsonl 이 깨져 해제하지 않습니다: " + "; ".join(probs[:3]), code="seal_jsonl_corrupt")
        if _unsealed_record(recs, sha256) is not None:
            raise AlreadyUnsealedError("이미 해제한 봉인입니다")
        # ---- 여기서부터 봉인 파일을 연다
        binp = os.path.join(d, SEALED_DIR, f"{sha256}.bin")
        try:
            with builtins.open(binp, "rb") as f:
                data = f.read(air.MAX_BYTES + 1)
        except OSError:
            raise SealError("봉인 파일을 읽지 못했습니다", code="sealed_unreadable") from None
        if hashlib.sha256(data).hexdigest() != sha256 or len(data) != srec.get("size"):
            raise SealError("봉인 파일이 기록과 다릅니다(바뀌었거나 손상)", code="sealed_mismatch")
        doc = air.parse_bytes(data)
        chk = air.check_doc(doc, proj.data, chat_ids=chat_ids)
        if not chk.ok:
            raise SealError("지금 프로젝트와 대조하면 거부됩니다: " + ",".join(chk.rejects), code="recheck_rejected")
        norm, info = air.normalize(doc, proj.data, chat_ids=chat_ids)
        dropped = sum(info.dropped_unknown.values())
        kept = sum(len(arr) for _k, arr in air._arrays(norm))
        if dropped and not kept:
            raise SealError(f"판정 {dropped}건이 모두 이 프로젝트 채팅에 없는 chat_id 라 해제하지 않습니다 "
                            "— 채팅 파일이 다른 방송이거나 손상됐는지 확인하세요 (06 6-2)", code="all_dropped")
        run_id = norm["run_id"]
        if not air.RUN_ID_RE.match(run_id):
            raise SealError("run_id 형식이 아닙니다", code="run_id_format")
        for r in recs:
            if r.get("kind") == K_UNSEALED and r.get("run_id") == run_id and r.get("sha256") != sha256:
                raise SealError(f"같은 run_id({run_id})의 다른 결과가 이미 해제되었습니다", code="dup_run_id")
        run_bytes = (json.dumps(norm, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")
        os.makedirs(os.path.join(d, RUNS_DIR), exist_ok=True)
        run_path = os.path.join(d, RUNS_DIR, f"{run_id}.json")
        atomic_write(run_path, run_bytes)
        # 짝은 같은 unseal 안에서 한 번에 푼다. 짝이 있을 때만 그 칸(<짝>·<짝>_error)을 쓴다 — 지난 판 기록과 같은 모양
        extra: dict = {}
        pairs: list[dict] = []
        for spec in _PAIRS:
            prec, perr, psha = _unseal_pair(spec, d, recs, sha256, run_id)
            if psha is None:
                continue
            extra[spec.name] = prec
            extra[f"{spec.name}_error"] = perr
            pairs.append({"kind": spec.name, "sha256": psha,
                          "file_sha256": prec["file_sha256"] if prec is not None else None, "error": perr})
        eng = norm.get("engine") if isinstance(norm.get("engine"), Mapping) else {}
        engine_version = eng.get("version") if isinstance(eng.get("version"), str) and eng.get("version") else None
        locks = _lock_docs(root)
        first = _first_primary_lock_at(locks)
        presealed = _is_presealed(srec.get("at", ""), first)
        rec = _append(d, {
            "kind": K_UNSEALED, "at": _now_iso(now), "by": by, "app_version": _app_version(app_version),
            "sha256": sha256, "run_id": run_id,
            "locks": [{"session_id": sid, "sha256": h, "locked_at": doc_.get("locked_at")} for sid, h, doc_ in locks],
            "conditions_met": cond.met, "reason": early_reason if not cond.met else None,
            "unmet": [] if cond.met else cond.reasons,
            "presealed": presealed, "first_primary_lock_at": first,
            "time_axis_in": info.time_axis_in, "calibration_rev": info.calibration_rev,
            "warnings": list(chk.warnings), "gate_warnings": len(cond.warnings),
            "dropped_unknown": info.dropped_unknown, "dropped_duplicate": info.dropped_duplicate,
            "run_sha256": hashlib.sha256(run_bytes).hexdigest(), "engine_version": engine_version, "pairs": pairs,
            **extra,
        })
    return UnsealResult(sha256=sha256, run_id=run_id, run_path=run_path, conditions_met=cond.met,
                        presealed=presealed, record=rec)


def read_run(project_root: str, run_id: str, *, ai_root: str | None = None, reader_kind: str | None = None) -> dict:
    """해제한 결과 runs\\<run_id>.json 을 읽는다. ai.unsealed 기록이 없으면(해제 전) 열지 않고 SealNotUnsealedError
    (SealBlindError). 파일 sha256 이 기록의 run_sha256 과 다르면 SealError. 분석 세션도 된다(해제된 것만,
    visibility UNSEALED_ONLY)."""
    _guard(reader_kind, unseal=True, unsealed_view=True)
    if not isinstance(run_id, str) or not air.RUN_ID_RE.match(run_id):
        raise SealError("run_id 형식이 아닙니다", code="run_id_format")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, _ = _read_records(d)
    u = _unsealed_or_blind(recs, run_id)
    with builtins.open(os.path.join(d, RUNS_DIR, f"{run_id}.json"), "rb") as f:
        raw = f.read(air.MAX_BYTES + 1)
    if hashlib.sha256(raw).hexdigest() != u.get("run_sha256"):
        raise SealError("해제한 결과 파일이 기록과 다릅니다", code="unsealed_mismatch")
    return air.parse_bytes(raw)


def read_topics(project_root: str, run_id: str, *, ai_root: str | None = None,
                reader_kind: str | None = None) -> dict | None:
    """해제한 run 의 주제 흐름 topics\\<run_id>.json. 해제 기록이 없으면(해제 전) 열지 않고 SealNotUnsealedError,
    짝 주제 흐름이 없었으면 None. 파일 sha256 이 기록과 다르면 SealError. 자리 규칙은 read_run 과 같다(분석 세션도 됨)."""
    return _read_pair(_SPEC_TOPICS, project_root, run_id, ai_root=ai_root, reader_kind=reader_kind)


def read_words(project_root: str, run_id: str, *, ai_root: str | None = None,
               reader_kind: str | None = None) -> dict | None:
    """해제한 run 의 시각별 전사 낱말 words\\<run_id>.json(read_topics 와 같은 규칙). 해제 기록이 없으면(해제 전) 열지 않고
    SealNotUnsealedError, 짝 전사 낱말이 없었으면 None. 파일 sha256 이 기록과 다르면 SealError. 분석 세션도 된다."""
    return _read_pair(_SPEC_WORDS, project_root, run_id, ai_root=ai_root, reader_kind=reader_kind)


def read_vectors(project_root: str, run_id: str, *, ai_root: str | None = None,
                 reader_kind: str | None = None) -> dict | None:
    """해제한 run 의 창별 주제 벡터 vectors\\<run_id>.json(read_topics 와 같은 규칙). 해제 기록이 없으면(해제 전) 열지
    않고 SealNotUnsealedError, 짝 벡터가 없었으면(옛 기록 포함) None. 파일 sha256 이 기록과 다르면 SealError.
    분석 세션도 된다. 풀기는 app.core.viewer_score.vectors_from_doc."""
    return _read_pair(_SPEC_VECTORS, project_root, run_id, ai_root=ai_root, reader_kind=reader_kind)
