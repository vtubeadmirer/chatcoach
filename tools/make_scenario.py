"""가상 채팅 시나리오 만들기 — 첫 판은 'retrieved' 만 (docs/design/20-plan-virtual-chat.md H-2, D-19 2).

사용:
  python tools/make_scenario.py --project <프로젝트 폴더> --from <시각> --to <시각>
         [--projects-root <폴더>] [--blocklist <txt>] [--topic-flow <topic-flow.json>] [--seed N] [--dry-run]
         [--threshold Q/T | test | default] [--quiet-max-real 2] [--target-per-min 6] [--max-virtual-ratio 0.5]

  --from/--to : 훈련 구간(VOD 시각). 1:02:03(.5) 형식 또는 정수 ms.
  --projects-root : 같은 채널(project.json broadcast.channel_id) 프로젝트를 찾을 곳. 기본은 프로젝트 폴더의 부모.
                    channel_id 가 없으면 같은 방송만 쓴다. pilot-* 폴더는 열지 않는다.
  --blocklist : 금칙어 목록(UTF-8, 한 줄에 하나, '#' 주석). 기본 <데이터 폴더>\\config\\blocklist-ko.txt (20 11-6, D-20 6).
                있으면 그 낱말이 든 줄을 빼고 시나리오 머리 filters.blocklist_sha256 에 파일 해시만 적는다(낱말은 어디에도
                적지 않는다). 없으면 금칙어 거르기 0낱말로 진행하고 경고만 낸다(출력 blocklist_found=false,
                warnings=["blocklist_missing"], 표준 오류에 한 줄). 있는데 읽지 못하면 만들지 않는다(bad_blocklist, rc 1).
                빈 값(--blocklist "")은 '목록 없이' — 시험용.
  --threshold : 채우기 문턱 한 번에. 'Q/T' = --quiet-max-real Q --target-per-min T (예: 45/60). 'test' = 45/60
                (시험 문턱, 21 보고 1-3 · gui_smoke), 'default' = 2/6(기본). --quiet-max-real·--target-per-min 과 같이 쓰면
                bad_argument.
  --seed : 0 이상 정수. 같은 입력·seed 면 같은 줄.
  --dry-run : 파일을 쓰지 않고 숫자만 낸다.
  --generated <파일> : 생성 담당(H-3, 로컬 LLM)이 만든 생성 채팅 후보 파일을 retrieved 와 합친다(20 4-3·6장, D-23 4).
                UTF-8 JSON Lines. 첫 줄은 선택 'generation.header'
                  {"kind": "generation.header", "project_id"?, "generator": {"backend": "lmstudio", "model_id"?,
                   "prompt_template_id"?, "prompt_sha256"?, "temperature"?, "seed"?, "context_ms"?},
                   "filters"?: {"versions"?: [...], "dropped"?: {까닭: 수}}}
                나머지는 scenario.chat 모양 생성 줄
                  {"kind": "scenario.chat", "source": "generated", "chat_kind": "text", "show_vod_ms" | "show_rel_ms",
                   "gen_id": "g0001", "text": "…(100자 이하)", "display_mask"?: "두*", "emotes"?: [...],
                   "context"?: {...}, "checks"?: {...}}   (line_id 는 있어도 다시 매긴다)
                합치는 순서: 모양 검사 → 금칙어·원 닉네임·실제 채팅 그대로 베낌(두 번째 막기) → 자리(드문 창만, 창 목표,
                구간 가상 비율 --max-virtual-ratio, 생성 비율 --max-generated-ratio) → G 채점기 거르기
                (app.core.gen_filter: 주제 적합성 weak·반복 weak 는 뺌, 확장성은 기록만) → 가림 이름(줄마다 다름,
                실제·retrieved 이름과 겹치면 새로). 걸러 낸 수는 머리 filters.dropped 의 'gen_*' 칸에 숫자로만.
                backend 가 lmstudio 가 아니면(외부 등) 거부(generated_backend, 20 8장 8). 이 도구는 LLM 을 부르지 않는다.
                원 닉네임 목록(chat\\protected\\identities.jsonl)이 없으면 거부(nicknames_missing, rc 1) —
                --allow-no-identities 를 줄 때만 진행하고 warnings 에 nicknames_missing.
                --generated·--topic-flow·--blocklist·--projects-root 가 시범 폴더(pilot-*, 시범 봉인 폴더 포함) 안이면
                읽기 전에 거부(pilot_locked, rc 2).
  --max-generated-ratio : 생성 / (실제 + 가상) 상한(0 이상 1 미만, 기본 0.25). 품질 관문(H-5) 전이라 가상 비율 상한
                (0.5)의 절반에서 시작한다.
  --generated-mode replace|room : replace(기본) = 생성 몫을 retrieved 보다 먼저 잡는다. 자리 계산에서 retrieved 를 빼고,
                남긴 생성 줄만큼 그 창의 retrieved 를 점수 낮은 것부터 뺀다(창 목표·구간 가상 비율 상한은 그대로, 뺀 수는
                filters.dropped.retrieved_displaced). room = retrieved 가 채우고 남은 자리에만(큰 방송은 시험 문턱에서 자리 0).

잇는 것: engine.retrieve(고르기, 표준 라이브러리만) → (--generated 면 merge_generated: app.core.gen_filter) →
  app.core.scenario.assemble(줄 목록·ID·끝 줄) →
  app.scenario_store.write_scenario(project_root, records)(검사 + scenarios\\<scenario_id>.jsonl 원자적 쓰기).
  같은 프로젝트·구간·lines_sha256 인 시나리오가 이미 있으면 write_scenario 가 새로 쓰지 않고 그 ID 를 준다.
  채울 줄이 0개면 쓰지 않는다(nothing_to_fill).
시범 프로젝트(pilot-*)는 읽기 전에 거부한다(pilot_locked). 이름이 달라도 시범 기록(freeze.json projects[]·setup.json
items[])에 적힌 프로젝트는 app.reference_set 으로 거른다 — 대상이면 거부, 같은 채널 후보면 열지 않는다(stats.pilot_skipped).
AI·외부 호출·모델 없음. messages.jsonl 은 읽기만.
출력: 표준 출력에 JSON 한 줄(숫자·ID·코드만. 본문·닉네임 없음).
종료 코드: 0 정상(이미 있음·채울 것 없음 포함) · 1 입력 오류(금칙어 파일 깨짐·생성 파일 깨짐 포함) · 2 시범 거부 ·
  3 scenario_store 없음 · 4 만들 수 없음
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import hashlib
import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import retrieve as R  # noqa: E402  (표준 라이브러리만 쓰는 모듈)


def _store():
    """(assemble, write_scenario, 오류 클래스들) 또는 None. H-1 담당 모듈(app.core.scenario, app.scenario_store)."""
    try:
        from app.core import scenario as sc
        from app import scenario_store as st
    except ImportError:
        return None
    return sc.assemble, st.write_scenario, st


def _pilot_checks():
    """(대상 막힘 까닭 함수, 같은 채널 후보 제외 함수) — app.reference_set 의 시범 판단(폴더 이름 + 시범 기록
    freeze.json projects[]·setup.json items[]). 엔진은 이름(pilot-*)만 보므로 이름이 다른 시범 프로젝트는 여기서 막는다.
    대상은 시범 세션이 모두 잠겼으면 열어도 되고(pilot_block_reason, write_scenario 와 같은 판단), 같은 채널 후보는
    시범이면 잠김과 상관없이 열지 않는다(원 채팅·원 닉네임을 읽게 되므로). 모듈이 없으면 None."""
    try:
        from app import reference_set as rs
    except ImportError:
        return None
    return rs.pilot_block_reason, rs.is_pilot_project


# engine/generate.py 가 파일 경로로 불러 쓰는 공개 이름(이음). 이름을 바꾸면 generate 가 scenario_store_missing 이 된다 —
# tests/test_h3_e2e.py 가 이 두 이름을 본다.
store_api = _store
pilot_api = _pilot_checks


_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$")


def parse_ms(text: str) -> int:
    s = str(text).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = _TIME.match(s)
    if not m:
        raise ValueError("시각 형식은 1:02:03(.5) 또는 정수 ms")
    h, mi, se, fr = m.group(1), m.group(2), m.group(3), m.group(4)
    ms = ((int(h or 0) * 60 + int(mi)) * 60 + int(se)) * 1000
    if fr:
        ms += int(fr.ljust(3, "0"))
    return ms


THRESHOLD_PRESETS = {"test": (45, 60), "default": (R.RetrieveConfig().quiet_max_real, R.RetrieveConfig().target_per_min)}


def parse_threshold(text: str) -> tuple[int, int]:
    """'Q/T' 또는 'test'·'default' → (quiet_max_real, target_per_min). 둘 다 0 이상 정수."""
    s = str(text).strip().lower()
    if s in THRESHOLD_PRESETS:
        return THRESHOLD_PRESETS[s]
    m = re.fullmatch(r"(\d{1,4})\s*/\s*(\d{1,4})", s)
    if not m:
        raise ValueError("문턱 형식은 Q/T (예: 45/60) 또는 test·default")
    return int(m.group(1)), int(m.group(2))


def default_blocklist(project_dir: str) -> str:
    """<데이터 폴더>\\config\\blocklist-ko.txt. 데이터 폴더 = projects 폴더의 부모(…\\chatcoach-data)."""
    projects = os.path.dirname(os.path.abspath(project_dir))
    return os.path.join(os.path.dirname(projects), "config", "blocklist-ko.txt")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def inside_pilot(path: str | None) -> bool:
    """경로의 어느 폴더든 이름이 pilot- 로 시작하면 True(시범 프로젝트·시범 봉인 폴더 chatcoach-data\\ai\\pilot-*).
    engine.generate.inside_pilot 과 같은 규칙(이 도구가 엔진 생성 모듈을 import 하지 않게 복사해 둔다)."""
    if not path:
        return False
    parts = os.path.normpath(os.path.abspath(path)).replace("\\", "/").split("/")
    return any(p.lower().startswith("pilot-") for p in parts)


# ───────────────────────────────────────────────────────────── 생성 채팅 합치기 (--generated, H-3)

GEN_HEADER_KIND = "generation.header"
GEN_MAX_BYTES = 5 * 1024 * 1024
GEN_MAX_LINES = 5000
GEN_TEXT_MAX = 100                      # scenario.schema chatGenerated.text maxLength
GEN_ID_RE = re.compile(r"^g[0-9]{4,6}$")
EMOTE_CODE_RE = re.compile(r"^\{:[A-Za-z0-9_]{1,40}:\}$")
EMOTE_TOKEN_RE = re.compile(r"\{:[^{}:\s]{1,40}:\}")
TOPIC_ID_RE = re.compile(r"^t[0-9]{1,4}$")
PROMPT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MS_MAX = 172_800_000
FILTER_VERSIONS_OK = ("nickname-v1", "copy-v1", "block-v1", "llm-check-v1")   # scenario.schema filters.versions
DEFAULT_MAX_GENERATED_RATIO = 0.25
CTX_MARGIN_MS = 120_000                 # 맥락 chats: 구간 앞 이만큼의 실제 채팅도(베끼기 창 최대 60초보다 넉넉히)


class GenInputError(Exception):
    """생성 파일을 받을 수 없을 때. code 만 밖으로 낸다(본문 없음)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def read_generated(path: str) -> tuple[dict | None, list, str]:
    """생성 파일 → (generation.header 또는 None, 나머지 줄, 읽은 바이트의 sha256). 깨졌으면 GenInputError(bad_generated 등)."""
    if not path or not os.path.isfile(path):
        raise GenInputError("no_generated")
    try:
        with open(path, "rb") as f:
            raw = f.read(GEN_MAX_BYTES + 1)
    except OSError:
        raise GenInputError("bad_generated") from None
    if len(raw) > GEN_MAX_BYTES:
        raise GenInputError("generated_too_big")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise GenInputError("bad_generated") from None
    head, lines = None, []
    for n, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            raise GenInputError("bad_generated") from None
        if isinstance(rec, dict) and rec.get("kind") == GEN_HEADER_KIND:
            if head is not None or lines:
                raise GenInputError("bad_generated")       # 머리는 첫 줄 하나만
            head = rec
            continue
        lines.append(rec)
        if len(lines) > GEN_MAX_LINES:
            raise GenInputError("generated_too_many")
    return head, lines, hashlib.sha256(raw).hexdigest()


