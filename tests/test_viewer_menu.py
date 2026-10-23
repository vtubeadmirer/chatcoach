"""메인 창 '훈련 → 시청자 훈련' 통합 (08 D-20 2·3·4·5, 21 6-1~6-3) — app/ui/mainwindow.py · app/viewer_flow.py ·
app/core/words_doc.py.

합성 데이터만 쓴다: 합성 최종 세트 프로젝트(tests.test_final_set.proj — 채팅 6개, 본문 '합성 본문')와 합성 asr.json.
예문·이모티콘 코드도 합성이다({:zz_test_NN:}). 실제 채팅·닉네임·전사문·시범 프로젝트 폴더는 쓰지 않는다.
시범 이름 사본은 tmp_path 안에 만들고 shutil.rmtree 로 지운다.
사람 클릭 수(D-16): 메뉴 '훈련' 1 + '시청자 훈련' 1 = 2, 채팅 쓰기는 Enter(0), 닫기 1. 입력 칸은 쓰기 칸 1개.
"""
import builtins
import json
import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from app import exposure_io  # noqa: E402
from app import training_store as ts  # noqa: E402
from app import viewer_flow as vf  # noqa: E402
from app import viewer_store as vs  # noqa: E402
from app.core import exposure as ex  # noqa: E402
from app.core import textnorm as TN  # noqa: E402
from app.core import words_doc as WD  # noqa: E402
from tests.test_final_set import _clear_locks, cid, proj  # noqa: E402,F401
from tests.test_integration_c4 import PARTS, ctx, qapp  # noqa: E402,F401
from tests.test_training_menu import P2, _open, _slow_poll, win  # noqa: E402,F401

Q_TOPIC = "보스 공략은 어떻게 하는 거예요?"        # 합성: 전사 낱말(보스·공략)과 겹치는 열린 질문
Q_EMOTE = "{:zz_test_01:}{:zz_test_01:}"            # 합성: 이모티콘만
Q_COPY = "합성 본문"                                 # 합성 프로젝트의 앞 채팅 베끼기


