"""H-3 가상 채팅 '생성' 끝에서 끝까지 스모크 — 가짜 LLM 으로 (08 D-23 4, 20 4장·8장·10장 H-3, D-05·D-06·D-16).

한 번에 (사람 클릭 0, Claude 가 돌린다):
  0. 자리: %LOCALAPPDATA%\\ChatCoachSmoke\\gen-e2e-<시각>-<pid>\\chatcoach-data\\{projects\\<원 폴더 이름>,config}.
     원 프로젝트(개발용 p15767532 등)는 **읽기만** 하고, 필요한 파일만(project.json · chat\\messages.jsonl ·
     chat\\protected\\identities.jsonl · derived\\asr-words.json·asr-topic-flow.json·asr-topic-vectors.json, 데이터 폴더의
     config\\blocklist-ko.txt·synonyms-ko.txt) 사본으로 옮긴다. 그 밖에는 쓰지 않는다(앱 데이터 폴더도 자리 안 appdata\\,
     %LOCALAPPDATA% 도 자리 안 localappdata\\). 끝나면 shutil.rmtree(--keep 이면 남김).
     원 프로젝트가 시범(폴더 이름 pilot-* · 시범 기록에 적힘)이면 아무것도 읽기 전에 종료 2.
  1. engine generate --fake <합성 응답 파일> --out <생성 후보 A>  (CLI 그대로, 함수 주입 가짜)
  2. engine generate --model <가짜 모델> --url http://127.0.0.1:<가짜 서버> --send-log … --out <생성 후보 B>
     (engine.llm_local → urllib → 이 PC 루프백의 engine.llm_fake.FakeLLMServer. 가짜는 받은 '자료' 낱말로 합성 줄을 짓는다).
     이 동안 소켓 연결은 가짜 서버 포트(127.0.0.1)만 허용하고 나머지는 막고 센다(외부 호출 0 확인).
     보낸 기록(D-06 5): 요청마다 한 줄, 보낸 글자 수, 본문 없음.
  3. make_scenario --generated <B> → retrieved 와 합쳐 G 채점기(app.core.gen_filter)로 거른 시나리오를 사본에 쓴다.
     (후보 A 는 --dry-run 으로 숫자만 — 고정 합성 줄은 대부분 주제 weak 로 빠진다)
  4. 스트리머 훈련: app.scenario_mix(find_scenario · load_messages · make_resolver) → TrainerView(offscreen, 가짜 플레이어)에
     섞여 흐르고, 가상 줄은 닉네임 칸 앞 '가'·'생' 한 글자 + 뒤 ' · 가져옴'·' · 생성' 으로 갈린다. 생성 줄 하나를 고르고 견주기.
     훈련 기록(training\\*.jsonl)에는 생성 본문·가림 이름·화면 id 가 없다.
  5. 바이트 검사: 시나리오 파일·생성 후보 파일·보낸 기록·가짜 서버가 받은 요청·훈련 기록에 실제 원 닉네임·viewer_id·
     user_id_hash·실제 가림 닉네임(가상 줄 이름으로)·실제 채팅 본문(8자 이상)이 0건, 생성 줄 본문이 실제 채팅과 같은 것 0건.

출력은 숫자·코드만(본문·닉네임·낱말 없음). 사람 클릭: 이 스모크 0. 실제 앱에서 훈련을 열 때는 지금과 같다
(훈련 → 스트리머 훈련, 가상 채팅 섞기는 기본 켬 — 생성 때문에 늘어나는 클릭 0). 실제 생성(시범 뒤, D-22 3)에서만
사용자가 LM Studio 서버를 켠다.

사용(저장소 루트, 앱 .venv): QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python tools\\gen_e2e.py
        [--project <원 프로젝트>(기본 ..\\chatcoach-data\\projects\\p15767532)] [--from 20:00] [--to 40:00]
        [--threshold test] [--seed 0] [--keep] [--base <자리>] [--json]
종료 코드: 0 검사 모두 통과 · 1 검사 실패 · 2 입력 오류(자리·시범·원 프로젝트 없음)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import time
from datetime import datetime
from typing import Iterable, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TOOL = "gen_e2e"
TOOL_VERSION = "0.1"
USER_CLICKS = 0                              # 이 스모크의 사람 클릭 (D-16)
SMOKE_DIR = "ChatCoachSmoke"
DEFAULT_PROJECT = os.path.join(os.path.dirname(ROOT), "chatcoach-data", "projects", "p15767532")
COPY_FILES = (("project.json",), ("chat", "messages.jsonl"), ("chat", "protected", "identities.jsonl"),
              ("derived", "asr-words.json"), ("derived", "asr-topic-flow.json"), ("derived", "asr-topic-vectors.json"))
CONFIG_FILES = ("blocklist-ko.txt", "synonyms-ko.txt")
BODY_MIN_CHARS = 8                           # 바이트 검사에 쓰는 실제 채팅 본문의 최소 길이(짧은 감탄은 우연히 겹친다)
LABELER = "L01"

# 고정 합성 응답(후보 A). 실제 채팅에서 가져오지 않았다.
STATIC_RESPONSES = (
    "1. 오 방금 그 장면 진짜 신기했어요\n2. 다음 판은 어떤 캐릭터로 하실 거예요?\n3. 저도 그거 해 봤는데 어렵더라고요",
    "- 그 노래 제목이 뭐였죠?\n- 오늘 분위기 좋네요\n- @합성멘션 이거 보세요",
)

# 받은 '자료' 낱말로 합성 줄을 짓는 틀(후보 B). 낱말 한두 개 + 합성 말 — 전사 베끼기·말버릇에 덜 걸리게 머리·끝을 바꿔 둔다.
TEMPLATES = (
    "{a} 얘기 조금 더 해 주세요",
    "방금 말한 {a} 혹시 처음 해 보신 거예요?",
    "{a} 쪽이 저는 더 궁금해요",
    "와 {a} 이건 몰랐네",
    "{a} 다음엔 뭐 하실 거예요?",
    "저도 {a} 좋아하는데 반갑네요",
    "{a} 이야기 들으니까 생각나는 게 있어요",
    "오늘 {a} 분위기 좋다",
    "{a} 랑 {b} 중에 뭐가 나아요?",
    "근데 {a} 는 어떻게 된 거예요",
)
BAD_LINE = "@합성멘션 여기 좀 봐요"            # 요청마다 하나 — 거르기 4(멘션)가 도는지
_WORDS_RE = re.compile(r"^- 방송에서 방금 나온 낱말: (.*)$", re.M)
_KW_RE = re.compile(r"^- 주제 낱말: (.*)$", re.M)
_N_RE = re.compile(r"시청자 채팅 (\d+)줄")


class E2EError(RuntimeError):
    """입력 오류(종료 2)."""


# ───────────────────────────────────────────────────────────── 자리 · 사본

def default_base(stamp: str | None = None) -> str:
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    lad = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(lad, SMOKE_DIR, f"gen-e2e-{stamp}-{os.getpid()}")


def check_base(base: str) -> str:
    """자리 규칙: 경로에 ChatCoachSmoke 폴더가 있어야 하고, 실제 데이터 폴더(chatcoach-data, 저장소 옆) 아래면 안 되며,
    아직 없어야 한다(tools/pilot_rehearsal 과 같은 규칙)."""
    b = os.path.abspath(base)
    parts = [p.lower() for p in os.path.normpath(b).split(os.sep)]
    if SMOKE_DIR.lower() not in parts:
        raise E2EError("자리는 %LOCALAPPDATA%\\ChatCoachSmoke 아래여야 합니다")
    real = os.path.normcase(os.path.join(os.path.dirname(ROOT), "chatcoach-data"))
    nb = os.path.normcase(b)
    if nb == real or nb.startswith(real + os.sep):
        raise E2EError("실제 데이터 폴더(chatcoach-data) 아래에는 사본을 만들지 않습니다")
    if os.path.exists(b):
        raise E2EError("자리가 이미 있습니다(새 자리에서만)")
    return b


def check_source(src: str) -> str:
    """원 프로젝트 규칙: 시범이면(이름 pilot-* — 아무것도 열기 전에 / 시범 기록에 적힘) E2EError. messages.jsonl 이 있어야 한다."""
    from engine import generate as G
    s = os.path.abspath(src)
    if G.inside_pilot(s):
        raise E2EError("시범 프로젝트·시범 봉인 폴더는 열지 않습니다 (D-23 2)")
    from app import reference_set as rs
    if rs.is_pilot_project(s):
        raise E2EError("시범 기록에 적힌 프로젝트는 열지 않습니다 (D-23 2)")
    if not os.path.isfile(os.path.join(s, "chat", "messages.jsonl")):
        raise E2EError("원 프로젝트에 chat\\messages.jsonl 이 없습니다")
    return s


def copy_project(src: str, data_dir: str) -> tuple[str, dict]:
    """필요한 파일만 사본으로(읽기만). (사본 프로젝트 폴더, 숫자 {files, bytes, config})."""
    dst = os.path.join(data_dir, "projects", os.path.basename(src))
    n = size = 0
    for rel in COPY_FILES:
        a = os.path.join(src, *rel)
        if not os.path.isfile(a):
            continue
        b = os.path.join(dst, *rel)
        os.makedirs(os.path.dirname(b), exist_ok=True)
        shutil.copyfile(a, b)
        n += 1
        size += os.path.getsize(b)
    cfg = 0
    src_cfg = os.path.join(os.path.dirname(os.path.dirname(src)), "config")
    for name in CONFIG_FILES:
        a = os.path.join(src_cfg, name)
        if os.path.isfile(a):
            os.makedirs(os.path.join(data_dir, "config"), exist_ok=True)
            shutil.copyfile(a, os.path.join(data_dir, "config", name))
            cfg += 1
    return dst, {"files": n, "bytes": size, "config": cfg}


# ───────────────────────────────────────────────────────────── 가짜 LLM

def adaptive_respond(messages: Sequence[Mapping]) -> str:
    """요청의 '자료' 칸(주제 낱말 → 전사 낱말 순)에서 낱말을 골라 합성 줄 n 개 + 거르기 시험 줄 1개. 결정적."""
    user = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")

    def _list(rx):
        m = rx.search(user)
        if not m or m.group(1).strip() == "(없음)":
            return []
        return [w.strip() for w in m.group(1).split(",") if w.strip()]
    pool = _list(_KW_RE) + list(reversed(_list(_WORDS_RE)))
    m = _N_RE.search(user)
    n = int(m.group(1)) if m else 3
    if not pool:
        return "\n".join(f"{i}. {t.format(a='오늘', b='내일')}" for i, t in enumerate(TEMPLATES[:n], 1))
    h = int(hashlib.sha256("|".join(pool).encode("utf-8")).hexdigest()[:8], 16)
    out = []
    for i in range(n):
        t = TEMPLATES[(h + i) % len(TEMPLATES)]
        a = pool[(h // 7 + i * 3) % len(pool)]
        b = pool[(h // 7 + i * 3 + 1) % len(pool)]
        out.append(t.format(a=a, b=b))
    out.append(BAD_LINE)
    return "\n".join(f"{i}. {s}" for i, s in enumerate(out, 1))


@contextlib.contextmanager
def loopback_only(ports: Iterable[int]):
    """이 블록 안의 소켓 연결은 127.0.0.1 의 ports 만. 나머지는 ConnectionRefusedError 로 막고 센다."""
    allow = {int(p) for p in ports}
    seen = {"allowed": 0, "blocked": 0}
    orig_connect, orig_cc = socket.socket.connect, socket.create_connection

    def _ok(addr) -> bool:
        return isinstance(addr, tuple) and len(addr) >= 2 and addr[0] == "127.0.0.1" and int(addr[1]) in allow

    def connect(self, addr):
        if _ok(addr):
            seen["allowed"] += 1
            return orig_connect(self, addr)
        seen["blocked"] += 1
        raise ConnectionRefusedError("gen_e2e: 루프백 가짜 서버 밖 연결은 막습니다")

    def create_connection(addr, *a, **k):
        if _ok(addr):
            return orig_cc(addr, *a, **k)
        seen["blocked"] += 1
        raise ConnectionRefusedError("gen_e2e: 루프백 가짜 서버 밖 연결은 막습니다")

    socket.socket.connect = connect
    socket.create_connection = create_connection
    try:
        yield seen
    finally:
        socket.socket.connect = orig_connect
        socket.create_connection = orig_cc


# ───────────────────────────────────────────────────────────── 도구 부르기(같은 프로세스)

def _make_scenario_module():
    path = os.path.join(ROOT, "tools", "make_scenario.py")
    spec = importlib.util.spec_from_file_location("_gen_e2e_make_scenario", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(main, argv, **kw) -> tuple[int, dict]:
    """도구 main(argv) → (종료 코드, 표준 출력 마지막 JSON 줄). 출력은 화면에 내지 않는다(숫자 검사만)."""
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = main(argv, **kw)
    out = {}
    for line in reversed(buf.getvalue().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                out = json.loads(line)
            except ValueError:
                out = {}
            break
    return int(rc), out


# ───────────────────────────────────────────────────────────── 바이트 검사

def _squash(s: str) -> str:
    from app.core import textnorm as TN
    return TN.squash(s or "")


def real_markers(project: str) -> dict:
    """실제 자료의 표지(메모리에서만): 원 닉네임(2자 이상), viewer_id, user_id_hash, 가림 닉네임, 본문(8자 이상), 본문 정규화 꼴."""
    nicks, vids, hashes, masks, bodies, squashed = set(), set(), set(), set(), set(), set()
    msg = os.path.join(project, "chat", "messages.jsonl")
    with open(msg, "r", encoding="utf-8-sig") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            v = r.get("viewer_id")
            if isinstance(v, str) and len(v) >= 6:
                vids.add(v)
            m = r.get("nickname_masked")
            if isinstance(m, str) and m:
                masks.add(m)
            t = r.get("text")
            if r.get("kind") == "text" and isinstance(t, str) and t.strip():
                sq = _squash(t)
                if sq:
                    squashed.add(sq)
                if len(t.strip()) >= BODY_MIN_CHARS:
                    bodies.add(t.strip())
    ident = os.path.join(project, "chat", "protected", "identities.jsonl")
    if os.path.isfile(ident):
        with open(ident, "r", encoding="utf-8-sig") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                h = r.get("user_id_hash")
                if isinstance(h, str) and len(h) >= 8:
                    hashes.add(h)
                v = r.get("viewer_id")
                if isinstance(v, str) and len(v) >= 6:
                    vids.add(v)
                for n in r.get("nicknames") or []:
                    name = n.get("nickname") if isinstance(n, dict) else n
                    if isinstance(name, str) and len(_squash(name)) >= 2:
                        nicks.add(name)
    return {"nicknames": nicks, "nick_squash": {_squash(n) for n in nicks}, "viewer_ids": vids, "hashes": hashes,
            "masks": masks, "bodies": bodies, "body_squash": squashed}


def scan_text(text: str, mk: Mapping) -> dict:
    """한 글(파일 내용)에서 실제 표지 건수. 가림 닉네임은 따로(가상 줄 이름) 본다."""
    return {"nickname": sum(1 for n in mk["nicknames"] if n in text),
            "viewer_id": sum(1 for v in mk["viewer_ids"] if v in text),
            "hash": sum(1 for h in mk["hashes"] if h in text),
            "body": sum(1 for b in mk["bodies"] if b in text)}


def scan_generated_lines(lines: Sequence[Mapping], mk: Mapping) -> dict:
    """생성 줄 본문·이름 검사: 원 닉네임(정규화) 포함 · 실제 본문과 같음 · 실제 가림 닉네임을 이름으로 · 식별자 칸."""
    out = {"nickname_in_text": 0, "same_as_real": 0, "real_mask_as_name": 0, "id_fields": 0, "not_marked": 0}
    for ln in lines:
        sq = _squash(str(ln.get("text") or ""))
        if any(n and n in sq for n in mk["nick_squash"]):
            out["nickname_in_text"] += 1
        if sq and sq in mk["body_squash"]:
            out["same_as_real"] += 1
        if ln.get("display_mask") in mk["masks"]:
            out["real_mask_as_name"] += 1
        if any(k in ln for k in ("chat_id", "viewer_id", "nickname", "nickname_masked", "from_chat_id")):
            out["id_fields"] += 1
        if ln.get("source") != "generated" or not str(ln.get("gen_id") or "").startswith("g"):
            out["not_marked"] += 1
    return out


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _read_jsonl(path: str) -> list[dict]:
    out = []
    for line in _read_text(path).splitlines():
        if line.strip():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
    return out


# ───────────────────────────────────────────────────────────── 기록

class Log:
    def __init__(self, verbose: bool = True):
        self.out: dict = {"tool": TOOL, "tool_version": TOOL_VERSION, "steps": {}, "checks": {}, "numbers": {},
                          "user_clicks": USER_CLICKS}
        self.verbose = verbose

    def step(self, name: str, t: float) -> None:
        self.out["steps"][name] = round(time.perf_counter() - t, 2)

    def check(self, name: str, ok, detail=None) -> bool:
        ok = bool(ok)
        self.out["checks"][name] = ok if detail is None else {"ok": ok, "detail": detail}
        if self.verbose:
            print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if detail is None else f"  {json.dumps(detail, ensure_ascii=False)}"),
                  flush=True)
        return ok

    def num(self, name: str, value) -> None:
        self.out["numbers"][name] = value

    @property
    def ok(self) -> bool:
        return all((v if isinstance(v, bool) else v.get("ok")) for v in self.out["checks"].values())


# ───────────────────────────────────────────────────────────── 훈련 화면

def _player_class():
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget

    class QuietPlayer(QWidget):
        """영상 없는 가짜 플레이어(소리·창 없음). 훈련 화면이 부르는 것만."""
        positionChanged = Signal(int)
        durationChanged = Signal(int)
        playingChanged = Signal(bool)
        errorOccurred = Signal(str)
        fileLoaded = Signal()
        clicked = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.pos = 0
            self.playing = False

        def load(self, path): return True
        def play(self): self.playing = True; self.playingChanged.emit(True)            # noqa: E702
        def pause(self): self.playing = False; self.playingChanged.emit(False)         # noqa: E702
        def toggle(self): self.pause() if self.playing else self.play()
        def seek(self, file_ms, exact=True): self.pos = int(file_ms)                    # noqa: E704
        def position_ms(self): return self.pos
        def duration_ms(self): return 0
        def frame_step(self, n): pass
        def set_speed(self, x): pass
        def set_volume(self, v): pass
        def set_ab_loop(self, a, b): pass
        def set_mute(self, m): pass
        def close_player(self): pass
    return QuietPlayer


def run_training(project: str, rng: tuple[int, int], scenario_id: str | None, log: Log) -> dict:
    """앱과 같은 길(scenario_mix)로 시나리오를 찾아 스트리머 훈련 화면에 섞는다. 숫자만 돌려준다."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app import reference_set as rs
    from app import scenario_mix, training_store as ts
    from app.core import training as trn
    from app.ui import trainer_view as tv
    app = QApplication.instance() or QApplication([])
    scn, counts = scenario_mix.find_scenario(project, rng)
    res: dict = {"found": scn is not None, "files": counts.get("files", 0)}
    if not log.check("training_finds_scenario", scn is not None and scn.header.get("scenario_id") == scenario_id,
                     {"files": counts.get("files", 0), "ok": counts.get("ok", 0)}):
        return res
    real = scenario_mix.load_messages(project)
    pid = scn.header.get("project_id")
    texts = [r for r in real if r.get("kind") == "text" and isinstance(r.get("t_ms"), int) and rng[0] <= r["t_ms"] < rng[1]]
    if not log.check("training_has_real_chat", bool(texts), {"real_in_range": len(texts)}):
        return res
    ref = trn.RefReaction(texts[0]["chat_id"], texts[0]["t_ms"], texts[0]["t_ms"] + 3000, "direct")
    refset = rs.ReferenceSet(source=trn.REF_FINAL_SET, reactions=[ref], ranges=[rng], sha256="e" * 64)
    tlog = ts.TrainingLog.create(project, LABELER)
    v = tv.TrainerView(parts={"player": _player_class()})
    ok = v.start(chat=real, segment=rng, reference=refset, media_path=None, log=tlog, project_id=pid,
                 project_name=os.path.basename(project),
                 scenario={"records": scn.records, "scenario_id": scn.header.get("scenario_id"),
                           "scenario_sha256": scn.sha256,
                           "resolve": scenario_mix.make_resolver(project, pid, real)})
    src_of = {"v:" + str(r.get("line_id")): r.get("source") for r in scn.records if r.get("kind") == "scenario.chat"}
    data = v.chat.chat_data()
    rows = [(data.chat_ids[i], data.nicks[i], int(data.times[i])) for i in range(data.n)]
    vrows = [(c, n, t) for c, n, t in rows if trn.is_virtual_id(c)]
    gen = [(c, n, t) for c, n, t in vrows if src_of.get(c) == "generated"]
    ret = [(c, n, t) for c, n, t in vrows if src_of.get(c) == "retrieved"]
    marks_gen = sum(1 for _c, n, _t in gen if n.startswith(tv.VIRTUAL_MARK["generated"] + " ")
                    and n.endswith(" · " + tv.VIRTUAL_TAG["generated"]))
    marks_ret = sum(1 for _c, n, _t in ret if n.startswith(tv.VIRTUAL_MARK["retrieved"] + " ")
                    and n.endswith(" · " + tv.VIRTUAL_TAG["retrieved"]))
    real_rows = [t for c, _n, t in rows if not trn.is_virtual_id(c)]
    # 섞여 흐름: 생성 줄 앞뒤에 실제 줄이 있다(시각 순 표 안에 끼어 있음)
    interleaved = sum(1 for _c, _n, t in gen if any(x < t for x in real_rows) and any(x > t for x in real_rows))
    res.update({"started": bool(ok), "virtual_lines": v.virtual_line_count(), "rows": len(rows), "real_rows": len(real_rows),
                "generated_rows": len(gen), "retrieved_rows": len(ret), "marks_generated": marks_gen,
                "marks_retrieved": marks_ret, "generated_interleaved": interleaved,
                "times_sorted": all(rows[i][2] <= rows[i + 1][2] for i in range(len(rows) - 1))})
    log.check("training_started_mixed", ok and len(gen) > 0 and res["times_sorted"],
              {k: res[k] for k in ("virtual_lines", "generated_rows", "retrieved_rows", "real_rows")})
    log.check("training_marks_distinct", marks_gen == len(gen) and marks_ret == len(ret),
              {"generated": [marks_gen, len(gen)], "retrieved": [marks_ret, len(ret)]})
    log.check("training_generated_interleaved", interleaved >= 1, {"interleaved": interleaved, "of": len(gen)})
    picked = None
    if gen:
        cid, _n, t = gen[0]
        v.seek_vod(min(rng[1] - 1, t + 500))
        picked = v.toggle_pick(cid)
        v.seek_vod(rng[1] - 1)
        v.show_compare()
    vr = v.virtual_result()
    res["picked"] = picked
    res["virtual_numbers"] = vr.numbers() if vr is not None else None
    log.check("training_pick_generated", picked == "pick" and (res["virtual_numbers"] or {}).get("virtual_picked") == 1,
              res["virtual_numbers"])
    v.close_view()
    v.deleteLater()
    app.processEvents()
    res["training_log"] = tlog.path
    return res


