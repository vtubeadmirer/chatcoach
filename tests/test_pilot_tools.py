"""tools/pilot_setup.py · tools/pilot_freeze.py (08 D-14 5·6·7).

합성 값만 쓴다. 영상 파일은 빈 파일(또는 몇 바이트)이고, VOD 번호는 같은 숫자를 늘어놓은 지어낸 값이다.
실제 시범 폴더·라벨 데이터 폴더에는 쓰지 않는다(모든 경로를 tmp_path 로 준다). 네트워크를 쓰지 않는다
(채팅 받기·앵커 후보는 가짜 run_tool 로 바꾼다).
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys

import pytest

from app import jsonl, project, store
from app.core.validate import canonical_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", os.path.join(ROOT, "tools", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod            # dataclass 가 모듈을 찾을 수 있게
    spec.loader.exec_module(mod)
    return mod


ps = _load_tool("pilot_setup")
pf = _load_tool("pilot_freeze")
new_project_tool = _load_tool("new_project")

VNO_A = "7" * 8          # 지어낸 VOD 번호
VNO_B = "8" * 8
VNO_C = "6" * 7


def clipslicer_time(sec: int) -> str:
    """ClipSlicer app/segments.py fmt_time_file 과 같은 꼴(읽기만 해서 옮겨 적음)."""
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h:02d}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def clipslicer_name(title: str, vno: str, start: int, end: int, ext: str = ".mp4") -> str:
    """ClipSlicer workers._download_section 의 기본 이름: f"{title} [{vid}] {start}-{end}"."""
    return f"{title} [{vno}] {clipslicer_time(start)}-{clipslicer_time(end)}{ext}"


# ================================================================================================ 이름 해석

@pytest.mark.parametrize("start,end", [(0, 1800), (600, 2700), (59 * 60 + 59, 3600 + 35 * 60), (3600, 3600 + 2400),
                                       (2 * 3600 + 5, 2 * 3600 + 5 + 1999)])
def test_clipslicer_names_roundtrip(start, end):
    c = ps.parse_clip_name(clipslicer_name("합성 방송 제목", VNO_A, start, end))
    assert c is not None and c.pattern == "clipslicer"
    assert (c.video_no, c.start_ms, c.end_ms, c.title) == (VNO_A, start * 1000, end * 1000, "합성 방송 제목")


def test_clipslicer_title_with_brackets_and_other_ext():
    c = ps.parse_clip_name(f"[공지] 합성 [잡담] [{VNO_B}] 01h20m00s-01h55m30s.mkv")
    assert c.video_no == VNO_B and c.title == "[공지] 합성 [잡담]"
    assert c.start_ms == 4_800_000 and c.end_ms == 6_930_000 and c.duration_ms == 2_130_000
    assert c.warnings == []                                  # 35분 30초: 30~40분 안


def test_clipslicer_dup_suffix_warns():
    c = ps.parse_clip_name(f"합성 [{VNO_A}] 10m00s-45m00s_1.mp4")
    assert c is not None and c.start_ms == 600_000
    assert any("_1" in w for w in c.warnings)


def test_clipslicer_six_digit_hhmmss_names():
    """실제 시범 폴더에 있는 꼴: '<제목> [<번호>] HHMMSS-HHMMSS_NN' (합성 제목·번호)."""
    c = ps.parse_clip_name(f"합성 제목입니다. [{VNO_A}] 000000-034900_01.mp4")
    assert c is not None and c.pattern == "clipslicer"
    assert (c.video_no, c.start_ms, c.end_ms) == (VNO_A, 0, (3 * 3600 + 49 * 60) * 1000)
    assert any("_01" in w for w in c.warnings) and any("30~40" in w or "분" in w for w in c.warnings)
    c2 = ps.parse_clip_name(f"합성 [{VNO_B}] 012000-015530.mp4")
    assert (c2.start_ms, c2.end_ms) == (4_800_000, 6_930_000) and not c2.warnings
    assert ps.parse_clip_name(f"합성 [{VNO_B}] 016100-019000.mp4") is None      # 분 61
    assert ps.parse_hhmmss("12345") is None and ps.parse_hhmmss("001060") is None


def test_plain_names():
    c = ps.parse_clip_name(f"{VNO_C}_01h00m00s-01h35m00s.mp4")
    assert (c.pattern, c.video_no, c.start_ms, c.end_ms, c.title) == ("plain", VNO_C, 3_600_000, 5_700_000, None)
    c2 = ps.parse_clip_name(f"{VNO_C}_3600-5700.webm")
    assert (c2.start_ms, c2.end_ms) == (3_600_000, 5_700_000)


@pytest.mark.parametrize("name", [
    f"합성 [{VNO_A}] 10m00s-45m00s.txt",          # 영상 확장자 아님
    f"합성 [{VNO_A}] 45m00s-10m00s.mp4",          # 끝이 시작보다 앞
    f"합성 [{VNO_A}] 10m00s-10m00s.mp4",          # 길이 0
    f"합성 [{VNO_A}] 10m61s-45m00s.mp4",          # 초 61
    f"합성 {VNO_A} 10m00s-45m00s.mp4",            # 대괄호 없음
    "합성 [000] 10m00s-45m00s.mp4",               # 번호 0
    f"합성 [{VNO_A}] 10:00-45:00.mp4",            # ClipSlicer 꼴 아님
    f"{VNO_A}-600-2700.mp4",                      # 밑줄 없음
    "그냥 영상.mp4",
])
def test_rejects_other_names(name):
    assert ps.parse_clip_name(name) is None


def test_length_outside_pilot_rule_warns():
    short = ps.parse_clip_name(clipslicer_name("합성", VNO_A, 0, 20 * 60))
    long_ = ps.parse_clip_name(clipslicer_name("합성", VNO_A, 0, 50 * 60))
    ok = ps.parse_clip_name(clipslicer_name("합성", VNO_A, 0, 30 * 60))
    assert any("30~40분" in w for w in short.warnings) and any("30~40분" in w for w in long_.warnings)
    assert ok.warnings == []


def _touch(folder, name, data=b""):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)


def test_scan_lists_skipped_and_duplicates(tmp_path):
    v = str(tmp_path / "시범 영상")
    _touch(v, clipslicer_name("합성 하나", VNO_A, 600, 2700))
    _touch(v, clipslicer_name("합성 하나 다시", VNO_A, 3000, 5000))
    _touch(v, f"{VNO_B}_00m00s-35m00s.mp4")
    _touch(v, "이름 모름.mp4")
    _touch(v, "메모.txt")
    os.makedirs(os.path.join(v, f"{VNO_C}_00m00s-35m00s.mp4"))      # 폴더는 무시
    clips, skipped = ps.scan(v)
    assert [c.video_no for c in clips] == [VNO_B, VNO_A, VNO_A]          # 이름 순
    assert skipped == ["이름 모름.mp4"]
    assert [any("같은 VOD" in w for w in c.warnings) for c in clips] == [False, False, True]


def test_anchor_plan_and_hint():
    c = ps.parse_clip_name(clipslicer_name("합성", VNO_A, 4800, 4800 + 2100))
    ap = ps.anchor_plan(c)
    assert ap == {"file_start_ms": 4_800_000, "chat_from": "4800000ms", "chat_to": "6900000ms"}
    h = ps.calibrate_hint("D:\\프로젝트", c)
    assert "--file-start-ms 4800000" in h and h.count("--anchor") == 3 and h.endswith("--dry-run")


# ================================================================================================ dry-run

def _run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_dry_run_only_parses(tmp_path):
    v = str(tmp_path / "시범 영상")
    for i, num in enumerate((VNO_A, VNO_B, VNO_C)):
        _touch(v, clipslicer_name(f"합성 {i}", num, 600, 600 + 33 * 60))
    proots, pdir = str(tmp_path / "projects"), str(tmp_path / "pilot")
    code, out, _ = _run(ps, ["--videos", v, "--projects-root", proots, "--pilot-dir", pdir, "--dry-run"])
    assert code == 0
    assert "읽은 영상 3개" in out and "--dry-run" in out and "참고: 시범은" not in out
    assert not os.path.exists(proots) and not os.path.exists(pdir)


def test_dry_run_missing_folder_or_no_names(tmp_path):
    code, _, err = _run(ps, ["--videos", str(tmp_path / "없음"), "--dry-run"])
    assert code == 1 and "없습니다" in err
    v = str(tmp_path / "빈")
    _touch(v, "이름 모름.mp4")
    code, out, _ = _run(ps, ["--videos", v, "--dry-run"])
    assert code == 1 and "읽지 못함: 이름 모름.mp4" in out


def test_real_pilot_folder_dry_run_if_present():
    """실제 시범 폴더가 있으면 이름만 읽어 본다(아무것도 쓰지 않음). 없으면 건너뛴다."""
    if not os.path.isdir(ps.DEFAULT_VIDEOS):
        pytest.skip("시범 영상 폴더가 아직 없다")
    clips, _ = ps.scan(ps.DEFAULT_VIDEOS)
    for c in clips:
        assert c.end_ms > c.start_ms and c.video_no.isdigit()


# ================================================================================================ 실제 흐름 (도구는 가짜)

class FakeTools:
    """run_tool 대신. new_project 는 진짜 도구를 같은 프로세스에서, 나머지는 흉내."""

    def __init__(self, fetch_code=0):
        self.calls: list[tuple[str, list[str]]] = []
        self.fetch_code = fetch_code

    def __call__(self, name, args, *, capture):
        self.calls.append((name, list(args)))
        if name == "new_project":
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = new_project_tool.main(args + ["--machine4", "test"])
            return code, out.getvalue()
        if name == "fetch_chat":
            proj = args[args.index("--project") + 1]
            if self.fetch_code == 0:
                msgs = [{"chat_id": "c" + str(i) * 20, "t_ms": 4_800_000 + i * 1000, "server_time_ms": i,
                         "type_code": 1, "kind": "text", "status": "normal", "viewer_id": "vw_aaaaaaaaaaaa",
                         "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
                         "text": "합성 질문인가요?", "flags": {}, "raw_ref": f"f:1:{i}"} for i in range(1, 4)]
                jsonl.write_all(os.path.join(proj, "chat", "messages.jsonl"), msgs)
            return self.fetch_code, ""
        if name == "find_anchors":
            return 0, "합성 후보 표 (본문 없음)\n"
        raise AssertionError(name)

    def names(self):
        return [n for n, _ in self.calls]


@pytest.fixture
def fake_probe(monkeypatch):
    from app import analysis
    monkeypatch.setattr(analysis, "probe", lambda path, *a, **k: {
        "duration_ms": 2_100_000, "video_codec": "h264", "audio_codec": "aac", "width": 1280, "height": 720,
        "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "vfr": False, "nb_frames": None})


def _pilot_dirs(tmp_path):
    v = str(tmp_path / "시범 영상")
    _touch(v, clipslicer_name("합성 방송", VNO_A, 4800, 4800 + 2100), b"\x00" * 4096)
    return v, str(tmp_path / "데이터" / "projects"), str(tmp_path / "데이터" / "pilot")


def _state(pdir):
    with open(os.path.join(pdir, ps.STATE_NAME), "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def test_full_run_creates_project_fetches_chat_links_media_and_only_suggests_calibration(
        tmp_path, monkeypatch, fake_probe):
    v, proots, pdir = _pilot_dirs(tmp_path)
    fake = FakeTools()
    monkeypatch.setattr(ps, "run_tool", fake)
    code, out, _ = _run(ps, ["--videos", v, "--projects-root", proots, "--pilot-dir", pdir, "--engine-author", "yes"])
    assert code == 0, out
    assert fake.names() == ["new_project", "fetch_chat", "find_anchors"]
    fa = dict(zip(fake.calls[2][1][::2], fake.calls[2][1][1::2]))
    assert fa["--file-start-ms"] == "4800000" and fa["--from"] == "4800000ms" and fa["--to"] == "6900000ms"
    assert "--no-text" in fake.calls[2][1]

    st = _state(pdir)
    assert len(st["items"]) == 1
    item = st["items"][0]
    assert item["video_no"] == VNO_A and item["start_ms"] == 4_800_000
    assert item["steps"] == {"project": "done", "chat": "done", "media": "linked", "anchors": "suggested"}
    p = project.open_project(item["project_root"])
    assert not p.read_only and p.project_id == item["project_id"]
    assert p.data["broadcast"]["video_no"] == VNO_A and p.data["broadcast"]["title"] == "합성 방송"
    assert p.data["media"]["media_rev"] == 1 and p.data["media"]["current"]["probe"]["duration_ms"] == 2_100_000
    cal = p.data["calibration"]
    assert cal["active_rev"] == 0 and len(cal["revs"]) == 1 and cal["revs"][0]["offset_ms"] == 0   # 보정은 쓰지 않음
    with open(os.path.join(pdir, "anchors", f"{VNO_A}.txt"), encoding="utf-8") as f:
        assert f.read() == "합성 후보 표 (본문 없음)\n"
    assert "--file-start-ms 4800000" in out and "제안만" in out


def test_rerun_continues_only_remaining_steps(tmp_path, monkeypatch, fake_probe):
    v, proots, pdir = _pilot_dirs(tmp_path)
    first = FakeTools(fetch_code=3)                       # 끝까지 받지 못함
    monkeypatch.setattr(ps, "run_tool", first)
    code, out, _ = _run(ps, ["--videos", v, "--projects-root", proots, "--pilot-dir", pdir])
    assert code == 2
    assert _state(pdir)["items"][0]["steps"]["chat"] == "failed:3"
    assert first.names() == ["new_project", "fetch_chat"]  # 채팅이 없으니 앵커 후보는 건너뜀

    second = FakeTools()
    monkeypatch.setattr(ps, "run_tool", second)
    code, out, _ = _run(ps, ["--videos", v, "--projects-root", proots, "--pilot-dir", pdir])
    assert code == 0, out
    assert second.names() == ["fetch_chat", "find_anchors"]   # 프로젝트는 다시 만들지 않음
    assert len(os.listdir(proots)) == 1

    third = FakeTools()
    monkeypatch.setattr(ps, "run_tool", third)
    code, _, _ = _run(ps, ["--videos", v, "--projects-root", proots, "--pilot-dir", pdir])
    assert code == 0 and third.names() == ["find_anchors"]   # 채팅·영상은 이미 됨


def test_existing_project_not_made_by_tool_is_untouched(tmp_path, monkeypatch, fake_probe):
    v, proots, pdir = _pilot_dirs(tmp_path)
    other = project.create(proots, "손으로 만든 합성 프로젝트", VNO_A, machine4="hand")
    with open(other.paths.project_json, "rb") as f:
        before = f.read()
    names_before = sorted(os.listdir(other.root))
    fake = FakeTools()
    monkeypatch.setattr(ps, "run_tool", fake)
    code, out, _ = _run(ps, ["--videos", v, "--projects-root", proots, "--pilot-dir", pdir])
    assert code == 0 and "건너뜀" in out
    assert fake.calls == []
    with open(other.paths.project_json, "rb") as f:
        assert f.read() == before
    assert sorted(os.listdir(other.root)) == names_before
    assert not os.path.exists(os.path.join(pdir, ps.STATE_NAME))


def test_link_media_does_not_replace_other_file(tmp_path, fake_probe):
    p = project.create(str(tmp_path / "pr"), "합성", VNO_A, machine4="test")
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"\x01" * 2048)
    b.write_bytes(b"\x02" * 2048)
    assert ps.link_media(p.root, str(a), "L01") == "linked"
    assert ps.link_media(p.root, str(a), "L01") == "same"
    assert ps.link_media(p.root, str(b), "L01") == "other"
    d = project.open_project(p.root).data
    assert d["media"]["media_rev"] == 1 and d["media"]["history"] == []


# ================================================================================================ pilot_freeze

def _fake_repo(tmp_path, *, doc=b"# synthetic guide v0.1\n", values=None, cfg_doc_sha=None):
    repo = tmp_path / "repo"
    values = values or {"reaction": {"time_window_sec": 20}}
    files = {
        "schemas/label-event.schema.json": json.dumps({
            "$id": "https://example.invalid/label-event.schema.json", "$defs": {},
            "properties": {"kind": {"enum": ["segment", "reaction"]}}}).encode(),
        "docs/labeling-guide/v0.1.md": doc,
        "assets/guide-config.v0.1.json": json.dumps({
            "schema": "chatcoach/guide-config", "guide_version": "0.1",
            "guide_doc_sha256": cfg_doc_sha or hashlib.sha256(doc).hexdigest(), "values": values}).encode(),
        "tests/fixtures/frozen_labels_v0_1.jsonl": b'{"v":1}\n',
        "VERSION": b"0.1.0\n",
    }
    for rel, data in files.items():
        _touch(str(repo / os.path.dirname(rel)), os.path.basename(rel), data)
    return str(repo), values


def test_freeze_writes_once_and_refuses_second(tmp_path, monkeypatch):
    repo, values = _fake_repo(tmp_path)
    monkeypatch.setattr(pf, "git_state", lambda r: ("1" * 40, []))
    out = str(tmp_path / "데이터" / "pilot" / "freeze.json")
    code, so, _ = _run(pf, ["--repo", repo, "--out", out])
    assert code == 0, so
    with open(out, "rb") as f:
        raw = f.read()
    d = json.loads(raw)
    assert d["schema"] == pf.FREEZE_SCHEMA and d["app_version"] == "0.1.0" and d["guide_version"] == "0.1"
    assert d["guide"]["doc_sha256"] == hashlib.sha256(b"# synthetic guide v0.1\n").hexdigest()
    assert d["guide"]["thresholds_sha256"] == hashlib.sha256(canonical_json(values)).hexdigest()
    assert d["label_event"]["kinds"] == ["reaction", "segment"] and d["label_event"]["record_v"] == store.RECORD_V
    assert set(d["files"]) == set(pf.TARGETS) and d["git_head"] == "1" * 40 and d["dirty"] == []

    code, _, err = _run(pf, ["--repo", repo, "--out", out])
    assert code == 1 and "이미" in err
    with open(out, "rb") as f:
        assert f.read() == raw


def test_freeze_check_detects_changes(tmp_path, monkeypatch):
    repo, _ = _fake_repo(tmp_path)
    monkeypatch.setattr(pf, "git_state", lambda r: (None, None))
    out = str(tmp_path / "pilot" / "freeze.json")
    assert _run(pf, ["--repo", repo, "--out", out])[0] == 0
    assert _run(pf, ["--repo", repo, "--out", out, "--check"])[0] == 0
    with open(os.path.join(repo, "VERSION"), "wb") as f:
        f.write(b"0.1.1\n")
    code, so, _ = _run(pf, ["--repo", repo, "--out", out, "--check"])
    assert code == 0 and "참고: app_version" in so          # 앱 판만 바뀐 것은 허용
    with open(os.path.join(repo, "schemas", "label-event.schema.json"), "ab") as f:
        f.write(b"\n")
    code, so, _ = _run(pf, ["--repo", repo, "--out", out, "--check"])
    assert code == 1 and "files.label_event_schema" in so
    assert _run(pf, ["--repo", repo, "--out", str(tmp_path / "없음.json"), "--check"])[0] == 2


def test_freeze_refusals(tmp_path, monkeypatch):
    repo, _ = _fake_repo(tmp_path)
    monkeypatch.setattr(pf, "git_state", lambda r: ("2" * 40, ["guide_doc"]))
    out = str(tmp_path / "pilot" / "freeze.json")
    code, _, err = _run(pf, ["--repo", repo, "--out", out])
    assert code == 1 and "커밋" in err and not os.path.exists(out)
    code, _, _ = _run(pf, ["--repo", repo, "--out", out, "--dry-run", "--allow-dirty"])
    assert code == 0 and not os.path.exists(out)
    code, _, _ = _run(pf, ["--repo", repo, "--out", out, "--allow-dirty"])
    assert code == 0
    with open(out, "rb") as f:
        assert json.loads(f.read())["dirty"] == ["guide_doc"]
    # 저장소 안에는 쓰지 않는다
    assert _run(pf, ["--repo", repo, "--out", os.path.join(repo, "freeze.json")])[0] == 2
    # 기준서와 시작값이 짝이 아니면 거부
    repo2, _ = _fake_repo(tmp_path / "둘", cfg_doc_sha="c" * 64)
    code, _, err = _run(pf, ["--repo", repo2, "--out", str(tmp_path / "둘" / "f.json")])
    assert code == 2 and "짝" in err


def test_freeze_values_match_what_projects_record():
    """실제 저장소 파일로 계산한 값이 project.create 가 project.json 에 적는 해시와 같다(읽기만)."""
    cur = pf.collect(ROOT)
    g, _ = project.default_guide()
    assert cur["guide"]["doc_sha256"] == hashlib.sha256(g.doc_bytes).hexdigest()
    assert cur["guide"]["thresholds_sha256"] == hashlib.sha256(canonical_json(g.thresholds)).hexdigest()
    assert cur["label_event"]["record_v"] == store.RECORD_V
    assert {"segment", "reaction", "session.open", "retract"} <= set(cur["label_event"]["kinds"])
    assert cur["files"]["label_golden"]["path"] == "tests/fixtures/frozen_labels_v0_1.jsonl"
