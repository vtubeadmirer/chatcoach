"""app/locking.py · ProjectStore.lock_session · app/ui/lock_dialog.py — 세션 잠금 (06 7-1·7-2·7-3, 03 6-14, 08 D-11 7).

합성 데이터만 쓴다(지어낸 문장·ID). 실제 프로젝트 폴더에는 쓰지 않는다(tmp_path).
"""
import builtins
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import locking, project, session, store
from app.core import phase as ph

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
SALT = bytes(range(32))
MID_A = "aaaaaaaa-0000-4000-8000-000000000001"
MID_B = "bbbbbbbb-0000-4000-8000-000000000002"
SID = "L01-P-20261021-ab12"
SID_R2 = "L02-R2-20261022-cd34"
SID_AD = "L09-AD-20261023-ef56"
SID_AN = "L01-AN-20261023-gh78"
C1 = "c" + "1" * 20
C2 = "c" + "2" * 20
C3 = "c" + "3" * 20


class FakeLock:
    held: set = set()

    def __init__(self, path):
        self.path = path
        self.locked = False

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

    def getLockInfo(self):
        return (1, "SYNTH-HOST", "chatcoach")


@pytest.fixture(autouse=True)
def _clear_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


class Clock:
    def __init__(self, start=NOW):
        self.t = start

    def __call__(self):
        self.t += timedelta(milliseconds=10)
        return self.t


@pytest.fixture
def root(tmp_path):
    g = project.GuideSource("0.1", b"# synthetic guide\n", {"reaction": {"time_window_sec": 20}})
    p = project.create(str(tmp_path / "합성 폴더"), "합성 방송", 123, "시험용", "L01", "0.0.1", NOW, PID, SALT,
                       guide=g, machine4="m3fa")
    return p.root


def ctx(sid=SID, kind="primary", labeler="L01", mid=MID_A):
    return session.SessionContext(labeler, kind, sid, session.machine4(mid), machine_id=mid,
                                  session_created_at="2026-10-21T06:00:00+09:00")


def open_store(root, c=None, **kw):
    kw.setdefault("clock", Clock())
    kw.setdefault("app_version", "0.0.1")
    return store.ProjectStore.open(root, c or ctx(), FakeLock, **kw)


def evidence(**present):
    return {k: ({"present": True, "at_ms": None, "note": "합성"} if k in present else {"present": False})
            for k in ("nickname_called", "text_repeated", "direct_reference", "answer_form", "unique_content",
                      "within_time_window", "same_topic")}


def seg(st, start, end, cat="just_chatting"):
    return st.append_label({"kind": "segment", "body": {"start_ms": start, "end_ms": end, "category": cat,
                                                        "confidence": "certain", "rationale": "00:00 합성 구간"}})


def reaction(st, sg, chat_id, t=61000):
    utt = st.append_label({"kind": "utterance", "body": {"segment_id": sg["entity_id"], "start_ms": t,
                                                         "end_ms": t + 2000, "heard_text": "지어낸 말",
                                                         "heard_text_source": "typed", "speaker": "streamer",
                                                         "modality": "verbal"}})
    return st.append_label({"kind": "reaction", "body": {
        "chat_id": chat_id, "segment_id": sg["entity_id"], "entry": "from_utterance", "result": "graded",
        "grade": "direct", "eligible_grades": ["direct"], "grade_vs_conditions": "consistent",
        "candidates": [{"utterance_id": utt["entity_id"], "evidence": evidence(nickname_called=True)}],
        "linked_utterance_id": utt["entity_id"], "first_visible_ms": t - 1000, "first_visible_source": "chat_clock",
        "reaction_time_ms": t + 500, "confidence": "certain", "rationale": "01:01 합성 호명"}})


def write_manifest(root, sg, items, sid=SID, rev=1):
    """잠금이 보는 칸만 가진 합성 매니페스트(전체 스키마 모양은 아니다)."""
    set_id = f"S-{sg['entity_id'][:8]}-d1-r{rev}"
    m = {"schema": "chatcoach/sample-manifest", "sample_set_id": set_id, "sample_rev": rev,
         "source_segment": {"session_id": sid, "entity_id": sg["entity_id"]},
         "requested_size": len(items), "census": False,
         "items": [{"chat_id": c, "order": i + 1} for i, c in enumerate(items)]}
    d = os.path.join(root, "samples")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, set_id + ".json"), "wb") as f:
        f.write(json.dumps(m, ensure_ascii=False).encode("utf-8"))
    return set_id


