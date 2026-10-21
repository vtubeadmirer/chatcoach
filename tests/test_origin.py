"""출처 판정 시험 (app/core/origin.py, app/ui/origin_view.py — 06 4-7, 03 6-11, 08 D-12 6).

합성 채팅(본문 'o…' 자리표시)과 지어낸 생성 기록 ID 만 쓴다. 실제 채팅·닉네임은 쓰지 않는다.
화면 시험은 QT_QPA_PLATFORM=offscreen.
"""
import json
import os
import sys
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app.core import origin as og  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SID = "L01-OR-20261021-a1b2"
SID_B = "L02-OR-20261021-c3d4"
CREATED = "2026-10-21T15:00:00+09:00"
SECRET = "BLIND-SECRET-MARKER"          # 화면 어디에도 나오면 안 되는 값


def cid(i):
    return "c%020x" % i


def targets_obj(n=6, auto=None):
    return og.build_targets(chat_ids=[cid(i) for i in range(n)], created_at=CREATED, created_by="L01",
                            basis="manual", auto=auto)


def gen_hint(origin="virtual", gen="g1"):
    return {"origin": origin, "method": "generation_log_match",
            "generation_ref": {"file": "gen-20261021.jsonl", "gen_id": gen}, "score": 0.9, "second_score": 0.3}


def lab(c, origin, method="manual", conf="certain", sid=SID, eid=None, rid=None):
    body = {"chat_id": c, "origin": origin, "method": method, "generation_ref": None, "ocr_ref": None,
            "score": None, "second_score": None,
            "confidence": None if origin == "undeterminable" else conf, "rationale": "00:01:00 합성 근거"}
    if method == "generation_log_match":
        body["generation_ref"] = {"file": "gen-20261021.jsonl", "gen_id": "g" + c[-3:]}
    return {"kind": "origin", "session_id": sid, "entity_id": eid or str(uuid.uuid4()), "id": rid or str(uuid.uuid4()),
            "body": body}


# =========================================================================== 대상 파일
def test_build_and_parse_targets_roundtrip():
    obj = targets_obj(4, auto={cid(1): gen_hint()})
    t = og.parse_targets(json.loads(json.dumps(obj)))
    assert t.target_id.startswith("OT-20261021-") and og.TARGET_ID_RE.match(t.target_id)
    assert t.chat_ids == tuple(cid(i) for i in range(4))
    assert t.item_of(cid(1)).auto.origin == "virtual" and t.item_of(cid(0)).auto is None
    # 같은 날 같은 목록 → 같은 ID (난수 없음)
    assert targets_obj(4)["target_id"] == obj["target_id"]
    assert targets_obj(5)["target_id"] != obj["target_id"]


@pytest.mark.parametrize("mutate, word", [
    (lambda o: o.update(schema="x"), "schema"),
    (lambda o: o["items"].append(dict(o["items"][0])), "두 번"),
    (lambda o: o["items"][0].update(chat_id="nope"), "채팅 ID"),
    (lambda o: o.update(chat_ids_sha256="0" * 64), "chat_ids_sha256"),
    (lambda o: o.update(target_id="OT-20261021-ffff"), "target_id"),
    (lambda o: o["items"][0].update(order=9), "order"),
    (lambda o: o["items"][0].update(auto={"origin": "real", "method": "manual"}), "auto.method"),
    (lambda o: o["items"][0].update(auto={"origin": "virtual", "method": "generation_log_match"}), "generation_ref"),
    (lambda o: o["items"][0].update(auto=dict(gen_hint(), score=0.2, second_score=0.5)), "second_score"),
    (lambda o: o["items"][0].update(auto=dict(gen_hint(), generation_ref={"file": "../x", "gen_id": "g"})), "파일 이름"),
    (lambda o: o.update(extra=1), "모르는 키"),
    (lambda o: o.update(items=[]), "items"),
])
def test_parse_targets_rejects(mutate, word):
    obj = targets_obj(3)
    mutate(obj)
    with pytest.raises(og.OriginError) as e:
        og.parse_targets(obj)
    assert word in str(e.value)


def test_build_targets_rejects_auto_outside():
    with pytest.raises(og.OriginError):
        og.build_targets(chat_ids=[cid(0)], created_at=CREATED, created_by="L01", auto={cid(9): gen_hint()})


