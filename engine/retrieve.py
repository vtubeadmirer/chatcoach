"""H-2 가상 채팅 'retrieved' 고르기 (docs/design/20-plan-virtual-chat.md 4-2, D-19 2).

훈련 구간 안에서 실제 채팅이 드문 1분 창을 찾고, 같은 방송의 **훈련 구간 밖** 채팅과 같은 채널(`project.json`
broadcast.channel_id 가 같은)의 다른 프로젝트 채팅에서 그 창에 넣을 후보를 고른다. AI·모델·외부 호출은 없다.

표준 라이브러리와 순수 모듈 app.core.textnorm(낱말 정규화, 앱 채점기와 같이 씀 — D-20 3)만 쓴다(엔진 venv 의 무거운
의존성도, 입출력 app 모듈도 import 하지 않는다). 그래서 앱 venv 에서도,
엔진 venv 에서도 그대로 부를 수 있다. 이 모듈은 header 칸과 scenario.chat 줄(dict)만 만든다. 줄 목록 조립은
`app.core.scenario.assemble(header, chats, yyyymmdd=…)`, 검사·쓰기는 `app.scenario_store.write_scenario(project_root, records)`
(H-1 담당) 가 하고, tools/make_scenario.py 가 셋을 잇는다.

지키는 것(20 1장·8장):
  - 본문을 시나리오에 복사하지 않는다. retrieved 줄에는 원 프로젝트 ID·chat_id·시각만 적고, 앱이 원 messages.jsonl 에서 읽는다.
  - 이름은 원 가림 닉네임이 아니라 이 모듈이 새로 붙인 가림 이름(display_mask). 실제 가림 닉네임과 겹치면 다시 뽑는다.
    **줄마다 다른 가림 이름**이다(D-20 7): 같은 원 시청자에게서 가져온 여러 줄도 서로 다른 이름을 단다 — 같은 이름으로
    묶어 보이면 말버릇·시각으로 실제 시청자를 짚는 단서가 된다. 시나리오 안에서 두 줄이 같은 이름을 쓰지 않는다.
  - 다른 시청자 닉네임(보호 구역 identities.jsonl 의 원 닉네임)·멘션·링크·전화번호 꼴·후원 문구·시스템 줄·
    너무 짧은/긴 줄·금칙어 목록에 걸린 줄은 후보에서 뺀다. 원 닉네임 목록은 메모리에서만 쓰고 결과에는 건수만 남긴다.
  - 금칙어 목록은 저장소 밖 파일(기본 <데이터 폴더>\\config\\blocklist-ko.txt, D-20 6)에서 읽는다. 시나리오에는 그 파일의
    sha256 만 적고 낱말은 적지 않는다(통계에도 낱말 수만). 파일이 없으면 0낱말로 진행하고 통계의 blocklist_found=false
    (도구가 경고 한 줄을 낸다). 파일이 있는데 읽을 수 없으면(깨진 UTF-8 등) 거르지 않은 채로 만들지 않고 멈춘다(bad_blocklist).
  - 시범 프로젝트(폴더 이름 pilot-*)는 대상이면 **읽기 전에** 거부하고(pilot_locked), 같은 채널 후보에서는 열지도 않고 뺀다.
  - 반환하는 통계·오류에는 숫자·코드만 넣는다(본문·닉네임 없음).
결정적: 같은 입력과 seed 면 같은 줄이 나온다(random.Random(str) 은 판 사이에 같은 값을 낸다).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Sequence

from app.core import textnorm as TN   # 순수 모듈(표준 라이브러리만). 앱 채점기와 같은 낱말 규칙(D-20 3)

TOOL_NAME = "engine retrieve"
TOOL_VERSION = "0.2.0"    # 0.2.0: 줄마다 다른 가림 이름(D-20 7), 금칙어 파일을 읽지 못하면 멈춤
SCENARIO_SCHEMA = "chatcoach-scenario-v1"
FILTER_VERSIONS = ("nickname-v1", "copy-v1", "block-v1")
RETRIEVE_SCOPES = ("same_broadcast", "same_channel")


class RetrieveError(Exception):
    """만들 수 없을 때. code 는 기계가 읽는 짧은 코드, 메시지에는 본문·닉네임을 넣지 않는다."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class RetrieveConfig:
    """20 3-2 fill·retrieval 의 시작값."""
    quiet_window_ms: int = 60000      # 창 길이
    quiet_max_real: int = 2           # 실제 채팅이 이 수 이하인 창을 채운다
    target_per_min: int = 6           # 채운 창의 실제+가상 목표(1분당)
    max_virtual_ratio: float = 0.5    # 구간 전체에서 가상 / (실제 + 가상) 상한
    context_ms: int = 60000           # 점수용 '그때 주제' 로 볼 창 앞 시간(실제 채팅 낱말)
    score_floor: float = 0.0          # 주제 낱말이 있을 때만 쓰는 점수 하한
    max_per_viewer: int = 3           # 같은 원 시청자(원 프로젝트·viewer_id)는 시나리오 하나에 최대
    min_len: int = 2                  # 이모티콘·공백을 뺀 글자 수 하한
    max_len: int = 60                 # 이모티콘·공백을 뺀 글자 수 상한

    def __post_init__(self):
        if self.quiet_window_ms <= 0 or self.target_per_min < 0 or self.quiet_max_real < 0:
            raise ValueError("창 길이·목표·문턱이 잘못됐습니다")
        if not (0.0 <= self.max_virtual_ratio < 1.0):
            raise ValueError("max_virtual_ratio 는 0 이상 1 미만")
        if self.max_per_viewer < 1 or self.min_len < 1 or self.max_len < self.min_len:
            raise ValueError("시청자 상한·길이 범위가 잘못됐습니다")