# ───────────────────────────────────────────────────────────── 한 번에

def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def run_e2e(src: str, *, base: str, from_ms: int, to_ms: int, threshold: str = "test", seed: int = 0,
            keep: bool = False, verbose: bool = True) -> dict:
    """끝에서 끝까지 한 번. 결과 dict(숫자·코드만, ok). 입력 오류면 E2EError(아무것도 만들기 전)."""
    from engine import generate as G
    from engine import llm_fake as LF
    from app import paths
    src = check_source(src)
    base = check_base(base)
    log = Log(verbose)
    t_all = time.perf_counter()
    real_lad = os.environ.get("LOCALAPPDATA")
    os.makedirs(base)
    os.environ["LOCALAPPDATA"] = os.path.join(base, "localappdata")   # 백업·폴백이 실제 %LOCALAPPDATA%\ChatCoach 로 가지 않게
    paths.set_data_root(os.path.join(base, "appdata"))
    try:
        t = time.perf_counter()
        data_dir = os.path.join(base, "chatcoach-data")
        proj, cinfo = copy_project(src, data_dir)
        log.num("copy", cinfo)
        log.step("copy", t)
        mk = real_markers(proj)
        log.num("real", {"chats_text": len(mk["body_squash"]), "bodies_8plus": len(mk["bodies"]),
                         "nicknames": len(mk["nicknames"]), "viewer_ids": len(mk["viewer_ids"])})
        scen_dir = os.path.join(proj, "scenarios")
        os.makedirs(scen_dir, exist_ok=True)
        rng_args = ["--from", str(from_ms), "--to", str(to_ms), "--threshold", threshold, "--seed", str(seed)]

        # 1. --fake (함수 주입 가짜, 고정 합성 응답)
        t = time.perf_counter()
        fake_file = os.path.join(base, "inputs", "fake-static.json")
        os.makedirs(os.path.dirname(fake_file), exist_ok=True)
        with open(fake_file, "w", encoding="utf-8") as f:
            json.dump({"model": "synthetic-static", "responses": list(STATIC_RESPONSES)}, f, ensure_ascii=False)
        gen_a = os.path.join(scen_dir, "gen-a-static.jsonl")
        with loopback_only(()) as net_a:
            rc, out = _call(G.main, ["--project", proj, *rng_args, "--fake", fake_file, "--out", gen_a])
        st = out.get("stats") or {}
        log.num("gen_a", {"rc": rc, "requests": st.get("requests"), "candidates": st.get("candidates"),
                          "generated": st.get("generated"), "filter_groups": st.get("filter_groups"),
                          "sent_chars": st.get("sent_chars")})
        log.check("generate_fake_cli", rc == 0 and out.get("written") and os.path.isfile(gen_a)
                  and out.get("backend") == "fake" and net_a["blocked"] == 0 and net_a["allowed"] == 0,
                  {"rc": rc, "net": net_a})
        log.step("generate_fake", t)

        # 2. 가짜 HTTP 서버(127.0.0.1) + engine.llm_local
        t = time.perf_counter()
        gen_b = os.path.join(scen_dir, "gen-b-http.jsonl")
        send_log = os.path.join(scen_dir, "llm-send.jsonl")
        with LF.FakeLLMServer(default={"respond": adaptive_respond}) as srv:
            with loopback_only([srv.port]) as net_b:
                rc, out = _call(G.main, ["--project", proj, *rng_args, "--model", LF.FAKE_MODEL, "--url", srv.base_url,
                                         "--send-log", send_log, "--out", gen_b])
            reqs = list(srv.requests)
            host = srv.host
        st = out.get("stats") or {}
        posts = [r for r in reqs if r["method"] == "POST"]
        log.num("gen_b", {"rc": rc, "requests": st.get("requests"), "candidates": st.get("candidates"),
                          "generated": st.get("generated"), "dropped": st.get("dropped"),
                          "filter_groups": st.get("filter_groups"), "sent_chars": st.get("sent_chars"),
                          "received_chars": st.get("received_chars"), "http_posts": len(posts),
                          "http_gets": len(reqs) - len(posts)})
        log.check("generate_http_localhost", rc == 0 and out.get("written") and out.get("backend") == "lmstudio"
                  and host == "127.0.0.1" and net_b["blocked"] == 0 and len(posts) == st.get("requests")
                  and (st.get("generated") or 0) > 0, {"rc": rc, "net": net_b, "posts": len(posts)})
        recs = _read_jsonl(send_log) if os.path.isfile(send_log) else []
        body_keys = {"messages", "content", "text", "prompt", "response"}
        log.num("send_log", {"records": len(recs), "sent_chars": sum(int(r.get("sent_chars") or 0) for r in recs),
                             "ok": sum(1 for r in recs if r.get("ok"))})
        sent_sum = sum(int(r.get("sent_chars") or 0) for r in recs)
        # 본문 칸: 글자·목록 값이면 본문(llm_local 기록의 'messages' 는 메시지 수 — 정수라 괜찮다)
        body_hits = sorted({k for r in recs for k in body_keys & set(r) if not isinstance(r[k], (int, float))})
        log.check("send_log_counts_no_body",
                  len(recs) == len(posts) and all(isinstance(r.get("sent_chars"), int) and r["sent_chars"] > 0
                                                  for r in recs if r.get("sent"))
                  and not body_hits and sent_sum == st.get("sent_chars"),
                  {"records": len(recs), "posts": len(posts), "sent_chars_log": sent_sum,
                   "sent_chars_generate": st.get("sent_chars"), "body_keys": body_hits})
        # 보낸 글(D-06 2): 가짜 서버가 받은 요청 본문 전체에 실제 표지 0
        sent_text = json.dumps([r.get("json") for r in posts], ensure_ascii=False)
        sent_scan = scan_text(sent_text, mk)
        sent_scan["mask"] = sum(1 for m in mk["masks"] if len(m) >= 2 and f"{m}" in sent_text and "*" in m)
        log.check("sent_prompts_no_real_markers", not any(sent_scan.values()), sent_scan)
        log.step("generate_http", t)

        # 3. make_scenario --generated (B 는 쓰기, A 는 dry-run 숫자만)
        t = time.perf_counter()
        MS = _make_scenario_module()
        with loopback_only(()) as net_c:
            rc_a, out_a = _call(MS.main, ["--project", proj, *rng_args, "--generated", gen_a, "--dry-run"])
            rc, out = _call(MS.main, ["--project", proj, *rng_args, "--generated", gen_b])
        ga, gb = out_a.get("generated") or {}, out.get("generated") or {}
        log.num("merge_a_dry", {"rc": rc_a, "counts": out_a.get("counts"), "kept": ga.get("kept"),
                                "dropped": (ga.get("filter") or {}).get("dropped")})
        log.num("merge_b", {"rc": rc, "counts": out.get("counts"), "valid": gb.get("valid"), "guard": gb.get("guard"),
                            "kept": gb.get("kept"), "dropped": (gb.get("filter") or {}).get("dropped"),
                            "axis_weak": (gb.get("filter") or {}).get("axis_weak"),
                            "quota": (gb.get("filter") or {}).get("quota"), "renamed": gb.get("renamed")})
        scenario_id = out.get("scenario_id")
        counts = out.get("counts") or {}
        log.check("make_scenario_generated_written", rc == 0 and out.get("written") and (counts.get("generated") or 0) > 0
                  and rc_a == 0 and net_c["blocked"] == 0,
                  {"rc": rc, "counts": counts, "rc_dry": rc_a})
        scen_path = os.path.join(scen_dir, f"{scenario_id}.jsonl") if scenario_id else None
        log.step("make_scenario", t)

        # 4. 스트리머 훈련에 섞여 흐름(표시 구분)
        t = time.perf_counter()
        tr_res: dict = {}
        if scen_path and os.path.isfile(scen_path):
            with loopback_only(()) as net_d:
                tr_res = run_training(proj, (from_ms, to_ms), scenario_id, log)
            log.check("training_no_network", net_d["blocked"] == 0 and net_d["allowed"] == 0, net_d)
        log.num("training", {k: v for k, v in tr_res.items() if k != "training_log"})
        log.step("training", t)

        # 5. 바이트 검사
        t = time.perf_counter()
        files = {"scenario": scen_path, "gen_a": gen_a, "gen_b": gen_b, "send_log": send_log,
                 "training_log": tr_res.get("training_log")}
        leak: dict = {}
        for name, p in files.items():
            if p and os.path.isfile(p):
                leak[name] = scan_text(_read_text(p), mk)
        log.check("files_no_real_markers", leak and all(not any(v.values()) for v in leak.values()),
                  {k: sum(v.values()) for k, v in leak.items()})
        scen_recs = _read_jsonl(scen_path) if scen_path and os.path.isfile(scen_path) else []
        glines = [r for r in scen_recs if r.get("kind") == "scenario.chat" and r.get("source") == "generated"]
        vlines = [r for r in scen_recs if r.get("kind") == "scenario.chat"]
        gscan = scan_generated_lines(glines, mk)
        gscan["virtual_mask_real"] = sum(1 for r in vlines if r.get("display_mask") in mk["masks"])
        head = scen_recs[0] if scen_recs else {}
        log.check("generated_lines_clean_and_marked",
                  glines and not any(gscan.values()) and (head.get("generator") or {}).get("backend") == "lmstudio"
                  and (head.get("counts") or {}).get("generated") == len(glines),
                  {**gscan, "generated": len(glines)})
        tl = _read_text(tr_res["training_log"]) if tr_res.get("training_log") and os.path.isfile(tr_res["training_log"]) else ""
        tl_gen = sum(1 for r in glines if r.get("text") and r["text"] in tl)
        tl_mask = sum(1 for r in vlines if r.get("display_mask") and r["display_mask"] in tl)
        log.check("training_log_no_virtual_text", bool(tl) and tl_gen == 0 and tl_mask == 0 and '"v:' not in tl,
                  {"generated_text": tl_gen, "masks": tl_mask})
        log.step("leak_scan", t)
    finally:
        paths.set_data_root(None)
        if real_lad is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_lad
        log.out["elapsed_s"] = round(time.perf_counter() - t_all, 2)
        log.out["range"] = [_fmt_ms(from_ms), _fmt_ms(to_ms)]
        if not keep:
            shutil.rmtree(base, ignore_errors=True)
        log.out["cleaned"] = not os.path.exists(base)
    log.out["ok"] = log.ok
    return log.out