def clean_generator(g) -> dict:
    """generation.header 의 generator → 시나리오 머리 generator(스키마 칸만). backend 는 lmstudio 만(외부 거부)."""
    if g is None:
        return {"backend": "lmstudio"}
    if not isinstance(g, dict) or g.get("backend") != "lmstudio":
        raise GenInputError("generated_backend")
    out: dict = {"backend": "lmstudio"}
    v = g.get("model_id")
    if isinstance(v, str) and 0 < len(v) <= 120 and "\n" not in v and "\r" not in v:
        out["model_id"] = v
    for k in ("model_sha256", "prompt_sha256"):
        if isinstance(g.get(k), str) and SHA_RE.match(g[k]):
            out[k] = g[k]
    if isinstance(g.get("prompt_template_id"), str) and PROMPT_ID_RE.match(g["prompt_template_id"]):
        out["prompt_template_id"] = g["prompt_template_id"]
    t = g.get("temperature")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= 2:
        out["temperature"] = t
    if _is_int(g.get("seed")) and g["seed"] >= 0:
        out["seed"] = g["seed"]
    if _is_int(g.get("context_ms")) and 0 <= g["context_ms"] <= 600_000:
        out["context_ms"] = g["context_ms"]
    return out


def sanitize_generated(rec) -> tuple[dict | None, str | None]:
    """생성 줄 하나 → (시나리오에 넣을 칸만 남긴 dict, None) 또는 (None, 뺀 까닭). 본문은 고치지 않는다."""
    if not isinstance(rec, dict) or rec.get("kind", "scenario.chat") != "scenario.chat" \
            or rec.get("source") != "generated":
        return None, "gen_invalid"
    if rec.get("chat_kind", "text") != "text":
        return None, "gen_kind"                    # 후원·운영자 등 다른 종류는 만들지 않는다(사칭 막기, 20 8장 2)
    gid, text = rec.get("gen_id"), rec.get("text")
    if not isinstance(gid, str) or not GEN_ID_RE.match(gid):
        return None, "gen_invalid"
    if not isinstance(text, str) or not text.strip() or len(text) > GEN_TEXT_MAX \
            or any(ch in text for ch in "\r\n\x00"):
        return None, "gen_invalid"
    sv, sr = rec.get("show_vod_ms"), rec.get("show_rel_ms")
    if (sv is None) == (sr is None):
        return None, "gen_invalid"
    t = sv if sv is not None else sr
    if not _is_int(t) or not 0 <= t <= MS_MAX:
        return None, "gen_invalid"
    out: dict = {"source": "generated", "chat_kind": "text",
                 ("show_vod_ms" if sv is not None else "show_rel_ms"): t, "gen_id": gid, "text": text}
    emotes = rec.get("emotes")
    if emotes is not None and (not isinstance(emotes, list) or len(emotes) > 10
                               or not all(isinstance(e, str) and EMOTE_CODE_RE.match(e) for e in emotes)):
        return None, "gen_emote"
    allowed = set(emotes or ())
    if any(tok not in allowed for tok in EMOTE_TOKEN_RE.findall(text)):
        return None, "gen_emote"                   # 본문의 이모티콘 코드는 모두 허용 목록에 있어야 한다(스키마)
    if emotes:
        out["emotes"] = sorted(set(emotes))
    ch = rec.get("checks")
    if isinstance(ch, dict):
        if any(ch.get(k, 0) not in (0, None) for k in ("nickname_hit", "blocked", "llm_flag")):
            return None, "gen_flagged"             # 생성 담당의 거르기에 걸린 줄
        c2 = {k: ch[k] for k in ("nickname_hit", "blocked", "llm_flag") if _is_int(ch.get(k)) and ch[k] == 0}
        cm = ch.get("copy_max")
        if isinstance(cm, (int, float)) and not isinstance(cm, bool) and 0 <= cm <= 1:
            c2["copy_max"] = cm
        if c2:
            out["checks"] = c2
    ctx = rec.get("context")
    if isinstance(ctx, dict):
        c3 = {k: ctx[k] for k in ("asr_from_ms", "asr_to_ms") if _is_int(ctx.get(k)) and 0 <= ctx[k] <= MS_MAX}
        if isinstance(ctx.get("topic_id"), str) and TOPIC_ID_RE.match(ctx["topic_id"]):
            c3["topic_id"] = ctx["topic_id"]
        if c3:
            out["context"] = c3
    if isinstance(rec.get("display_mask"), str):
        out["display_mask"] = rec["display_mask"]
    return out, None


