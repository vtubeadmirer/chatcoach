"""app/core/timecode.py · tools/calibrate.py 테스트 (합성 값만)."""
import json
import os
import re
import subprocess
import sys

import pytest

from app.core import timecode as tc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = "2026-10-20T09:00:00+09:00"


# ---------------------------------------------------------------- 파싱·표시
@pytest.mark.parametrize("text,ms", [
    ("1:02:03.5", 3723500),
    ("62:03", 3723000),
    ("3723.5", 3723500),
    ("01:02:03,500", 3723500),
    ("1h2m3s", 3723000),
    ("1h2m3.25s", 3723250),
    ("83.5s", 83500),
    ("0", 0),
    ("00:00.001", 1),
    ("0.0005", 1),        # 4자리 이상은 반올림
    ("0.0004", 0),
    ("-12", -12000),
    ("+0.9", 900),
    ("  5:00 ", 300000),
])
def test_parse_time_formats(text, ms):
    assert tc.parse_time(text) == ms


@pytest.mark.parametrize("text", ["", "  ", "abc", "1::2", "1:2:3:4", "1.5:00", ":30", "-", "1:xx"])
def test_parse_time_rejects(text):
    assert tc.parse_time(text) is None


def test_parse_time_no_float_error():
    # 부동소수로 계산하면 0.29*1000 = 289.99... 가 될 수 있다
    assert tc.parse_time("0.29") == 290
    assert tc.parse_time("1:00:00.007") == 3600007


def test_fmt_time():
    assert tc.fmt_time(3723500) == "01:02:03.500"
    assert tc.fmt_time(83500) == "01:23.500"
    assert tc.fmt_time(83500, ms_digits=False) == "01:23"
    assert tc.fmt_time(-12000) == "-00:12.000"
    assert tc.fmt_display(842100) == "00:14:02.1"
    assert tc.fmt_display(842199) == "00:14:02.1"   # 버림
    assert tc.fmt_offset(900) == "+0.9s"
    assert tc.fmt_offset(-12000) == "-12.0s"


@pytest.mark.parametrize("ms", [0, 1, 999, 59999, 3723500, 36000000 + 7])
def test_fmt_parse_roundtrip(ms):
    assert tc.parse_time(tc.fmt_time(ms)) == ms


def test_parse_timestamps():
    text = "0:30 시작, 1:02:03.5 에 호명, 다시 0:30 · 12:34,5"
    assert tc.parse_timestamps(text) == [30000, 754500, 3723500]


# ---------------------------------------------------------------- 변환
@pytest.mark.parametrize("offset", [0, 900, -12000, 1234567])
@pytest.mark.parametrize("fs", [0, 300000])
def test_roundtrip_single_offset(offset, fs):
    cal = tc.Calibration(offset_ms=offset)
    for chat in (0, 1, 299999, 300000, 842100, 9000000):
        v = tc.chat_to_video_ms(chat, cal, fs)
        assert v == chat - fs + offset
        assert tc.video_to_chat_ms(v, cal, fs) == chat


def test_negative_offset_head_cut():
    # 파일 앞 12초가 잘렸으면 offset_ms = -12000 (03 4-2)
    cal = tc.Calibration(offset_ms=-12000)
    assert tc.chat_to_video_ms(60000, cal) == 48000
    assert tc.video_to_chat_ms(0, cal) == 12000
    # 영상 길이 밖(음수)이어도 버리지 않는다
    assert tc.chat_to_video_ms(5000, cal) == -7000


def test_broadcast_delay_not_applied():
    cal = tc.Calibration(offset_ms=500, broadcast_delay_ms=8000)
    assert tc.chat_to_video_ms(10000, cal) == 10500


def test_rejects_float():
    cal = tc.Calibration()
    with pytest.raises(TypeError):
        tc.chat_to_video_ms(1.5, cal)
    with pytest.raises(TypeError):
        tc.video_to_chat_ms(True, cal)


def test_piecewise_linear():
    anchors = (tc.Anchor("a1", vod_ms=0, file_ms=1000), tc.Anchor("a2", vod_ms=10000, file_ms=13000))
    cal = tc.Calibration(method="piecewise_linear", anchors=anchors)
    assert tc.chat_to_video_ms(5000, cal) == 7000
    assert tc.video_to_chat_ms(7000, cal) == 5000
    assert tc.chat_to_video_ms(20000, cal) == 23000   # 끝 밖은 마지막 오프셋
    assert tc.chat_to_video_ms(-1000, cal) == 0
    for c in range(-2000, 25000, 777):
        assert abs(tc.video_to_chat_ms(tc.chat_to_video_ms(c, cal), cal) - c) <= 1