def labeled_store(root):
    st = open_store(root)
    a = seg(st, 0, 600000)
    seg(st, 600000, 700000, "game")
    b = seg(st, 700000, 900000)
    st.append_ops("phase.mark", ph.mark_body("segments", True, [a["entity_id"]]))
    st.append_ops("phase.mark", ph.mark_body("reactions", True, [a["entity_id"]]))
    reaction(st, a, C1)
    return st, a, b


def lock_file(root, sid=SID):
    return os.path.join(root, "locks", f"{sid}.json")


# ---------------------------------------------------------------------------- 잠그기

def test_lock_session_writes_once_and_blocks_writes(root):
    st, a, _ = labeled_store(root)
    set_id = write_manifest(root, a, [C1, C2])
    doc = st.lock_session()
    p = lock_file(root)
    assert os.path.isfile(p)
    with open(p, "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\n")
    on_disk = json.loads(raw)
    assert on_disk == doc
    assert locking.check_lock_doc(on_disk) == []
    assert doc["session_id"] == SID and doc["labeler_id"] == "L01" and doc["locked_by"] == "L01"
    assert doc["session_kind"] == "primary" and doc["project_id"] == PID
    assert doc["locked_at"].endswith("+09:00") and doc["app_version"] == "0.0.1"
    assert doc["guide"]["guide_version"] == "0.1" and len(doc["guide"]["guide_doc_sha256"]) == 64
    assert doc["locked_on_machine4"] == session.machine4(MID_A)
    # 기록 파일 해시·줄 수는 디스크 바이트와 같다. 마지막 줄은 session.close
    (fe,) = doc["files"]
    with open(st.path, "rb") as f:
        data = f.read()
    assert fe["sha256"] == locking.sha256_bytes(data) and fe["size"] == len(data)
    assert fe["lines"] == data.count(b"\n") == fe["last_seq"]
    assert json.loads(data.splitlines()[-1])["kind"] == "session.close"
    assert doc["label_counts"]["segment"] == 3 and doc["label_counts"]["reaction"] == 1
    assert doc["label_counts"]["utterance"] == 1
    assert doc["sample_manifests"] == [{"sample_set_id": set_id,
                                        "sha256": locking.sha256_bytes(open(os.path.join(root, "samples", set_id + ".json"), "rb").read())}]
    assert doc["summary"]["samples_judged"] == 1 and doc["summary"]["samples_total"] == 2
    assert "samples_pending" in doc["summary"]["warnings"]
    # 잠근 뒤: 읽기 전용, 쓰기 거부, 다시 잠그기 거부, 파일 그대로
    assert st.read_only and st.locked and locking.LOCKED_REASON in st.read_only_reasons
    with pytest.raises(store.ReadOnlyStoreError):
        seg(st, 900000, 950000)
    with pytest.raises(store.ReadOnlyStoreError):
        st.append_ops("warning", {"code": "other", "action": "shown"})
    with pytest.raises(locking.AlreadyLockedError):
        st.lock_session()
    st.close()
    with open(st.path, "rb") as f:
        assert f.read() == data                        # 닫아도 session.close 를 또 쓰지 않는다
    with open(p, "rb") as f:
        assert f.read() == raw
    assert not [n for n in os.listdir(os.path.join(root, "locks")) if n.endswith(".tmp")]


def test_reopen_locked_is_read_only_without_project_lock(root):
    st, _, _ = labeled_store(root)
    st.lock_session()
    st.close()
    with open(st.path, "rb") as f:
        before = f.read()
    st2 = open_store(root, run_info={"utc_offset_min": 540, "clock_source": "none", "data_dir_mode": "portable",
                                     "update_check_state": "never", "checked_at": None, "latest_seen_version": None})
    assert st2.read_only and st2.locked
    assert locking.LOCKED_REASON in st2.read_only_reason
    assert FakeLock.held == set()                         # 잠긴 세션은 open.lock 을 잡지 않는다
    assert st2.lock_check is not None and st2.lock_check.ok
    assert st2.lock_doc["session_id"] == SID
    assert [r["kind"] for r in st2.records()][-1] == "session.close"
    assert st2.lock_blockers() and not st2.lock_summary().can_lock
    with pytest.raises(store.ReadOnlyStoreError):
        seg(st2, 0, 1000)
    st2.close()
    with open(st.path, "rb") as f:
        assert f.read() == before                         # run.start 도 쓰지 않았다