def test_combine_targets_dedups_by_target_id_order():
    a = og.parse_targets(targets_obj(3))
    b = og.parse_targets(og.build_targets(chat_ids=[cid(2), cid(7)], created_at="2026-10-22T09:00:00+09:00",
                                          created_by="L01"))
    c = og.combine_targets([b, a])
    assert c.target_ids == (a.target_id, b.target_id)
    assert c.chat_ids == (cid(0), cid(1), cid(2), cid(7)) and c.duplicates == 1


# =========================================================================== 판정 검증
def test_draft_matches_label_schema():
    from app.store import LabelSchema
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        schema = LabelSchema(json.load(f))
    t = og.parse_targets(targets_obj(3, auto={cid(1): gen_hint()}))
    drafts = [
        og.origin_draft(chat_id=cid(0), origin="real", confidence="certain", rationale="00:01:00 일반 시청자 대화"),
        og.origin_draft(chat_id=cid(2), origin="undeterminable", confidence="certain", rationale="기록 빠진 시간대",
                        method="none", targets=t),
        og.draft_from_auto(t.item_of(cid(1)).auto, chat_id=cid(1), rationale="자동 대조", confidence="probably"),
    ]
    assert drafts[1]["body"]["confidence"] is None       # 판별 불가면 확신도를 적지 않는다
    assert drafts[2]["body"]["method"] == "generation_log_match" and drafts[2]["body"]["score"] == 0.9
    for i, d in enumerate(drafts, 1):
        rec = {"v": 1, "id": str(uuid.uuid4()), "seq": i, "kind": "origin", "session_id": SID, "labeler_id": "L01",
               "created_at": "2026-10-21T15:01:00.000+09:00", "app_version": "0.1.0", "entity_id": str(uuid.uuid4()),
               "supersedes": None, "guide_version": "0.1", "guide_sha12": "a" * 12, "thresholds_sha12": "b" * 12,
               "media_rev": 1, "calibration_rev": 0, "body": d["body"]}
        assert schema.check(rec) == [], d


@pytest.mark.parametrize("kw, word", [
    (dict(origin="real", confidence=None), "확신도"),
    (dict(origin="virtual", confidence="undeterminable"), "확신도"),
    (dict(origin="maybe"), "origin"),
    (dict(method="generation_log_match"), "generation_ref"),
    (dict(method="ocr_match"), "ocr_ref"),
    (dict(method="none"), "근거 없음"),
    (dict(method="auto"), "method"),
    (dict(rationale="   "), "비었"),
    (dict(rationale="두 줄\n근거"), "한 줄"),
    (dict(rationale="x" * 301), "300"),
    (dict(score=0.2, second_score=0.4), "second_score"),
    (dict(score=1.5), "score"),
    (dict(chat_id=cid(99)), "대상 목록"),
])
def test_origin_draft_rejects(kw, word):
    base = dict(chat_id=cid(0), origin="real", confidence="certain", rationale="00:01:00 합성 근거",
                targets=og.parse_targets(targets_obj(3)))
    base.update(kw)
    with pytest.raises(og.OriginError) as e:
        og.origin_draft(**base)
    assert word in str(e.value)


def test_head_draft_and_value_helpers():
    d = og.origin_draft(chat_id=cid(0), origin="virtual", confidence="probably", rationale="r",
                        method="viewer_id_match", head=("e" * 8, "i" * 8))
    assert d["entity_id"] == "e" * 8 and d["supersedes"] == "i" * 8
    assert og.is_auto(d) and og.origin_value(d) == "virtual"
    assert og.origin_value({"body": {"chat_id": cid(0), "origin": "real"}}) == og.INVALID