def test_relabel_shift():
    assert tc.relabel_shift_ms(10000, 0, 900) == 9100


# ---------------------------------------------------------------- 오프셋 맞추기
def test_fit_three_anchors_median():
    # 02 7-5 대화상자 예: +0.9 / +1.2 / +0.8 → 중앙값 +0.9, 흩어짐 0.4
    pairs = [(843000, 842100), (3761200, 3760000), (9076300, 9075500)]
    fit = tc.fit_offset(pairs)
    assert fit.offset_ms == 900
    assert fit.spread_ms == 400
    assert fit.mad_ms == 100
    assert fit.outliers == (False, False, False)
    assert fit.warnings == ()
    assert fit.suggested_status == "verified"


def test_fit_one_outlier():
    pairs = [(10900, 10000), (21200, 20000), (30800, 30000), (55000, 40000)]
    fit = tc.fit_offset(pairs)
    assert fit.outliers == (False, False, False, True)
    assert fit.offset_ms == 1050          # 짝수 개 중앙값 (900+1200)/2
    assert "outliers" in fit.warnings and "spread_large" in fit.warnings
    assert fit.suggested_status == "unverified"
    assert fit.residual_max_ms == 15000 - 1050


def test_fit_three_with_outlier():
    fit = tc.fit_offset([(10900, 10000), (21200, 20000), (55000, 40000)])
    assert fit.offset_ms == 1200
    assert fit.outliers == (False, False, True)


def test_fit_negative_offset():
    fit = tc.fit_offset([(48000, 60000), (88100, 100000), (107900, 120000)])
    assert fit.offset_ms == -12000
    assert fit.warnings == ()


def test_fit_too_few():
    fit = tc.fit_offset([(1000, 0)])
    assert fit.offset_ms == 1000
    assert fit.warnings == ("too_few_anchors",)
    assert fit.suggested_status == "unverified"
    with pytest.raises(ValueError):
        tc.fit_offset([])


def test_fit_clip_start():
    # VOD 5:00 부터 자른 클립. 파일 0:10.9 에서 VOD 5:10 채팅이 들림 → 남은 어긋남 +0.9
    fs = 300000
    pairs = [(10900, 310000), (60800, 360000), (121000, 420000)]
    fit = tc.fit_offset(pairs, file_start_in_vod_ms=fs)
    assert fit.offset_ms == 900
    cal = tc.Calibration(offset_ms=fit.offset_ms)
    assert tc.chat_to_video_ms(310000, cal, fs) == 10900
    assert tc.video_to_chat_ms(10900, cal, fs) == 310000
    # 클립 시작을 모르면 거대한 음수 오프셋이 같은 결과를 낸다(스키마 저장 모양)
    fit0 = tc.fit_offset(pairs)
    assert fit0.offset_ms == 900 - fs
    assert tc.chat_to_video_ms(310000, tc.Calibration(offset_ms=fit0.offset_ms)) == 10900