@dataclass(frozen=True)
class Chat:
    chat_id: str
    t_ms: int | None
    kind: str
    status: str
    viewer_id: str
    role: str
    nickname_masked: str | None
    text: str


@dataclass
class ProjectChats:
    """한 프로젝트에서 읽은 것. nicknames 는 원 닉네임(보호 구역) — 메모리에서만 쓴다. None 이면 목록 없음."""
    project_dir: str
    project_id: str
    channel_id: str | None
    chats: list[Chat]
    messages_sha256: str
    nicknames: frozenset[str] | None
    masks: frozenset[str] = field(default_factory=frozenset)


# ───────────────────────────────────────────────────────────── 시범 프로젝트

def is_pilot_dir(project_dir: str | None) -> bool:
    """폴더 이름이 pilot- 로 시작하면 시범 프로젝트(engine.link·topics·tools/engine_to_ai 와 같은 규칙)."""
    return bool(project_dir) and os.path.basename(os.path.abspath(project_dir)).lower().startswith("pilot-")


def _refuse_pilot(project_dir: str) -> None:
    if is_pilot_dir(project_dir):
        raise RetrieveError("pilot_locked", "시범 프로젝트는 시범 세션이 모두 잠기기 전에는 가상 채팅을 만들지 않습니다 (D-18 7)")


# ───────────────────────────────────────────────────────────── 읽기

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _read_jsonl(path: str) -> Iterable[dict]:
    """BOM 을 벗기고 줄마다 dict. 깨진 줄은 건너뛴다(마지막 줄 잘림 포함) — 후보를 덜 고를 뿐 틀린 줄을 넣지 않는다."""
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        d = json.load(f)
    return d if isinstance(d, dict) else {}


def read_project_meta(project_dir: str) -> tuple[str, str | None]:
    """(project_id, channel_id). project.json 이 없거나 깨졌으면 (폴더 이름, None)."""
    pj = os.path.join(project_dir, "project.json")
    pid, ch = os.path.basename(os.path.abspath(project_dir)), None
    if os.path.isfile(pj):
        try:
            d = _read_json(pj)
        except (OSError, ValueError):
            return pid, None
        pid = str(d.get("project_id") or pid)
        c = (d.get("broadcast") or {}).get("channel_id")
        ch = str(c) if c else None
    return pid, ch


def load_nicknames(project_dir: str) -> frozenset[str] | None:
    """보호 구역 identities.jsonl 의 원 닉네임(정규화). 파일이 없으면 None(대조 불가)."""
    p = os.path.join(project_dir, "chat", "protected", "identities.jsonl")
    if not os.path.isfile(p):
        return None
    out = set()
    for r in _read_jsonl(p):
        for n in r.get("nicknames") or []:
            name = n.get("nickname") if isinstance(n, dict) else n
            k = _squash(name or "")
            if len(k) >= 2:          # 한 글자 닉네임은 거의 모든 줄에 걸려 대조 뜻이 없다(확인 필요로 보고)
                out.add(k)
    return frozenset(out)


