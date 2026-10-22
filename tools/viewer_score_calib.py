"""시청자 훈련(G) 채점 보정 자료 — 손라벨에서 '반응받은 채팅' 과 같은 구간의 다른 채팅의 세 축 분포 (22 7-1 의 1·2, D-21 1·4).

무엇을 재나
  손라벨(`labels\\manual-*.csv`)이 '스트리머가 직접 반응했다' 고 적은 채팅(반응받음)과, 같은 구간(반응받은 채팅의 첫 시각 ~
  끝 시각)의 다른 일반 채팅(반응 없음)을 채점기(app/core/viewer_score.py)로 매겨 축마다 good·ok·weak 비율을 낸다.
  '반응받은 쪽에서 good 비율이 더 높은가' 를 숫자(차이 %p, 피셔 정확 검정 양쪽 p)로 보고, good 의 **lift**(반응받음 good 률
  ÷ 반응 없음 good 률)와 **정밀도**(good 가운데 반응받은 쪽 비율)도 낸다. 문턱 후보(candidates)는 정밀도 우선(D-21 2)에
  맞춰 lift 가 base 보다 높은 판만 고른다(good 차이는 보조 — 문턱을 느슨하게 해 양쪽 good 이 함께 늘어도 커지므로).
  base 판에는 주제 흐름 누출 크기(flow_presend: flow_hit 가운데 겹친 낱말이 보낸 시각 앞 전사에도 있는 수)도 센다.
  같은 시각 근처(±near 초)의
  다른 채팅만 따로 묶은 '가까운 반응 없음' 과, 낱말(2자 이상)이 하나라도 있는 채팅만 묶은 '낱말 있는 반응 없음'
  ('ㅋㅋ'·이모티콘만인 채팅이 차이를 부풀리지 않는지 보려고)도 낸다.
  문턱 몇 개를 바꿔 본 판(변형)과, 22 5-2 의 3(flow_head2: 주제 흐름 2자 머리 허용)·4(habit_soft_ok_only: 끝 표지·흔한
  첫 낱말은 ok 까지만)를 켜고 끈 차이도 낸다. **기본값은 바꾸지 않는다** — 표본 30건이라 '후보' 로만 적는다.

채점 방법(실제 시청자 채팅을 '연습 채팅' 처럼 본다)
  - 보낸 시각 = 그 채팅의 VOD 시각(messages.jsonl 의 t_ms — 손라벨 chat_vod_ms 와 같은 축).
  - 맥락 = 주제 흐름 덩어리(--topics 파일 또는 해제된 run) + VOD 로 보정한 전사 낱말(derived\\asr-words.json, 상태 ok 일 때만)
    (--topics 는 <P>\\derived\\asr-topic-flow.json 처럼 보호 구역 안. 프로젝트 안의 보호 구역 밖 파일은 거부, 03 2-1)
    + 그 시각 앞 60초의 다른 시청자 채팅(같은 시청자의 글과 자기 자신은 뺀다 — 자기 글은 history 로 본다).
  - history = 같은 시청자가 앞 10분 안에 쓴 일반 채팅(오래된 것 → 최근).
  - 일반 채팅 = kind text · status NORMAL. 맥락 채팅에는 후원 글도 넣는다(화면에 보이는 글).

읽기만 한다: project.json, labels\\manual-*.csv(chat_id·chat_vod_ms·grade 칸만, note 칸은 읽지 않는다), chat\\messages.jsonl,
derived\\asr-words.json, 주제 흐름 파일. 아무것도 쓰지 않는다(--out 을 주면 그 파일 하나만).
출력은 **숫자·코드만**이다: 채팅 본문·닉네임·viewer_id·chat_id·낱말은 표준 출력·결과 파일·로그 어디에도 넣지 않는다.
시범 프로젝트(pilot-* 또는 시범 기록에 적힌 것)는 손라벨 길로는 열지 않고 거부한다(D-15 8, D-17 4).

시범 최종 세트 길 (--final-set, D-22 1·2, 23 6장 '나')
  손라벨 CSV 대신 **사람 최종 세트**(app.export.build_final_set — 잠긴 1차 + 재라벨 + 조정 기록)를 반응받음의 기준으로 쓴다.
  - --final-set <zip>        export_final_set 이 만든 zip(export_type ground_truth_lenient). manifest.json 의 project_id 가
                             --project 와 같아야 한다. labels.jsonl(참고 점수 행)·ambiguous.jsonl 만 읽는다.
  - --final-set auto | <1차 세션 ID>   그 프로젝트에서 바로 만든다(세션 밖 읽기 — 잠긴 세션만, 06 7-1 조건을 라벨보다 먼저).
  반응받음 = set=final · reaction · graded · grade **direct 만**(기본, --grades 로 바꿈) · eval.<--final-eval>(기본 lenient).
  반응 없음(대조군) = 같은 구간의 다른 일반 채팅 가운데 최종 세트 어느 행에도 없는 것 + 최종 구간 조각(set=final) 안의 것.
  **사람이 '반응 미확인' 으로 본 채팅**(result unconfirmed, 같은 eval)은 따로 '확인된 반응 없음'(labelled_none) 묶음으로 낸다.
  zip 에는 평가 제외(exclude_from_eval) 행이 없어 그 채팅은 반응 없음 쪽에 섞일 수 있다(건수만 info 에 '모름' 으로 적는다).
  시범 프로젝트는 이 길로만, **시범 세션이 모두 잠긴 뒤**(reference_set.pilot_sessions_locked = seal.unseal_conditions 와 같은
  판단)에만 연다. 맥락(주제 흐름·전사 낱말)은 시범이면 **해제된 run 의 짝**(seal.read_topics · seal.read_words)에서만 읽는다
  (derived\\asr-words.json 같은 평문 자리는 보지 않는다). --run-id 로 고르고, 없으면 전사 낱말 짝이 있는 가장 늦게 푼 run.

여러 프로젝트 묶기: pool_reports(결과 목록) 가 묶음·판별 건수를 더해 같은 숫자(분포·lift·정밀도·p·후보)를 다시 낸다.

사용(저장소 루트, 앱 .venv)
  python tools/viewer_score_calib.py --project <P> [--topics <topic-flow.json>] [--labels <csv>]
         [--grades direct,likely] [--near-sec 30] [--out <결과.json>] [--quick]
  python tools/viewer_score_calib.py --project <pilot> --final-set <zip|auto|1차 세션 ID> [--final-eval lenient|strict]
         [--run-id <해제된 run>] [--ai-root <폴더>] [--out <결과.json>] [--quick]
종료 코드: 0 정상 · 1 거부(시범·손라벨 없음·잠기지 않음) · 2 입력 오류
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import time
import zipfile
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import viewer_score as vs  # noqa: E402

TOOL = "viewer_score_calib"
TOOL_VERSION = "0.2"
DEFAULT_GRADES = ("direct", "likely")
FINAL_GRADES = ("direct",)              # 시범 최종 세트는 직접 반응만(D-22 1, 23 5-1 의 19)
FINAL_EVALS = ("lenient", "strict")
NONE_CODE = "unconfirmed"               # 최종 세트에서 사람이 '반응 미확인' 으로 본 채팅
EXCLUDED_CODE = "_excluded"             # 최종 세트 행이지만 평가에 들지 않는 것(대조군에서도 뺀다)
FINAL_EXPORT_TYPE = "ground_truth_lenient"
ZIP_MEMBER_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_NEAR_SEC = 30
HISTORY_SPAN_MS = 10 * 60_000          # history: 같은 시청자가 앞 10분 안에 쓴 글
HISTORY_MAX = 50
CTX_CHAT_SPAN_MS = 60_000              # 맥락 채팅: 앞 60초(copy_window 최대값)
MESSAGES_MAX_BYTES = 512 * 1024 * 1024
CSV_MAX_BYTES = 16 * 1024 * 1024
SCORE_KIND = "text"
VISIBLE_KINDS = ("text", "donation")
VISIBLE_STATUS = "NORMAL"

G_REACTED = "reacted"
G_OTHERS = "others"
G_NEAR = "others_near"
G_TEXT = "others_text"
G_DIRECT = "reacted_direct"
G_NONE = "labelled_none"               # 최종 세트에서 사람이 '반응 미확인' 으로 본 채팅(손라벨 길에서는 비어 있음)
GROUPS = (G_REACTED, G_OTHERS, G_NEAR, G_TEXT)
ALL_GROUPS = GROUPS + (G_DIRECT, G_NONE)

#: 바꿔 볼 판: (이름, 보는 축, guide-config values.viewer_score 칸, 맥락). 맥락 full = 주제 흐름 + 전사, no_flow = 전사만.
#: 축이 None 이면 세 축 모두를 본다. 값은 settings_from_values 로 검사한다(범위 밖이면 기본값 + 메모).
VARIANTS: tuple[tuple[str, str | None, dict, str], ...] = (
    ("base", None, {}, "full"),
    ("no_flow", "topic", {}, "no_flow"),
    ("flow_head2", "topic", {"flow_head2": True}, "full"),
    ("habit_soft", "repeat", {"habit_soft_ok_only": True}, "full"),
    ("head2+soft", None, {"flow_head2": True, "habit_soft_ok_only": True}, "full"),
    ("topic_lookback_60", "topic", {"topic_lookback_sec": 60}, "full"),
    ("topic_lookback_180", "topic", {"topic_lookback_sec": 180}, "full"),
    ("transcript_lookback_60", "topic", {"transcript_lookback_sec": 60}, "full"),
    ("transcript_lookback_120", "topic", {"transcript_lookback_sec": 120}, "full"),
    ("good_talk_hits_1", "topic", {"topic_good_talk_hits": 1}, "full"),
    ("good_talk_hits_3", "topic", {"topic_good_talk_hits": 3}, "full"),
    ("good_flow_hits_2", "topic", {"topic_good_flow_hits": 2}, "full"),
    ("concrete_words_1", "expand", {"expand_concrete_words": 1}, "full"),
    ("concrete_words_3", "expand", {"expand_concrete_words": 3}, "full"),
    ("min_chars_2", "expand", {"expand_min_chars": 2}, "full"),
    ("min_chars_6", "expand", {"expand_min_chars": 6}, "full"),
    ("max_chars_40", "expand", {"expand_max_chars": 40}, "full"),
    ("copy_window_30", "repeat", {"copy_window_sec": 30}, "full"),
    ("copy_window_60", "repeat", {"copy_window_sec": 60}, "full"),
    ("copy_weak_70", "repeat", {"copy_weak_pct": 70}, "full"),
    ("copy_weak_90", "repeat", {"copy_weak_pct": 90}, "full"),
    ("self_weak_50", "repeat", {"self_weak_pct": 50}, "full"),
    ("self_weak_70", "repeat", {"self_weak_pct": 70}, "full"),
    ("habit_weak_4", "repeat", {"habit_weak_min": 4}, "full"),
)
QUICK = ("base", "no_flow", "flow_head2", "habit_soft", "head2+soft")
#: 이 두 판은 '켜고 끈 차이'(단계가 바뀐 채팅 수)를 따로 센다(22 5-2 의 3·4)
TOGGLES = ("flow_head2", "habit_soft", "head2+soft")


class CalibError(RuntimeError):
    """사용자에게 보일 거부·입력 오류(문구에 본문·닉네임 없음)."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


