"""시각 변환·보정 (docs/design/03-data-model.md 4장, 02-architecture.md 3-3·7-5).

순수 함수만 둔다. 시계·난수·파일·Qt 를 쓰지 않는다(02 3장 층 규칙). 모든 시각은 정수 ms.

용어
- 채팅 시각(chat_ms) = VOD 시각 = 치지직 playerMessageTime 축. messages.t_ms 와 같은 축이다.
- 영상 시각(video_ms) = 로컬 영상 파일의 재생 위치.
- 기본식(03 4-2, project.schema calibrationRev): 영상 파일 시각 = VOD 시각 + offset_ms

클립 파일(ClipSlicer 로 VOD 의 일부 구간만 잘라 받은 파일)
- 파일 0 초가 VOD 의 file_start_in_vod_ms 에 해당하면:
      video_ms = chat_ms − file_start_in_vod_ms + offset_ms
  여기서 Calibration.offset_ms 는 '클립 시작을 뺀 나머지 어긋남' 이다.
- project.schema 에는 이 값을 둘 필드가 없다(calibrationRev·mediaRef 모두 additionalProperties=false).
  그래서 JSON 으로 쓸 때는 스키마 식이 그대로 성립하도록 offset_ms 에 합쳐 쓴다
  (저장 offset_ms = offset_ms − file_start_in_vod_ms). 읽을 때 같은 값을 넘기면 되돌린다.

ClipSlicer app/segments.py 의 parse_time·parse_timestamps·fmt_time 를 ms 정수용으로 다시 썼다.
초(float) 대신 ms(int)를 쓰고, 소수 부분을 문자열에서 바로 읽어 부동소수 오차가 없다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# 시작값 (설계 근거는 각 줄 주석)
# ---------------------------------------------------------------------------
#: 앵커 차이 흩어짐(최대−최소) 경고 기준. 02 7-5 '흩어짐이 크면(예: 3초 넘음, 시작값) 경고'.
SPREAD_WARN_MS = 3000
#: verified 로 확정하는 데 필요한 앵커 수. 03 4-2 '앵커를 3개 이상 찍으면 중앙값으로 새 판(verified)'.
MIN_ANCHORS_VERIFIED = 3
#: 이상치 판정: |차이 − 중앙값| > max(OUTLIER_MIN_MS, OUTLIER_K × 1.4826 × MAD).
#: 설계에 숫자가 없어 정한 시작값. 1.5 초는 사람 손으로 찍는 앵커의 흔한 흔들림(7-5 예시 0.4 초)보다
#: 충분히 크고 경고 기준 3 초의 절반이다. K=3 은 흔히 쓰는 '3 표준편차 상당' 기준.
OUTLIER_MIN_MS = 1500
OUTLIER_K = 3.0
MAD_SCALE = 1.4826

OFFSET_LIMIT_MS = 86_400_000  # project.schema calibrationRev.offset_ms 범위
METHODS = ("single_offset", "piecewise_linear")
STATUSES = ("verified", "unverified", "skipped")
ANCHOR_KINDS = ("nickname_call", "text_read", "other")


# ---------------------------------------------------------------------------
# 파싱·표시
# ---------------------------------------------------------------------------
_HMS_RE = re.compile(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)(?:[.,]([0-9]+))?s?)?")
_DIGITS_RE = re.compile(r"[0-9]+")
_NUM_RE = re.compile(r"([0-9]+)(?:[.,]([0-9]+))?")


def _frac_ms(frac: str | None) -> int:
    """소수 부분 문자열 → ms. 4자리 이상은 반올림(0.0005 → 1ms)."""
    if not frac:
        return 0
    if len(frac) <= 3:
        return int(frac.ljust(3, "0"))
    head, rest = int(frac[:3]), frac[3:]
    return head + (1 if rest[0] >= "5" else 0)


def parse_time(text: str) -> int | None:
    """시각 문자열 → ms 정수. 못 읽으면 None.

    받는 형식: '1:02:03.5', '62:03', '3723.5', '01:02:03,500'(SRT), '1h2m3s', '83.5s'.
    앞에 '-' 를 붙이면 음수(오프셋 입력용). ':' 로 나눈 앞부분은 정수여야 하고 소수는 마지막 칸에만.
    """
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    sign = 1
    if t[0] in "+-":
        sign = -1 if t[0] == "-" else 1
        t = t[1:].strip()
        if not t:
            return None
    if ":" in t:
        parts = t.split(":")
        if len(parts) > 3 or any(p == "" for p in parts):
            return None
        *heads, last = parts
        if not all(_DIGITS_RE.fullmatch(p) for p in heads):   # isdigit() 는 '²' 도 True 라 ASCII 숫자만
            return None
        m = _NUM_RE.fullmatch(last)
        if not m:
            return None
        total_s = 0
        for p in heads:
            total_s = total_s * 60 + int(p)
        total_s = total_s * 60 + int(m.group(1))
        return sign * (total_s * 1000 + _frac_ms(m.group(2)))
    m = _NUM_RE.fullmatch(t)
    if m:
        return sign * (int(m.group(1)) * 1000 + _frac_ms(m.group(2)))
    m = _HMS_RE.fullmatch(t)
    if m and any(m.groups()):
        h, mi, s, frac = m.groups()
        return sign * ((int(h or 0) * 3600 + int(mi or 0) * 60 + int(s or 0)) * 1000 + _frac_ms(frac))
    return None


_TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:[.,](\d{1,3}))?(?![\d:])")


def parse_timestamps(text: str) -> list[int]:
    """글 안의 h:mm:ss / mm:ss(.ms) 시각을 모두 찾아 ms 정렬 목록으로(중복 제거)."""
    found = set()
    for m in _TS_RE.finditer(text or ""):
        h, mi, s, frac = m.groups()
        found.add((int(h or 0) * 3600 + int(mi) * 60 + int(s)) * 1000 + _frac_ms(frac))
    return sorted(found)


def fmt_time(ms: int, ms_digits: bool = True, *, frac_digits: int | None = None,
             always_hours: bool = False) -> str:
    """ms → 'h:mm:ss.mmm' (시가 0 이면 'mm:ss.mmm'). ClipSlicer fmt_time 과 같은 모양.

    ms_digits=False 면 소수 없이 'h:mm:ss'. frac_digits(0~3)로 소수 자리를 직접 정할 수 있다
    (화면 표시는 03 4-3 규칙 6 의 h:mm:ss.s → frac_digits=1 또는 fmt_display).
    소수 자리를 줄일 때는 버림(재생 위치가 다음 칸으로 넘어가 보이지 않게). 음수는 '-' 를 붙인다.
    """
    ms = int(ms)
    sign = "-" if ms < 0 else ""
    ms = abs(ms)
    digits = (3 if ms_digits else 0) if frac_digits is None else max(0, min(3, int(frac_digits)))
    total_s, rem = divmod(ms, 1000)
    h, r = divmod(total_s, 3600)
    mi, s = divmod(r, 60)
    frac = ""
    if digits:
        frac = "." + f"{rem:03d}"[:digits]
    if h or always_hours:
        return f"{sign}{h:02d}:{mi:02d}:{s:02d}{frac}"
    return f"{sign}{mi:02d}:{s:02d}{frac}"


def fmt_display(ms: int) -> str:
    """화면 표시용 h:mm:ss.s (03 4-3 규칙 6, 02 7-5 대화상자 모양 '00:14:02.1')."""
    return fmt_time(ms, frac_digits=1, always_hours=True)


def fmt_offset(ms: int) -> str:
    """오프셋·차이 표시 '+0.9s' / '-12.0s' (02 7-5)."""
    ms = int(ms)
    sign = "-" if ms < 0 else "+"
    a = abs(ms)
    return f"{sign}{a // 1000}.{(a % 1000) // 100}s"


# ---------------------------------------------------------------------------
# 보정
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Anchor:
    """project.schema $defs/anchor. vod_ms = 채팅 시각, file_ms = 영상 파일에서 들린 시각."""
    anchor_id: str
    vod_ms: int
    file_ms: int
    kind: str = "nickname_call"
    chat_id: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Calibration:
    """보정 한 판(project.schema $defs/calibrationRev).

    offset_ms 는 '클립 시작을 뺀' 어긋남이다(파일이 VOD 전체면 스키마 offset_ms 와 같다).
    broadcast_delay_ms 는 표시·경고에만 쓰고 변환에 넣지 않는다(03 4-3 규칙 9).
    """
    offset_ms: int = 0
    broadcast_delay_ms: int | None = None
    anchors: tuple[Anchor, ...] = ()
    rev: int = 0
    method: str = "single_offset"
    media_rev: int = 0
    status: str = "unverified"
    residual_max_ms: int | None = None
    skip_reason: str | None = None
    delay_anchors: tuple[dict, ...] = ()
    created_at: str | None = None   # 시계를 쓰지 않으므로 호출하는 쪽이 넣는다
    created_by: str | None = None
    note: str | None = None


def _check_int(name: str, v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"{name} 는 정수 ms 여야 한다: {v!r}")
    return v


def _piecewise_points(cal: Calibration, file_start_in_vod_ms: int) -> list[tuple[int, int]]:
    """(chat_ms, video_ms) 점들을 chat_ms 순으로. 같은 chat_ms 는 첫 것만."""
    pts: dict[int, int] = {}
    for a in sorted(cal.anchors, key=lambda a: a.vod_ms):
        pts.setdefault(a.vod_ms, a.file_ms)
    return sorted(pts.items())


def _interp(x: int, xs: Sequence[int], ys: Sequence[int]) -> int:
    """선형 보간(정수 반올림). 양 끝 밖은 가장 가까운 구간의 차이(오프셋)를 그대로 쓴다."""
    if x <= xs[0]:
        return x + (ys[0] - xs[0])
    if x >= xs[-1]:
        return x + (ys[-1] - xs[-1])
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    x0, x1, y0, y1 = xs[lo], xs[hi], ys[lo], ys[hi]
    num = (y1 - y0) * (x - x0)
    den = x1 - x0
    # 정수 반올림(half away from zero) — 부동소수 없이
    q, r = divmod(abs(num), den)
    if 2 * r >= den:
        q += 1
    return y0 + (q if num >= 0 else -q)


def chat_to_video_ms(chat_ms: int, cal: Calibration, file_start_in_vod_ms: int = 0) -> int:
    """채팅(VOD) 시각 → 영상 파일 시각. 영상 길이 밖이어도 버리지 않는다(03 4-3 규칙 7)."""
    _check_int("chat_ms", chat_ms)
    fs = _check_int("file_start_in_vod_ms", file_start_in_vod_ms)
    if cal.method == "piecewise_linear" and len(cal.anchors) >= 2:
        pts = _piecewise_points(cal, fs)
        # 앵커 file_ms 는 이미 이 파일 기준이므로 클립 시작을 따로 빼지 않는다.
        return _interp(chat_ms, [p[0] for p in pts], [p[1] for p in pts])
    return chat_ms - fs + cal.offset_ms


def video_to_chat_ms(video_ms: int, cal: Calibration, file_start_in_vod_ms: int = 0) -> int:
    """영상 파일 시각 → 채팅(VOD) 시각. chat_to_video_ms 의 역."""
    _check_int("video_ms", video_ms)
    fs = _check_int("file_start_in_vod_ms", file_start_in_vod_ms)
    if cal.method == "piecewise_linear" and len(cal.anchors) >= 2:
        pts = _piecewise_points(cal, fs)
        ys = [p[1] for p in pts]
        if all(b > a for a, b in zip(ys, ys[1:])):
            return _interp(video_ms, ys, [p[0] for p in pts])
        # 영상 시각이 단조 증가하지 않는 앵커 묶음은 역함수가 없다 → 단일 오프셋으로 물러난다
    return video_ms + fs - cal.offset_ms


def relabel_shift_ms(stored_ms: int, label_rev_offset_ms: int, active_offset_ms: int) -> int:
    """플레이어에서 찍은 저장값을 활성 보정 기준으로 다시 계산(03 4-2 표):
    저장값 + offset[라벨의 calibration_rev] − offset[활성 rev]."""
    return _check_int("stored_ms", stored_ms) + label_rev_offset_ms - active_offset_ms


@dataclass(frozen=True)
class OffsetFit:
    """fit_offset 결과."""
    offset_ms: int                     # 앵커 차이의 중앙값(클립 시작을 뺀 값)
    mad_ms: int                        # 중앙값 절대 편차(MAD)
    spread_ms: int                     # 차이의 최대 − 최소 (02 7-5 대화상자의 '흩어짐')
    residual_max_ms: int               # max |차이 − 중앙값| (스키마 residual_max_ms)
    diffs_ms: tuple[int, ...]          # 앵커별 차이(video − chat + file_start), 입력 순서
    outliers: tuple[bool, ...]         # 앵커별 이상치 표시, 입력 순서
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n(self) -> int:
        return len(self.diffs_ms)

    @property
    def n_inliers(self) -> int:
        return sum(1 for o in self.outliers if not o)

    @property
    def suggested_status(self) -> str:
        """verified = 앵커 3개 이상이고 경고 없음. 그 밖은 unverified(사람이 보고 정한다)."""
        return "verified" if self.n >= MIN_ANCHORS_VERIFIED and not self.warnings else "unverified"


def _median_int(vals: Sequence[int]) -> int:
    s = sorted(vals)
    n = len(s)
    if n % 2:
        return s[n // 2]
    a, b = s[n // 2 - 1], s[n // 2]
    tot = a + b
    # 짝수 개: 두 가운데 값의 평균, 0.5 는 0 에서 먼 쪽으로 반올림
    return tot // 2 if tot % 2 == 0 else (tot + (1 if tot > 0 else -1)) // 2


def fit_offset(anchors: Iterable[tuple[int, int]], file_start_in_vod_ms: int = 0,
               *, spread_warn_ms: int = SPREAD_WARN_MS) -> OffsetFit:
    """앵커 [(video_ms, chat_ms)] → 오프셋 중앙값·퍼짐·이상치 (03 4-2, 02 7-5).

    차이 d_i = video_ms − (chat_ms − file_start_in_vod_ms). offset = median(d).
    경고 코드:
      too_few_anchors  — 앵커 < 3 (verified 불가)
      spread_large     — 최대−최소 > spread_warn_ms (앞부분이 잘린 파일·중간에 끊긴 VOD 의심)
      outliers         — 이상치가 1개 이상
    """
    pairs = [(_check_int("video_ms", v), _check_int("chat_ms", c)) for v, c in anchors]
    if not pairs:
        raise ValueError("앵커가 없다")
    fs = _check_int("file_start_in_vod_ms", file_start_in_vod_ms)
    diffs = [v - (c - fs) for v, c in pairs]
    med = _median_int(diffs)
    absdev = [abs(d - med) for d in diffs]
    mad = _median_int(absdev)
    thr = max(OUTLIER_MIN_MS, OUTLIER_K * MAD_SCALE * mad)
    outliers = tuple(a > thr for a in absdev)
    spread = max(diffs) - min(diffs)
    warns = []
    if len(diffs) < MIN_ANCHORS_VERIFIED:
        warns.append("too_few_anchors")
    if spread > spread_warn_ms:
        warns.append("spread_large")
    if any(outliers):
        warns.append("outliers")
    if not -OFFSET_LIMIT_MS <= med - fs <= OFFSET_LIMIT_MS:
        raise ValueError(f"오프셋이 스키마 범위(±{OFFSET_LIMIT_MS}ms) 밖: {med - fs}")
    return OffsetFit(offset_ms=med, mad_ms=mad, spread_ms=spread, residual_max_ms=max(absdev),
                     diffs_ms=tuple(diffs), outliers=outliers, warnings=tuple(warns))


# ---------------------------------------------------------------------------
# JSON (project.schema calibration / calibrationRev)
# ---------------------------------------------------------------------------
def anchor_to_json(a: Anchor) -> dict:
    d = {"anchor_id": a.anchor_id, "kind": a.kind, "chat_id": a.chat_id,
         "vod_ms": a.vod_ms, "file_ms": a.file_ms}
    if a.note is not None:
        d["note"] = a.note
    return d


def anchor_from_json(d: dict) -> Anchor:
    return Anchor(anchor_id=d["anchor_id"], vod_ms=_check_int("vod_ms", d["vod_ms"]),
                  file_ms=_check_int("file_ms", d["file_ms"]), kind=d.get("kind", "other"),
                  chat_id=d.get("chat_id"), note=d.get("note"))


def calibration_to_json(cal: Calibration, file_start_in_vod_ms: int = 0) -> dict:
    """Calibration → calibrationRev 한 판(dict). 클립 시작은 offset_ms 에 합친다(머리말 참고).

    created_at·created_by 가 없으면 스키마 필수 필드가 비므로 ValueError.
    """
    if cal.method not in METHODS:
        raise ValueError(f"method 가 잘못됨: {cal.method!r}")
    if cal.status not in STATUSES:
        raise ValueError(f"status 가 잘못됨: {cal.status!r}")
    if not cal.created_at or not cal.created_by:
        raise ValueError("created_at·created_by 가 필요하다(스키마 필수)")
    off = _check_int("offset_ms", cal.offset_ms) - _check_int("file_start_in_vod_ms", file_start_in_vod_ms)
    if not -OFFSET_LIMIT_MS <= off <= OFFSET_LIMIT_MS:
        raise ValueError(f"offset_ms 가 스키마 범위 밖: {off}")
    d = {
        "rev": cal.rev,
        "media_rev": cal.media_rev,
        "method": cal.method,
        "offset_ms": off,
        "anchors": [anchor_to_json(a) for a in cal.anchors],
        "residual_max_ms": cal.residual_max_ms,
        "status": cal.status,
        "skip_reason": cal.skip_reason,
        "broadcast_delay_ms": cal.broadcast_delay_ms,
        "delay_anchors": [dict(x) for x in cal.delay_anchors],
        "created_at": cal.created_at,
        "created_by": cal.created_by,
    }
    if cal.note is not None:
        d["note"] = cal.note
    return d


def calibration_from_json(obj: dict, file_start_in_vod_ms: int = 0, rev: int | None = None) -> Calibration:
    """calibration({active_rev, revs}) 또는 calibrationRev 한 판 → Calibration.

    rev 를 주면 그 판, 아니면 active_rev 판. file_start_in_vod_ms 는 calibration_to_json 에 준 값과 같게.
    """
    if "revs" in obj:
        want = obj["active_rev"] if rev is None else rev
        found = [r for r in obj["revs"] if r.get("rev") == want]
        if not found:
            raise KeyError(f"보정 판 {want} 이 없다")
        d = found[-1]
    else:
        d = obj
    fs = _check_int("file_start_in_vod_ms", file_start_in_vod_ms)
    return Calibration(
        offset_ms=_check_int("offset_ms", d["offset_ms"]) + fs,
        broadcast_delay_ms=d.get("broadcast_delay_ms"),
        anchors=tuple(anchor_from_json(a) for a in d.get("anchors", [])),
        rev=_check_int("rev", d["rev"]),
        method=d.get("method", "single_offset"),
        media_rev=d.get("media_rev", 0),
        status=d.get("status", "unverified"),
        residual_max_ms=d.get("residual_max_ms"),
        skip_reason=d.get("skip_reason"),
        delay_anchors=tuple(dict(x) for x in d.get("delay_anchors", [])),
        created_at=d.get("created_at"),
        created_by=d.get("created_by"),
        note=d.get("note"),
    )


def initial_calibration_json(created_at: str, created_by: str, media_rev: int = 0) -> dict:
    """프로젝트를 만들 때의 보정 기록: 판 0, 오프셋 0, unverified (03 4-2)."""
    cal = Calibration(rev=0, media_rev=media_rev, created_at=created_at, created_by=created_by)
    return {"active_rev": 0, "revs": [calibration_to_json(cal)]}


def next_rev(calib_obj: dict | None) -> int:
    if not calib_obj or not calib_obj.get("revs"):
        return 0
    return max(r["rev"] for r in calib_obj["revs"]) + 1


def append_rev(calib_obj: dict | None, rev_json: dict) -> dict:
    """새 판을 붙이고 active_rev 로 삼은 새 calibration 객체를 돌려준다(판은 추가만, 스키마 설명).
    rev_json['rev'] 가 이미 있는 번호면 ValueError."""
    revs = list((calib_obj or {}).get("revs", []))
    if any(r["rev"] == rev_json["rev"] for r in revs):
        raise ValueError(f"보정 판 {rev_json['rev']} 이 이미 있다")
    revs.append(rev_json)
    return {"active_rev": rev_json["rev"], "revs": revs}