def test_lines_appended_after_lock_are_excluded(root):
    st, _, _ = labeled_store(root)
    doc = st.lock_session()
    n_locked = len(st.records())
    st.close()
    # 메모장 등으로 잠금 뒤에 줄을 붙인 상황(합성)
    extra = dict(json.loads(open(st.path, "rb").read().splitlines()[-1]))
    extra.update(id="99999999-0000-4000-8000-000000000099", seq=doc["files"][0]["last_seq"] + 1, kind="warning",
                 body={"code": "other", "action": "shown"})
    with open(st.path, "ab") as f:
        f.write(json.dumps(extra).encode("utf-8") + b"\n")
    st2 = open_store(root)
    assert st2.lock_check.files[0].status == locking.ST_APPENDED
    assert not st2.lock_check.tampered
    assert len(st2.records()) == n_locked
    assert any("잠금 후 추가" in w for w in st2.warnings)
    v = locking.verify_lock(root, SID, reader_kind="primary", reader_session_id=SID)
    assert v.by_status(locking.ST_APPENDED) and not v.tampered
    st2.close()


def test_tampered_file_is_reported(root):
    st, _, _ = labeled_store(root)
    st.lock_session()
    st.close()
    data = bytearray(open(st.path, "rb").read())
    i = data.index("합성 구간".encode("utf-8"))
    data[i:i + 3] = "가".encode("utf-8")                    # 같은 길이로 바꿈
    with open(st.path, "wb") as f:
        f.write(bytes(data))
    st2 = open_store(root)
    assert st2.lock_check.tampered
    assert any("변조/손상" in r for r in st2.read_only_reasons)
    assert any("변조/손상" in w for w in st2.warnings)
    st2.close()
    v = locking.verify_lock(root, SID, reader_kind="primary", reader_session_id=SID)
    assert v.tampered and v.files[0].detail == "changed" and not v.ok
    # 파일이 없어져도 변조/손상
    os.remove(st.path)
    v2 = locking.verify_lock(root, SID, reader_kind="primary", reader_session_id=SID)
    assert v2.files[0].status == locking.ST_TAMPERED and v2.files[0].detail == "missing"


def test_file_created_after_lock_on_other_pc_is_ignored(root):
    st, _, _ = labeled_store(root)
    st.lock_session()
    st.close()
    first = open(st.path, "rb").read().splitlines()[0]
    so = json.loads(first)
    so["body"]["machine4"] = session.machine4(MID_B)
    other = os.path.join(root, "labels", session.session_file_name(SID, session.machine4(MID_B)))
    with open(other, "wb") as f:
        f.write(json.dumps(so, ensure_ascii=False).encode("utf-8") + b"\n")
    st2 = open_store(root)
    assert [f.status for f in st2.lock_check.files] == [locking.ST_OK, locking.ST_EXTRA] or \
        sorted(f.status for f in st2.lock_check.files) == sorted([locking.ST_OK, locking.ST_EXTRA])
    assert all(r["body"].get("machine4") != session.machine4(MID_B) for r in st2.records()
               if r["kind"] == "session.open")
    st2.close()


def test_corrupt_lock_file_stays_read_only(root):
    st, _, _ = labeled_store(root)
    st.close()
    os.makedirs(os.path.join(root, "locks"), exist_ok=True)
    with open(lock_file(root), "wb") as f:
        f.write(b"{not json")
    st2 = open_store(root)
    assert st2.read_only and st2.locked
    assert any("변조/손상" in r for r in st2.read_only_reasons)
    with pytest.raises(locking.AlreadyLockedError):
        st2.lock_session()
    st2.close()
    with pytest.raises(locking.LockFormatError):
        locking.read_lock(root, SID, reader_kind="primary", reader_session_id=SID)