def main(argv: list[str] | None = None) -> int:
    from engine import generate as G
    ap = argparse.ArgumentParser(description="H-3 생성 끝에서 끝까지 스모크(가짜 LLM, 사람 클릭 0). 자리는 %LOCALAPPDATA%\\ChatCoachSmoke 아래")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="원 프로젝트(읽기만, 사본으로 돈다)")
    ap.add_argument("--from", dest="from_t", default="20:00")
    ap.add_argument("--to", dest="to_t", default="40:00")
    ap.add_argument("--threshold", default="test")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--base", help="자리(기본 %%LOCALAPPDATA%%\\ChatCoachSmoke\\gen-e2e-<시각>-<pid>)")
    ap.add_argument("--keep", action="store_true", help="끝나도 자리를 지우지 않는다(실제 채팅 사본이 남으니 확인 뒤 지울 것)")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    try:
        a, b = G.parse_ms(ns.from_t), G.parse_ms(ns.to_t)
        if b <= a:
            raise ValueError
    except ValueError:
        print(json.dumps({"error": "bad_range"}))
        return 2
    try:
        res = run_e2e(ns.project, base=ns.base or default_base(), from_ms=a, to_ms=b, threshold=ns.threshold,
                      seed=ns.seed, keep=ns.keep, verbose=not ns.json)
    except E2EError as e:
        print(json.dumps({"error": "input", "why": str(e)}, ensure_ascii=False))
        return 2
    if ns.json:
        print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    else:
        for k in ("numbers", "steps"):
            print(f"{k}: {json.dumps(res[k], ensure_ascii=False, sort_keys=True)}")
        n_ok = sum(1 for v in res["checks"].values() if (v if isinstance(v, bool) else v.get("ok")))
        print(f"검사 {n_ok}/{len(res['checks'])} 통과 · {res['elapsed_s']}초 · 사람 클릭 {USER_CLICKS} · 자리 지움 {res['cleaned']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