def _write_asr(root, segs=((100_000, 104_000, "보스 공략을 시작"),)):
    """합성 asr.json (영상 파일 시각). 합성 프로젝트의 보정은 오프셋 0 이라 VOD 시각과 같다."""
    os.makedirs(os.path.join(root, "derived"), exist_ok=True)
    doc = {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "synthetic"}, "settings": {},
           "audio": {"duration_ms": 600_000},
           "segments": [{"start_ms": a, "end_ms": b, "text": t} for a, b, t in segs]}
    with open(vf.asr_path(root), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _build_words_inprocess(cmd, cancelled):
    """시험용 words_runner: 엔진 도구를 프로세스로 띄우는 대신 같은 함수를 이 자리에서 부른다(결과 파일은 같다)."""
    from engine import transcript_words as TW
    root = cmd[cmd.index("--project") + 1] if cmd else None
    doc = TW.from_project(root)
    TW.write_json(doc, TW.default_out_path(root))
    return {"windows": doc["stats"]["windows"], "words": doc["stats"]["words"], "rc": 0, "elapsed_ms": 1}


def _kinds(root):
    return [r["kind"] for s in vs.list_sessions(root) for r in vs.read_valid(vs.session_path(root, s))[0]]


# ----------------------------------------------------------------------------- 순수: 구간 고르기
def test_choose_segment_order():
    assert vf.choose_segment(scenario_range=(5, 9), reference_range=(1, 2), first_chat_ms=0) == ((5, 9), "scenario")
    assert vf.choose_segment(scenario_range=None, reference_range=(1, 2), first_chat_ms=0) == ((1, 2), "reference")
    assert vf.choose_segment(first_chat_ms=1_000, duration_ms=None) == ((1_000, 601_000), "first_chat")
    assert vf.choose_segment(first_chat_ms=1_000, duration_ms=5_000) == ((1_000, 5_000), "first_chat")
    assert vf.choose_segment(scenario_range=(9, 5), reference_range=(True, 3)) == (None, None)


# ----------------------------------------------------------------------------- 전사 낱말 파일 상태
def test_words_status_codes(tmp_path, proj):  # noqa: F811
    from app import project as pm
    root = proj["root"]
    pdata = pm.open_project(root).data
    assert vf.words_status(root, pdata) == (vf.W_NO_ASR, None)
    _write_asr(root)
    assert vf.words_status(root, pdata)[0] == vf.W_MISSING
    _build_words_inprocess(["--project", root], None)
    code, doc = vf.words_status(root, pdata)
    assert code == vf.W_OK and WD.validate(doc) == []
    win_ = WD.score_windows(doc)
    assert win_ and win_[0]["start_ms"] == 100_000 and "보스" in win_[0]["words"]
    # 보정이 확인 전(unverified)이면 쓰지 않는다 — 판·오프셋이 같아도(검토 2026-10-23, 03 4-2 와 같은 기준)
    import copy
    unv = copy.deepcopy(pdata)
    for r in unv["calibration"]["revs"]:
        r["status"] = "unverified"
    assert vf.words_status(root, unv) == (vf.W_UNVERIFIED_CAL, None)
    assert vf.W_UNVERIFIED_CAL not in vf.REBUILD and vf.WORDS_NOTE_KO[vf.W_UNVERIFIED_CAL]
    ctx_, info_ = vf.build_context(root, unv)
    assert ctx_["transcript"] == [] and info_["words"] == vf.W_UNVERIFIED_CAL
    p = vf.words_path(root)
    raw = open(p, "rb").read()
    d = json.loads(raw)
    d["calibration"]["status"] = "unverified"     # 만든 뒤 같은 판·오프셋이 확인됐으면 지금 project.json 의 상태로 본다
    open(p, "w", encoding="utf-8").write(json.dumps(d))
    assert vf.words_status(root, pdata)[0] == vf.W_OK
    open(p, "wb").write(raw)
    # textnorm 판이 다르면 오래됨
    d = json.loads(raw)
    d["engine"]["textnorm"] = "textnorm-v0"
    open(p, "w", encoding="utf-8").write(json.dumps(d))
    assert vf.words_status(root, pdata)[0] == vf.W_STALE_TEXTNORM
    # 보정 판이 다르면 오래됨, 보정 없이 만든 파일은 보정이 생긴 뒤 오래됨
    d = json.loads(raw)
    d["calibration"]["rev"] = 99
    open(p, "w", encoding="utf-8").write(json.dumps(d))
    assert vf.words_status(root, pdata)[0] == vf.W_STALE_CAL
    d = json.loads(raw)
    d["calibration"]["source"] = "default"
    open(p, "w", encoding="utf-8").write(json.dumps(d))
    assert vf.words_status(root, pdata)[0] == vf.W_STALE_CAL
    assert vf.words_status(root, {})[0] == vf.W_DEFAULT_CAL            # 프로젝트에 보정이 없으면 쓰지 않음
    # 깨진 파일
    open(p, "w", encoding="utf-8").write("{\"schema\": 1}")
    assert vf.words_status(root, pdata)[0] == vf.W_BAD
    # asr 이 바뀌면 오래됨
    open(p, "wb").write(raw)
    assert vf.words_status(root, pdata)[0] == vf.W_OK
    _write_asr(root, segs=((100_000, 104_000, "다른 합성 문장"),))
    assert vf.words_status(root, pdata)[0] == vf.W_STALE_ASR
    assert set(vf.REBUILD) >= {vf.W_MISSING, vf.W_STALE_ASR, vf.W_STALE_CAL, vf.W_STALE_TEXTNORM, vf.W_BAD}
    assert doc["engine"]["textnorm"] == TN.VERSION


def _derived_watch(monkeypatch, roots, *, also=()):
    """roots 아래 derived\\ (그리고 also 의 경로) 를 여는·목록 읽는·있는지 보는 호출을 센다."""
    seen = []
    needles = [os.path.normcase(os.path.join(os.path.abspath(r), "derived")) for r in roots]
    needles += [os.path.normcase(os.path.abspath(a)) for a in also]
    real = {"open": builtins.open, "isfile": os.path.isfile, "exists": os.path.exists, "isdir": os.path.isdir,
            "listdir": os.listdir, "scandir": os.scandir, "os_open": os.open}

    def watch(name):
        fn = real[name]

        def inner(p, *a, **k):
            try:
                s = os.path.normcase(os.path.abspath(os.fspath(p)))
            except TypeError:
                s = ""
            if any(s == n or s.startswith(n + os.sep) for n in needles):
                seen.append((name, s))
            return fn(p, *a, **k)
        return inner
    monkeypatch.setattr(builtins, "open", watch("open"))
    monkeypatch.setattr(os.path, "isfile", watch("isfile"))
    monkeypatch.setattr(os.path, "exists", watch("exists"))
    monkeypatch.setattr(os.path, "isdir", watch("isdir"))
    monkeypatch.setattr(os, "listdir", watch("listdir"))
    monkeypatch.setattr(os, "scandir", watch("scandir"))
    monkeypatch.setattr(os, "open", watch("os_open"))
    return seen


def test_pilot_name_opens_no_derived(tmp_path, monkeypatch):
    """시범 이름(pilot-*) 폴더는 derived\\ 를 열지도 보지도 않는다(23 5-2 의 3). project.json 이 없어 봉인 기록을 못 읽으면
    W_PILOT — 전사 낱말·주제 흐름·벡터 모두 없음. 엔진 명령도 만들지 않는다."""
    pilot = str(tmp_path / "projects" / "pilot-합성")
    os.makedirs(os.path.join(pilot, "derived"))
    seen = _derived_watch(monkeypatch, [pilot])
    assert vf.words_status(pilot) == (vf.W_PILOT, None)
    assert vf.pilot_words(pilot) == (vf.W_PILOT, None, None)
    assert vf.topic_blocks(pilot) == ([], None)
    assert vf.words_command(pilot) is None
    assert vf.read_topic_vectors(pilot, None) == (vf.F_NO_WORDS, None)
    assert vf.derived_topic_blocks(pilot) == (vf.F_PILOT, [])
    ctx_, info = vf.build_context(pilot)
    assert ctx_["transcript"] == [] and ctx_["topic_blocks"] == [] and info["words"] == vf.W_PILOT
    assert info["pilot"] == 1 and info["sealed_count"] == 0 and info["topic_derived"] == vf.F_PILOT
    assert seen == []
    assert vf.W_SEALED not in vf.REBUILD and vf.W_RUN_STALE not in vf.REBUILD and vf.W_RUN_NO_WORDS not in vf.REBUILD
    assert all(vf.WORDS_NOTE_KO[c] for c in (vf.W_SEALED, vf.W_RUN_STALE, vf.W_RUN_NO_WORDS))


# ----------------------------------------------------------------------------- 23 5-2 의 3: 시범은 해제된 run 폴더에서
SEALED_AT = "2026-10-23T09:00:00+09:00"


def _vec_doc(words_doc, windows=((100_000, 110_000, (1.0, 0.0, 0.0)),)):
    """합성 창별 주제 벡터(round4, 3차원) — 전사 낱말 문서와 asr 해시·보정이 같다."""
    from app.core import viewer_score as vsc
    return {"schema": vsc.TOPIC_VECTORS_SCHEMA, "schema_version": vsc.TOPIC_VECTORS_SCHEMA_VERSION,
            "time_axis": "vod_ms", "encoding": "round4", "dim": 3,
            "target": dict(words_doc["target"]), "calibration": dict(words_doc["calibration"]),
            "windows": [{"start_ms": a, "end_ms": b, "v": list(v)} for a, b, v in windows]}


def _synthetic_pilot(proj, tmp_path):
    """합성 시범 프로젝트(tmp 아래 'pilot-합성'): 합성 프로젝트에 합성 asr 로 전사 낱말을 만든 뒤 복사한다(엔진은 시범 이름을
    거부하므로). 복사본의 derived\\ 에도 전사 낱말이 남아 있다 — 시범 길이 그것을 읽지 않는지 본다. → (경로, 전사 낱말 바이트)."""
    root = proj["root"]
    _write_asr(root)
    _build_words_inprocess(["--project", root], None)
    wbytes = open(vf.words_path(root), "rb").read()
    dst = str(tmp_path / "data" / "projects" / "pilot-합성")
    shutil.copytree(root, dst)
    return dst, wbytes


def _seal_pilot(dst, wbytes, monkeypatch):
    """시범 봉인: 합성 엔진 결과 + 짝 전사 낱말·주제 흐름(seal 담당 공개 API). → 판정 봉인 SealEntry."""
    from datetime import datetime
    from app import seal
    from tests.test_ai_compare import _engine_bytes
    from tests.test_topics_integration import tbytes
    at = datetime.fromisoformat(SEALED_AT)
    e = seal.seal_bytes(dst, _engine_bytes(), source="file", by="L01", now=at)
    assert seal.seal_words_bytes(dst, wbytes, pair_sha256=e.sha256, by="L01", now=at).check == "valid"
    assert seal.seal_topics_bytes(dst, tbytes(), pair_sha256=e.sha256, by="L01", now=at).check == "valid"
    return e


def test_pilot_reads_words_vectors_topics_from_unsealed_run(proj, tmp_path, monkeypatch):  # noqa: F811
    """해제 전: '봉인됨 1건' 만(run 파일·derived\\ 를 열지 않음). 해제 뒤: 전사 낱말·벡터·주제 흐름을 해제된 run 폴더에서만
    읽는다. 보정이 바뀌면 W_RUN_STALE(다시 만들지 않음). 합성 시범 프로젝트(tmp)만 쓴다."""
    from app import project as pm
    from app import seal
    dst, wbytes = _synthetic_pilot(proj, tmp_path)
    try:
        e = _seal_pilot(dst, wbytes, monkeypatch)
        pdata = pm.open_project(dst).data
        pid = pdata["project_id"]
        run_dirs = [os.path.join(seal.default_ai_root(dst), pid, d) for d in ("sealed", "runs", "words", "vectors",
                                                                             "topics")]
        vec_calls = []

        def fake_read_vectors(root, run_id, *, ai_root=None, reader_kind=None):
            """seal.read_vectors 자리(벡터 짝은 seal 담당 몫 — 여기서는 '해제된 run 에서만 부른다' 만 본다)."""
            vec_calls.append(run_id)
            u = [x for x in seal.unseal_events(root)[0] if x["run_id"] == run_id]
            assert u, "해제 전 run 의 벡터를 읽으려 했다"
            return _vec_doc(json.loads(wbytes))
        monkeypatch.setattr(seal, "read_vectors", fake_read_vectors, raising=False)

        # 1) 해제 전: 봉인됨 1건, run·derived 0회
        seen = _derived_watch(monkeypatch, [dst], also=run_dirs)
        assert vf.unsealed_runs(dst) == ([], 1)
        assert vf.words_status(dst, pdata) == (vf.W_SEALED, None)
        ctx_, info = vf.build_context(dst, pdata)
        assert ctx_["transcript"] == [] and ctx_["topic_blocks"] == [] and ctx_["topic_vectors"] is None
        assert (info["words"], info["sealed_count"], info["topic_run"], info["vectors"]) == (vf.W_SEALED, 1, 0,
                                                                                            vf.F_NO_WORDS)
        assert seen == [] and vec_calls == []
        monkeypatch.undo()
        monkeypatch.setattr(seal, "read_vectors", fake_read_vectors, raising=False)

        # 2) 해제(합성 리허설: 계획 없이 사유를 적어 연다 — 사용자 클릭 0, Claude 가 도구로)
        monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
        res = seal.unseal(dst, e.sha256, by="L01", early_reason="합성 리허설: 시범 해제 길")
        assert res.record.get("words") and res.record.get("topics")

        # 3) 해제 뒤: run 폴더에서만, derived\ 는 0회
        seen = _derived_watch(monkeypatch, [dst])
        runs, n = vf.unsealed_runs(dst)
        assert runs == [res.run_id] and n == 0
        code, doc, rid = vf.pilot_words(dst, pdata)
        assert (code, rid) == (vf.W_OK, res.run_id) and WD.validate(doc) == []
        assert vf.words_status(dst, pdata)[0] == vf.W_OK
        ctx_, info = vf.build_context(dst, pdata)
        assert info["words"] == vf.W_OK and info["transcript_windows"] >= 1 and ctx_["transcript"]
        assert info["topic_run"] == 1 and info["topic_source"] == "run" and len(ctx_["topic_blocks"]) == 2
        assert info["vectors"] == vf.F_OK and info["vector_windows"] == 1 and vec_calls == [res.run_id]
        assert info["sealed_count"] == 0 and info["pilot"] == 1
        assert seen == []
        blob = json.dumps(info, ensure_ascii=False)
        assert "보스" not in blob and "공략" not in blob                    # info 에는 숫자·코드만

        # 4) 보정이 바뀌면 다시 만들 수 없으므로 W_RUN_STALE, 확인 전이면 W_UNVERIFIED_CAL
        import copy
        moved = copy.deepcopy(pdata)
        for r in moved["calibration"]["revs"]:
            r["offset_ms"] = r.get("offset_ms", 0) + 1000
        assert vf.words_status(dst, moved) == (vf.W_RUN_STALE, None)       # 오프셋이 다르면 오래됨
        bumped = copy.deepcopy(pdata)
        bumped["calibration"]["active_rev"] = 99                            # 활성 판을 못 찾음 → 오래됨
        assert vf.words_status(dst, bumped) == (vf.W_RUN_STALE, None)
        unv = copy.deepcopy(pdata)
        for r in unv["calibration"]["revs"]:
            r["status"] = "unverified"
        assert vf.words_status(dst, unv) == (vf.W_UNVERIFIED_CAL, None)
        assert vf.words_command(dst) is None                                # 시범은 엔진 명령도 만들지 않는다
        assert not seen
    finally:
        shutil.rmtree(dst)


def test_menu_pilot_after_unseal_uses_run_words(win, proj, tmp_path, monkeypatch):  # noqa: F811
    """메인 창: 시범 세션이 모두 잠기고 해제된 뒤 '훈련 → 시청자 훈련'(메뉴 2클릭)이 해제된 run 의 전사 낱말·주제 흐름으로
    채점 맥락을 만든다. 엔진(전사 낱말 만들기)은 띄우지 않고 derived\\asr* 는 열지 않는다. 사용자 클릭: 메뉴 2 + 닫기 1."""
    from app import reference_set as rsm
    from app import seal
    dst, wbytes = _synthetic_pilot(proj, tmp_path)
    try:
        e = _seal_pilot(dst, wbytes, monkeypatch)
        monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
        seal.unseal(dst, e.sha256, by="L01", early_reason="합성 리허설: 시범 해제 길")
        monkeypatch.setattr(rsm, "pilot_sessions_locked", lambda root: (True, []))
        built = []
        win.words_runner = lambda c, cancelled: built.append(1) or {}
        _open(win, dst, None)
        assert win.a_viewer_training.isEnabled(), win.a_viewer_reason.text()
        seen = _derived_watch(monkeypatch, [dst])
        win.a_viewer_training.trigger()                                     # 메뉴 한 번
        view = win.viewer_view
        assert view is not None and built == []
        assert view._context["transcript"] and len(view._context["topic_blocks"]) == 2
        assert not [s for s in seen if os.sep + "asr" in s[1]], seen
        win.close_viewer_training()
        win.show_home()
    finally:
        shutil.rmtree(dst)


def test_words_note_sealed_shows_count():
    """상태 줄 안내: 시범 봉인 중이면 '봉인됨 n건'(D-18 3). 건수를 모르면 고정 글, 안내 없는 코드는 None."""
    assert "봉인됨 2건" in vf.words_note(vf.W_SEALED, 2)
    assert vf.words_note(vf.W_SEALED, None) == vf.WORDS_NOTE_KO[vf.W_SEALED]
    assert vf.words_note(vf.W_SEALED, 0) == vf.WORDS_NOTE_KO[vf.W_SEALED]
    assert vf.words_note(vf.W_UNVERIFIED_CAL, 3) == vf.WORDS_NOTE_KO[vf.W_UNVERIFIED_CAL]
    assert vf.words_note(vf.W_OK, 1) is None and vf.words_note(None) is None


def test_pilot_run_order_prefers_engine_version(proj, tmp_path, monkeypatch):  # noqa: F811
    """해제된 run 이 둘이면 해제 기록의 엔진 판이 높은 run 의 짝을 먼저 본다(analysis_flow.run_order). 판이 없으면 늦게 푼 것."""
    from types import SimpleNamespace
    from app import analysis_flow as af
    from app import seal
    entries = [SimpleNamespace(run_id="run-a", unsealed_at="2026-10-30T09:00", engine_version="link-v0.3"),
               SimpleNamespace(run_id="run-b", unsealed_at="2026-10-30T10:00", engine_version="0.1.0")]
    st = SimpleNamespace(entries=entries, sealed_count=0)
    monkeypatch.setattr(seal, "seal_status", lambda root, **k: st)
    asked = []

    def fake_words(root, rid, **k):
        asked.append(rid)
        return None
    monkeypatch.setattr(seal, "read_words", fake_words)
    pilot = str(tmp_path / "projects" / "pilot-합성")
    assert vf.unsealed_runs(pilot) == (["run-a", "run-b"], 0)
    assert vf.pilot_words(pilot, {}) == (vf.W_RUN_NO_WORDS, None, None) and asked == ["run-a", "run-b"]
    entries[0].engine_version = None                                       # 판을 모르면 늦게 푼 것 먼저
    assert af.run_order(st) == ["run-b", "run-a"]
    for e in entries:
        e.engine_version = None
    assert af.run_order(st) == ["run-b", "run-a"]


def test_words_command_runs_engine(tmp_path, proj):  # noqa: F811
    """열 때 워커가 띄우는 명령(엔진 venv 의 python -I -c … transcript-words)이 실제로 파일을 만든다(합성 asr)."""
    if not vf.tool_available():
        pytest.skip("엔진 파일 없음(묶은 exe)")
    root = proj["root"]
    _write_asr(root)
    cmd = vf.words_command(root)
    assert cmd[1:3] == ["-I", "-c"] and cmd[-2:] == ["--project", os.path.abspath(root)]
    res = vf.run_words(cmd)
    assert res.get("rc") == 0 and res.get("windows", 0) >= 1, res.get("error")
    assert "보스" not in json.dumps(res, ensure_ascii=False)          # 표준 출력에는 숫자·코드만
    from app import project as pm
    assert vf.words_status(root, pm.open_project(root).data)[0] == vf.W_OK


# ----------------------------------------------------------------------------- 메인 창
def test_menu_opens_viewer_writes_and_closes(win, proj):  # noqa: F811
    from app.ui.viewer_train_view import ViewerTrainView, input_fields
    root = proj["root"]
    _write_asr(root)
    built = []
    win.words_runner = lambda c, cancelled: built.append(1) or _build_words_inprocess(c, cancelled)
    assert "시청자 훈련(&W)" in [a.text() for a in win.m_training.actions() if a.isVisible()]
    win.update_training_actions()                                       # 프로젝트 없음
    assert not win.a_viewer_training.isEnabled() and win.a_viewer_reason.isVisible()

    wb = _open(win, root, None, media=False)                            # 보기 전용, 영상 없음 → 꺼짐
    assert not win.a_viewer_training.isEnabled() and "영상" in win.a_viewer_reason.text()
    wb.media_path = os.path.join(root, "합성영상.mp4")
    win.update_training_actions()
    assert win.a_viewer_training.isEnabled() and not win.a_viewer_reason.isVisible()

    win.a_viewer_training.trigger()                                     # 메뉴 한 번 = 열림
    view = win.viewer_view
    assert isinstance(view, ViewerTrainView) and win.stack.currentWidget() is view and view.state() == "training"
    assert built == [1] and os.path.isfile(vf.words_path(root))         # 전사 낱말을 열 때 한 번 만들었다
    assert not view.is_muted()
    assert len(input_fields(view)) == 1                                 # 쓰기 칸 하나(D-19 5)
    assert _kinds(root) == [vs.K_START]
    win.update_training_actions()                                       # 열려 있는 동안은 두 훈련 다 꺼짐
    assert not win.a_viewer_training.isEnabled() and not win.a_streamer_training.isEnabled()
    assert win.agreement_block_reason() and not win.m_analysis.isEnabled()

    seg = view._segment
    t = max(seg[0], 125_000)
    view.seek_vod(t)
    r1 = view.write(Q_TOPIC)
    r2 = view.write(Q_EMOTE)
    r3 = view.write(Q_COPY)
    assert (r1["levels"]["topic"], r1["levels"]["expand"], r1["levels"]["repeat"]) == ("good", "good", "good")
    assert (r2["levels"]["topic"], r2["levels"]["expand"]) == ("weak", "weak")
    assert r3["levels"]["repeat"] == "weak" and r3["codes"]["repeat"] == "repeat.copy_same"
    assert view.write_count() == 3
    assert view.show_summary()
    s = view.summary()
    assert s["writes"] == 3 and s["levels"]["topic"]["good"] == 1
    view.btn_close.click()                                              # 닫기 1번 → 작업대로
    assert win.viewer_view is None and win.stack.currentWidget() is wb
    assert _kinds(root) == [vs.K_START, vs.K_WRITE, vs.K_WRITE, vs.K_WRITE, vs.K_END]
    recs, summ = vs.read_valid(vs.session_path(root, vs.list_sessions(root)[0]))
    assert summ.get("invalid", 0) == 0
    from app.core import viewer_score as vsc
    assert recs[1]["body"]["scorer"] == f"viewer_score/{vsc.VERSION}"
    # 노출로 세지 않는다(training\ 에 아무것도 없고, 그 뒤 라벨링 세션도 대표값에 남는다)
    assert ts.list_sessions(root) == []
    later = ex.SessionInfo("L99-P-20991231-zz99", "primary", "2099-12-31T00:00:00+09:00")
    xv, _w = exposure_io.compute_for(root, [later], include_unseals=False)
    assert xv.exposed_any() == frozenset()
    # 보호 구역·백업 제외
    from app import backup
    sid = vs.list_sessions(root)[0]
    assert backup.in_protected_zone(f"viewer/{sid}.jsonl", backup.protected_patterns(wb.project.data))
    files = backup.select_files(root, include_chat=True, include_protected=False)
    assert not any("viewer" in str(getattr(f, "rel", f)).replace("\\", "/").split("/")[0] for f in files)
    # 다시 열면 이미 만든 전사 낱말은 다시 만들지 않는다
    win.update_training_actions()
    assert win.a_viewer_training.isEnabled()
    v2 = win.viewer_training()
    assert v2 is not None and built == [1]
    win.close_viewer_training()
    win.show_home()
    assert win.viewer_view is None


def test_viewer_without_asr_opens_without_words(win, proj):  # noqa: F811
    """asr.json 이 없으면 만들지 않고(워커 0) 연다 — 주제 적합성은 맥락 없음(ok)."""
    root = proj["root"]
    built = []
    win.words_runner = lambda c, cancelled: built.append(1) or {}
    _open(win, root, None)
    view = win.viewer_training()
    assert view is not None and built == []
    view.seek_vod(max(view._segment[0], 125_000))
    r = view.write(Q_TOPIC)
    assert r["codes"]["topic"] == "topic.no_context" and r["levels"]["topic"] == "ok"
    win.close_viewer_training()
    assert _kinds(root)[-1] == vs.K_END
    win.show_home()


def test_labeling_session_reopens_view_only_for_viewer(win, proj, monkeypatch):  # noqa: F811
    _slow_poll(monkeypatch)
    root = proj["root"]
    wb = _open(win, root, ctx(P2, "primary", "L01"))
    assert win.a_viewer_training.isEnabled() and "보기 전용" in win.a_viewer_training.text()
    monkeypatch.setattr(wb, "resolve_unsaved", lambda allow_cancel=True: True)
    win.a_viewer_training.trigger()
    assert win.viewer_view is None and vs.list_sessions(root) == []    # 라벨링 세션 위에서는 열지 않았다
    nwb = win.workbench
    assert nwb is not wb and nwb.session is None and win._pending_kind == "viewer"
    assert nwb.wait_jobs(15_000)
    nwb.media_path = os.path.join(root, "합성영상.mp4")
    view = win._try_pending_training()
    assert view is not None, win.statusBar().currentMessage()
    assert win.viewer_view is view and win.training_view is None
    assert win._pending_kind == "streamer" and _kinds(root) == [vs.K_START]
    assert ts.list_sessions(root) == []
    win.close_viewer_training()
    win.show_home()


def test_pilot_project_viewer_menu_off(win, proj, tmp_path, monkeypatch):  # noqa: F811
    """시범 프로젝트(잠기기 전)는 보기 전용에서도 라벨링 세션에서도 메뉴가 꺼진다(D-20 5). viewer\\ 를 만들지 않고,
    전사 낱말도 만들지 않는다. 라벨링 세션에서는 잠김 판정도 하지 않는다."""
    from app import reference_set as rsm
    dst = str(tmp_path / "data" / "projects" / "pilot-합성")
    shutil.copytree(proj["root"], dst)
    try:
        built, calls = [], []
        win.words_runner = lambda c, cancelled: built.append(1) or {}
        monkeypatch.setattr(rsm, "pilot_sessions_locked", lambda root: calls.append("locked") or (False, ["x"]))
        wb = _open(win, dst, ctx(P2, "primary", "L01"))
        assert not win.a_viewer_training.isEnabled() and "시범" in win.a_viewer_reason.text()
        assert win.viewer_training() is None and calls == [] and win.workbench is wb
        win.show_home()
        _open(win, dst, None)
        assert not win.a_viewer_training.isEnabled() and "시범" in win.a_viewer_reason.text()
        assert win.viewer_training() is None
        assert built == [] and not os.path.isdir(vs.viewer_dir(dst))
        assert not os.path.exists(vf.words_path(dst))
        # 시범 세션이 모두 잠긴 뒤에는 보기 전용에서 켜진다
        monkeypatch.setattr(rsm, "pilot_sessions_locked", lambda root: (True, []))
        win.update_training_actions()
        assert win.a_viewer_training.isEnabled()
        win.show_home()
    finally:
        shutil.rmtree(dst)


def test_spec_bundles_runtime_schemas():
    """묶은 exe 에서도 기록 검사가 되도록, 앱 모듈의 SCHEMA_REL 스키마가 모두 ChatCoach.spec datas 에 있다."""
    import importlib
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = open(os.path.join(root, "ChatCoach.spec"), encoding="utf-8").read()
    mods = ("store", "export", "locking", "ai_results", "scenario_store", "training_store", "viewer_store",
            "discern_store")
    for m in mods:
        rel = importlib.import_module(f"app.{m}").SCHEMA_REL
        assert os.path.isfile(os.path.join(root, *rel)), m
        assert f"('schemas\\\\{rel[-1]}', 'schemas')" in spec, m


# ----------------------------------------------------------------------------- 22 5-2 의 5: 문턱을 기준서에서 읽기
def _with_viewer_score(pdata, values):
    import copy
    d = copy.deepcopy(pdata)
    for g in d["guides"]:
        if g["guide_version"] == d["active_guide_version"]:
            g["thresholds"] = {**(g.get("thresholds") or {}), "viewer_score": values}
    return d


def test_score_settings_from_active_guide(proj):  # noqa: F811
    from app import project as pm
    from app.core import viewer_score as vsc
    pdata = pm.open_project(proj["root"]).data
    # 시범 중인 기준서 v0.1 에는 viewer_score 칸이 없다 → 기본값(D-14 7: 그 파일은 그대로)
    assert vf.score_settings(pdata) == (vsc.DEFAULT_SETTINGS, [], "default")
    assert vf.score_settings(None) == (vsc.DEFAULT_SETTINGS, [], "default")
    s, notes, src = vf.score_settings(_with_viewer_score(pdata, {"copy_window_sec": 60, "self_ok_pct": 30}))
    assert src == "guide" and notes == [] and s.copy_window_ms == 60_000 and s.self_ok_pct == 30
    s, notes, src = vf.score_settings(_with_viewer_score(pdata, {"copy_window_sec": 999, "ngram": "x"}))
    assert src == "guide" and s.copy_window_ms == vsc.DEFAULT_SETTINGS.copy_window_ms and s.ngram == vsc.DEFAULT_NGRAM
    assert len(notes) == 2 and all(n.startswith("viewer_score.") for n in notes)
    # 현재 기준서가 아닌 판의 값은 보지 않는다
    other = _with_viewer_score(pdata, {"copy_window_sec": 60})
    other["active_guide_version"] = "9.9"
    assert vf.score_settings(other)[2] == "default"
    # 기준서 맨 위의 같은 이름 칸(viewer_score 밖)은 채점 문턱으로 읽지 않는다
    import copy
    top = copy.deepcopy(pdata)
    for g in top["guides"]:
        g["thresholds"] = {**(g.get("thresholds") or {}), "copy_window_sec": 60}
    assert vf.score_settings(top) == (vsc.DEFAULT_SETTINGS, [], "default")


def test_build_context_settings_change_score(proj):  # noqa: F811
    """설정을 바꾸면 결과가 바뀐다(22 7-1 의 2): 55초 앞 채팅 베끼기는 기본(45초)이면 새 말, 기준서 60초면 같은 말."""
    from app import project as pm
    from app.core import viewer_score as vsc
    root = proj["root"]
    pdata = pm.open_project(root).data
    chats = [(100_000, "합성 베끼기 문장")]
    results = {}
    for name, pd in (("default", pdata), ("guide", _with_viewer_score(pdata, {"copy_window_sec": 60}))):
        ctx_, info = vf.build_context(root, pd)
        assert info["settings"] == name and info["settings_notes"] == 0
        sc = vsc.ScoreContext.build(ctx_["topic_blocks"], ctx_["transcript"], chats, settings=ctx_["settings"])
        results[name] = vsc.score_chat("합성 베끼기 문장", 155_000, sc, settings=ctx_["settings"]).repeat.code
    assert results == {"default": vsc.REPEAT_FRESH, "guide": vsc.REPEAT_COPY_SAME}


def test_viewer_view_gets_guide_settings(win, proj):  # noqa: F811
    """메인 창이 연 시청자 훈련 화면이 기준서 문턱을 받는다(build_context → start(context=…))."""
    root = proj["root"]
    win.words_runner = lambda c, cancelled: {}
    wb = _open(win, root, None)
    wb.project.data = _with_viewer_score(wb.project.data, {"copy_window_sec": 60})
    view = win.viewer_training()
    assert view is not None and view._context["settings"].copy_window_ms == 60_000
    win.close_viewer_training()
    win.show_home()


# ----------------------------------------------------------------------------- 22 5-2 의 8: 같은 asr 의 실패 기억
def test_words_failure_remembered_per_asr_hash(proj, monkeypatch):  # noqa: F811
    from app import project as pm
    from app import scenario_mix
    root = proj["root"]
    pdata = pm.open_project(root).data
    vf.forget_failure()
    try:
        _write_asr(root)
        assert vf.words_status(root, pdata)[0] == vf.W_MISSING
        cmd = ["python", "-I", "-c", "x", "repo", "transcript-words", "--project", os.path.abspath(root)]
        outs = iter([{"error": "cancelled", "rc": None}, {"error": "engine_failed", "rc": 4}])
        calls = []
        monkeypatch.setattr(scenario_mix, "run_make", lambda c, **k: calls.append(1) or next(outs))
        vf.run_words(cmd)                                               # 취소는 실패로 세지 않는다
        assert vf.words_status(root, pdata)[0] == vf.W_MISSING
        vf.run_words(cmd)                                               # 실패 → 이 asr 로는 다시 띄우지 않는다
        code, doc = vf.words_status(root, pdata)
        assert (code, doc) == (vf.W_BUILD_FAILED, None)
        assert vf.W_BUILD_FAILED not in vf.REBUILD and vf.WORDS_NOTE_KO[vf.W_BUILD_FAILED]
        _ctx, info = vf.build_context(root, pdata)
        assert info["words"] == vf.W_BUILD_FAILED and info["transcript_windows"] == 0
        _write_asr(root, segs=((100_000, 104_000, "다른 합성 문장"),))   # asr 이 바뀌면 다시 시도
        assert vf.words_status(root, pdata)[0] == vf.W_MISSING
        _write_asr(root)
        assert vf.words_status(root, pdata)[0] == vf.W_BUILD_FAILED
        # 성공하면 기억을 지운다
        monkeypatch.setattr(scenario_mix, "run_make", lambda c, **k: _build_words_inprocess(c, None))
        vf.run_words(cmd)
        assert vf.words_status(root, pdata)[0] == vf.W_OK and not vf._FAILED
        assert calls == [1, 1]
        # 판정 함수
        assert vf.words_failed(None) and vf.words_failed({"rc": 1}) and vf.words_failed({"error": "timeout"})
        assert not vf.words_failed({"rc": 0, "windows": 3}) and not vf.words_failed({"error": "cancelled"})
        # 시범 이름·asr 없음은 기억하지 않는다
        assert vf.remember_failure(str(proj["root"]) + "-없음") is False
        assert vf.remember_failure(os.path.join(os.path.dirname(root), "pilot-합성")) is False
    finally:
        vf.forget_failure()


def test_menu_does_not_rerun_failed_words(win, proj, monkeypatch):  # noqa: F811
    """메인 창: 전사 낱말 만들기가 실패하면 같은 실행에서 다시 열 때 엔진을 다시 띄우지 않는다(상태 줄 한 줄)."""
    from app import scenario_mix
    if vf.words_command(proj["root"]) is None:
        pytest.skip("엔진 파일 없음(묶은 exe)")
    root = proj["root"]
    _write_asr(root)
    vf.forget_failure()
    calls = []
    monkeypatch.setattr(scenario_mix, "run_make", lambda c, **k: calls.append(1) or {"error": "engine_failed", "rc": 4})
    win.words_runner = None                                            # 실제 길(vf.run_words)
    try:
        _open(win, root, None)
        v1 = win.viewer_training()
        assert v1 is not None and calls == [1]
        win.close_viewer_training()
        v2 = win.viewer_training()
        assert v2 is not None and calls == [1]                         # 두 번째는 띄우지 않는다
        assert win.statusBar().currentMessage() == vf.WORDS_NOTE_KO[vf.W_BUILD_FAILED]
        win.close_viewer_training()
        win.show_home()
    finally:
        vf.forget_failure()


# ----------------------------------------------------------------------------- 22 5-2 의 11: 상태 줄 문턱은 도구 출력의 fill
def test_make_virtual_status_uses_tool_fill(win, proj):  # noqa: F811
    from app import scenario_mix
    root = proj["root"]
    seen = []

    def runner(cmd, cancelled):
        seen.append(list(cmd))
        return {"scenario_id": "SC-20261023-zz01", "counts": {"retrieved": 0}, "written": False,
                "reason": "nothing_to_fill", "fill": {"quiet_max_real": 45, "target_per_min": 60}, "rc": 0}
    win.scenario_runner = runner
    _open(win, root, None)
    win.make_virtual_chat(extra_args=("--threshold", "test"))           # --quiet-max-real 인자가 없다
    assert seen and "--quiet-max-real" not in seen[0]
    assert win.statusBar().currentMessage() == scenario_mix.MSG_NOTHING.format(45)
    # 출력에 fill 이 없으면(옛 도구) 인자, 그것도 없으면 기본 2
    win.scenario_runner = lambda c, k: {"counts": {"retrieved": 0}, "reason": "nothing_to_fill", "rc": 0}
    win.make_virtual_chat(extra_args=("--quiet-max-real", "7"))
    assert win.statusBar().currentMessage() == scenario_mix.MSG_NOTHING.format(7)
    win.make_virtual_chat(extra_args=())
    assert win.statusBar().currentMessage() == scenario_mix.MSG_NOTHING.format(2)
    win.show_home()


def test_blocked_viewer_start_leaves_no_empty_file(win, proj, monkeypatch):  # noqa: F811
    from app.ui import viewer_train_view as vtv
    root = proj["root"]
    _open(win, root, None)

    def blocked(self, **_k):
        self.set_blocked("합성 막힘")
        return False
    monkeypatch.setattr(vtv.ViewerTrainView, "start", blocked)
    for _ in range(2):
        assert win.viewer_training() is None
        assert win.statusBar().currentMessage() == "합성 막힘"
    d = vs.viewer_dir(root)
    assert not os.path.isdir(d) or os.listdir(d) == []
    win.show_home()