def load_project(project_dir: str) -> ProjectChats:
    """시범이면 읽기 전에 거부. messages.jsonl 을 Chat 목록으로(시각 순)."""
    _refuse_pilot(project_dir)
    msg = os.path.join(project_dir, "chat", "messages.jsonl")
    if not os.path.isfile(msg):
        raise RetrieveError("no_messages", "chat/messages.jsonl 이 없습니다")
    pid, ch = read_project_meta(project_dir)
    chats, masks = [], set()
    for r in _read_jsonl(msg):
        cid = r.get("chat_id")
        if not isinstance(cid, str):
            continue
        t = r.get("t_ms")
        t = int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None
        nm = r.get("nickname_masked")
        if isinstance(nm, str) and nm:
            masks.add(nm)
        chats.append(Chat(cid, t, str(r.get("kind") or "other"), str(r.get("status") or ""),
                          str(r.get("viewer_id") or "anon"), str(r.get("role") or "unknown"),
                          nm if isinstance(nm, str) else None, str(r.get("text") or "")))
    chats.sort(key=lambda c: (c.t_ms if c.t_ms is not None else -1, c.chat_id))
    return ProjectChats(project_dir, pid, ch, chats, _sha256_file(msg), load_nicknames(project_dir), frozenset(masks))


def _excluded(exclude: Callable[[str], bool] | None, d: str) -> bool:
    """부르는 쪽의 제외 판정(예: 앱의 시범 기록 대조). 판정이 실패하면 뺀다(블라인드 쪽으로 안전하게)."""
    if exclude is None:
        return False
    try:
        return bool(exclude(d))
    except Exception:  # noqa: BLE001
        return True


def find_same_channel_projects(project_dir: str, projects_root: str | None, channel_id: str | None, *,
                               exclude: Callable[[str], bool] | None = None) -> tuple[list[str], int]:
    """같은 channel_id 의 다른 프로젝트 폴더(이름 순)와 뺀 시범 폴더 수.

    channel_id 를 모르면 빈 목록(같은 방송만 쓴다). pilot-* 는 project.json 도 열지 않는다.
    exclude(폴더) 가 참이면 그 폴더도 시범으로 보고 뺀다 — 엔진은 폴더 이름만 보므로, 이름이 pilot-* 가 아닌 시범
    프로젝트(시범 기록 freeze.json·setup.json 에 적힘)는 부르는 쪽(tools/make_scenario.py → app.reference_set)이 준다.
    """
    if not channel_id or not projects_root or not os.path.isdir(projects_root):
        return [], 0
    me = os.path.normcase(os.path.abspath(project_dir))
    out, pilots = [], 0
    for name in sorted(os.listdir(projects_root)):
        d = os.path.join(projects_root, name)
        if not os.path.isdir(d) or os.path.normcase(os.path.abspath(d)) == me:
            continue
        if is_pilot_dir(d) or _excluded(exclude, d):
            pilots += 1
            continue
        if not os.path.isfile(os.path.join(d, "chat", "messages.jsonl")):
            continue
        _, ch = read_project_meta(d)
        if ch == channel_id:
            out.append(d)
    return out, pilots


def load_blocklist(path: str | None) -> tuple[tuple[str, ...], str | None]:
    """금칙어 목록(UTF-8, 한 줄에 하나, '#' 줄·빈 줄 무시). (정규화한 낱말들, 파일 sha256). 경로가 없거나 파일이 없으면
    ((), None) — 부르는 쪽이 경고만 내고 진행한다(D-20 6). 비교는 _squash(공백·기호 뺌) 뒤 부분 일치(reject_reason).

    해시는 읽은 바이트 그대로의 sha256(같은 바이트를 한 번만 읽어 낱말과 해시가 어긋나지 않게).
    파일이 있는데 읽을 수 없거나 UTF-8 이 아니면 RetrieveError('bad_blocklist') — 거르지 않은 시나리오를 만들지 않는다.
    낱말은 메모리에서만 쓴다(반환값을 로그·통계·시나리오에 옮기지 않는다)."""
    if not path or not os.path.isfile(path):
        return (), None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise RetrieveError("bad_blocklist", "금칙어 목록 파일을 읽지 못했습니다(UTF-8 텍스트여야 합니다)") from None
    words = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        k = _squash(s)
        if k:
            words.append(k)
    return tuple(sorted(set(words))), hashlib.sha256(raw).hexdigest()


