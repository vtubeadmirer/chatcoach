"""세션 선택 화면과 그 아래 계산 (08 D-09 1·4·5·6·8). 합성 데이터만 쓴다.

- app/session.py: 라벨러 명부(labelers.json), 프로젝트 훑기(survey_project), 열 수 있는 세션 종류(kind_choices),
  이어 하기(existing_sessions·resume_session), 노출 안내(exposure_notes), default_blind
- app/core/visibility.py: 분석 세션은 잠긴 1차만, analysis_exposure
- app/store.py + 스키마: analysis 세션·session.close 줄
- app/ui/session_dialog.py: 화면 동작과 블라인드(다른 세션·엔진 결과 내용이 화면에 나오지 않음)
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import project, session, store  # noqa: E402
from app.core import visibility as V  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
SALT = bytes(range(32))
MID = "aaaaaaaa-0000-4000-8000-000000000001"
SECRET = "합성비밀문장-zq9"          # 다른 세션·엔진 결과 안에만 넣는 표식. 화면 어디에도 나오면 안 된다
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeLock:
    held: set = set()

    def __init__(self, path):
        self.path, self.locked = path, False

    def setStaleLockTime(self, ms):
        pass

    def tryLock(self, timeout=0):
        if self.path in FakeLock.held:
            return False
        FakeLock.held.add(self.path)
        self.locked = True
        return True

    def unlock(self):
        if self.locked:
            FakeLock.held.discard(self.path)
            self.locked = False


@pytest.fixture(autouse=True)
def _locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


@pytest.fixture
def proj(tmp_path):
    g = project.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}})
    p = project.create(str(tmp_path / "한글 프로젝트"), "합성 방송", 123, "시험용 목적", "L01", "0.0.1", NOW, PID,
                       SALT, guide=g, machine4=session.machine4(MID))
    assert not p.read_only
    return p


@pytest.fixture
def data_root(tmp_path):
    d = tmp_path / "앱 data"
    d.mkdir()
    return str(d)


def touch(root, *rel, content=b"{}"):
    p = os.path.join(root, *rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content)
    return p


def write_other_session(root, sid="L02-P-20261020-zz11", m4="abcd"):
    """다른 라벨러의 세션 파일(내용에 SECRET). 화면은 이 파일을 열지 않아야 한다."""
    line = json.dumps({"kind": "segment", "body": {"rationale": SECRET}}, ensure_ascii=False)
    return touch(root, "labels", f"{sid}.{m4}.jsonl", content=(line + "\n").encode("utf-8"))


def open_session(proj, ctx):
    return store.ProjectStore.open(proj.root, ctx, FakeLock, app_version="0.0.1")


# ============================================================================ 세션 ID·종류 (D-09 4)

def test_analysis_kind_code_everywhere():
    assert session.KIND_CODES["analysis"] == "AN" == V.SESSION_CODES[V.ANALYSIS]
    assert set(session.KIND_CODES) == set(V.SESSION_KINDS)
    sid = session.new_session_id("L01", "analysis", "20261021", "ab12")
    assert sid == "L01-AN-20261021-ab12"
    assert session.parse_session_file_name(f"{sid}.m3fa.jsonl") == (sid, "m3fa")
    schema = json.load(open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8"))
    pat = schema["$defs"]["sessionId"]["pattern"]
    assert re.search(pat, sid) and "AN" in pat
    assert schema["$defs"]["sessionKind"]["enum"][-1] == "analysis"
    assert "session.close" in schema["properties"]["kind"]["enum"]
    # 세 정규식(core.ids, 스키마, session)이 같은 종류 코드를 안다
    for code in session.CODE_KINDS:
        s = f"L01-{code}-20261021-ab12"
        assert session.SESSION_ID_RE.match(s) and re.search(pat, s)
    assert not session.SESSION_ID_RE.match("L01-XX-20261021-ab12")


def test_default_blind_follows_visibility():
    assert session.default_blind("primary") == session.DEFAULT_BLIND
    b = session.default_blind("analysis")
    assert b == {"to_ai": True, "to_other_sessions": False, "to_origin": False, "nickname_masked_default": True}
    ctx = session.new_session("L01", "analysis", machine_id_value=MID, now=NOW, rand4="ab12")
    assert ctx.blind == b and ctx.session_id == "L01-AN-20261021-ab12"
    assert session.new_session("L01", "primary", machine_id_value=MID, now=NOW, rand4="ab12").blind is None


def test_resume_session_takes_kind_from_id():
    ctx = session.resume_session("L03-RS-20261001-k9q2", machine_id_value=MID)
    assert (ctx.labeler_id, ctx.session_kind, ctx.machine4) == ("L03", "relabel_same", session.machine4(MID))
    assert ctx.session_created_at is None and ctx.guide_version is None   # 메타는 store 가 첫 줄에서
    with pytest.raises(ValueError):
        session.resume_session("../x", machine_id_value=MID)


# ============================================================================ 라벨러 명부

def test_labelers_roster_add_and_reload(data_root):
    assert session.load_labelers(data_root).entries == []
    r = session.add_labeler(data_root, "L07", "  시청자" + chr(0x202E) + "7 " + chr(10), now=NOW)   # 제어·방향 문자 정리
    assert r.ids == ["L07"] and r.get("L07").display_name == "시청자7"
    session.add_labeler(data_root, "R2D2", None, now=NOW)
    r = session.add_labeler(data_root, "L07", "시청자8", now=NOW)        # 있으면 이름만 바꾼다
    assert r.ids == ["L07", "R2D2"] and r.get("L07").display_name == "시청자8"
    again = session.load_labelers(data_root)
    assert again.ids == ["L07", "R2D2"] and again.problem is None
    assert again.get("R2D2").label == "R2D2" and again.get("L07").label == "L07 (시청자8)"
    raw = open(os.path.join(data_root, "labelers.json"), "rb").read()
    assert raw.endswith(b"\n") and json.loads(raw)["v"] == 1


@pytest.mark.parametrize("bad", ["l07", "7L", "L", "L0123456789", "L-1", "..\\x", "", None])
def test_labelers_reject_bad_id(data_root, bad):
    with pytest.raises(ValueError):
        session.add_labeler(data_root, bad)
    assert not os.path.exists(os.path.join(data_root, "labelers.json"))


def test_labelers_corrupt_file_is_preserved(data_root):
    p = os.path.join(data_root, "labelers.json")
    with open(p, "wb") as f:
        f.write(b"{broken")
    r = session.load_labelers(data_root)
    assert r.entries == [] and r.problem
    session.add_labeler(data_root, "L01", now=NOW)
    assert session.load_labelers(data_root).ids == ["L01"]
    kept = [n for n in os.listdir(data_root) if n.startswith("labelers.json.corrupt-")]
    assert len(kept) == 1 and open(os.path.join(data_root, kept[0]), "rb").read() == b"{broken"


def test_labelers_skip_bad_entries(data_root):
    doc = {"v": 1, "labelers": [{"labeler_id": "L01"}, {"labeler_id": "bad"}, "x", {"labeler_id": "L01"}]}
    with open(os.path.join(data_root, "labelers.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    r = session.load_labelers(data_root)
    assert r.ids == ["L01"] and "2개" in r.problem


# ============================================================================ 열 수 있는 세션 종류 (D-09 1)

def _choices(proj, labeler="L01"):
    sv = session.survey_project(proj.root, proj.data)
    mine = session.existing_sessions(proj.root, labeler, sv, session.machine4(MID))
    return sv, mine, {c.kind: c for c in session.kind_choices(sv, labeler, mine)}


def test_fresh_project_only_primary(proj):
    sv, mine, ch = _choices(proj)
    assert sv.active_guide_version == "0.1" and sv.guide_sha8 and len(sv.guide_sha8) == 8
    assert mine == []
    assert ch["primary"].enabled
    for k in ("relabel_same", "relabel_second", "adjudication", "origin_review", "analysis"):
        assert not ch[k].enabled and ch[k].reason, k
    assert list(ch) == list(session.KIND_ORDER)


def test_choices_follow_project_state(proj):
    touch(proj.root, "derived", "link-합성.json")
    _, _, ch = _choices(proj)
    assert ch["analysis"].enabled                        # 엔진 결과가 있으면 분석 세션
    touch(proj.root, "locks", "L02-P-20261001-aa11.json")
    _, _, ch = _choices(proj)
    assert not ch["adjudication"].enabled and "1개" in ch["adjudication"].reason
    touch(proj.root, "locks", "L03-R2-20261001-bb22.json")
    touch(proj.root, "locks", "not-a-session.json")      # 이름 규칙 밖은 세지 않는다
    sv, _, ch = _choices(proj)
    assert ch["adjudication"].enabled and len(sv.locked_ids) == 2
    touch(proj.root, "origin", "gen-합성.jsonl")
    _, _, ch = _choices(proj)
    assert ch["origin_review"].enabled
    touch(proj.root, "relabel", "RP-20261021-ab12.json")
    _, _, ch = _choices(proj)
    # 빈 JSON 계획은 '읽을 수 있는 계획 없음 — 계획 형식이 아님' 으로 꺼진다. 옛 검사("v1" in reason)는
    # 형식 이름 'relabel-plan v1' 에 우연히 걸려 통과했다(13 보고 4-2 12번, D-12 2).
    reason = ch["relabel_same"].reason
    assert not ch["relabel_same"].enabled
    assert "읽을 수 있는 재라벨 계획이 없습니다" in reason and "계획 형식" in reason


def test_analysis_enabled_by_locked_primary_without_engine(proj):
    touch(proj.root, "locks", "L02-P-20261001-aa11.json")
    _, _, ch = _choices(proj)
    assert ch["analysis"].enabled


def test_project_roster_restricts_kinds(proj):
    touch(proj.root, "derived", "link-1.json")
    proj.data["labelers"] = [{"labeler_id": "L01", "role": "primary", "assigned_session_kinds": ["origin_review"],
                              "engine_author": False}]
    touch(proj.root, "origin", "x.jsonl")
    _, _, ch = _choices(proj)
    assert not ch["primary"].enabled and "명부" in ch["primary"].reason
    assert ch["origin_review"].enabled
    assert ch["analysis"].enabled                        # 분석 세션은 명부 종류 목록에 없어 막지 않는다
    _, _, ch2 = _choices(proj, "L09")                    # 명부에 없는 라벨러는 막지 않는다
    assert ch2["primary"].enabled


def test_existing_sessions_only_mine_and_resume(proj):
    write_other_session(proj.root)
    ctx = session.new_session("L01", "primary", machine_id_value=MID, now=NOW, rand4="ab12")
    st = open_session(proj, ctx)
    st.close()
    touch(proj.root, "labels", "L01-P-20261021-ab12.m3fa (1).jsonl")   # 충돌 사본은 세지 않는다
    sv, mine, ch = _choices(proj)
    assert [e.session_id for e in mine] == ["L01-P-20261021-ab12"]
    e = mine[0]
    assert e.resumable and e.on_this_pc and e.guide_version == "0.1" and e.session_kind == "primary"
    assert not ch["primary"].enabled and "L01-P-20261021-ab12" in ch["primary"].reason
    assert "L02-P-20261020-zz11" in sv.session_ids        # 이름은 셀 수 있지만
    assert all(x.session_id.startswith("L01-") for x in mine)   # 다른 라벨러 세션은 돌려주지 않는다
    # 잠그면 이어 할 수 없고 새 1차가 다시 켜진다
    touch(proj.root, "locks", "L01-P-20261021-ab12.json")
    _, mine, ch = _choices(proj)
    assert mine[0].locked and not mine[0].resumable and ch["primary"].enabled
    # 이어 쓰기: 같은 session_id 로 store 가 기존 메타를 쓴다
    os.remove(os.path.join(proj.root, "locks", "L01-P-20261021-ab12.json"))
    st2 = open_session(proj, session.resume_session("L01-P-20261021-ab12", machine_id_value=MID))
    assert not st2.read_only and st2.session_meta["session_created_at"] == ctx.session_created_at
    st2.close()


# ============================================================================ 분석 세션 노출 (D-09 5)

def test_analysis_exposure_marks_labeler_sessions():
    sids = ["L01-P-20261001-aa11", "L01-AN-20261021-bb22", "L01-RS-20261030-cc33", "L02-P-20261001-dd44",
            "L02-AD-20261001-ee55", "../evil", 7]
    exp = V.analysis_exposure(sids)
    assert exp.labelers == frozenset({"L01"})
    assert exp.analysis_sessions == frozenset({"L01-AN-20261021-bb22"})
    assert exp.exposed_sessions == frozenset({"L01-P-20261001-aa11", "L01-RS-20261030-cc33"})
    assert exp.is_exposed("L01-P-20261001-aa11") and not exp.is_exposed("L02-P-20261001-dd44")
    assert V.analysis_exposure([]).labelers == frozenset()
    assert V.session_kind_of_id("L01-AN-20261021-bb22") == V.ANALYSIS and V.session_kind_of_id("x") is None


def test_exposure_notes(proj):
    ctx = session.new_session("L01", "primary", machine_id_value=MID, now=NOW, rand4="ab12")
    open_session(proj, ctx).close()
    sv = session.survey_project(proj.root, proj.data)
    notes = session.exposure_notes(sv, "L01", "analysis")
    assert any("노출" in n for n in notes) and any("잠기지 않은" in n for n in notes)
    assert session.exposure_notes(sv, "L01", "primary") == []
    an = session.new_session("L01", "analysis", machine_id_value=MID, now=NOW, rand4="cd34")
    open_session(proj, an).close()
    sv = session.survey_project(proj.root, proj.data)
    assert any("분석 세션을 연 적" in n for n in session.exposure_notes(sv, "L01", "primary"))
    assert session.exposure_notes(sv, "L02", "primary") == []


def test_analysis_session_store_ops_only_and_close_line(proj):
    ctx = session.new_session("L01", "analysis", machine_id_value=MID, now=NOW, rand4="ab12")
    st = open_session(proj, ctx)
    assert not st.read_only, st.read_only_reasons
    so = st.records()[0]
    assert so["body"]["session_kind"] == "analysis" and so["body"]["blind"]["to_other_sessions"] is False
    with pytest.raises(store.LabelValidationError):
        st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 1000, "category": "just_chatting",
                                                     "confidence": "certain", "rationale": "00:00 합성"}})
    st.append_ops("phase.mark", {"phase": "segments", "scope": "all", "done": True})
    st.close("app_exit")
    st.close()                                           # 두 번째는 아무것도 안 함
    recs = [json.loads(x) for x in open(st.path, encoding="utf-8").read().splitlines()]
    assert [r["kind"] for r in recs] == ["session.open", "phase.mark", "session.close"]
    body = recs[-1]["body"]
    assert body["reason"] == "app_exit" and body["lines_written"] == 2 and body["run_ms"] >= 0
    st2 = open_session(proj, session.resume_session(ctx.session_id, machine_id_value=MID))
    assert not st2.read_only, st2.read_only_reasons        # session.close 가 있어도 다시 열린다(스키마 통과)
    st2.close()


def test_close_bad_reason_and_read_only_close_writes_nothing(proj):
    ctx = session.new_session("L01", "primary", machine_id_value=MID, now=NOW, rand4="ab12")
    st = open_session(proj, ctx)
    with pytest.raises(ValueError):
        st.close("whatever")
    assert not st.closed
    st.close()
    n = len(open(st.path, "rb").read().splitlines())
    FakeLock.held.add(os.path.join(proj.root, "locks", "open.lock"))
    ro = open_session(proj, ctx)
    assert ro.read_only and ro.lock_busy and ro.lock_holder is None   # FakeLock 은 getLockInfo 가 없다
    assert "open.lock" in ro.lock_help                                 # 누가 쥐었는지 모르면 지우는 안내까지
    ro.close()
    assert len(open(st.path, "rb").read().splitlines()) == n


def test_close_line_failure_still_unlocks(proj, monkeypatch):
    st = open_session(proj, session.new_session("L01", "primary", machine_id_value=MID, now=NOW, rand4="ab12"))

    def boom(rec):
        raise OSError("disk")
    monkeypatch.setattr(st, "_append_record", boom)
    st.close()
    assert st.closed and not FakeLock.held


def test_spec_bundles_label_schema():
    spec = open(os.path.join(ROOT, "ChatCoach.spec"), encoding="utf-8").read()
    assert "('schemas\\\\label-event.schema.json', 'schemas')" in spec
    assert os.path.join(*store.SCHEMA_REL) == os.path.join("schemas", "label-event.schema.json")


# ============================================================================ 화면

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def make_dialog(qapp, proj, data_root, **kw):
    from app.ui.session_dialog import SessionDialog
    n = iter(["ab12", "ab12", "cd34", "ef56"])
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW, rand4=lambda: next(n), **kw)
    dlg.load_context(proj.root, proj.data)
    return dlg


def all_text(widget):
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit
    out = [widget.windowTitle()]
    for w in widget.findChildren(QLabel):
        out.append(w.text() + " " + w.toolTip())
    for w in widget.findChildren(QAbstractButton):
        out.append(w.text() + " " + w.toolTip())
    for w in widget.findChildren(QComboBox):
        out += [w.itemText(i) for i in range(w.count())]
    for w in widget.findChildren(QLineEdit):
        out.append(w.text() + " " + w.placeholderText())
    return "\n".join(out)


def test_dialog_empty_roster_add_labeler_and_open_primary(qapp, proj, data_root):
    dlg = make_dialog(qapp, proj, data_root)
    assert not dlg.new_box.isHidden() and dlg.labeler_id() is None
    assert not dlg.btn_open.isEnabled()
    assert "L01" not in dlg.new_id.text() + dlg.new_id.placeholderText()   # 예시 ID 를 채우지 않는다 (S-37)
    dlg.new_id.setText("l07")                             # 소문자로 쳐도 대문자로
    dlg.new_name.setText("합성 표시 이름")
    dlg._add_labeler()
    assert dlg.labeler_id() == "L07" and session.load_labelers(data_root).ids == ["L07"]
    assert "v0.1" in dlg.guide_label.text()
    keys = dlg.choice_buttons()
    assert keys["new:primary"].isEnabled() and not keys["new:analysis"].isEnabled()
    assert not dlg.choose("analysis") and dlg.selected_key() is None    # 꺼진 것은 못 고른다
    assert dlg.selected_key() is None                    # 새 세션 종류를 미리 고르지 않는다
    assert dlg.choose("primary") and dlg.btn_open.isEnabled()
    got = []
    dlg.sessionChosen.connect(got.append)
    dlg._choose_open()
    ctx = dlg.context()
    assert ctx is not None and got == [ctx] and not dlg.view_only
    assert ctx.session_id == "L07-P-20261021-ab12" and ctx.machine4 == session.machine4(MID)
    assert ctx.session_created_at.startswith("2026-10-21T06:00:00")
    dlg.deleteLater()


def test_dialog_bad_new_labeler_id(qapp, proj, data_root):
    dlg = make_dialog(qapp, proj, data_root)
    dlg.new_id.setText("7X")
    dlg._add_labeler()
    assert "대문자" in dlg.new_msg.text() and session.load_labelers(data_root).ids == []
    dlg.deleteLater()


def test_dialog_view_only_and_cancel_return_none(qapp, proj, data_root):
    session.add_labeler(data_root, "L01", now=NOW)
    dlg = make_dialog(qapp, proj, data_root, default_labeler="L01")
    assert dlg.labeler_id() == "L01" and dlg.new_box.isHidden()
    dlg.choose("primary")
    dlg._choose_view_only()
    assert dlg.context() is None and dlg.view_only
    dlg2 = make_dialog(qapp, proj, data_root)
    assert dlg2.labeler_id() is None                     # default_labeler 를 주지 않으면 미리 고르지 않는다
    dlg2.select_labeler("L01")
    dlg2.choose("primary")
    dlg2.reject()
    assert dlg2.context() is None and not dlg2.view_only
    for d in (dlg, dlg2):
        d.deleteLater()


def test_dialog_resume_preselected_and_unique_new_id(qapp, proj, data_root):
    session.add_labeler(data_root, "L01", now=NOW)
    first = session.new_session("L01", "primary", machine_id_value=MID, now=NOW, rand4="ab12")
    open_session(proj, first).close()
    touch(proj.root, "derived", "link-x.json")
    dlg = make_dialog(qapp, proj, data_root, default_labeler="L01")
    assert dlg.selected_key() == "resume:L01-P-20261021-ab12"       # 하나뿐이면 이어 하기를 미리 고른다
    assert "이어 하는 세션" in dlg.guide_label.text()
    assert not dlg.choice_buttons()["new:primary"].isEnabled()
    dlg._choose_open()
    assert dlg.context().session_id == first.session_id and dlg.context().session_kind == "primary"
    # 분석 세션: 이미 있는 ID(ab12)와 겹치지 않게 다시 뽑는다, 노출 안내가 보인다
    dlg2 = make_dialog(qapp, proj, data_root, default_labeler="L01")
    assert dlg2.choose("analysis")
    assert "노출" in dlg2.notes.text()
    dlg2._choose_open()
    assert dlg2.context().session_id == "L01-AN-20261021-ab12"
    for d in (dlg, dlg2):
        d.deleteLater()


def test_dialog_never_shows_other_sessions_or_engine_results(qapp, proj, data_root):
    """블라인드(안내서 원칙 3): 다른 라벨러 세션·엔진 결과의 내용과 이름이 화면에 나오지 않는다."""
    session.add_labeler(data_root, "L01", now=NOW)
    write_other_session(proj.root)
    touch(proj.root, "derived", "link-합성.json",
          content=json.dumps({"links": [{"grade": "direct", "note": SECRET}]}, ensure_ascii=False).encode("utf-8"))
    dlg = make_dialog(qapp, proj, data_root, default_labeler="L01")
    for kind in session.KIND_ORDER:
        dlg.choose(kind)
        text = all_text(dlg)
        assert SECRET not in text and "L02" not in text and "direct" not in text
    dlg.deleteLater()


def test_dialog_read_only_banner_and_roster_problem(qapp, proj, data_root):
    with open(os.path.join(data_root, "labelers.json"), "wb") as f:
        f.write(b"[1,")
    from app.ui.session_dialog import SessionDialog
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID)
    dlg.load_context(proj.root, proj.data, project_title="합성 제목", read_only=True,
                     read_only_reasons=["다른 창이나 PC 가 이 프로젝트를 쓰고 있습니다"])
    assert not dlg.ro_label.isHidden() and "읽기 전용" in dlg.ro_label.text()
    assert not dlg.roster_msg.isHidden() and "합성 제목" in dlg.title.text()
    dlg.deleteLater()


def test_choose_session_function(qapp, proj, data_root, monkeypatch):
    from app.ui import session_dialog as sd
    session.add_labeler(data_root, "L01", now=NOW)

    def fake_exec(self):
        self.choose("primary")
        self._choose_open()
        return 1
    monkeypatch.setattr(sd.SessionDialog, "exec", fake_exec)
    ctx = sd.choose_session(None, proj.root, proj.data, data_root=data_root, default_labeler="L01",
                            machine_id_value=MID)
    assert ctx is not None and ctx.labeler_id == "L01" and ctx.session_kind == "primary"