# ---------------------------------------------------------------- JSON
def _schema_errors(obj):
    """jsonschema 가 있으면 그것으로, 없으면 required·additionalProperties·enum 만 손으로 검사."""
    with open(os.path.join(ROOT, "schemas", "project.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    defs = schema["$defs"]
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        sub = {"$defs": defs, "$ref": "#/$defs/calibration"}
        cls = jsonschema.validators.validator_for(schema)
        return [e.message for e in cls(sub).iter_errors(obj)]
    errs = []

    def check(o, d, where):
        for k in d.get("required", []):
            if k not in o:
                errs.append(f"{where}: {k} 없음")
        props = d.get("properties", {})
        if d.get("additionalProperties") is False:
            errs.extend(f"{where}: 모르는 키 {k}" for k in o if k not in props)
        for k, v in o.items():
            pd = props.get(k, {})
            if "enum" in pd and v not in pd["enum"]:
                errs.append(f"{where}.{k}: enum 밖 {v!r}")
            t = pd.get("type")
            if t == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
                errs.append(f"{where}.{k}: 정수 아님")

    check(obj, defs["calibration"], "calibration")
    for i, r in enumerate(obj["revs"]):
        check(r, defs["calibrationRev"], f"revs[{i}]")
        for j, a in enumerate(r["anchors"]):
            check(a, defs["anchor"], f"revs[{i}].anchors[{j}]")
            if a["chat_id"] is not None and not re.fullmatch(r"c[0-9a-f]{20}(_[0-9]+)?", a["chat_id"]):
                errs.append("chat_id 모양")
            if not re.fullmatch(defs["uuid"]["pattern"], a["anchor_id"]):
                errs.append("anchor_id 모양")
        if not re.fullmatch(defs["labelerId"]["pattern"], r["created_by"]):
            errs.append("created_by 모양")
        if not re.fullmatch(defs["datetime"]["pattern"], r["created_at"]):
            errs.append("created_at 모양")
    return errs


def _cal(offset=900, fs=0):
    anchors = (tc.Anchor("0b6f8b8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", vod_ms=842100, file_ms=843000 - fs,
                         chat_id="c0123456789abcdef0123", note="합성"),)
    return tc.Calibration(offset_ms=offset, broadcast_delay_ms=4000, anchors=anchors, rev=1,
                          media_rev=1, status="verified", residual_max_ms=300,
                          created_at=NOW, created_by="L01", note="시험")


@pytest.mark.parametrize("fs", [0, 300000])
def test_json_roundtrip(fs):
    cal = _cal(fs=fs)
    d = tc.calibration_to_json(cal, file_start_in_vod_ms=fs)
    assert d["offset_ms"] == 900 - fs
    obj = tc.append_rev(tc.initial_calibration_json(NOW, "L01"), d)
    assert obj["active_rev"] == 1 and len(obj["revs"]) == 2
    back = tc.calibration_from_json(obj, file_start_in_vod_ms=fs)
    assert back == cal
    assert tc.calibration_from_json(obj, rev=0).offset_ms == 0
    assert tc.calibration_from_json(obj, rev=0).status == "unverified"
    json.dumps(obj)  # 직렬화 가능


def test_json_matches_schema():
    obj = tc.append_rev(tc.initial_calibration_json(NOW, "L01"), tc.calibration_to_json(_cal()))
    errs = _schema_errors(obj)
    assert not errs, errs


def test_json_rejects():
    with pytest.raises(ValueError):
        tc.calibration_to_json(tc.Calibration())             # created_* 없음
    with pytest.raises(ValueError):
        tc.calibration_to_json(tc.Calibration(method="x", created_at=NOW, created_by="L01"))
    obj = tc.initial_calibration_json(NOW, "L01")
    with pytest.raises(ValueError):
        tc.append_rev(obj, obj["revs"][0])                   # 같은 rev
    assert tc.next_rev(obj) == 1 and tc.next_rev(None) == 0
    with pytest.raises(KeyError):
        tc.calibration_from_json(obj, rev=5)


# ---------------------------------------------------------------- 층 규칙
def test_core_imports_pure():
    src = open(os.path.join(ROOT, "app", "core", "timecode.py"), encoding="utf-8").read()
    for bad in ("import time", "import datetime", "import random", "import os", "PySide", "PyQt",
                "import socket", "urllib", "import uuid", "open("):
        assert bad not in src, bad


# ---------------------------------------------------------------- 도구
def _run(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "calibrate.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)


def test_tool_dry_run(tmp_path):
    r = _run("--project", str(tmp_path), "--dry-run",
             "--anchor", "00:14:03.0=00:14:02.1", "--anchor", "1:02:41.2=1:02:40.0",
             "--anchor", "2:31:16.3=2:31:15.5")
    assert r.returncode == 0, r.stderr
    assert "+0.9s" in r.stdout
    js = json.loads(r.stdout[r.stdout.index("{"):])
    assert js["offset_ms"] == 900 and js["status"] == "verified" and len(js["anchors"]) == 3
    assert not (tmp_path / "project.json").exists()


def test_tool_dry_run_clip_and_outlier(tmp_path):
    r = _run("--project", str(tmp_path), "--dry-run", "--file-start-ms", "300000",
             "--anchor", "0:10.9=5:10", "--anchor", "1:00.8=6:00", "--anchor", "3:00=7:00")
    assert r.returncode == 0, r.stderr
    assert "이상치" in r.stdout and "경고" in r.stdout
    js = json.loads(r.stdout[r.stdout.index("{"):])
    assert js["offset_ms"] == 900 - 300000
    assert js["status"] == "unverified"
    assert "file_start_in_vod_ms=300000" in js["note"]


def test_tool_bad_input(tmp_path):
    assert _run("--project", str(tmp_path), "--dry-run", "--anchor", "abc=1:00").returncode == 1
    assert _run("--project", str(tmp_path), "--dry-run").returncode == 1


def test_tool_saves_with_fake_project(tmp_path, monkeypatch, capsys):
    """app.project 가 아직 없을 수 있어 가짜 모듈로 update 계약만 확인."""
    import types
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import calibrate
    finally:
        sys.path.pop(0)
    store = {"media": {"media_rev": 2}, "calibration": tc.initial_calibration_json(NOW, "L01")}

    class P:
        def update(self, fn, **kw):
            fn(store)

    fake = types.ModuleType("app.project")
    fake.open = lambda path: P()
    monkeypatch.setitem(sys.modules, "app.project", fake)
    rc = calibrate.main(["--project", str(tmp_path), "--anchor", "10.9=10", "--anchor", "21.2=20",
                         "--anchor", "30.8=30"])
    assert rc == 0
    cal = store["calibration"]
    assert cal["active_rev"] == 1 and cal["revs"][1]["media_rev"] == 2
    assert cal["revs"][1]["offset_ms"] == 900
    errs = _schema_errors(cal)
    assert not errs, errs


def test_tool_saves_real_project(tmp_path):
    """app.project 가 있으면 실제 프로젝트(합성)에 저장하고 다시 열어 확인."""
    project = pytest.importorskip("app.project")
    root = tmp_path / "합성방송-0"
    try:
        p = project.create(str(root), "합성방송", 0)
    except Exception as e:  # 다른 에이전트가 만드는 중인 모듈 — 그쪽 문제면 건너뛴다
        pytest.skip(f"app.project.create 실패: {e!r}")
    # create 는 root 아래에 <slug> 폴더를 만든다. 저장·다시 열기는 그 폴더(p.root)로 한다
    r = _run("--project", str(p.root),
             "--anchor", "10.9=10", "--anchor", "21.2=20", "--anchor", "30.8=30")
    assert r.returncode == 0, r.stdout + r.stderr
    q = project.open(str(p.root))
    assert q.rev == p.rev + 1
    assert not q.read_only, q.problems
    cal = q.data["calibration"]
    assert cal["active_rev"] == 1 and cal["revs"][1]["offset_ms"] == 900
    assert tc.calibration_from_json(cal).status == "verified"


# ── 검토 수정 재현 (2026-10-20) ──────────────────────────────────────────
@pytest.mark.parametrize("text", ["²:00", "1:²0", "١:00", "²", "1h²m", "1:0²"])
def test_parse_time_non_ascii_digits_return_none(text):
    assert tc.parse_time(text) is None      # 예전에는 '²:00' 이 ValueError


# ── 영상이 덮는 VOD 범위 (08 D-10 3) ─────────────────────────────────────
def _cov_cal(offset_ms):
    c = tc.Calibration(offset_ms=offset_ms, rev=1, status="verified", created_at="2026-10-21T09:00:00+09:00",
                       created_by="L01")
    return tc.append_rev(tc.initial_calibration_json("2026-10-21T09:00:00+09:00", "L01"), tc.calibration_to_json(c))


def _media(duration_ms):
    return {"media_rev": 1, "current": {"probe": {"duration_ms": duration_ms, "vfr": False}}, "history": []}


def test_media_coverage():
    # 영상 파일 = VOD + offset → 파일 0초가 VOD 20분이면 offset = −20분
    assert tc.media_coverage(_media(1_153_000), _cov_cal(-1_238_000)) == [(1_238_000, 2_391_000)]
    assert tc.media_coverage(_media(60_000), _cov_cal(0)) == [(0, 60_000)]
    assert tc.media_coverage(_media(60_000), _cov_cal(10_000)) == [(0, 50_000)]     # VOD 0 앞은 자른다
    assert tc.media_coverage(_media(60_000), _cov_cal(90_000)) == []                # 다 VOD 0 앞
    assert tc.media_coverage(_media(60_000), None) == [(0, 60_000)]             # 보정 없음 = 오프셋 0
    assert tc.media_coverage({"media_rev": 0, "current": None, "history": []}, _cov_cal(0)) == []   # 영상 없음
    assert tc.media_coverage(None, None) == []
    assert tc.media_coverage(_media(0), _cov_cal(0)) is None                        # 길이 모름
    assert tc.media_coverage({"current": {"probe": {}}}, None) is None
    assert tc.media_coverage(_media(60_000), {"active_rev": 9, "revs": []}) == [(0, 60_000)]   # 못 읽으면 오프셋 0