def test_lock_created_elsewhere_blocks_next_write(root):
    """다른 PC 가 같은 세션을 잠갔으면 이 PC 의 다음 쓰기가 거부된다(잠금 후 추가 방지)."""
    st, _, _ = labeled_store(root)
    os.makedirs(os.path.join(root, "locks"), exist_ok=True)
    with open(lock_file(root), "wb") as f:
        f.write(b"{}")
    with pytest.raises(store.ReadOnlyStoreError):
        seg(st, 900000, 950000)
    assert st.read_only and st.locked
    st.close()


def test_write_lock_exclusive_never_overwrites(tmp_path):
    p = str(tmp_path / "locks" / f"{SID}.json")
    locking.write_lock_exclusive(p, b"first\n")
    with pytest.raises(locking.AlreadyLockedError):
        locking.write_lock_exclusive(p, b"second\n")
    assert open(p, "rb").read() == b"first\n"
    assert os.listdir(tmp_path / "locks") == [f"{SID}.json"]


def test_write_lock_exclusive_fallback_without_hardlink(tmp_path, monkeypatch):
    def no_link(a, b):
        raise OSError("hard links not supported")
    monkeypatch.setattr(os, "link", no_link)
    p = str(tmp_path / "locks" / f"{SID}.json")
    locking.write_lock_exclusive(p, b"first\n")
    with pytest.raises(locking.AlreadyLockedError):
        locking.write_lock_exclusive(p, b"second\n")
    assert open(p, "rb").read() == b"first\n"
    assert os.listdir(tmp_path / "locks") == [f"{SID}.json"]


def test_failed_lock_leaves_no_file_and_store_writable(root, monkeypatch):
    st, _, _ = labeled_store(root)

    def boom(path, data):
        raise OSError("disk full (synthetic)")
    monkeypatch.setattr(locking, "write_lock_exclusive", boom)
    with pytest.raises(OSError):
        st.lock_session()
    assert not os.path.exists(lock_file(root))
    assert not st.read_only and not st.locked
    seg(st, 900000, 950000)                           # 계속 쓸 수 있다
    st.close()


def test_analysis_session_cannot_lock(root):
    st, _, _ = labeled_store(root)
    st.lock_session()
    st.close()
    an = open_store(root, ctx(SID_AN, "analysis"))
    assert not an.read_only, an.read_only_reasons
    s = an.lock_summary()
    assert not s.can_lock and any("analysis" in b for b in s.blockers)
    with pytest.raises(store.StoreError):
        an.lock_session()
    assert not os.path.exists(lock_file(root, SID_AN))
    an.close()


def test_other_pc_file_changed_since_open_refuses(root):
    st, _, _ = labeled_store(root)
    st.close()
    # 같은 세션을 PC B 에서 이어 씀
    b = open_store(root, ctx(mid=MID_B))
    b.close()
    FakeLock.held = set()
    st2 = open_store(root)
    # st2 가 연 뒤 PC B 파일이 늘어남(합성)
    other = os.path.join(root, "labels", session.session_file_name(SID, session.machine4(MID_B)))
    last = json.loads(open(other, "rb").read().splitlines()[-1])
    last.update(id="98989898-0000-4000-8000-000000000098", seq=last["seq"] + 1, kind="warning",
                body={"code": "other", "action": "shown"})
    with open(other, "ab") as f:
        f.write(json.dumps(last).encode("utf-8") + b"\n")
    assert "other_machine_files" in st2.lock_summary().warnings
    with pytest.raises(store.StoreError):
        st2.lock_session()
    assert not os.path.exists(lock_file(root))
    st2.close()


# ---------------------------------------------------------------------------- 요약