# =========================================================================== 일치도 (06 4-7)
def test_confusion_and_kappa_hand_checked():
    # A\B      real virtual und
    # real       5     1     0
    # virtual    0     3     1
    # und        0     0     2      → n=12, p_o=10/12, A 합 (6,4,2), B 합 (5,4,3)
    pairs = [("real", "real")] * 5 + [("real", "virtual")] + [("virtual", "virtual")] * 3 + \
            [("virtual", "undeterminable")] + [("undeterminable", "undeterminable")] * 2
    conf = og.confusion_from_pairs(pairs)
    assert conf["labels"] == ["real", "virtual", "undeterminable"]
    assert conf["rows"] == [[5, 1, 0], [0, 3, 1], [0, 0, 2]]
    assert conf["row_totals"] == [6, 4, 2] and conf["col_totals"] == [5, 4, 3] and conf["n"] == 12
    k = og.nominal_kappa(conf)
    p_e = (6 * 5 + 4 * 4 + 2 * 3) / 144
    assert k["p_o"] == pytest.approx(10 / 12)
    assert k["p_e"] == pytest.approx(p_e)
    assert k["kappa"] == pytest.approx((10 / 12 - p_e) / (1 - p_e))


def test_kappa_undefined_cases():
    assert og.nominal_kappa(og.confusion_from_pairs([]))["kappa"] is None
    assert og.nominal_kappa(og.confusion_from_pairs([("real", "real")] * 4))["kappa"] is None   # p_e = 1


def test_compare_origins_split_and_counts():
    t = og.parse_targets(targets_obj(8))
    a = [lab(cid(0), "real"), lab(cid(1), "real"), lab(cid(2), "virtual", "generation_log_match"),
         lab(cid(3), "virtual", "generation_log_match"), lab(cid(4), "undeterminable", "none"),
         lab(cid(5), "real"), lab(cid(50), "real"),                     # 대상 밖
         {"kind": "origin", "session_id": SID, "body": {"chat_id": cid(6), "origin": "real"}}]   # 형식 틀림
    b = [lab(cid(0), "real", sid=SID_B), lab(cid(1), "virtual", sid=SID_B),
         lab(cid(2), "virtual", "generation_log_match", sid=SID_B), lab(cid(3), "real", sid=SID_B),
         lab(cid(4), "real", sid=SID_B), lab(cid(7), "real", sid=SID_B)]
    r = og.compare_origins(a, b, targets=t)
    assert r["pairs"] == 5 and r["only_a"] == 1 and r["only_b"] == 1
    assert r["invalid_a"] == 1 and r["out_of_targets_a"] == 1 and r["targets"] == 8
    assert r["all"]["n"] == 5 and r["human"]["n"] == 3 and r["auto"]["n"] == 2
    assert r["auto_pairs"] == {"both": 1, "one_side": 1}
    assert r["undeterminable"] == {"both": 0, "a_only": 1, "b_only": 0}
    assert r["human"]["confusion"]["rows"] == [[1, 1, 0], [0, 0, 0], [1, 0, 0]]
    assert [d["chat_id"] for d in r["disagreements"]] == [cid(1), cid(3), cid(4)]
    assert r["disagreements"][1]["auto"] is True
    assert r["by_origin_a"] == {"real": 3, "virtual": 2, "undeterminable": 1}
    assert og.LOW_AGREEMENT_NOTE in r["notes"]
    # 근거·본문은 결과에 없다
    assert "합성 근거" not in json.dumps(r, ensure_ascii=False)


def test_compare_origins_duplicate_entity_dropped():
    a = [lab(cid(0), "real"), lab(cid(0), "virtual"), lab(cid(1), "real")]
    b = [lab(cid(0), "real", sid=SID_B), lab(cid(1), "real", sid=SID_B)]
    r = og.compare_origins(a, b)
    assert r["duplicate_a"] == 1 and r["pairs"] == 1 and r["targets"] is None


def test_progress_counts():
    t = og.parse_targets(targets_obj(4))
    heads, _ = og.heads_by_chat([lab(cid(0), "real"), lab(cid(1), "virtual", "generation_log_match"),
                                 lab(cid(9), "real"), lab(cid(2), "real", sid=SID_B)], session_id=SID)
    p = og.progress(t, heads)
    assert (p.done, p.total, p.auto, p.out_of_targets) == (2, 4, 1, 1)
    assert p.by_origin == {"real": 1, "virtual": 1, "undeterminable": 0}


def test_core_has_no_forbidden_imports():
    src = open(os.path.join(ROOT, "app", "core", "origin.py"), encoding="utf-8").read()
    for bad in ("PySide6", "import os", "import random", "import time", "datetime", "open("):
        assert bad not in src, bad