# ───────────────────────────────────────────────────────────── 입력

@dataclass(frozen=True)
class Chat:
    """메모리에만 둔다. repr 에 본문·시청자를 보이지 않는다."""
    chat_id: str
    t_ms: int
    viewer: str
    text: str
    scorable: bool      # kind text · status NORMAL (채점 대상)
    visible: bool       # 맥락 채팅으로 화면에 보이는 글(text·donation, NORMAL)

    def __repr__(self) -> str:
        return f"Chat(t_ms={self.t_ms}, scorable={self.scorable})"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_messages(lines: Iterable[str | bytes]) -> list[Chat]:
    """messages.jsonl 줄 → Chat 목록(시각 순). 형식이 틀린 줄은 건너뛴다(개수만 센다)."""
    out: list[Chat] = []
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        cid, t, text = d.get("chat_id"), d.get("t_ms"), d.get("text")
        if not isinstance(cid, str) or not _is_int(t) or not isinstance(text, str):
            continue
        viewer = d.get("viewer_id")
        viewer = viewer if isinstance(viewer, str) and viewer else "?" + cid
        kind, status = d.get("kind"), d.get("status")
        out.append(Chat(cid, int(t), viewer, text, kind == SCORE_KIND and status == VISIBLE_STATUS,
                        kind in VISIBLE_KINDS and status == VISIBLE_STATUS))
    out.sort(key=lambda c: (c.t_ms, c.chat_id))
    return out


def read_messages(path: str) -> list[Chat]:
    if os.path.getsize(path) > MESSAGES_MAX_BYTES:
        raise CalibError("messages.jsonl 이 너무 큽니다")
    with open(path, "rb") as f:
        return parse_messages(f)


@dataclass(frozen=True)
class LabelRow:
    chat_id: str
    chat_vod_ms: int
    grade: str

    def __repr__(self) -> str:
        return f"LabelRow(grade={self.grade})"


def parse_manual_csv(text: str) -> list[LabelRow]:
    """손라벨 CSV → chat_id·chat_vod_ms·grade 만(다른 칸은 읽지 않는다). 머리줄에 세 칸이 없으면 CalibError."""
    rd = csv.DictReader(text.splitlines())
    need = ("chat_id", "chat_vod_ms", "grade")
    if not rd.fieldnames or any(k not in rd.fieldnames for k in need):
        raise CalibError("손라벨 CSV 머리줄에 chat_id·chat_vod_ms·grade 가 없습니다")
    rows: list[LabelRow] = []
    for r in rd:
        cid = (r.get("chat_id") or "").strip()
        grade = (r.get("grade") or "").strip()
        try:
            t = int((r.get("chat_vod_ms") or "").strip())
        except ValueError:
            continue
        if cid and grade:
            rows.append(LabelRow(cid, t, grade))
    return rows


def find_manual_csv(project: str) -> str | None:
    d = os.path.join(project, "labels")
    try:
        names = sorted(n for n in os.listdir(d) if n.lower().startswith("manual-") and n.lower().endswith(".csv"))
    except OSError:
        return None
    return os.path.join(d, names[0]) if names else None


def read_manual_csv(path: str) -> list[LabelRow]:
    with open(path, "rb") as f:
        raw = f.read(CSV_MAX_BYTES + 1)
    if len(raw) > CSV_MAX_BYTES:
        raise CalibError("손라벨 CSV 가 너무 큽니다")
    return parse_manual_csv(raw.decode("utf-8-sig"))


def read_topic_blocks(path: str) -> list[tuple[int, int, tuple[str, ...]]]:
    from app.core import topics as T
    with open(path, "rb") as f:
        doc = json.loads(f.read().decode("utf-8-sig"))
    try:
        blocks = T.blocks_from_doc(doc)
    except ValueError:
        raise CalibError("주제 흐름 파일 형식이 틀립니다") from None
    return [(int(b.start_ms), int(b.end_ms), tuple(b.keywords)) for b in blocks]


# ───────────────────────────────────────────────────────────── 시범 최종 세트 (D-22 1·2)

@dataclass
class FinalInput:
    """최종 세트에서 읽은 것. rows 는 (set, export-row) — 메모리에만 두고 결과에는 숫자(info)만 낸다."""
    rows: list[tuple[str, dict]]
    ranges: list[tuple[int, int]]
    info: dict

    def __repr__(self) -> str:
        return f"FinalInput(rows={len(self.rows)}, ranges={len(self.ranges)})"


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


def _piece_range(item: Mapping) -> tuple[int, int] | None:
    pc = item.get("piece") if isinstance(item.get("piece"), Mapping) else item.get("row") or {}
    a, b = pc.get("start_ms"), pc.get("end_ms")
    return (a, b) if _is_int(a) and _is_int(b) and b > a else None