def test_summary_counts_and_warnings(root):
    st, a, b = labeled_store(root)
    s = st.lock_summary()
    assert s.segments == 3 and s.jc_segments == 2 and s.reactions == 1
    assert s.segments_unmarked == 1 and "segments_unmarked" in s.warnings    # b 는 구간 완료 표시 없음
    assert s.samples_missing == 1 and "samples_missing" in s.warnings        # a 는 반응 완료인데 표본 없음
    assert s.samples == () and s.can_lock
    write_manifest(root, a, [C1, C2, C3])
    s2 = st.lock_summary()
    assert "samples_missing" not in s2.warnings and "samples_pending" in s2.warnings
    (p,) = s2.samples
    assert (p.done, p.total, p.pending) == (1, 3, 2)
    assert any("2건" in t for t in s2.warning_texts())
    # 계열의 가장 큰 sample_rev 만 센다
    write_manifest(root, a, [C1], rev=2)
    (p2,) = st.lock_summary().samples
    assert p2.sample_set_id.endswith("-r2") and (p2.done, p2.total) == (1, 1)
    # 다른 세션 구간의 매니페스트는 세지 않는다
    write_manifest(root, {"entity_id": "abcdef01-0000-4000-8000-000000000001"}, [C1], sid="L05-P-20261021-zz99")
    assert len(st.lock_summary().samples) == 1
    st.close()


def test_empty_session_warns_no_labels(root):
    st = open_store(root)
    s = st.lock_summary()
    assert "no_labels" in s.warnings and "no_segments" in s.warnings and s.can_lock
    st.close()


# ---------------------------------------------------------------------------- 블라인드

class OpenSpy:
    def __init__(self, monkeypatch):
        self.paths = []
        real = builtins.open

        def spy(file, *a, **kw):
            self.paths.append(os.path.abspath(str(file)))
            return real(file, *a, **kw)
        monkeypatch.setattr(builtins, "open", spy)

    def under(self, d):
        d = os.path.abspath(d)
        return [p for p in self.paths if p.startswith(d)]


def test_second_labeler_never_opens_first_session_files(root, monkeypatch):
    st, a, _ = labeled_store(root)
    write_manifest(root, a, [C1])
    st.lock_session()
    st.close()
    first_files = [os.path.join(root, "labels", n) for n in os.listdir(os.path.join(root, "labels"))]
    spy = OpenSpy(monkeypatch)
    r2 = open_store(root, ctx(SID_R2, "relabel_second", "L02", MID_B))
    assert not r2.read_only, r2.read_only_reasons
    r2.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600000, "category": "just_chatting",
                                                 "confidence": "certain", "rationale": "00:00 두 번째 사람"}})
    s = r2.lock_summary()
    assert s.samples == ()                                    # samples/ 를 열지 않는다
    with pytest.raises(locking.LockBlindError):
        locking.read_lock(root, SID, reader_kind="relabel_second", reader_session_id=SID_R2)
    with pytest.raises(locking.LockBlindError):
        locking.verify_lock(root, SID, reader_kind="relabel_second", reader_session_id=SID_R2)
    with pytest.raises(locking.LockBlindError):
        locking.verify_lock(root, SID, reader_kind="relabel_same", reader_session_id="L01-RS-20261025-aa11")
    assert locking.is_locked(root, SID)                       # 이름만 보는 것은 된다
    doc = r2.lock_session()
    assert doc["sample_manifests"] == [] and doc["session_kind"] == "relabel_second"
    r2.close()
    for p in first_files:
        assert os.path.abspath(p) not in spy.paths
    assert os.path.abspath(lock_file(root)) not in spy.paths
    assert not spy.under(os.path.join(root, "samples"))


def test_adjudication_and_analysis_may_verify_locked(root):
    st, _, _ = labeled_store(root)
    st.lock_session()
    st.close()
    assert locking.verify_lock(root, SID, reader_kind="adjudication", reader_session_id=SID_AD).ok
    assert locking.verify_lock(root, SID, reader_kind="analysis", reader_session_id=SID_AN).ok
    # 분석 세션은 잠긴 1차만 — 잠긴 두 번째 사람 세션은 막힌다
    r2 = open_store(root, ctx(SID_R2, "relabel_second", "L02", MID_B))
    seg(r2, 0, 1000)
    r2.lock_session()
    r2.close()
    with pytest.raises(locking.LockBlindError):
        locking.verify_lock(root, SID_R2, reader_kind="analysis", reader_session_id=SID_AN)
    assert locking.verify_lock(root, SID_R2, reader_kind="adjudication", reader_session_id=SID_AD).ok
    with pytest.raises(locking.LockBlindError):
        locking.verify_lock(root, SID, reader_kind="primary", reader_session_id="L03-P-20261021-qq11")