# =========================================================================== 화면
qt = pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def synth_messages(n=8):
    return [{"chat_id": cid(i), "t_ms": 60_000 + i * 5_000, "kind": "text", "viewer_id": "vw_abcdefghijk" + "a",
             "is_anonymous": False, "text": f"o{i}", "engine_grade": SECRET, "nickname": SECRET,
             "reaction": SECRET} for i in range(n)]


class FakeSave:
    def __init__(self):
        self.drafts = []

    def __call__(self, d):
        self.drafts.append(d)
        rec = {"kind": "origin", "session_id": SID, "entity_id": d.get("entity_id") or str(uuid.uuid4()),
               "id": str(uuid.uuid4()), "supersedes": d.get("supersedes"), "body": d["body"]}
        return rec


def all_text(w):
    from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton
    out = []
    for c in w.findChildren(QLabel):
        out.append(c.text())
    for c in w.findChildren(QPushButton):
        out.append(c.text() + c.toolTip())
    for c in w.findChildren(QLineEdit):
        out.append(c.text() + c.placeholderText())
    for c in w.findChildren(QComboBox):
        out.extend(c.itemText(i) for i in range(c.count()))
    return "\n".join(out)


def make_view(qapp, kind="origin_review", labels=(), auto=None, n=4):
    from app.ui import origin_view as ov
    save = FakeSave()
    v = ov.OriginView(save, lambda: None)
    v.load_context(session_kind=kind, session_id=SID, targets=[targets_obj(n, auto=auto)],
                   messages=synth_messages(), labels=labels)
    return v, save


def test_view_one_by_one_flow(qapp):
    v, save = make_view(qapp)
    assert v.current_item().chat_id == cid(0)
    assert v.save_current() is None and "출처" in v.msg.text()          # 출처를 안 골랐다
    v.set_origin("real")
    assert v.save_current() is None and "확신도" in v.msg.text()
    v.set_confidence("certain")
    assert v.save_current() is None and "근거" in v.msg.text()
    v.rationale.setText("00:01:00 평범한 대화")
    rec = v.save_current()
    assert rec and save.drafts[-1]["body"]["origin"] == "real" and save.drafts[-1]["body"]["method"] == "manual"
    assert v.current_item().chat_id == cid(1)                            # 다음 대기로 간다
    # 판별 불가: 확신도 칸이 꺼지고 null 로 저장
    v.set_origin("undeterminable")
    assert not v.conf_btns["certain"].isEnabled()
    v.set_method("none")
    v.rationale.setText("생성 기록 빠진 시간대")
    v.save_current()
    assert save.drafts[-1]["body"]["confidence"] is None and save.drafts[-1]["body"]["method"] == "none"
    assert v.progress().done == 2 and "판정 2" in v.head.text()


def test_view_auto_hint_take_and_override(qapp):
    v, save = make_view(qapp, auto={cid(0): gen_hint("virtual", "g7")})
    assert "[자동]" in v.auto_line.text() and "가상" in v.auto_line.text() and v.auto_btn.isEnabled()
    assert v.take_auto() and v.origin() == "virtual" and v.method() == "generation_log_match"
    v.set_confidence("probably")
    v.save_current()
    b = save.drafts[-1]["body"]
    assert b["method"] == "generation_log_match" and b["generation_ref"]["gen_id"] == "g7" and b["score"] == 0.9
    # 다른 출처를 고르면 수동으로 돌아간다
    v.go(0)
    v.take_auto()
    v.set_origin("real")
    assert v.method() == "manual"
    # 자동 결과 없는 대상에서는 받기 단추가 없다
    v.go(1)
    assert not v.auto_btn.isEnabled() and not v.take_auto()
    # 생성 기록 대조는 화면 목록에 없다(자동 결과 받기로만)
    assert v.method_box.findData("generation_log_match") < 0