def _viewer_context(project: str) -> tuple[dict, dict]:
    """시청자 훈련과 같은 채점 맥락(app.viewer_flow.build_context: 주제 흐름·전사 낱말·벡터·동의어·문턱).
    읽지 못하면 빈 맥락(주제 축은 no_context → ok). info 는 숫자·코드만."""
    try:
        from app import viewer_flow as vf
        pdata = None
        try:
            with open(os.path.join(project, "project.json"), "r", encoding="utf-8-sig") as f:
                pdata = json.load(f)
        except (OSError, ValueError):
            pdata = None
        return vf.build_context(project, pdata if isinstance(pdata, dict) else None)
    except Exception as e:  # noqa: BLE001 — 맥락 없이도 반복 축은 본다
        return {}, {"error": type(e).__name__}


def _squash_text(text: str) -> str:
    return R._squash(R._EMOTE.sub(" ", text or ""))


GEN_MODE_REPLACE = "replace"
GEN_MODE_ROOM = "room"
GEN_MODES = (GEN_MODE_REPLACE, GEN_MODE_ROOM)


def displace_retrieved(chats: list, kept: list, ws, *, cap_virtual: int, range_ms) -> tuple[list, int]:
    """생성 줄(kept)을 넣을 자리를 retrieved 에서 비운다 → (남긴 줄 목록, 뺀 retrieved 수). 입력은 고치지 않는다.

    1. 창마다: 실제 + retrieved + 생성 이 창 목표(target)를 넘는 만큼 그 창의 retrieved 를 뺀다.
    2. 구간 전체: retrieved + 생성 이 가상 상한(cap_virtual)을 넘는 만큼 더 뺀다.
    빼는 차례는 retrieval_score 낮은 것 → 늦게 보이는 것 → 앞선 줄 순서(결정적). retrieved 가 아닌 줄은 건드리지 않는다."""
    import bisect
    from app.core import gen_filter as GF
    wins = sorted((w.start_ms, w.end_ms, w.real, w.target) for w in ws)
    starts = [w[0] for w in wins]

    def widx(t) -> int | None:
        if not _is_int(t):
            return None
        i = bisect.bisect_right(starts, t) - 1
        return i if 0 <= i < len(wins) and wins[i][0] <= t < wins[i][1] else None

    def order(j: int):
        ln = chats[j]
        s = ln.get("retrieval_score")
        s = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else 0.0
        t = ln.get("show_vod_ms") if _is_int(ln.get("show_vod_ms")) else -1
        return (s, -t, -j)

    per_win: dict[int, list[int]] = {}
    ret_all: list[int] = []
    for j, ln in enumerate(chats):
        if ln.get("source") != "retrieved":
            continue
        ret_all.append(j)
        i = widx(ln.get("show_vod_ms"))
        if i is not None:
            per_win.setdefault(i, []).append(j)
    gen_per: dict[int, int] = {}
    for c in kept:
        i = widx(GF.show_time(c, range_ms))
        if i is not None:
            gen_per[i] = gen_per.get(i, 0) + 1
    drop: set[int] = set()
    for i, g in sorted(gen_per.items()):
        _a, _b, real, target = wins[i]
        mine = per_win.get(i, [])
        over = real + len(mine) + g - target
        if over > 0:
            drop.update(sorted(mine, key=order)[:over])
    over = (len(ret_all) - len(drop)) + len(kept) - int(cap_virtual)
    if over > 0:
        drop.update(sorted((j for j in ret_all if j not in drop), key=order)[:over])
    return [ln for j, ln in enumerate(chats) if j not in drop], len(drop)