# ---------------------------------------------------------------------------- 형식

def test_lock_doc_checks():
    sch = locking.load_lock_schema()
    with pytest.raises(ValueError):
        locking.lock_path("x", "../evil")
    assert locking.check_lock_doc({"schema": "chatcoach/session-lock"}, sch)
    s = locking.LockSummary(SID, "primary", 0, 0, 0, {k: 0 for k in locking.COUNT_KINDS},
                            {"active": 0, "retracted": 0, "conflict": 0, "invalid": 0})
    ent = locking.file_entry(f"{SID}.m3fa.jsonl", b'{"seq": 1}\n{"seq": 2}\n{"se')
    assert ent["lines"] == 2 and ent["last_seq"] == 2
    good = locking.build_lock_doc(session_id=SID, project_id=PID, labeler_id="L01", session_kind="primary",
                                  locked_at="2026-10-21T06:00:00.000+09:00", locked_on_machine4="m3fa",
                                  app_version="0.0.1", guide={"guide_version": "0.1", "guide_doc_sha256": "a" * 64,
                                                              "thresholds_sha256": "b" * 64},
                                  relabel_plan_id=None, files=[ent], summary=s)
    assert locking.check_lock_doc(good, sch) == []
    bad = json.loads(json.dumps(good))
    bad["labeler_id"] = "L02"
    assert any("라벨러" in p for p in locking.check_lock_doc(bad, sch))
    bad2 = json.loads(json.dumps(good))
    bad2["files"][0]["name"] = "L05-P-20261021-zz99.m3fa.jsonl"
    assert any("이 세션의 기록 파일" in p for p in locking.check_lock_doc(bad2, sch))
    bad3 = json.loads(json.dumps(good))
    bad3["unlock"] = True
    assert locking.check_lock_doc(bad3, sch)


# ---------------------------------------------------------------------------- 화면

@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_two_step_lock(root, qapp):
    from app.ui.lock_dialog import LockDialog, summary_rows
    st, a, _ = labeled_store(root)
    write_manifest(root, a, [C1, C2])
    dlg = LockDialog(store=st)
    rows = dict(summary_rows(dlg.summary))
    assert rows["구간"].startswith("3개") and rows["반응"] == "1건" and rows["표본 진행"].startswith("1 / 2")
    assert dlg.warning_labels and dlg.step == 1 and dlg.next_btn.isEnabled()
    assert not dlg.do_lock()                                   # 1단계에서는 잠그지 않는다
    dlg.go_confirm()
    assert dlg.step == 2 and not dlg.lock_btn.isEnabled()
    assert "미완료" in dlg.confirm_box.text()
    assert not dlg.do_lock() and not os.path.exists(lock_file(root))
    dlg.confirm_box.setChecked(True)
    assert dlg.lock_btn.isEnabled()
    dlg.go_summary()
    assert dlg.step == 1 and not dlg.confirm_box.isChecked()   # 뒤로 가면 확인을 다시 받는다
    dlg.go_confirm()
    dlg.confirm_box.setChecked(True)
    got = []
    dlg.sessionLocked.connect(got.append)
    assert dlg.do_lock()
    assert os.path.exists(lock_file(root)) and got and got[0]["session_id"] == SID
    assert st.read_only and not dlg.lock_btn.isEnabled()
    st.close()


def test_dialog_blocked_when_locked_and_shows_error(root, qapp, monkeypatch):
    from app.ui.lock_dialog import LockDialog
    st, _, _ = labeled_store(root)
    dlg = LockDialog(store=st)

    def fail(**kw):
        raise store.StoreError("합성 실패")
    monkeypatch.setattr(st, "lock_session", fail)
    dlg.go_confirm()
    dlg.confirm_box.setChecked(True)
    assert not dlg.do_lock()
    assert "합성 실패" in dlg.error_label.text() and dlg.lock_doc is None
    monkeypatch.undo()
    st.lock_session()
    dlg2 = LockDialog(store=st)
    assert not dlg2.next_btn.isEnabled() and dlg2.blocker_labels
    dlg2.go_confirm()
    assert dlg2.step == 1
    st.close()