def load_topic_flow(path: str | None) -> tuple[list[tuple[int, int, tuple[str, ...]]], str | None]:
    """topic-flow(VOD 축)의 (start_ms, end_ms, keywords) 목록과 파일 sha256. 경로가 없거나 형식이 다르면 ([], None)."""
    if not path or not os.path.isfile(path):
        return [], None
    try:
        d = _read_json(path)
    except (OSError, ValueError):
        return [], None
    if d.get("schema") != "chatcoach/topic-flow" or d.get("time_axis") != "vod_ms":
        return [], None
    blocks = []
    for b in d.get("blocks") or []:
        try:
            blocks.append((int(b["start_ms"]), int(b["end_ms"]),
                           tuple(str(k) for k in (b.get("keywords") or []))))
        except (KeyError, TypeError, ValueError):
            continue
    return blocks, _sha256_file(path)


# ───────────────────────────────────────────────────────────── 정규화·거르기

# 낱말 나누기·정규화는 앱 채점기(G)와 같이 쓰도록 순수 모듈 app/core/textnorm.py 로 옮겼다(D-20 3). 이름은 그대로 둔다.
_EMOTE = TN.EMOTE_RE
_JAMO_RUN = TN.JAMO_RUN_RE
_NON_WORD = TN.NON_WORD_RE
_PARTICLES = TN.PARTICLES

_MENTION = re.compile(r"@\S")
_LINK = re.compile(r"(https?://|www\.|[a-z0-9-]+\.(com|net|org|kr|io|gg|tv|me|ly|co|xyz|link|site)\b)", re.I)
_PHONE = re.compile(r"(0\d{1,2}[\s.\-]?\d{3,4}[\s.\-]?\d{4})|(\d[\d\s.\-]{8,}\d)")
_DONATION = re.compile(r"(후원|도네|구독|치즈|풍선|별풍|기부|\d[\d,]*\s*원)")
_SYSTEM_TEXT = re.compile(r"^\s*[\[【(](공지|알림|시스템|system|notice)[\]】)]", re.I)


_squash = TN.squash            # 대조용: NFKC, 소문자, 공백·기호 없앰(한글·영문·숫자만)
visible_len = TN.visible_len   # 이모티콘 코드와 공백을 뺀 글자 수
tokens = TN.tokens             # 점수용 낱말(2자 이상). 조사를 뗀 꼴과 원래 꼴을 둘 다 넣는다


def _nfkc(s: str) -> str:
    """꼴 검사(멘션·링크·전화·후원·공지)용 NFKC. 대소문자는 그대로 둔다(정규식이 알아서 본다)."""
    return unicodedata.normalize("NFKC", s or "")


def reject_reason(chat: Chat, *, nicknames: Iterable[str] | None, blocklist: Sequence[str], cfg: RetrieveConfig) -> str | None:
    """후보에서 뺄 까닭(코드) 또는 None. 앞 조건부터 하나만 낸다(통계 칸 이름과 같다)."""
    if chat.t_ms is None:
        return "invalid_time"
    if chat.kind != "text":
        return "not_text"            # 후원·구독·시스템·이미지 등
    if chat.status and chat.status != "NORMAL":
        return "status"              # 클린봇 블라인드 등
    if chat.role in ("streamer", "manager"):
        return "role"                # 스트리머·운영자 말을 시청자처럼 옮기지 않는다(사칭 막기)
    if chat.viewer_id == "anon":
        return "anon"
    t = chat.text or ""
    n = visible_len(t)
    if n < cfg.min_len:
        return "too_short"
    if n > cfg.max_len:
        return "too_long"
    # 꼴 검사는 NFKC 정규화한 글에(전각 '＠'·'ｗｗｗ．'·전각 숫자·'（공지）' 도 잡는다). engine/generate 의 거르기 4 와 같은
    # 방식 — 정규식만 정규화한 글에 돌리고, 길이·옮길 본문은 원문 그대로(25 보고 5-2 의 9번).
    nt = _nfkc(t)
    if _SYSTEM_TEXT.search(nt):
        return "system_text"
    if _MENTION.search(nt):
        return "mention"
    if _LINK.search(nt):
        return "link"
    if _PHONE.search(nt):
        return "phone"
    if _DONATION.search(nt):
        return "donation"
    sq = _squash(t)
    if nicknames:
        for nk in nicknames:
            if nk in sq:
                return "nickname"
    for w in blocklist:
        if w in sq:
            return "blocked"
    return None