def test_view_reopens_existing_label_as_new_version(qapp):
    old = lab(cid(0), "virtual", conf="probably")
    other = lab(cid(1), "real", sid=SID_B)            # 다른 세션 라벨: 버린다
    v, save = make_view(qapp, labels=[old, other])
    assert v.state_of(cid(0)) == "judged" and v.state_of(cid(1)) == "pending"
    assert v.current_item().chat_id == cid(1)        # 첫 대기 대상에서 시작
    v.go(0)
    assert v.origin() == "virtual" and v.confidence() == "probably" and "판정함 (가상)" in v.item_line.text()
    v.set_origin("real")
    v.set_confidence("certain")
    v.save_current()
    d = save.drafts[-1]
    assert d["entity_id"] == old["entity_id"] and d["supersedes"] == old["id"]


@pytest.mark.parametrize("kind", ["primary", "relabel_same", "relabel_second", "adjudication", "analysis", None])
def test_view_blind_other_sessions(qapp, kind):
    v, save = make_view(qapp, kind=kind, labels=[lab(cid(0), "virtual")], auto={cid(0): gen_hint()})
    txt = all_text(v)
    assert v.targets() is None and v.current_item() is None
    assert "o0" not in txt and "가상" not in txt.replace("가상 Ctrl+2", "")
    v.set_origin("real")
    v.set_confidence("certain")
    v.rationale.setText("x")
    assert v.save_current() is None and save.drafts == []


def test_view_never_shows_hidden_keys(qapp):
    v, _ = make_view(qapp, auto={cid(0): gen_hint()})
    for i in range(4):
        v.go(i)
        assert SECRET not in all_text(v)
    assert set(next(iter(v._msgs.values()))) <= {"chat_id", "t_ms", "text", "viewer_id", "is_anonymous"}


def test_view_store_rejection_is_shown(qapp):
    from app.ui import origin_view as ov

    def bad(_):
        raise RuntimeError("읽기 전용")
    v = ov.OriginView(bad, lambda: None)
    v.load_context(session_kind="origin_review", session_id=SID, targets=[targets_obj(2)], messages=synth_messages())
    v.set_origin("real")
    v.set_confidence("certain")
    v.rationale.setText("r")
    assert v.save_current() is None and "저장하지 못했습니다" in v.msg.text()


def test_view_all_judged_signal_and_player(qapp):
    from app.ui import origin_view as ov

    class P:
        def __init__(self):
            self.calls = []

        def set_ab_loop(self, a, b):
            self.calls.append(("loop", a, b))

        def seek(self, ms):
            self.calls.append(("seek", ms))

        def play(self):
            self.calls.append(("play",))

        def position_ms(self):
            return 61_000

    p = P()
    save = FakeSave()
    v = ov.OriginView(save, lambda: None)
    got = []
    v.allJudged.connect(lambda: got.append(1))
    v.load_context(session_kind="origin_review", session_id=SID, targets=[targets_obj(2)], messages=synth_messages(),
                   player=p, to_file=lambda ms: ms + 1000, to_vod=lambda ms: ms - 1000)
    assert ("loop", 51_000, 91_000) in p.calls              # 60s − 10s ~ 60s + 30s, +1s 파일 오프셋
    v.insert_time()
    assert v.rationale.text().startswith("00:01:00")
    for _ in range(2):
        v.set_origin("real")
        v.set_confidence("certain")
        v.rationale.setText("r")
        v.save_current()
    assert got == [1]


def test_targets_file_roundtrip(tmp_path):
    from app.ui import origin_view as ov
    d = tmp_path / "origin"
    obj = targets_obj(3, auto={cid(2): gen_hint()})
    path = ov.write_targets_file(str(d), obj)
    assert os.path.basename(path) == obj["target_id"] + ".json"
    with pytest.raises(FileExistsError):
        ov.write_targets_file(str(d), obj)
    (d / "OT-20261021-zzzz.json").write_text("{bad", encoding="utf-8")     # 읽지 못하는 파일은 건너뛴다
    (d / "gen-20261021.jsonl").write_text("", encoding="utf-8")           # 대상 파일이 아니다
    ts = ov.list_targets(str(d))
    assert [t.target_id for t in ts] == [obj["target_id"]]
    with pytest.raises(ValueError):
        ov.targets_path(str(d), "../x")
    # 이름이 target_id 와 다르면 거부
    other = d / "OT-20261021-0000.json"
    other.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError):
        ov.read_targets_file(str(other))