def final_from_export(fx) -> FinalInput:
    """export.build_final_set 결과(FinalSetExport 또는 {items, segment_items, info} dict) → FinalInput.
    반응 행은 모든 세트(final·excluded·ambiguous)를 넣는다 — 평가에 들지 않는 행도 대조군에서 빼야 하므로."""
    items = fx.get("items") if isinstance(fx, Mapping) else getattr(fx, "items", None)
    segs = fx.get("segment_items") if isinstance(fx, Mapping) else getattr(fx, "segment_items", None)
    fi = (fx.get("info") if isinstance(fx, Mapping) else getattr(fx, "info", None)) or {}
    rows = [(str(it.get("set")), it["row"]) for it in items or () if isinstance(it, Mapping)
            and isinstance(it.get("row"), Mapping)]
    ranges = _merge_ranges(r for s in segs or () if isinstance(s, Mapping) and s.get("set") == "final"
                           for r in [_piece_range(s)] if r)
    info = {"source": "project", "excluded_rows_known": True,
            "relabel_sessions": len(fi.get("relabel_session_ids") or ()),
            "adjudication_sessions": len(fi.get("adjudication_session_ids") or ()),
            "warnings": len(fi.get("warnings") or ())}
    return FinalInput(rows, ranges, info)


def _zip_member(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        zi = z.getinfo(name)
    except KeyError:
        return None
    if zi.file_size > ZIP_MEMBER_MAX_BYTES:
        raise CalibError("최종 세트 zip 의 파일이 너무 큽니다")
    return z.read(zi)


def _jsonl_rows(raw: bytes | None) -> list[dict]:
    out = []
    for line in (raw or b"").decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            raise CalibError("최종 세트 zip 의 줄이 JSON 이 아닙니다") from None
        if isinstance(d, dict):
            out.append(d)
    return out


def final_from_zip(path: str, *, project_id: str | None = None) -> FinalInput:
    """export_final_set 이 만든 zip → FinalInput. manifest.json(export_type·project_id)·labels.jsonl·ambiguous.jsonl 만 읽는다.
    labels.jsonl 은 참고 점수 행(eval.lenient)만 들어 있다. 평가 제외 행은 zip 에 없다(info.excluded_rows_known = False)."""
    try:
        with zipfile.ZipFile(path) as z:
            man_raw = _zip_member(z, "manifest.json")
            if man_raw is None:
                raise CalibError("최종 세트 zip 에 manifest.json 이 없습니다")
            try:
                man = json.loads(man_raw.decode("utf-8-sig"))
            except ValueError:
                raise CalibError("최종 세트 zip 의 manifest.json 이 JSON 이 아닙니다") from None
            if not isinstance(man, dict) or man.get("export_type") != FINAL_EXPORT_TYPE:
                raise CalibError("최종 세트 zip 이 아닙니다(export_type ground_truth_lenient 아님)")
            if project_id is not None and man.get("project_id") != project_id:
                raise CalibError("최종 세트 zip 의 project_id 가 이 프로젝트와 다릅니다")
            good = _jsonl_rows(_zip_member(z, "labels.jsonl"))
            amb = _jsonl_rows(_zip_member(z, "ambiguous.jsonl"))
    except zipfile.BadZipFile:
        raise CalibError("최종 세트 파일이 zip 이 아닙니다") from None
    rows = [("final", r) for r in good if r.get("label_kind") == "reaction"]
    rows += [("ambiguous", r) for r in amb if r.get("label_kind") == "reaction"]
    ranges = _merge_ranges(r for row in good if row.get("label_kind") == "segment" for r in [_piece_range({"row": row})]
                           if r)
    info = {"source": "zip", "excluded_rows_known": False,
            "messages_sha256": man.get("messages_sha256") if isinstance(man.get("messages_sha256"), str) else None,
            "warnings": len(man.get("warnings") or ())}
    return FinalInput(rows, ranges, info)


def labels_from_final(fin: FinalInput, *, eval_mode: str = "lenient") -> tuple[list[LabelRow], dict]:
    """최종 세트 반응 행 → LabelRow(chat_id·채팅 시각·등급 코드). 등급 코드는 세트 final 이고 eval.<eval_mode> 인 행만
    등급(direct·likely·…) 또는 NONE_CODE(반응 미확인), 나머지는 EXCLUDED_CODE(대조군에서도 빠지게 이름만 남긴다)."""
    if eval_mode not in FINAL_EVALS:
        raise CalibError("--final-eval 은 lenient 또는 strict")
    from app.core.ids import CHAT_ID_RE
    out: list[LabelRow] = []
    counts = {"reaction_rows": 0, "bad_rows": 0, "codes": {}}
    for st, row in fin.rows:
        if row.get("label_kind") != "reaction":
            continue
        cid = row.get("chat_id")
        if not isinstance(cid, str) or not CHAT_ID_RE.match(cid):
            counts["bad_rows"] += 1
            continue
        counts["reaction_rows"] += 1
        t = row.get("chat_t_ms") if _is_int(row.get("chat_t_ms")) else -1
        ev = row.get("eval") if isinstance(row.get("eval"), Mapping) else {}
        ok = st == "final" and ev.get(eval_mode) is True
        res, grade = row.get("result"), row.get("grade")
        if ok and res == "graded" and isinstance(grade, str) and grade:
            code = grade
        elif ok and res == "unconfirmed":
            code = NONE_CODE
        else:
            code = EXCLUDED_CODE
        counts["codes"][code] = counts["codes"].get(code, 0) + 1
        out.append(LabelRow(cid, int(t), code))
    counts["codes"] = dict(sorted(counts["codes"].items()))
    return out, counts


def load_final_set(project: str, spec, *, ai_root: str | None = None) -> FinalInput:
    """spec: zip 경로 · 'auto' · 1차 세션 ID · 이미 만든 export.build_final_set 결과. 잠기지 않았으면 CalibError(1)."""
    if not isinstance(spec, str):
        return final_from_export(spec)
    if spec.lower().endswith(".zip") or os.path.isfile(spec):
        if not os.path.isfile(spec):
            raise CalibError("최종 세트 zip 이 없습니다")
        pid = None
        try:
            with open(os.path.join(project, "project.json"), "rb") as f:
                pid = json.loads(f.read().decode("utf-8-sig")).get("project_id")
        except (OSError, ValueError, AttributeError):
            raise CalibError("project.json 을 읽지 못했습니다") from None
        return final_from_zip(spec, project_id=pid if isinstance(pid, str) else None)
    from app import export
    from app.core.ids import SESSION_ID_RE
    primary = None
    if spec != "auto":
        m = SESSION_ID_RE.match(spec)
        if not m or m.group(2) != "P":
            raise CalibError("--final-set 은 zip 경로·auto·1차 세션 ID 가운데 하나")
        primary = spec
    try:
        fx = export.build_final_set(project, primary_session_id=primary, ai_root=ai_root)
    except Exception as e:  # noqa: BLE001 — 잠기지 않음(NotLockedError)·재현 검증 실패·형식 오류. 문구에 내용 없음
        raise CalibError(f"최종 세트를 만들지 못했습니다: {type(e).__name__}", 1) from None
    return final_from_export(fx)


# ───────────────────────────────────────────────────────────── 해제된 run 의 맥락 (시범)

def _active_calibration(pdata: Mapping | None):
    pj = (pdata or {}).get("calibration") if isinstance(pdata, Mapping) else None
    if not pj:
        return None
    try:
        from app.core.timecode import calibration_from_json
        return calibration_from_json(pj)
    except (KeyError, TypeError, ValueError):
        return False


def words_code(project: str, doc: Mapping | None, pdata: Mapping | None, *, pilot: bool | None = None) -> str:
    """해제된 전사 낱말(seal.read_words 결과)을 채점에 쓸 수 있나 — viewer_flow 의 상태 코드와 같은 이름(ok·bad·
    stale_textnorm·stale_asr·stale_calibration·default_calibration·unverified_calibration·missing). 낱말은 보지 않는다.
    pilot(None = 폴더 이름으로 판단): 시범이면 derived\\asr.json 을 열지 않고 asr 해시도 견주지 않는다 — 앱의 시범 길
    (viewer_flow.pilot_words: 해제된 run 의 짝만, derived\\ 는 열지 않음)과 같은 규칙. 짝 전사 낱말은 같은 엔진 실행에서
    봉인됐으므로 그 run 에는 맞는 전사다(지금 asr.json 이 다시 만들어졌어도 보정 숫자가 앱 채점과 갈리지 않게)."""
    from app import viewer_flow as vf
    from app.core import textnorm as TN
    from app.core import words_doc as WD
    if pilot is None:
        pilot = vf.is_pilot_name(project)
    if doc is None:
        return vf.W_MISSING
    if WD.validate(doc):
        return vf.W_BAD
    if (doc.get("engine") or {}).get("textnorm") != TN.VERSION:
        return vf.W_STALE_TEXTNORM
    ap = os.path.join(project, *vf.ASR_REL)
    want_asr = (doc.get("target") or {}).get("asr_sha256")
    if not pilot and want_asr is not None and os.path.isfile(ap):
        h = hashlib.sha256()
        with open(ap, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != want_asr:
            return vf.W_STALE_ASR
    cal = doc.get("calibration") or {}
    act = _active_calibration(pdata)
    if cal.get("source") in ("project.json", "project.json+cli_file_start"):
        if not act or (int(act.rev), int(act.offset_ms)) != (cal.get("rev"), cal.get("offset_ms")):
            return vf.W_STALE_CAL
    elif cal.get("source") == "default":
        return vf.W_STALE_CAL if act else vf.W_DEFAULT_CAL
    status = getattr(act, "status", None) if act else None
    if (status or cal.get("status")) not in vf.USABLE_CAL_STATUSES:
        return vf.W_UNVERIFIED_CAL
    return vf.W_OK


def unsealed_context(project: str, pdata: Mapping | None, *, run_id: str | None = None,
                     ai_root: str | None = None) -> tuple[list, list, dict]:
    """(주제 흐름 덩어리, 전사 창, 숫자 정보) — 해제된 run 의 짝 주제 흐름·전사 낱말(seal 해제 길)만 읽는다.
    run_id 가 없으면 analysis_flow.run_order(해제 기록의 엔진 판이 가장 높은 run 먼저 → 같으면 늦게 푼 것 — 시청자 훈련·
    분석 화면과 같은 규칙, 23 5-2 의 10·13) 순서로 전사 낱말 짝이 있는 첫 run, 그것도 없으면 주제 흐름 짝이 있는 첫 run."""
    from app import analysis_flow as af
    from app import seal
    from app.core import topics as T
    from app.core import words_doc as WD
    info: dict = {"topic_source": "none", "words": "missing", "context_run": None}
    try:
        st = seal.seal_status(project, ai_root=ai_root)
    except seal.SealError:
        raise CalibError("봉인 기록을 읽지 못했습니다") from None
    runs = af.unsealed_run_ids(st)
    info["unsealed_runs"] = len(runs)
    if run_id is not None:
        if run_id not in runs:
            raise CalibError("--run-id 가 해제된 run 이 아닙니다", 1)
        cands = [run_id]
    else:
        cands = af.run_order(st)
    picked = None
    for rid in cands:
        try:
            w = seal.read_words(project, rid, ai_root=ai_root)
            t = seal.read_topics(project, rid, ai_root=ai_root)
        except (seal.SealError, OSError, ValueError):
            continue
        if w is not None or run_id is not None:
            picked = (rid, w, t)
            break
        if picked is None and t is not None:
            picked = (rid, w, t)
    blocks: list = []
    windows: list = []
    if picked is not None:
        rid, w, t = picked
        info["context_run"] = rid
        if t is not None:
            try:
                blocks = [(int(b.start_ms), int(b.end_ms), tuple(b.keywords)) for b in T.blocks_from_doc(t)]
                info["topic_source"] = "unsealed_run"
            except ValueError:
                info["topic_source"] = "bad"
        code = words_code(project, w, pdata)
        info["words"] = code
        if code == "ok":
            windows = WD.score_windows(w)
    info["topic_blocks"] = len(blocks)
    info["transcript_windows"] = len(windows)
    return blocks, windows, info


# ───────────────────────────────────────────────────────────── 묶음 나누기

def _in_ranges(t: int, starts: Sequence[int], ranges: Sequence[tuple[int, int]]) -> bool:
    k = bisect.bisect_right(starts, t) - 1
    return k >= 0 and ranges[k][0] <= t < ranges[k][1]


def build_groups(chats: Sequence[Chat], labels: Sequence[LabelRow], *, grades: Sequence[str] = DEFAULT_GRADES,
                 near_ms: int = DEFAULT_NEAR_SEC * 1000, none_grades: Sequence[str] = (),
                 ranges: Sequence[tuple[int, int]] | None = None) -> tuple[dict[str, list[int]], dict]:
    """(묶음 → chats 의 번호 목록, 숫자 정보).

    반응받음 = 손라벨의 grade 가 grades 안이고 messages 에서 찾은 채점 대상 채팅. 반응 없음 = 반응받음의 첫~끝 시각 안의
    다른 채점 대상 채팅(손라벨 어느 줄에도 없는 것). 가까운 반응 없음 = 그 가운데 반응받은 채팅과 ±near_ms 안인 것.
    none_grades: 이 코드의 라벨 줄은 '확인된 반응 없음'(G_NONE) 으로 따로 묶는다(사람이 본 채팅이라 반응받은 첫~끝 시각으로는
    자르지 않고 ranges 로만 거른다). 최종 세트 길에서 쓴다.
    ranges: 주면 반응 없음·확인된 반응 없음을 이 범위([시작, 끝)) 안으로 더 좁힌다(최종 구간 조각)."""
    by_id = {c.chat_id: i for i, c in enumerate(chats)}
    labelled = {r.chat_id for r in labels}
    want = set(grades)
    want_none = set(none_grades)
    reacted: list[int] = []
    direct: list[int] = []
    none_idx: list[int] = []
    info = {"label_rows": len(labels), "label_grades": {}, "label_found": 0, "label_time_match": 0,
            "label_not_scorable": 0}
    for r in labels:
        info["label_grades"][r.grade] = info["label_grades"].get(r.grade, 0) + 1
        i = by_id.get(r.chat_id)
        if i is None:
            continue
        info["label_found"] += 1
        if chats[i].t_ms == r.chat_vod_ms:
            info["label_time_match"] += 1
        if not chats[i].scorable:
            info["label_not_scorable"] += 1
            continue
        if r.grade in want and i not in reacted:
            reacted.append(i)
            if r.grade == "direct":
                direct.append(i)
        elif r.grade in want_none and i not in none_idx:
            none_idx.append(i)
    reacted.sort()
    direct.sort()
    none_idx = sorted(set(none_idx) - set(reacted))
    groups = {G_REACTED: reacted, G_DIRECT: direct, G_OTHERS: [], G_NEAR: [], G_TEXT: [], G_NONE: []}
    if not reacted:
        return groups, info
    lo, hi = chats[reacted[0]].t_ms, chats[reacted[-1]].t_ms
    rg = list(ranges) if ranges else []
    rg_starts = [a for a, _b in rg]

    def in_scope(t: int) -> bool:
        return not rg or _in_ranges(t, rg_starts, rg)

    def inside(t: int) -> bool:
        return lo <= t <= hi and in_scope(t)
    # 확인된 반응 없음은 사람이 본 채팅이라 반응받은 첫~끝 시각으로 자르지 않고 범위(최종 구간 조각)로만 거른다
    groups[G_NONE] = [i for i in none_idx if in_scope(chats[i].t_ms)]
    if want_none:
        info["label_none_out_of_span"] = len(none_idx) - len(groups[G_NONE])
    rts = [chats[i].t_ms for i in reacted]
    for i, c in enumerate(chats):
        if not c.scorable or c.chat_id in labelled or not inside(c.t_ms):
            continue
        groups[G_OTHERS].append(i)
        k = bisect.bisect_left(rts, c.t_ms)
        near = any(abs(rts[j] - c.t_ms) <= near_ms for j in (k - 1, k) if 0 <= j < len(rts))
        if near:
            groups[G_NEAR].append(i)
        if vs.word_set(c.text):
            groups[G_TEXT].append(i)
    info["span_ms"] = hi - lo
    return groups, info


# ───────────────────────────────────────────────────────────── 채점

class CalibScorer:
    """실제 채팅을 연습 채팅처럼 매기는 틀. 맥락 채팅은 채팅마다 앞 60초를 잘라 같은 시청자·자기 자신을 뺀다."""

    def __init__(self, chats: Sequence[Chat], base_ctx: vs.ScoreContext):
        self.chats = chats
        self.base_ctx = base_ctx
        self._vis = [(c.t_ms, c.viewer, c.chat_id, vs.canon(c.text)) for c in chats if c.visible]
        self._vis = [r for r in self._vis if r[3]]
        self._vis_ms = [r[0] for r in self._vis]
        self._grams: dict[tuple[str, int], frozenset[str]] = {}
        self._by_viewer: dict[str, list[int]] = {}
        for i, c in enumerate(chats):
            if c.scorable:
                self._by_viewer.setdefault(c.viewer, []).append(i)

    def _g(self, key: str, n: int) -> frozenset[str]:
        k = (key, n)
        g = self._grams.get(k)
        if g is None:
            g = self._grams[k] = vs.grams(key, n)
        return g

    def context_for(self, i: int, ctx: vs.ScoreContext) -> vs.ScoreContext:
        c = self.chats[i]
        a = bisect.bisect_left(self._vis_ms, c.t_ms - CTX_CHAT_SPAN_MS)
        b = bisect.bisect_right(self._vis_ms, c.t_ms)
        rows = [r for r in self._vis[a:b] if r[1] != c.viewer and r[2] != c.chat_id]
        return replace(ctx, chat_ms=tuple(r[0] for r in rows),
                       chat_keys=tuple((r[3], self._g(r[3], ctx.ngram)) for r in rows))

    def history_for(self, i: int) -> list[str]:
        c = self.chats[i]
        own = self._by_viewer.get(c.viewer, [])
        k = bisect.bisect_left(own, i)
        out = [self.chats[j].text for j in own[max(0, k - HISTORY_MAX):k]
               if c.t_ms - HISTORY_SPAN_MS <= self.chats[j].t_ms < c.t_ms
               or (self.chats[j].t_ms == c.t_ms and j < i)]
        return out

    def score(self, idxs: Iterable[int], settings: vs.ViewerScoreSettings,
              ctx: vs.ScoreContext | None = None) -> dict[int, vs.ViewerScore]:
        base = ctx or self.base_ctx
        out: dict[int, vs.ViewerScore] = {}
        for i in idxs:
            out[i] = vs.score_chat(self.chats[i].text, self.chats[i].t_ms, self.context_for(i, base),
                                   self.history_for(i), settings=settings)
        return out

    def flow_presend(self, idxs: Iterable[int], scores: Mapping[int, vs.ViewerScore], settings: vs.ViewerScoreSettings,
                     ctx: vs.ScoreContext | None = None) -> dict:
        """주제 흐름 누출 크기(숫자만). 주제 흐름 덩어리는 방송 전체의 전사·채팅으로 만든 것이라 보낸 시각 **뒤**의 말과
        채점 대상 채팅 자신의 낱말이 덩어리 낱말에 들어갈 수 있다(앱의 연습 채팅은 주제 흐름의 재료가 아니다).

        topic.flow_hit 인 채팅마다 겹친 주제 흐름 낱말 가운데 하나라도 '덩어리 시작 ~ 보낸 시각' 에 시작한 전사 창에
        있으면 presend, 아니면 postsend_only(보낸 시각 뒤의 말이나 채팅 낱말만이 받쳐 줌). block_past_send = 걸린 덩어리
        가운데 하나라도 보낸 시각 뒤까지 이어지는 채팅 수. 동의어·flow_head2 는 보지 않는다(base 판 전용)."""
        base = ctx or self.base_ctx
        out = {"flow_hit": 0, "presend": 0, "postsend_only": 0, "block_past_send": 0}
        for i in idxs:
            if scores[i].topic.code != vs.TOPIC_FLOW_HIT:
                continue
            c = self.chats[i]
            lo, hi = c.t_ms - settings.topic_lookback_ms, c.t_ms
            rows = [(a, b, ws) for a, b, ws in base.topic.items if b >= lo and a < hi]
            if not rows:
                continue
            pool = frozenset().union(*(ws for _a, _b, ws in rows))
            units = vs._content_units(c.text, base.talk_common)
            hit_words = sorted({w for u in units for w in u if any(vs._match(x, pool) for x in sorted(u))})
            talk_pre = base.talk.words_started(min(a for a, _b, _w in rows), hi)
            out["flow_hit"] += 1
            if any(vs._match(w, talk_pre) for w in hit_words):
                out["presend"] += 1
            else:
                out["postsend_only"] += 1
            if any(b > hi for _a, b, _w in rows):
                out["block_past_send"] += 1
        return out


# ───────────────────────────────────────────────────────────── 숫자

def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2×2 표 [[a, b], [c, d]] 의 피셔 정확 검정 양쪽 p (확률이 관측값 이하인 표들의 합)."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2
    if n == 0:
        return 1.0
    denom = math.comb(n, k)

    def p(x: int) -> float:
        return math.comb(n1, x) * math.comb(n2, k - x) / denom

    obs = p(a)
    lo, hi = max(0, k - n2), min(k, n1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs * (1 + 1e-9)))


def _pct(x: int, n: int) -> float | None:
    return round(100.0 * x / n, 1) if n else None


def distribution(scores: Mapping[int, vs.ViewerScore], idxs: Sequence[int]) -> dict:
    """축마다 {n, good, ok, weak, good_pct, ok_pct, weak_pct, codes{코드: 수}}. 숫자·코드만."""
    out: dict = {"n": len(idxs)}
    for axis in vs.AXES:
        cnt = {lv: 0 for lv in vs.LEVELS}
        codes: dict[str, int] = {}
        for i in idxs:
            r = getattr(scores[i], axis)
            cnt[r.level] += 1
            codes[r.code] = codes.get(r.code, 0) + 1
        n = len(idxs)
        out[axis] = {**cnt, **{f"{lv}_pct": _pct(cnt[lv], n) for lv in vs.LEVELS},
                     "codes": dict(sorted(codes.items()))}
    return out


def good_lift(a_good: int, na: int, b_good: int, nb: int) -> float | None:
    """lift = (a 의 good 률) ÷ (b 의 good 률). b 의 good 이 0 이거나 묶음이 비면 None(정의되지 않음)."""
    if not na or not nb or not b_good:
        return None
    return round((a_good / na) / (b_good / nb), 2)


def good_precision(a_good: int, b_good: int) -> float | None:
    """정밀도 = good 가운데 a(반응받음) 쪽 비율 = a_good / (a_good + b_good). good 이 하나도 없으면 None.
    두 묶음의 크기 비에 따라 달라지므로(29 : 713 등) 판끼리 견줄 때는 lift 를 주 지표로 쓴다."""
    tot = a_good + b_good
    return round(a_good / tot, 3) if tot else None


def compare(dist_a: Mapping, dist_b: Mapping) -> dict:
    """축마다 good 비율 차이(a − b, %p), 피셔 양쪽 p(good 대 good 아님), good 의 정밀도·lift.

    정밀도·lift 는 '정밀도 우선'(D-21 2 — 틀린 칭찬이 틀린 지적보다 나쁘다)으로 문턱 후보를 고를 때 쓴다. good 차이는
    문턱을 느슨하게 해 양쪽 good 이 함께 늘어도 커질 수 있어 보조 숫자로만 본다(검토 2026-10-23)."""
    out = {}
    for axis in vs.AXES:
        a, b = dist_a[axis], dist_b[axis]
        na, nb = dist_a["n"], dist_b["n"]
        if not na or not nb:
            out[axis] = {"good_diff_pp": None, "weak_diff_pp": None, "fisher_p_good": None,
                         "good_precision": None, "good_lift": None}
            continue
        out[axis] = {"good_diff_pp": round(a["good_pct"] - b["good_pct"], 1),
                     "weak_diff_pp": round(a["weak_pct"] - b["weak_pct"], 1),
                     "fisher_p_good": round(fisher_two_sided(a["good"], na - a["good"], b["good"], nb - b["good"]), 4),
                     "good_precision": good_precision(a["good"], b["good"]),
                     "good_lift": good_lift(a["good"], na, b["good"], nb)}
    return out


def changed(before: Mapping[int, vs.ViewerScore], after: Mapping[int, vs.ViewerScore], idxs: Sequence[int]) -> dict:
    """축마다 단계가 바뀐 채팅 수와 방향(올라감·내려감)."""
    rank = {vs.WEAK: 0, vs.OK: 1, vs.GOOD: 2}
    out = {}
    for axis in vs.AXES:
        up = down = 0
        for i in idxs:
            x, y = rank[getattr(before[i], axis).level], rank[getattr(after[i], axis).level]
            up += y > x
            down += y < x
        out[axis] = {"up": up, "down": down}
    return out


def settings_for(overrides: Mapping) -> tuple[vs.ViewerScoreSettings, list[str]]:
    return vs.settings_from_values({"viewer_score": dict(overrides)})


def row_compares(dists: Mapping[str, Mapping]) -> dict:
    """묶음별 분포 → 판 한 줄의 비교 칸들(run_variants·pool_reports 가 같은 규칙으로 쓴다)."""
    empty = {"n": 0, **{a: {lv: 0 for lv in vs.LEVELS} for a in vs.AXES}}
    none = dists.get(G_NONE) or empty
    return {"reacted_vs_others": compare(dists[G_REACTED], dists[G_OTHERS]),
            "reacted_vs_near": compare(dists[G_REACTED], dists[G_NEAR]),
            "reacted_vs_text": compare(dists[G_REACTED], dists[G_TEXT]),
            "direct_vs_others": compare(dists[G_DIRECT], dists[G_OTHERS]),
            "reacted_vs_none": compare(dists[G_REACTED], none)}


def run_variants(scorer: CalibScorer, groups: Mapping[str, Sequence[int]], *, no_flow_ctx: vs.ScoreContext,
                 names: Sequence[str] | None = None) -> dict:
    """판마다 묶음별 분포·비교. base 와 견준 '단계가 바뀐 수' 는 TOGGLES 판에만."""
    idx_all = sorted({i for g in ALL_GROUPS for i in groups.get(g, ())})
    results: dict = {}
    base_scores = None
    for name, axis, ov, ctx_kind in VARIANTS:
        if names is not None and name not in names:
            continue
        s, notes = settings_for(ov)
        ctx = no_flow_ctx if ctx_kind == "no_flow" else None
        t0 = time.perf_counter()
        sc = scorer.score(idx_all, s, ctx)
        el = time.perf_counter() - t0
        dists = {g: distribution(sc, groups.get(g, ())) for g in ALL_GROUPS}
        row = {"axis": axis, "overrides": dict(ov), "context": ctx_kind, "notes": notes,
               "elapsed_s": round(el, 3), "groups": dists, **row_compares(dists)}
        if name == "base":
            base_scores = sc
            row["flow_presend"] = {g: scorer.flow_presend(groups.get(g, ()), sc, s) for g in ALL_GROUPS}
        elif base_scores is not None and name in TOGGLES:
            row["changed_vs_base"] = {g: changed(base_scores, sc, groups.get(g, ())) for g in (G_REACTED, G_OTHERS)}
        results[name] = row
    return results


#: 후보는 반응받은 쪽 good 수가 base 의 이 비율 이상일 때만(정밀도만 보면 good 을 거의 다 없애는 판이 이길 수 있어서)
CANDIDATE_MIN_RECALL = 0.5


def candidates(results: Mapping) -> dict:
    """축마다 **good lift(반응받음 good 률 ÷ 반응 없음 good 률)가 base 보다 높은** 판 가운데 lift 가 가장 큰 판.
    없으면 base. **후보일 뿐 기본값을 바꾸지 않는다.**

    정밀도 우선(D-21 2): good 차이(%p)가 가장 큰 판을 고르면 문턱을 느슨하게 해 반응 없는 쪽 good 도 함께 느는 판이
    이긴다(검토 2026-10-23). 그래서 lift 를 주 지표로, 정밀도·good 차이·p 는 보조 숫자로 함께 적는다.
    반응받은 쪽 good 수가 base 의 CANDIDATE_MIN_RECALL 미만이거나 0 이면 후보로 보지 않는다. 같은 lift 면 반응받은 쪽
    good 이 많은 판, 그다음 VARIANTS 순서."""
    out = {}
    base = results.get("base")
    if not base:
        return out
    for axis in vs.AXES:
        base_c = base["reacted_vs_others"][axis]
        base_rg = base["groups"][G_REACTED][axis]["good"]
        min_rg = max(1, math.ceil(base_rg * CANDIDATE_MIN_RECALL))
        best_name, best_lift, best_rg = "base", base_c["good_lift"], base_rg
        if best_lift is not None:
            for name, row in results.items():
                if row["axis"] not in (axis, None) or name == "base":
                    continue
                lift = row["reacted_vs_others"][axis]["good_lift"]
                rg = row["groups"][G_REACTED][axis]["good"]
                if lift is None or rg < min_rg:
                    continue
                if lift > best_lift or (lift == best_lift and best_name != "base" and rg > best_rg):
                    best_name, best_lift, best_rg = name, lift, rg
        row = results[best_name]
        c, t = row["reacted_vs_others"][axis], row["reacted_vs_text"][axis]
        out[axis] = {"variant": best_name, "metric": "good_lift",
                     "good_lift": c["good_lift"], "base_good_lift": base_c["good_lift"],
                     "good_precision": c["good_precision"], "base_good_precision": base_c["good_precision"],
                     "good_diff_pp": c["good_diff_pp"], "base_good_diff_pp": base_c["good_diff_pp"],
                     "fisher_p_good": c["fisher_p_good"],
                     "reacted_good": row["groups"][G_REACTED][axis]["good"], "base_reacted_good": base_rg,
                     "others_good": row["groups"][G_OTHERS][axis]["good"],
                     "base_others_good": base["groups"][G_OTHERS][axis]["good"],
                     "text_good_lift": t["good_lift"],
                     "base_text_good_lift": base["reacted_vs_text"][axis]["good_lift"],
                     "text_good_diff_pp": t["good_diff_pp"],
                     "base_text_good_diff_pp": base["reacted_vs_text"][axis]["good_diff_pp"]}
    return out


# ───────────────────────────────────────────────────────────── 여러 프로젝트 묶기

def _sum_dist(dists: Sequence[Mapping]) -> dict:
    n = sum(int(d.get("n") or 0) for d in dists)
    out: dict = {"n": n}
    for axis in vs.AXES:
        cnt = {lv: sum(int((d.get(axis) or {}).get(lv) or 0) for d in dists) for lv in vs.LEVELS}
        codes: dict[str, int] = {}
        for d in dists:
            for k, v in ((d.get(axis) or {}).get("codes") or {}).items():
                codes[k] = codes.get(k, 0) + int(v)
        out[axis] = {**cnt, **{f"{lv}_pct": _pct(cnt[lv], n) for lv in vs.LEVELS}, "codes": dict(sorted(codes.items()))}
    return out


def _sum_counts(rows: Sequence[Mapping]) -> dict:
    out: dict = {}
    for r in rows:
        for k, v in (r or {}).items():
            if isinstance(v, Mapping):
                out[k] = _sum_counts([out.get(k) or {}, v])
            elif _is_int(v):
                out[k] = out.get(k, 0) + v
    return out


def pool_reports(reps: Sequence[Mapping]) -> dict:
    """calibrate 결과 여러 개(프로젝트마다) → 묶음·판별 건수를 더한 결과(같은 모양: input·variants·candidates).
    판은 모든 결과에 있는 것만. 채점기 판이 다르면 CalibError. 숫자만 다룬다(채팅·본문 없음)."""
    reps = [r for r in reps if isinstance(r, Mapping) and r.get("variants")]
    if not reps:
        raise CalibError("묶을 결과가 없습니다")
    scorers = {r.get("scorer") for r in reps}
    if len(scorers) != 1:
        raise CalibError("채점기 판이 다른 결과는 묶지 않습니다")
    names = [n for n in reps[0]["variants"] if all(n in r["variants"] for r in reps)]
    variants: dict = {}
    for name in names:
        rows = [r["variants"][name] for r in reps]
        dists = {g: _sum_dist([(row.get("groups") or {}).get(g) or {"n": 0} for row in rows]) for g in ALL_GROUPS}
        out = {"axis": rows[0].get("axis"), "overrides": dict(rows[0].get("overrides") or {}),
               "context": rows[0].get("context"), "notes": sorted({n for row in rows for n in row.get("notes") or ()}),
               "elapsed_s": round(sum(float(row.get("elapsed_s") or 0) for row in rows), 3), "groups": dists,
               **row_compares(dists)}
        if all("flow_presend" in row for row in rows):
            out["flow_presend"] = _sum_counts([row["flow_presend"] for row in rows])
        if all("changed_vs_base" in row for row in rows):
            out["changed_vs_base"] = _sum_counts([row["changed_vs_base"] for row in rows])
        variants[name] = out
    inp = _sum_counts([{k: v for k, v in (r.get("input") or {}).items()
                        if k in ("messages", "scorable", "visible", "label_rows", "label_found", "topic_blocks",
                                 "transcript_windows", "groups")} for r in reps])
    return {"tool": TOOL, "tool_version": TOOL_VERSION, "scorer": reps[0].get("scorer"),
            "normalizer": reps[0].get("normalizer"), "pooled": len(reps), "input": inp,
            "load_s": round(sum(float(r.get("load_s") or 0) for r in reps), 3),
            "variants": variants, "candidates": candidates(variants)}


# ───────────────────────────────────────────────────────────── 실행

def _is_pilot(project: str) -> bool:
    from app import reference_set as rs
    from app import viewer_flow as vf
    try:
        return vf.is_pilot_name(project) or rs.is_pilot_project(project)
    except Exception:  # noqa: BLE001 — 판단 못 하면 시범으로 본다(블라인드 쪽으로 안전하게)
        return True


def topics_zone(project: str, path: str, pdata: Mapping | None = None) -> str:
    """--topics 파일이 어디 있나: 'protected'(프로젝트 보호 구역 안), 'unprotected'(프로젝트 안, 보호 구역 밖),
    'outside_project'(프로젝트 밖)."""
    from app import backup
    root, p = os.path.abspath(project), os.path.abspath(path)
    try:
        rel = os.path.relpath(p, root)
    except ValueError:          # 다른 드라이브
        return "outside_project"
    if rel == os.curdir or rel.startswith(os.pardir) or os.path.isabs(rel):
        return "outside_project"
    pats = backup.protected_patterns(dict(pdata) if isinstance(pdata, Mapping) else None)
    return "protected" if backup.in_protected_zone(rel, pats) else "unprotected"


def load_context(project: str, *, topics_path: str | None) -> tuple[list, list, dict]:
    """(주제 흐름 덩어리, 전사 창, 숫자 정보). 전사 낱말은 viewer_flow.words_status 가 ok 일 때만."""
    from app import viewer_flow as vf
    from app.core import words_doc as WD
    info: dict = {}
    try:
        with open(os.path.join(project, "project.json"), "rb") as f:
            pdata = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        raise CalibError("project.json 을 읽지 못했습니다") from None
    if topics_path:
        # 주제 흐름 낱말은 전사·채팅에서 나온 값이라 보호 구역(derived/asr*)에 둔다(03 2-1). 프로젝트 안의 보호 구역 밖
        # 파일은 읽지 않는다(그런 이름으로 다시 만드는 일이 없게, 검토 2026-10-23). 프로젝트 밖(합성 시험 등)은 표시만.
        zone = topics_zone(project, topics_path, pdata)
        if zone == "unprotected":
            raise CalibError("주제 흐름 파일이 프로젝트 보호 구역 밖입니다 — <P>\\derived\\asr-topic-flow.json 처럼 "
                             "derived\\asr* 이름으로 두십시오")
        info["topic_zone"] = zone
    code, wdoc = vf.words_status(project, pdata)
    info["words"] = code
    windows = WD.score_windows(wdoc) if wdoc is not None else []
    info["transcript_windows"] = len(windows)
    if topics_path:
        blocks = read_topic_blocks(topics_path)
        info["topic_source"] = "file"
    else:
        blocks, rid = vf.topic_blocks(project)
        info["topic_source"] = "unsealed_run" if rid else "none"
    info["topic_blocks"] = len(blocks)
    return blocks, windows, info


def _pilot_locked(project: str) -> bool:
    from app import reference_set as rs
    try:
        return bool(rs.pilot_sessions_locked(project)[0])
    except Exception:  # noqa: BLE001 — 판단 못 하면 잠기지 않은 것으로(블라인드 쪽으로 안전하게)
        return False


def _read_pdata(project: str) -> dict:
    try:
        with open(os.path.join(project, "project.json"), "rb") as f:
            d = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        raise CalibError("project.json 을 읽지 못했습니다") from None
    return d if isinstance(d, dict) else {}


def calibrate(project: str, *, topics_path: str | None = None, labels_path: str | None = None,
              grades: Sequence[str] | None = None, near_sec: int = DEFAULT_NEAR_SEC, quick: bool = False,
              final_set=None, final_eval: str = "lenient", run_id: str | None = None,
              ai_root: str | None = None) -> dict:
    """보정 자료 한 프로젝트. final_set 을 주면(zip 경로·'auto'·1차 세션 ID·build_final_set 결과) 손라벨 대신 최종 세트를
    쓰고 grades 기본은 FINAL_GRADES(direct 만). 시범 프로젝트는 final_set 길로만, 시범 세션이 모두 잠긴 뒤에만 연다."""
    project = os.path.abspath(project)
    if not os.path.isfile(os.path.join(project, "project.json")):
        raise CalibError("프로젝트 폴더가 아닙니다(project.json 없음)")
    pilot = _is_pilot(project)
    if final_set is None:
        if pilot:
            raise CalibError("시범 프로젝트는 손라벨 길로 열지 않습니다(--final-set, 시범 세션이 모두 잠긴 뒤, D-17 4)", 1)
        grades = tuple(grades) if grades else DEFAULT_GRADES
        lp = labels_path or find_manual_csv(project)
        if not lp or not os.path.isfile(lp):
            raise CalibError("손라벨 labels\\manual-*.csv 가 없습니다", 1)
    else:
        if final_eval not in FINAL_EVALS:
            raise CalibError("--final-eval 은 lenient 또는 strict")
        if pilot and not _pilot_locked(project):
            raise CalibError("시범 세션이 모두 잠기기 전에는 최종 세트를 열지 않습니다 (D-17 4)", 1)
        if pilot and topics_path:
            raise CalibError("시범 프로젝트의 주제 흐름은 해제된 run 에서만 읽습니다(--topics 를 쓰지 않음)")
        grades = tuple(grades) if grades else FINAL_GRADES
    t0 = time.perf_counter()
    fin_info: dict | None = None
    ranges = None
    if final_set is None:
        labels = read_manual_csv(lp)
        none_grades: tuple[str, ...] = ()
    else:
        fin = load_final_set(project, final_set, ai_root=ai_root)
        labels, lc = labels_from_final(fin, eval_mode=final_eval)
        ranges = fin.ranges or None
        none_grades = (NONE_CODE,)
        fin_info = {**fin.info, **lc, "eval": final_eval, "ranges": len(fin.ranges)}
    chats = read_messages(os.path.join(project, "chat", "messages.jsonl"))
    if fin_info is not None and fin_info.get("messages_sha256"):
        h = hashlib.sha256()
        with open(os.path.join(project, "chat", "messages.jsonl"), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        fin_info["messages_match"] = h.hexdigest() == fin_info.pop("messages_sha256")
    else:
        if fin_info is not None:
            fin_info.pop("messages_sha256", None)
    if pilot:
        blocks, windows, cinfo = unsealed_context(project, _read_pdata(project), run_id=run_id, ai_root=ai_root)
    elif run_id is not None:
        blocks, windows, cinfo = unsealed_context(project, _read_pdata(project), run_id=run_id, ai_root=ai_root)
    else:
        blocks, windows, cinfo = load_context(project, topics_path=topics_path)
    groups, ginfo = build_groups(chats, labels, grades=grades, near_ms=int(near_sec) * 1000,
                                 none_grades=none_grades, ranges=ranges)
    if not groups[G_REACTED]:
        raise CalibError("반응받은 채팅을 찾지 못했습니다(손라벨·최종 세트)", 1)
    base_ctx = vs.ScoreContext.build(topic_blocks=blocks, transcript=windows)
    no_flow_ctx = vs.ScoreContext.build(topic_blocks=(), transcript=windows)
    scorer = CalibScorer(chats, base_ctx)
    load_s = time.perf_counter() - t0
    results = run_variants(scorer, groups, no_flow_ctx=no_flow_ctx, names=QUICK if quick else None)
    inp = {"messages": len(chats), "scorable": sum(1 for c in chats if c.scorable),
           "visible": sum(1 for c in chats if c.visible), **ginfo, **cinfo,
           "grades": list(grades), "near_sec": int(near_sec), "pilot": pilot,
           "label_source": "final_set" if fin_info is not None else "manual_csv",
           "groups": {g: len(groups.get(g, ())) for g in ALL_GROUPS}}
    if fin_info is not None:
        inp["final_set"] = fin_info
    return {
        "tool": TOOL, "tool_version": TOOL_VERSION, "scorer": f"viewer_score/{vs.VERSION}",
        "normalizer": vs.NORMALIZER,
        "input": inp,
        "load_s": round(load_s, 3),
        "variants": results,
        "candidates": candidates(results),
    }


def _table(rep: Mapping) -> list[str]:
    """사람이 읽는 숫자 표(본문 없음)."""
    lines = []
    inp = rep["input"]
    gr = inp.get("groups") or {}
    head = (f"{rep['scorer']} · 채팅 {inp.get('messages')} · 반응받음 {gr.get(G_REACTED)}"
            f"(direct {gr.get(G_DIRECT)}) · 반응 없음 {gr.get(G_OTHERS)}"
            f" · 가까운 반응 없음 {gr.get(G_NEAR)} · 낱말 있는 반응 없음 {gr.get(G_TEXT)}"
            f" · 주제 흐름 {inp.get('topic_blocks')} · 전사 창 {inp.get('transcript_windows')}({inp.get('words', '-')})")
    if gr.get(G_NONE):
        head += f" · 확인된 반응 없음 {gr.get(G_NONE)}"
    if rep.get("pooled"):
        head += f" · 묶은 프로젝트 {rep['pooled']}"
    lines.append(head)
    lines.append("판 | 축 | 반응받음 good/ok/weak % | 반응 없음 good/ok/weak % | good 건수(반응받음/반응 없음) | lift | 정밀도"
                 " | good 차이 %p | p | 낱말 있는 쪽과 lift | 차이 %p | p")
    for name, row in rep["variants"].items():
        for axis in vs.AXES:
            if row["axis"] not in (None, axis):
                continue
            a, b = row["groups"][G_REACTED][axis], row["groups"][G_OTHERS][axis]
            c, t = row["reacted_vs_others"][axis], row["reacted_vs_text"][axis]
            lines.append(f"{name} | {axis} | {a['good_pct']}/{a['ok_pct']}/{a['weak_pct']} | "
                         f"{b['good_pct']}/{b['ok_pct']}/{b['weak_pct']} | {a['good']}/{b['good']} | {c['good_lift']}"
                         f" | {c['good_precision']} | {c['good_diff_pp']} | {c['fisher_p_good']}"
                         f" | {t['good_lift']} | {t['good_diff_pp']} | {t['fisher_p_good']}")
    base = rep["variants"].get("base") or {}
    if (base.get("groups") or {}).get(G_NONE, {}).get("n"):
        lines.append("base 반응받음 ↔ 확인된 반응 없음: " + " · ".join(
            f"{axis} lift {base['reacted_vs_none'][axis]['good_lift']} 차이 {base['reacted_vs_none'][axis]['good_diff_pp']}"
            f" p {base['reacted_vs_none'][axis]['fisher_p_good']}" for axis in vs.AXES))
    fp = base.get("flow_presend")
    if fp:
        lines.append("주제 흐름 누출(base): " + " · ".join(
            f"{g} flow_hit {fp[g]['flow_hit']} = 보낸 시각 앞 전사에도 {fp[g]['presend']} + 뒤에만 "
            f"{fp[g]['postsend_only']} (덩어리가 보낸 뒤까지 {fp[g]['block_past_send']})" for g in (G_REACTED, G_OTHERS)))
    lines.append("후보(lift 가 base 보다 높은 판, 기본값 아님): " + " · ".join(
        f"{axis} {c['variant']}(lift {c['base_good_lift']} → {c['good_lift']})"
        for axis, c in rep.get("candidates", {}).items()))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시청자 훈련 채점 보정 자료(숫자만)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--topics", help="주제 흐름 파일(engine.topics 결과, <P>\\derived\\asr-topic-flow.json 처럼 보호 구역 "
                                     "derived\\asr* 안). 프로젝트 안의 보호 구역 밖 파일은 거부. 없으면 해제된 run 의 주제 흐름")
    ap.add_argument("--labels", help="손라벨 CSV(기본 labels\\manual-*.csv 이름 순 첫 파일)")
    ap.add_argument("--grades", default=None, help="반응받음으로 볼 grade(쉼표). 기본: 손라벨 direct,likely · "
                                                   "최종 세트 direct")
    ap.add_argument("--near-sec", type=int, default=DEFAULT_NEAR_SEC)
    ap.add_argument("--out", help="결과 JSON 파일(숫자만)")
    ap.add_argument("--quick", action="store_true", help="기본 + 켜고 끄기 판만")
    ap.add_argument("--json", action="store_true", help="표 대신 JSON 을 표준 출력으로")
    ap.add_argument("--final-set", help="손라벨 대신 사람 최종 세트: export_final_set zip · auto · 1차 세션 ID "
                                        "(시범은 이 길로만, 시범 세션이 모두 잠긴 뒤)")
    ap.add_argument("--final-eval", choices=FINAL_EVALS, default="lenient",
                    help="최종 세트에서 반응받음으로 볼 행: eval.lenient(참고, 기본) · eval.strict(엄격)")
    ap.add_argument("--run-id", help="맥락(주제 흐름·전사 낱말)을 읽을 해제된 run (기본: 전사 낱말 짝이 있는 가장 늦은 해제)")
    ap.add_argument("--ai-root", help="AI 결과 폴더(기본 app.seal 규칙)")
    a = ap.parse_args(argv)
    if not 0 < a.near_sec <= 600:
        print("--near-sec 는 1~600", file=sys.stderr)
        return 2
    grades = None
    if a.grades is not None:
        grades = tuple(g.strip() for g in a.grades.split(",") if g.strip()) or None
    try:
        rep = calibrate(a.project, topics_path=a.topics, labels_path=a.labels, grades=grades,
                        near_sec=a.near_sec, quick=a.quick, final_set=a.final_set, final_eval=a.final_eval,
                        run_id=a.run_id, ai_root=a.ai_root)
    except CalibError as e:
        print(f"거부: {e}", file=sys.stderr)
        return e.code
    except (OSError, ValueError) as e:
        print(f"입력 오류: {type(e).__name__}", file=sys.stderr)
        return 2
    if rep["input"].get("topic_zone") == "outside_project":
        print("주의: 주제 흐름 파일이 프로젝트 밖입니다(실제 방송이면 <P>\\derived\\asr-topic-flow.json 에 두십시오)",
              file=sys.stderr)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, sort_keys=True))
    else:
        print("\n".join(_table(rep)))
    return 0


__all__ = ["Chat", "LabelRow", "CalibError", "CalibScorer", "VARIANTS", "QUICK", "TOGGLES", "CANDIDATE_MIN_RECALL",
           "parse_messages", "parse_manual_csv", "build_groups", "distribution", "compare", "good_lift",
           "good_precision", "changed", "fisher_two_sided", "settings_for", "run_variants", "candidates",
           "topics_zone", "calibrate", "main",
           "FinalInput", "final_from_export", "final_from_zip", "labels_from_final", "load_final_set",
           "words_code", "unsealed_context", "row_compares", "pool_reports", "ALL_GROUPS", "G_NONE",
           "FINAL_GRADES", "FINAL_EVALS"]

if __name__ == "__main__":
    raise SystemExit(main())