# ───────────────────────────────────────────────────────────── 드문 창

@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int
    real: int        # 창 안의 실제 일반 채팅(kind=text) 수
    target: int      # 이 길이의 목표 수(실제+가상)
    quiet: bool      # real <= quiet_max_real (채울 자리)


def windows(chats: Sequence[Chat], from_ms: int, to_ms: int, cfg: RetrieveConfig) -> list[Window]:
    """[from_ms, to_ms) 을 창으로 나누고 창마다 실제 일반 채팅 수를 센다. 끝 창이 짧으면 목표를 길이 비율로 줄인다."""
    if to_ms <= from_ms:
        raise RetrieveError("bad_range", "구간 끝이 시작보다 커야 합니다")
    W = cfg.quiet_window_ms
    n = math.ceil((to_ms - from_ms) / W)
    counts = [0] * n
    for c in chats:
        if c.kind == "text" and c.t_ms is not None and from_ms <= c.t_ms < to_ms:
            counts[(c.t_ms - from_ms) // W] += 1
    out = []
    for i in range(n):
        a, b = from_ms + i * W, min(to_ms, from_ms + (i + 1) * W)
        out.append(Window(a, b, counts[i], int(cfg.target_per_min * (b - a) / 60000), counts[i] <= cfg.quiet_max_real))
    return out


def allocate(ws: Sequence[Window], real_total: int, cfg: RetrieveConfig) -> list[int]:
    """창마다 넣을 가상 수. 드문 창만, 목표까지. 구간 전체 가상 비율 상한을 넘으면 앞 창부터 하나씩 돌려 나눈다."""
    need = [max(0, w.target - w.real) if w.quiet else 0 for w in ws]
    r = cfg.max_virtual_ratio
    cap = math.floor(r * real_total / (1.0 - r) + 1e-9)
    if sum(need) <= cap:
        return need
    got, left = [0] * len(ws), cap
    while left > 0:
        moved = False
        for i, k in enumerate(need):
            if left == 0:
                break
            if got[i] < k:
                got[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    return got


# ───────────────────────────────────────────────────────────── 고르기

@dataclass(frozen=True)
class Candidate:
    project_id: str
    scope: str
    chat_id: str
    t_ms: int
    viewer_key: str
    toks: frozenset[str]
    norm: str


# 가림 이름 첫 글자 후보(합성). 실제 가림 닉네임과 겹치면 다른 것을 뽑는다.
_MASK_SYLLABLES = tuple("가나다라마바사아자차카타파하고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후"
                        "기니디리미비시이지치키티피히")


def _hkey(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _context_words(ws: Window, chats: Sequence[Chat], topic: Sequence[tuple[int, int, tuple[str, ...]]],
                   cfg: RetrieveConfig) -> frozenset[str]:
    out = set()
    for a, b, kws in topic:
        if a < ws.end_ms and b > ws.start_ms:
            out.update(kws)
    lo = ws.start_ms - cfg.context_ms
    for c in chats:
        if c.kind == "text" and c.t_ms is not None and lo <= c.t_ms < ws.end_ms:
            out.update(tokens(c.text))
    return frozenset(out)


def _score(c: Candidate, ctx: frozenset[str]) -> float:
    if not c.toks or not ctx:
        return 0.0
    return round(len(c.toks & ctx) / len(c.toks), 4)


def build_pool(target: ProjectChats, others: Sequence[ProjectChats], from_ms: int, to_ms: int, *,
               blocklist: Sequence[str], cfg: RetrieveConfig) -> tuple[list[Candidate], dict]:
    """후보 모으기. 같은 방송은 훈련 구간 밖만. 거른 까닭별 수를 함께 돌려준다."""
    removed: dict[str, int] = {}
    nick_all: set[str] = set()
    nick_missing = 0
    for p in (target, *others):
        if p.nicknames is None:
            nick_missing += 1
        else:
            nick_all.update(p.nicknames)
    # 구간 안 실제 채팅과 같은 말(정규화)은 넣지 않는다(같은 화면에 두 번 보이면 티가 난다)
    seen_norm = {_squash(c.text) for c in target.chats if c.t_ms is not None and from_ms <= c.t_ms < to_ms}
    pool: list[Candidate] = []
    for p, scope in [(target, "same_broadcast")] + [(o, "same_channel") for o in others]:
        for c in p.chats:
            if scope == "same_broadcast" and c.t_ms is not None and from_ms <= c.t_ms < to_ms:
                removed["in_range"] = removed.get("in_range", 0) + 1
                continue
            why = reject_reason(c, nicknames=nick_all, blocklist=blocklist, cfg=cfg)
            if why is None:
                norm = _squash(c.text)
                if norm in seen_norm:
                    why = "duplicate"
                else:
                    seen_norm.add(norm)
            if why:
                removed[why] = removed.get(why, 0) + 1
                continue
            pool.append(Candidate(p.project_id, scope, c.chat_id, int(c.t_ms), f"{p.project_id}|{c.viewer_id}",
                                  tokens(c.text), norm))
    return pool, {"removed": dict(sorted(removed.items())), "projects_without_nicknames": nick_missing}


def _line_key(c: Candidate) -> str:
    """가림 이름을 정하는 열쇠: 원 프로젝트·chat_id(한 시나리오에서 한 채팅은 한 번만 고른다 — 줄마다 하나)."""
    return f"{c.project_id}|{c.chat_id}"


def _masks(keys: Sequence[str], taken: set[str], seed) -> dict[str, str]:
    """열쇠(줄)마다 새 가림 이름(첫 글자 + '*' 2~5개, 다 차면 2~7개 — scenario.schema displayMask 모양). 실제 가림
    닉네임·서로와 겹치지 않게. 별 모양이 다 찼으면 '시청자N' 가명으로 넘어간다.

    D-20 7 부터 열쇠는 **줄**이다(_line_key). 같은 원 시청자의 여러 줄도 서로 다른 이름을 받는다. 이름은 원 시청자와
    상관없이 seed·줄 열쇠로만 정해진다(viewer_id 가 이름에 스며들지 않는다)."""
    out: dict[str, str] = {}
    used = set(taken)
    n_plain = 0
    for vk in keys:
        rng = random.Random(f"{seed}|mask|{vk}")
        m = None
        for _ in range(200):
            x = rng.choice(_MASK_SYLLABLES) + "*" * rng.randint(2, 5)
            if x not in used:
                m = x
                break
        if m is None:
            for x in (f"{a}{'*' * k}" for k in range(2, 8) for a in _MASK_SYLLABLES):
                if x not in used:
                    m = x
                    break
        while m is None or m in used:
            n_plain += 1
            m = f"시청자{n_plain}"
        used.add(m)
        out[vk] = m
    return out


def select_retrieved(target: ProjectChats, others: Sequence[ProjectChats], from_ms: int, to_ms: int, *,
                     seed=0, blocklist: Sequence[str] = (), topic: Sequence[tuple[int, int, tuple[str, ...]]] = (),
                     cfg: RetrieveConfig | None = None) -> tuple[list[dict], dict]:
    """scenario.chat 줄(retrieved) 목록과 통계(숫자만). 줄은 보일 시각 순, line_id 는 s0001 부터."""
    cfg = cfg or RetrieveConfig()
    ws = windows(target.chats, from_ms, to_ms, cfg)
    real_total = sum(w.real for w in ws)
    if real_total == 0:
        raise RetrieveError("no_real_chat", "구간에 실제 채팅이 없어 견줄 것이 없습니다(20 5-1)")
    alloc = allocate(ws, real_total, cfg)
    pool, pst = build_pool(target, others, from_ms, to_ms, blocklist=blocklist, cfg=cfg)
    used_chat: set[tuple[str, str]] = set()
    per_viewer: dict[str, int] = {}
    picked: list[tuple[int, Candidate, float]] = []
    below_floor = viewer_capped = 0
    for w, k in zip(ws, alloc):
        if k <= 0:
            continue
        ctx = _context_words(w, target.chats, topic, cfg)
        ranked = sorted(pool, key=lambda c: (-_score(c, ctx), _hkey(seed, w.start_ms, c.project_id, c.chat_id)))
        times = sorted(random.Random(f"{seed}|times|{w.start_ms}").randrange(w.start_ms, w.end_ms) for _ in range(k))
        got = 0
        for c in ranked:
            if got >= k:
                break
            if (c.project_id, c.chat_id) in used_chat:
                continue
            s = _score(c, ctx)
            if ctx and s < cfg.score_floor:
                below_floor += 1
                break                         # 점수 순이라 뒤는 모두 하한 아래
            if per_viewer.get(c.viewer_key, 0) >= cfg.max_per_viewer:
                viewer_capped += 1
                continue
            used_chat.add((c.project_id, c.chat_id))
            per_viewer[c.viewer_key] = per_viewer.get(c.viewer_key, 0) + 1
            picked.append((times[got], c, s))
            got += 1
    picked.sort(key=lambda x: (x[0], x[1].project_id, x[1].chat_id))
    taken = set(target.masks)
    for o in others:
        taken.update(o.masks)
    # 줄마다 다른 가림 이름(D-20 7). 이름 뽑는 차례는 seed·줄 열쇠의 해시 순(보일 시각·원 시청자 순이 이름에 드러나지 않게)
    lkeys = sorted({_line_key(c) for _, c, _ in picked}, key=lambda k: _hkey(seed, "ln", k))
    mask = _masks(lkeys, taken, seed)
    lines = []
    for i, (show, c, s) in enumerate(picked, 1):
        lines.append({
            "kind": "scenario.chat", "line_id": f"s{i:04d}", "source": "retrieved", "chat_kind": "text",
            "show_vod_ms": int(show), "from_project_id": c.project_id, "from_chat_id": c.chat_id,
            "from_t_ms": c.t_ms, "from_scope": c.scope, "retrieval_score": s, "display_mask": mask[_line_key(c)],
            "checks": {"nickname_hit": 0, "blocked": 0},
        })
    stats = {
        "windows": len(ws), "quiet_windows": sum(1 for w in ws if w.quiet), "real_in_range": real_total,
        "virtual_wanted": sum(max(0, w.target - w.real) for w in ws if w.quiet), "virtual_allowed": sum(alloc),
        "virtual_filled": len(lines), "pool": len(pool), "below_floor": below_floor, "viewer_capped": viewer_capped,
        "from_scope": {s: sum(1 for ln in lines if ln["from_scope"] == s) for s in RETRIEVE_SCOPES},
        "masks_distinct": len({ln["display_mask"] for ln in lines}),
        "masks_pseudonym": sum(1 for ln in lines if ln["display_mask"].startswith("시청자")),
        **pst,
    }
    return lines, stats


# ───────────────────────────────────────────────────────────── 시나리오 레코드

def selection_sha256(lines: Sequence[dict]) -> str:
    """줄 목록의 정규화 해시(키 정렬·공백 없는 JSON 을 LF 로 이음). 같은 입력·seed 면 같다."""
    body = "\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for x in lines)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_header(target: ProjectChats, others: Sequence[ProjectChats], from_ms: int, to_ms: int, stats: dict, *,
                 used_projects: Iterable[str], seed: int, cfg: RetrieveConfig, blocklist_sha256: str | None,
                 topic_flow_sha256: str | None, now: datetime | None = None) -> dict:
    """scenario.header 의 칸(schemas/scenario.schema.json). kind·schema·counts·scenario_id 는
    app.core.scenario.assemble 이 채운다(그 담당의 조립 함수, tools/make_scenario.py 가 부른다).

    from_projects 에는 실제로 줄을 가져온 다른 프로젝트만 적는다(후보로 읽었지만 고르지 않은 것은 빼, 0개면 칸 생략).
    """
    now = now or datetime.now().astimezone()
    used = set(used_projects)
    inputs: dict = {"messages_sha256": target.messages_sha256}
    fp = [{"project_id": o.project_id, "messages_sha256": o.messages_sha256} for o in others if o.project_id in used]
    if fp:
        inputs["from_projects"] = fp
    if topic_flow_sha256:
        inputs["topic_flow_sha256"] = topic_flow_sha256
    filters: dict = {"versions": list(FILTER_VERSIONS), "dropped": dict(stats.get("removed", {}))}
    if blocklist_sha256:
        filters["blocklist_sha256"] = blocklist_sha256
    return {
        "project_id": target.project_id, "range": {"vod_from_ms": int(from_ms), "vod_to_ms": int(to_ms)},
        "created_at": now.isoformat(timespec="seconds"), "created_by": f"{TOOL_NAME} {TOOL_VERSION}",
        "inputs": inputs,
        "fill": {"quiet_window_ms": cfg.quiet_window_ms, "quiet_max_real": cfg.quiet_max_real,
                 "target_per_min": cfg.target_per_min, "max_virtual_ratio": cfg.max_virtual_ratio},
        # method: 스키마 값은 topic_words 하나(+e5). 주제 흐름이 없으면 앞 60초 실제 채팅 낱말만 쓴다 — 같은 값으로 적고
        # inputs.topic_flow_sha256 이 없는 것으로 구별한다.
        "retrieval": {"method": "topic_words", "min_score": cfg.score_floor, "max_per_viewer": cfg.max_per_viewer,
                      "seed": int(seed)},
        "generator": {"backend": "none"},
        "filters": filters,
        "external": {"enabled": False},
    }


def make_retrieved_scenario(project_dir: str, from_ms: int, to_ms: int, *, projects_root: str | None = None,
                            blocklist_path: str | None = None, topic_flow_path: str | None = None, seed: int = 0,
                            cfg: RetrieveConfig | None = None, now: datetime | None = None,
                            exclude: Callable[[str], bool] | None = None
                            ) -> tuple[dict, list[dict], dict]:
    """한 번에: 시범 거부 → 읽기 → 고르기. (header 칸, scenario.chat 줄들, 통계). 통계는 숫자만.

    파일 줄 목록은 app.core.scenario.assemble(header, chats, yyyymmdd=…) 로 만들고 app.scenario_store.write_scenario
    (project_root, records) 로 쓴다(tools/make_scenario.py).
    projects_root 를 주면 그 아래 같은 channel_id 프로젝트(pilot-* 제외)도 후보로 쓴다. 기본은 project_dir 의 부모.
    exclude(폴더) 가 참인 같은 채널 후보도 열지 않는다(find_same_channel_projects).
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise RetrieveError("bad_seed", "seed 는 0 이상 정수")
    t0 = time.perf_counter()
    _refuse_pilot(project_dir)
    cfg = cfg or RetrieveConfig()
    # 금칙어 목록을 먼저 읽는다: 깨졌으면(bad_blocklist) 채팅을 읽기 전에 멈춘다. 없으면 ((), None) — 경고는 부르는 쪽(D-20 6)
    blocklist, bl_sha = load_blocklist(blocklist_path)
    target = load_project(project_dir)
    root = projects_root if projects_root is not None else os.path.dirname(os.path.abspath(project_dir))
    dirs, pilots = find_same_channel_projects(project_dir, root, target.channel_id, exclude=exclude)
    others = [load_project(d) for d in dirs]
    topic, tf_sha = load_topic_flow(topic_flow_path)
    lines, stats = select_retrieved(target, others, from_ms, to_ms, seed=seed, blocklist=blocklist, topic=topic, cfg=cfg)
    used = {ln["from_project_id"] for ln in lines if ln["from_scope"] == "same_channel"}
    header = build_header(target, others, from_ms, to_ms, stats, used_projects=used, seed=seed, cfg=cfg,
                          blocklist_sha256=bl_sha, topic_flow_sha256=tf_sha, now=now)
    stats.update({"same_channel_projects": len(others), "same_channel_used": len(used), "pilot_skipped": pilots,
                  "channel_known": target.channel_id is not None, "blocklist_words": len(blocklist),
                  "blocklist_found": bl_sha is not None,
                  "topic_blocks": len(topic), "selection_sha256": selection_sha256(lines),
                  "elapsed_ms": int((time.perf_counter() - t0) * 1000)})
    return header, lines, stats


__all__ = [
    "RetrieveError", "RetrieveConfig", "Chat", "ProjectChats", "Window", "Candidate",
    "is_pilot_dir", "load_project", "load_nicknames", "load_blocklist", "load_topic_flow", "read_project_meta",
    "find_same_channel_projects", "reject_reason", "visible_len", "tokens", "windows", "allocate", "build_pool",
    "select_retrieved", "selection_sha256", "build_header", "make_retrieved_scenario",
    "SCENARIO_SCHEMA", "TOOL_NAME", "TOOL_VERSION",
]