def merge_generated(project: str, from_ms: int, to_ms: int, header: dict, chats: list, *, path: str,
                    blocklist_path: str | None, topic_flow_path: str | None, seed: int, cfg,
                    max_generated_ratio: float | None, projects_root: str | None = None, exclude=None,
                    mode: str = "replace", allow_no_identities: bool = False) -> tuple[dict, list, dict]:
    """retrieved 결과(header 칸, 줄들)에 생성 줄을 합친다 → (새 header, 합친 줄들, 통계). 통계는 숫자·코드만.

    mode 'replace'(기본): 생성 몫(--max-generated-ratio)을 retrieved 보다 먼저 잡고, 남긴 생성 줄만큼 그 창의 retrieved 를
    점수(retrieval_score) 낮은 것부터 뺀다 — 창 목표·구간 가상 비율은 그대로 지킨다(displace_retrieved). 'room': retrieved
    가 채우고 남은 자리에만 넣는다. 입력 dict 는 고치지 않는다. 생성 줄을 하나도 남기지 못하면 header 는 filters.dropped 만
    늘어난다.

    원 닉네임 목록(chat\\protected\\identities.jsonl)이 없으면 두 번째 막기의 닉네임 대조가 꺼지므로 GenInputError
    ('nicknames_missing') 로 닫는다. allow_no_identities=True 일 때만 진행(통계 nicknames_known=False)."""
    from app.core import gen_filter as GF
    from app.core import scenario as SCN
    from app.core import viewer_score as VS
    ghead, raw, file_sha = read_generated(path)
    target = R.load_project(project)
    if target.nicknames is None and not allow_no_identities:
        raise GenInputError("nicknames_missing")
    if ghead is not None and ghead.get("project_id") not in (None, target.project_id):
        raise GenInputError("generated_project_mismatch")
    generator = clean_generator(ghead.get("generator") if ghead is not None else None)
    rng = (int(from_ms), int(to_ms))

    invalid: dict[str, int] = {}
    cands: list[dict] = []
    seen_gid: set[str] = set()
    for rec in raw:
        c, why = sanitize_generated(rec)
        if c is None:
            invalid[why] = invalid.get(why, 0) + 1
            continue
        if c["gen_id"] in seen_gid:
            invalid["gen_dup_id"] = invalid.get("gen_dup_id", 0) + 1
            continue
        seen_gid.add(c["gen_id"])
        cands.append(c)

    # 두 번째 막기(20 8장 1·4·5): 금칙어 · 원 닉네임 · 실제 채팅을 그대로 옮긴 줄. 낱말·닉네임은 메모리에서만
    blocklist, _bl_sha = R.load_blocklist(blocklist_path)
    nicks = target.nicknames or frozenset()
    real_keys = {k for k in (_squash_text(c.text) for c in target.chats if c.kind == "text") if k}
    guard = {"blocked": 0, "nickname": 0, "copy": 0}
    passed: list[dict] = []
    for c in cands:
        sq = _squash_text(c["text"])
        if any(w in sq for w in blocklist):
            guard["blocked"] += 1
        elif any(nk in sq for nk in nicks):
            guard["nickname"] += 1
        elif sq and sq in real_keys:
            guard["copy"] += 1
        else:
            passed.append(c)

    # 화면에 함께 흐를 채팅(실제 + retrieved 본문) — G 채점기의 베끼기 맥락. 본문은 메모리에서만
    by_id = {c.chat_id: c for c in target.chats}
    ctx_chats = [(c.t_ms, c.text) for c in target.chats
                 if c.kind == "text" and c.t_ms is not None and rng[0] - CTX_MARGIN_MS <= c.t_ms < rng[1]]
    others: dict[str, dict] = {}
    if any(ln.get("from_scope") == "same_channel" for ln in chats):
        root = projects_root if projects_root is not None else os.path.dirname(os.path.abspath(project))
        dirs, _p = R.find_same_channel_projects(project, root, target.channel_id, exclude=exclude)
        want = {ln.get("from_project_id") for ln in chats if ln.get("from_scope") == "same_channel"}
        for d in dirs:
            if R.read_project_meta(d)[0] in want:
                o = R.load_project(d)
                others[o.project_id] = {c.chat_id: c for c in o.chats}
    retrieved_times = []
    for ln in chats:
        t = ln.get("show_vod_ms")
        if not _is_int(t):
            continue
        retrieved_times.append(t)
        src = by_id if ln.get("from_project_id") == target.project_id else others.get(ln.get("from_project_id"), {})
        oc = src.get(ln.get("from_chat_id"))
        if oc is not None:
            ctx_chats.append((t, oc.text))

    context, ctx_info = _viewer_context(project)
    blocks = context.get("topic_blocks") or []
    if topic_flow_path:
        tf_blocks, _sha = R.load_topic_flow(topic_flow_path)
        if tf_blocks:
            blocks = tf_blocks
    settings = context.get("settings") if isinstance(context.get("settings"), VS.ViewerScoreSettings) else None
    sctx = VS.ScoreContext.build(blocks, context.get("transcript") or [], ctx_chats, settings=settings,
                                 topic_vectors=context.get("topic_vectors"), synonyms=context.get("synonyms"))

    ws = R.windows(target.chats, rng[0], rng[1], cfg)
    if mode not in GEN_MODES:
        raise GenInputError("bad_generated_mode")
    # replace: 생성 몫을 retrieved 보다 먼저 잡는다(자리 계산에서 retrieved 를 빼고, 남긴 생성 줄만큼 그 창의 retrieved 를
    # 점수 낮은 것부터 뺀다). room: 지난 방식 — retrieved 가 채우고 남은 자리에만(큰 방송은 거의 늘 0)
    quota = GF.plan_quota(ws, () if mode == GEN_MODE_REPLACE else retrieved_times,
                          max_virtual_ratio=cfg.max_virtual_ratio, max_generated_ratio=max_generated_ratio)
    res = GF.filter_generated(passed, sctx, settings=settings, quota=quota, range_ms=rng)
    displaced = 0
    if mode == GEN_MODE_REPLACE and res.kept:
        chats, displaced = displace_retrieved(chats, res.kept, ws, cap_virtual=quota.info["cap_virtual"],
                                              range_ms=rng)

    # 가림 이름: 줄마다 다름(D-20 7). 실제 가림 닉네임·retrieved 이름·앞선 생성 이름과 겹치거나 모양이 틀리면 새로
    taken = set(target.masks) | {ln.get("display_mask") for ln in chats if isinstance(ln.get("display_mask"), str)}
    kept: list[dict] = []
    renamed = 0
    for c in res.kept:
        d = dict(c)
        m = d.get("display_mask")
        if not isinstance(m, str) or not SCN.DISPLAY_MASK_RE.match(m) or len(m) > 10 or m in taken:
            m = R._masks([f"gen|{d['gen_id']}"], taken, seed)[f"gen|{d['gen_id']}"]
            renamed += 1
        taken.add(m)
        d["display_mask"] = m
        kept.append(d)

    # 머리: 생성기·거르기 수(숫자만). 거르기 판 칸(versions)은 스키마 목록 안의 것만 옮긴다
    h = json.loads(json.dumps(header))
    filt = h.setdefault("filters", {})
    dropped = filt.setdefault("dropped", {})
    gfil = (ghead or {}).get("filters") if isinstance((ghead or {}).get("filters"), dict) else {}
    for k, n in (gfil.get("dropped") or {}).items() if isinstance(gfil.get("dropped"), dict) else ():
        if isinstance(k, str) and re.fullmatch(r"[a-z0-9_.-]{1,40}", k) and _is_int(n) and n >= 0:
            key = k if k.startswith("gen_") else f"gen_{k}"
            dropped[key] = dropped.get(key, 0) + n
    for k, n in invalid.items():
        dropped[k] = dropped.get(k, 0) + n
    for k, n in guard.items():
        if n:
            dropped[f"gen_{k}"] = dropped.get(f"gen_{k}", 0) + n
    for k, n in GF.dropped_by_axis(res.stats).items():
        if n:
            dropped[f"gen_{k}"] = dropped.get(f"gen_{k}", 0) + n
    vers = list(filt.get("versions") or [])
    for v in (gfil.get("versions") or []) if isinstance(gfil.get("versions"), list) else ():
        if v in FILTER_VERSIONS_OK and v not in vers:
            vers.append(v)
    if vers:
        filt["versions"] = vers
    if kept:
        h["generator"] = generator
        cb = f"{h.get('created_by', 'make_scenario')} + gen_filter {GF.VERSION}"
        h["created_by"] = cb[:80]
    if displaced:
        dropped["retrieved_displaced"] = dropped.get("retrieved_displaced", 0) + displaced
    stats = {"file_sha256": file_sha, "lines": len(raw), "valid": len(cands), "invalid": dict(sorted(invalid.items())),
             "guard": guard, "filter": res.stats, "renamed": renamed, "kept": len(kept), "mode": mode,
             "retrieved_displaced": displaced, "nicknames_known": target.nicknames is not None,
             "context": {k: v for k, v in (ctx_info or {}).items() if isinstance(v, (int, str)) or v is None},
             "topic_blocks": len(blocks)}
    return h, list(chats) + kept, stats


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    d = R.RetrieveConfig()
    ap = argparse.ArgumentParser(description="가상 채팅 시나리오(retrieved) 만들기")
    ap.add_argument("--project", required=True)
    ap.add_argument("--from", dest="from_t", required=True)
    ap.add_argument("--to", dest="to_t", required=True)
    ap.add_argument("--projects-root")
    ap.add_argument("--blocklist")
    ap.add_argument("--topic-flow")
    ap.add_argument("--seed", default="0")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", help="채우기 문턱 'Q/T'(예: 45/60) · test(45/60) · default(2/6)")
    ap.add_argument("--quiet-max-real", type=int, default=None,
                    help=f"이 수 이하인 1분 창을 채운다(기본 {d.quiet_max_real})")
    ap.add_argument("--target-per-min", type=int, default=None, help=f"채운 창의 실제+가상 목표(기본 {d.target_per_min})")
    ap.add_argument("--max-virtual-ratio", type=float, default=d.max_virtual_ratio)
    ap.add_argument("--generated", help="생성 채팅 후보 파일(JSON Lines)을 retrieved 와 합친다")
    ap.add_argument("--max-generated-ratio", type=float, default=DEFAULT_MAX_GENERATED_RATIO,
                    help=f"생성 / (실제 + 가상) 상한(기본 {DEFAULT_MAX_GENERATED_RATIO})")
    ap.add_argument("--generated-mode", choices=GEN_MODES, default=GEN_MODE_REPLACE,
                    help="replace(기본): 생성 몫을 먼저 잡고 그 창의 점수 낮은 retrieved 를 뺀다 · room: 남은 자리에만")
    ap.add_argument("--allow-no-identities", action="store_true",
                    help="--generated 를 원 닉네임 목록(identities.jsonl) 없이도 합친다(기본은 nicknames_missing 로 거부)")
    ns = ap.parse_args(argv)

    project = os.path.abspath(ns.project)
    # 읽기 전에 거부: 시범 프로젝트, 그리고 시범 폴더(시범 봉인 폴더 ai\pilot-* 포함) 안의 어떤 입력 파일·찾을 곳도(D-23 2)
    if R.is_pilot_dir(project) or any(inside_pilot(p) for p in (project, ns.generated, ns.topic_flow, ns.blocklist,
                                                                  ns.projects_root) if p):
        _emit({"error": "pilot_locked"})
        return 2
    try:
        a, b = parse_ms(ns.from_t), parse_ms(ns.to_t)
        seed = int(ns.seed)
        if seed < 0:
            raise ValueError
        qmr = d.quiet_max_real if ns.quiet_max_real is None else ns.quiet_max_real
        tpm = d.target_per_min if ns.target_per_min is None else ns.target_per_min
        if ns.threshold is not None:
            if ns.quiet_max_real is not None or ns.target_per_min is not None:
                raise ValueError("--threshold 와 --quiet-max-real·--target-per-min 은 같이 쓰지 않는다")
            qmr, tpm = parse_threshold(ns.threshold)
        cfg = R.RetrieveConfig(quiet_max_real=qmr, target_per_min=tpm, max_virtual_ratio=ns.max_virtual_ratio)
        gr = ns.max_generated_ratio
        if gr != gr or not 0.0 <= gr < 1.0:
            raise ValueError("--max-generated-ratio 는 0 이상 1 미만")
    except ValueError:
        _emit({"error": "bad_argument"})
        return 1
    if not os.path.isdir(project):
        _emit({"error": "no_project"})
        return 1
    store = _store()
    if store is None:
        _emit({"error": "scenario_store_missing"})
        return 3
    assemble, write_scenario, st = store
    checks = _pilot_checks()
    if checks is None:
        _emit({"error": "scenario_store_missing"})
        return 3
    block_reason, is_pilot = checks
    try:
        blocked = block_reason(project) is not None
    except Exception:  # noqa: BLE001 — 판단이 안 되면 막는다(블라인드 쪽으로 안전하게)
        blocked = True
    if blocked:                                     # 시범 기록에 적힌 프로젝트도 읽기 전에 거부
        _emit({"error": "pilot_locked"})
        return 2
    bl = ns.blocklist if ns.blocklist is not None else default_blocklist(project)
    try:
        header, chats, stats = R.make_retrieved_scenario(project, a, b, projects_root=ns.projects_root,
                                                         blocklist_path=bl, topic_flow_path=ns.topic_flow,
                                                         seed=seed, cfg=cfg, now=now, exclude=is_pilot)
    except R.RetrieveError as e:
        _emit({"error": e.code})
        return 2 if e.code == "pilot_locked" else (
            1 if e.code in ("bad_range", "no_messages", "bad_seed", "bad_blocklist") else 4)
    gen_stats = None
    if ns.generated:
        try:
            header, chats, gen_stats = merge_generated(project, a, b, header, chats, path=ns.generated,
                                                       blocklist_path=bl, topic_flow_path=ns.topic_flow, seed=seed,
                                                       cfg=cfg, max_generated_ratio=ns.max_generated_ratio,
                                                       projects_root=ns.projects_root, exclude=is_pilot,
                                                       mode=ns.generated_mode,
                                                       allow_no_identities=bool(ns.allow_no_identities))
        except GenInputError as e:
            _emit({"error": e.code})
            return 1
        except R.RetrieveError as e:
            _emit({"error": e.code})
            return 2 if e.code == "pilot_locked" else 1
    when = now or datetime.now().astimezone()
    records = assemble(header, chats, yyyymmdd=when.strftime("%Y%m%d"))
    head = records[0]
    found = "blocklist_sha256" in head.get("filters", {})
    out = {"scenario_id": head["scenario_id"], "counts": head["counts"], "stats": stats,
           "blocklist_found": found, "written": False,
           "fill": {"quiet_max_real": cfg.quiet_max_real, "target_per_min": cfg.target_per_min}}
    if gen_stats is not None:
        out["generated"] = gen_stats
        out["fill"]["max_generated_ratio"] = ns.max_generated_ratio
    if not found and bl:
        # 지금처럼 경고만 하고 진행한다(D-20 6). 경로·낱말은 적지 않는다(코드만)
        out["warnings"] = ["blocklist_missing"]
        print("경고: 금칙어 목록 파일이 없어 금칙어 거르기 없이 만듭니다 (blocklist_missing)", file=sys.stderr)
    if gen_stats is not None and not gen_stats.get("nicknames_known", True):   # --allow-no-identities 로 연 때만
        out.setdefault("warnings", []).append("nicknames_missing")
        print("경고: 원 닉네임 목록이 없어 생성 줄의 닉네임 대조 없이 합칩니다 (nicknames_missing)", file=sys.stderr)
    if ns.dry_run:
        problems = st.validate(records)
        out["valid"] = not problems
        out["problems"] = len(problems)
        _emit(out)
        return 0
    if not (head["counts"].get("retrieved") or head["counts"].get("generated")):   # 채울 창·후보가 없다: 빈 시나리오는 쓰지 않는다
        out["reason"] = "nothing_to_fill"
        _emit(out)
        return 0
    try:
        res = write_scenario(project, records)
    except st.ScenarioBlockedError:
        _emit({"error": "pilot_locked"})
        return 2
    except st.ScenarioInvalidError as e:
        _emit({"error": "invalid", "problems": len(getattr(e, "problems", ()) or ())})
        return 4
    except (st.ScenarioStoreError, OSError):
        _emit({"error": "store_error"})
        return 4
    out.update({"scenario_id": res.scenario_id, "written": bool(res.created)})
    if not res.created:
        out["existing"] = res.scenario_id
    _emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
