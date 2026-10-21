"""놓친 채팅 후보 (4단계) — app/core/missed.py(순수)와 app/ui/forms/missed_form.py(화면 없이). 합성 데이터만.

설계: 06 2-9·4-6, 03 5-5·6-10·12-3, 안내서 6장, 08 D-10 3(영상 밖은 막음)·D-12 5(재라벨 블록 안만).
"""
import copy
import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core import missed as mc  # noqa: E402
from app.core.validate import check as schema_check  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEG = "11111111-1111-4111-8111-111111111111"     # 합성 저스트채팅 구간 (0 ~ 600초)
SEG2 = "22222222-2222-4222-8222-222222222222"    # 합성 게임 구간 (600 ~ 900초)
SET1 = "S-11111111-d1-r1"
SET2 = "S-11111111-d1-r2"


def cid(n: int) -> str:
    return f"c{n:020x}"


def uid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def seg_rec(eid, a, z, cat="just_chatting"):
    return {"id": uid(0xA000 + a // 1000), "entity_id": eid, "body": {"start_ms": a, "end_ms": z, "category": cat}}


SEGMENTS = [seg_rec(SEG, 0, 600_000), seg_rec(SEG2, 600_000, 900_000, "game")]


def msg(n, t, kind="text"):
    return {"chat_id": cid(n), "t_ms": t, "kind": kind, "status": "normal"}


def manifest(set_id, items, rev=None):
    rev = rev if rev is not None else int(set_id.rsplit("r", 1)[1])
    return {"sample_set_id": set_id, "sample_rev": rev, "source_segment": {"entity_id": SEG},
            "items": [{"order": i + 1, "chat_id": c, "t_ms": t} for i, (c, t) in enumerate(items)]}


def reaction(n, result="unconfirmed", grade=None, set_id=SET1, order=1, entry="from_sample"):
    b = {"chat_id": cid(n), "entry": entry, "result": result, "grade": grade,
         "sample_ref": {"sample_set_id": set_id, "order": order} if entry == "from_sample" else None}
    return {"id": uid(0xB000 + n), "entity_id": uid(0xC000 + n), "kind": "reaction", "body": b}


# 합성 장면: 채팅 1~8 은 표본, 9 는 표본 밖
MESSAGES = [msg(1, 100_000), msg(2, 120_000), msg(3, 140_000), msg(4, 160_000), msg(5, 180_000),
            msg(6, 200_000), msg(7, 400_000), msg(8, 590_000), msg(9, 101_000)]
ITEMS = [(cid(i), m["t_ms"]) for i, m in zip(range(1, 9), MESSAGES)]
REACTIONS = [reaction(1, "unconfirmed"), reaction(2, "graded", "related_only"), reaction(3, "graded", "unclear"),
             reaction(4, "graded", "direct"), reaction(5, "graded", "likely"),
             reaction(7, "unconfirmed"), reaction(8, "unconfirmed")]          # 6 은 아직 판정 없음


def select(**kw):
    args = dict(manifests=[manifest(SET1, ITEMS)], reactions=REACTIONS, messages=MESSAGES, segments=SEGMENTS)
    args.update(kw)
    return mc.select_targets(args.pop("manifests"), args.pop("reactions"), args.pop("messages"),
                             args.pop("segments"), **args)


# ============================================================================ 순수 쪽
def test_target_state():
    assert mc.target_state({"result": "unconfirmed"}) == "unconfirmed"
    assert mc.target_state({"result": "graded", "grade": "related_only"}) == "related_only"
    assert mc.target_state({"result": "graded", "grade": "unclear"}) == "unclear"
    assert mc.target_state({"result": "graded", "grade": "direct"}) is None
    assert mc.target_state({"result": "graded", "grade": "likely"}) is None
    assert mc.target_state(None) is None


def test_select_targets_basic():
    sel = select()
    assert [t.chat_id for t in sel.targets] == [cid(1), cid(2), cid(3), cid(7), cid(8)]
    assert sel.counts[mc.SKIP_REACTED] == 2
    assert sel.counts[mc.SKIP_NOT_JUDGED] == 1
    assert sel.sample_total == 8
    t1 = sel.by_chat()[cid(1)]
    assert t1.state == "unconfirmed" and t1.sample_ref == {"sample_set_id": SET1, "order": 1}
    assert t1.segment_id == SEG and t1.cond1.value and t1.suggested_reason is None
    assert t1.reaction_entity_id == uid(0xC001)
    assert not t1.done


def test_select_uses_latest_rev_and_dedupes():
    old = manifest(SET1, ITEMS)
    new = manifest(SET2, ITEMS[:3])
    reacts = [reaction(i, "unconfirmed", set_id=SET2, order=i) for i in (1, 2, 3)]
    sel = select(manifests=[old, new], reactions=reacts)
    assert [t.chat_id for t in sel.targets] == [cid(1), cid(2), cid(3)]
    assert all(t.sample_ref["sample_set_id"] == SET2 for t in sel.targets)


def test_reaction_from_utterance_counts_as_judgment():
    """D-10 4: ② 반응 라벨이 그 표본 채팅의 판정이다."""
    sel = select(reactions=[reaction(6, "graded", "related_only", entry="from_utterance")])
    assert [t.chat_id for t in sel.targets] == [cid(6)]


def test_scope_blocks_only():
    """D-12 5: 재라벨 블록 안만."""
    sel = select(scope_ranges=[(0, 150_000)])
    assert [t.chat_id for t in sel.targets] == [cid(1), cid(2), cid(3)]
    assert sel.counts[mc.SKIP_OUT_OF_SCOPE] == 5


def test_scope_ranges_of():
    assert mc.scope_ranges_of({"type": "whole"}, "primary") is None
    assert mc.scope_ranges_of(None, "primary") is None
    assert mc.scope_ranges_of({"type": "blocks", "ranges": [[600_000, 1_200_000], [0, 600_000]]},
                              "relabel_second") == ((0, 1_200_000),)
    assert mc.scope_ranges_of({"type": "whole"}, "relabel_same") == ()
    assert mc.scope_ranges_of(None, "relabel_second") == ()
    sel = select(scope_ranges=mc.scope_ranges_of(None, "relabel_second"))
    assert sel.targets == ()


def test_out_of_media_excluded():
    """D-10 3: 7-2 확인 범위가 영상 밖이면 대상에서 뺀다. 같은 coverage 인자."""
    sel = select(coverage=[(0, 450_000)])
    ids = [t.chat_id for t in sel.targets]
    assert cid(8) not in ids and cid(7) in ids                  # 590초 + 30초 > 450초
    assert sel.counts[mc.SKIP_OUT_OF_MEDIA] == 1
    sel = select(coverage=[])                                   # 영상 없음 → 모두 영상 밖
    assert sel.targets == () and sel.counts[mc.SKIP_OUT_OF_MEDIA] == 5
    sel = select(coverage=None)                                 # 검사 안 함
    assert len(sel.targets) == 5


def test_samples_done_gate():
    """03 12-3: 원 구간에 ③ 완료 표시가 없으면 뺀다."""
    assert select(samples_done=set()).targets == ()
    assert len(select(samples_done={SEG}).targets) == 5


def test_cond1_buffer_and_outside():
    sel = select(buffers=[(95_000, 105_000)], segments=[seg_rec(SEG, 0, 300_000), seg_rec(SEG2, 300_000, 900_000, "game")])
    t = sel.by_chat()
    assert t[cid(1)].cond1 == mc.Cond1(False, True, True, SEG)
    assert t[cid(1)].suggested_reason == "A" and t[cid(1)].suggested_source == "auto_suggested"
    assert t[cid(7)].cond1.in_segment is False and t[cid(7)].segment_id is None
    assert t[cid(2)].cond1.value and t[cid(2)].suggested_reason is None


def test_cond2_aux_and_b_suggestion():
    msgs = [msg(1, 100_000)] + [msg(100 + i, 92_000 + i * 1000) for i in range(6)] \
        + [msg(200 + i, 101_000 + i * 500) for i in range(5)]
    ordered = mc.chat_times(msgs)
    thr = mc.MissedThresholds(visible_lines=3)
    aux = mc.cond2_aux(cid(1), 100_000, ordered, thr)
    assert aux.chat_rate_10s == 6                               # 92~97초, 10초 창 안
    assert aux.exposure_est_ms == 2_000                         # 셋째 뒷줄 = 102초
    assert aux.as_body(None)["visible_lines"] == 3
    c1 = mc.Cond1(True, True, False, SEG)
    no_vis = mc.cond2_aux(cid(1), 100_000, ordered, mc.MissedThresholds())
    assert no_vis.exposure_est_ms is None and no_vis.chat_rate_10s == 6
    assert mc.suggest_reason(c1, "text", no_vis, mc.MissedThresholds()) == (None, None)   # v0.1: 미정이라 제안 없음
    assert mc.suggest_reason(c1, "text", aux, thr) == ("B", "auto_suggested")             # 노출 2초 < 10초
    assert mc.suggest_reason(c1, "text", no_vis, mc.MissedThresholds(max_chat_rate_per_10s=5)) == ("B", "auto_suggested")
    assert mc.suggest_reason(c1, "text", no_vis, mc.MissedThresholds(max_chat_rate_per_10s=6)) == (None, None)
    assert mc.suggest_reason(c1, "donation", aux, thr) == ("F", "auto")


def test_thresholds_from_guide_v01():
    with open(os.path.join(ROOT, "assets", "guide-config.v0.1.json"), encoding="utf-8") as f:
        values = json.load(f)["values"]
    thr = mc.thresholds_from_guide(values)
    assert thr == mc.MissedThresholds(10_000, None, None, 120_000)
    assert mc.prior_window(100_000, thr) == (0, 220_000)
    assert mc.thresholds_from_guide(None) == mc.MissedThresholds()


def _target(n=1, **kw):
    return select(**kw).by_chat()[cid(n)]


def _ok_answers(**kw):
    a = dict(cond2=True, cond3=False, cond4=None, reason_code="C", check_question=False, confidence="certain",
             rationale="00:01:40 합성 인사 채팅")
    a.update(kw)
    return mc.Answers(**a)


def _validate(d, t, **kw):
    return mc.validate_draft(d, chat_t_ms=t.t_ms, chat_kind=t.chat_kind, target=t, **kw)


def test_build_draft_not_candidate():
    t = _target()
    d = mc.build_draft(t, _ok_answers())
    assert d["kind"] == "missed_candidate" and d["entity_id"] is None and d["supersedes"] is None
    b = d["body"]
    assert b["is_candidate"] is False and b["reason_code"] == "C" and b["reason_code_source"] == "manual"
    assert b["scope"] == "sample" and b["sample_ref"] == {"sample_set_id": SET1, "order": 1}
    assert b["cond1"] == {"value": True, "in_segment": True, "in_buffer": False}
    assert b["cond2"]["value"] is True and b["cond2"]["chat_rate_10s"] == 0
    c = _validate(d, t, coverage=[(0, 900_000)])
    assert c.ok, c.errors
    assert c.warnings == ()


def test_build_draft_candidate_clears_reason():
    t = _target()
    d = mc.build_draft(t, _ok_answers(cond3=True, cond4=True, reason_code="C", check_question=True))
    assert d["body"]["is_candidate"] is True and d["body"]["reason_code"] is None
    assert d["body"]["reason_code_source"] is None
    assert _validate(d, t).ok


def test_reason_required_when_not_candidate():
    t = _target()
    c = _validate(mc.build_draft(t, _ok_answers(reason_code=None)), t)
    assert any("사유 코드" in e for e in c.errors)


@pytest.mark.parametrize("code,field", [("B", "cond2"), ("C", "cond3"), ("E", "cond3"), ("D", "cond4")])
def test_reason_contradicts_condition(code, field):
    t = _target()
    kw = dict(cond2=None, cond3=None, cond4=None, reason_code=code)
    kw[field] = True
    c = _validate(mc.build_draft(t, _ok_answers(**kw)), t)
    assert any(f"사유 {code}" in e for e in c.errors)
    kw[field] = False
    assert _validate(mc.build_draft(t, _ok_answers(**kw)), t).ok


def test_reason_g_always_allowed():
    t = _target()
    assert _validate(mc.build_draft(t, _ok_answers(cond2=True, cond3=True, cond4=None, reason_code="G")), t).ok


def test_reason_a_only_when_cond1_fails():
    t = _target()
    c = _validate(mc.build_draft(t, _ok_answers(reason_code="A")), t)
    assert any("A(구간 밖)" in e for e in c.errors)
    tb = _target(buffers=[(95_000, 105_000)])
    d = mc.build_draft(tb, _ok_answers(cond2=None, cond3=None, reason_code="A"))
    assert d["body"]["reason_code_source"] == "auto_suggested" and d["body"]["is_candidate"] is False
    assert _validate(d, tb).ok
    # ① 실패면 나머지가 모두 예여도 후보가 아니다
    d = mc.build_draft(tb, _ok_answers(cond2=True, cond3=True, cond4=True, reason_code="A"))
    assert d["body"]["is_candidate"] is False and _validate(d, tb).ok
    # 제안과 다른 코드를 고르면 manual
    d = mc.build_draft(tb, _ok_answers(cond2=None, cond3=False, reason_code="C"))
    assert d["body"]["reason_code_source"] == "manual" and _validate(d, tb).ok


def test_reason_f_forced_for_non_text():
    msgs = [m if m["chat_id"] != cid(1) else msg(1, 100_000, "donation") for m in MESSAGES]
    t = select(messages=msgs).by_chat()[cid(1)]
    assert t.suggested_reason == "F" and t.suggested_source == "auto"
    d = mc.build_draft(t, _ok_answers(cond2=True, cond3=True, cond4=True, reason_code="C"))
    assert d["body"]["reason_code"] == "F" and d["body"]["reason_code_source"] == "auto"
    assert d["body"]["is_candidate"] is False
    assert _validate(d, t).ok
    t_text = _target()
    bad = mc.build_draft(t_text, _ok_answers(reason_code="F"))
    assert any("F 는" in e for e in _validate(bad, t_text).errors)


def test_validate_media_and_scope():
    """D-10 3·D-12 5: 영상 밖·블록 밖은 저장을 막는다."""
    t = _target(8)
    d = mc.build_draft(t, _ok_answers())
    assert any("영상 밖" in e for e in _validate(d, t, coverage=[(0, 600_000)]).errors)
    assert any("영상이 연결되지 않았습니다" in e for e in _validate(d, t, coverage=[]).errors)
    assert _validate(d, t, coverage=[(0, 700_000)]).ok
    assert any("블록" in e for e in _validate(d, t, scope_ranges=[(0, 300_000)]).errors)
    assert any("블록" in e for e in _validate(d, t, scope_ranges=()).errors)


def test_validate_confidence_rationale():
    t = _target()
    c = _validate(mc.build_draft(t, _ok_answers(confidence=None, rationale="  ")), t)
    assert any("확신도" in e for e in c.errors) and any("근거" in e for e in c.errors)
    c = _validate(mc.build_draft(t, _ok_answers(rationale="시각 없는 합성 근거")), t)
    assert c.ok and [w for w, _ in c.warnings] == ["rationale_without_time"]


def test_validate_rejects_tampered_cond1():
    t = _target()
    d = mc.build_draft(t, _ok_answers())
    d2 = copy.deepcopy(d)
    d2["body"]["cond1"] = {"value": False, "in_segment": False, "in_buffer": False}
    d2["body"]["segment_id"] = None
    assert any("① 은 앱이" in e for e in _validate(d2, t).errors)
    d3 = copy.deepcopy(d)
    d3["body"]["is_candidate"] = True
    assert any("후보 여부" in e for e in _validate(d3, t).errors)
    d4 = copy.deepcopy(d)
    d4["body"]["sample_ref"] = None
    assert any("sample_ref" in e for e in mc.validate_draft(d4, chat_t_ms=t.t_ms, chat_kind="text").errors)


def test_existing_record_makes_new_version():
    rec = {"id": uid(0xD001), "entity_id": uid(0xE001), "kind": "missed_candidate", "seq": 5,
           "body": mc.build_draft(_target(), _ok_answers())["body"]}
    sel = select(missed_records=[rec])
    t = sel.by_chat()[cid(1)]
    assert t.done and sel.done == 1
    d = mc.build_draft(t, _ok_answers(reason_code="G"))
    assert d["entity_id"] == uid(0xE001) and d["supersedes"] == uid(0xD001)
    assert mc.answers_from_body(rec["body"]) == _ok_answers()


def _schema():
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        root = json.load(f)
    spec = dict(root["$defs"]["missedCandidateBody"])
    spec.pop("allOf", None)            # 해석기가 if/then 을 모른다 → 아래에서 손으로 본다
    spec.pop("description", None)
    for p in spec["properties"].values():
        p.pop("description", None)
    return root, spec


def _strip_desc(o):
    if isinstance(o, dict):
        return {k: _strip_desc(v) for k, v in o.items() if k != "description"}
    if isinstance(o, list):
        return [_strip_desc(v) for v in o]
    return o


@pytest.mark.parametrize("answers,buffers", [
    (dict(), None),
    (dict(cond3=True, cond4=True, check_question=True), None),
    (dict(cond2=None, cond3=None, reason_code="A"), [(95_000, 105_000)]),
])
def test_body_matches_schema(answers, buffers):
    root, spec = _schema()
    root, spec = _strip_desc(root), _strip_desc(spec)
    t = _target(buffers=buffers or [])
    b = mc.build_draft(t, _ok_answers(**answers))["body"]
    assert schema_check(b, spec, root) == []
    if b["is_candidate"]:                                       # allOf if/then
        assert b["reason_code"] is None and b["cond1"]["value"] and b["cond2"]["value"] and b["cond3"] and b["cond4"]
    else:
        assert b["reason_code"] in root["$defs"]["reasonCode"]["enum"]


def test_reason_codes_match_schema():
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        root = json.load(f)
    assert tuple(root["$defs"]["reasonCode"]["enum"]) == mc.REASON_CODES
    assert set(mc.MANUAL_REASON_CODES) | set(mc.AUTO_REASON_CODES) == set(mc.REASON_CODES)
    src = root["$defs"]["missedCandidateBody"]["properties"]["reason_code_source"]["enum"]
    assert set(src) == {"auto", "auto_suggested", "manual", None}


def test_core_has_no_qt_or_io():
    import ast
    with open(os.path.join(ROOT, "app", "core", "missed.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
            names.add("open()")
    assert not names & {"PySide6", "os", "time", "random", "datetime", "pathlib", "open()", "io", "uuid"}


# ============================================================================ 폼 (화면 없이)
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Sink:
    def __init__(self, fail=False):
        self.drafts, self.ops, self.cancels, self.n, self.fail = [], [], 0, 0, fail

    def save(self, draft):
        if self.fail:
            raise RuntimeError("합성 실패")
        self.drafts.append(draft)
        self.n += 1
        return {"id": uid(0xF000 + self.n), "entity_id": draft["entity_id"] or uid(0xF100 + self.n),
                "kind": draft["kind"], "supersedes": draft["supersedes"], "body": draft["body"]}

    def cancel(self):
        self.cancels += 1

    def op(self, kind, body):
        self.ops.append((kind, body))


def make_form(qapp, sink=None, **ctx):
    from app.ui.forms.missed_form import MissedForm
    sink = sink or Sink()
    f = MissedForm(sink.save, sink.cancel, ops=sink.op)
    args = dict(selection=select(), chat_texts={cid(i): f"합성 채팅 {i}" for i in range(1, 10)},
                media_coverage=[(0, 900_000)], scope_ranges=None, position_fn=lambda: 100_000)
    args.update(ctx)
    f.load_context(**args)
    return f, sink


def test_form_lists_targets_and_saves(qapp):
    f, sink = make_form(qapp)
    seen = []
    f.targetChanged.connect(lambda c, t: seen.append((c, t)))
    assert f.list.count() == 5 and f.current_chat_id() == cid(1)
    assert "합성 채팅 1" in f.lbl_chat.text()
    assert not f.btn_save.isEnabled()                           # 확신도·근거·사유 없음
    f.set_answers(_ok_answers())
    assert f.btn_save.isEnabled(), f.lbl_errors.text()
    rec = f.save()
    assert rec is not None and len(sink.drafts) == 1
    assert sink.drafts[0]["body"]["reason_code"] == "C" and sink.drafts[0]["entity_id"] is None
    assert f.done_count() == 1 and f.list.item(0).text().startswith("✓")
    # 다시 저장하면 같은 대상의 새 판
    f.set_answers(_ok_answers(reason_code="G"))
    f.save()
    assert sink.drafts[1]["entity_id"] == rec["entity_id"] and sink.drafts[1]["supersedes"] == rec["id"]
    assert f.select_relative(1) and seen[-1] == (cid(2), 120_000)


def test_form_candidate_disables_reason(qapp):
    f, sink = make_form(qapp)
    f.set_answers(_ok_answers(cond3=True, cond4=True, reason_code=None, check_question=True))
    assert not f.reason.isEnabled() and "후보 (네 조건" in f.lbl_result.text()
    rec = f.save()
    assert rec["body"]["is_candidate"] is True and rec["body"]["reason_code"] is None


def test_form_blocks_out_of_media(qapp):
    """D-10 3: 영상을 바꾼 뒤 coverage 만 다시 받아도 저장을 막는다."""
    f, sink = make_form(qapp)
    f.set_answers(_ok_answers())
    f.load_context(media_coverage=[])
    assert not f.btn_save.isEnabled() and "영상이 연결되지 않았습니다" in f.lbl_errors.text()
    assert f.save() is None and sink.drafts == []
    f.load_context(media_coverage=None)
    assert f.save() is not None


def test_form_blocks_out_of_scope(qapp):
    f, sink = make_form(qapp, scope_ranges=[(300_000, 600_000)])
    f.set_answers(_ok_answers())
    assert f.save() is None and sink.drafts == []
    assert "블록" in f.lbl_status.text()


def test_form_auto_reason_suggestion_and_disabled_items(qapp):
    f, _ = make_form(qapp, selection=select(buffers=[(95_000, 105_000)]))
    assert f.reason.currentData() == "A"                        # ① 실패 → A 제안
    model = f.reason.model()
    codes = {f.reason.itemData(i): model.item(i).isEnabled() for i in range(f.reason.count())}
    assert codes["A"] and not codes["F"] and codes["C"]
    f.select_chat(cid(2))
    codes = {f.reason.itemData(i): model.item(i).isEnabled() for i in range(f.reason.count())}
    assert not codes["A"] and not codes["F"]
    assert f.reason.currentData() is None


def test_form_warning_written_and_read_only(qapp):
    f, sink = make_form(qapp)
    f.set_answers(_ok_answers(rationale="시각 없는 합성 근거"))
    assert f.save() is not None
    assert sink.ops and sink.ops[0][1]["code"] == "rationale_without_time"
    f.load_context(read_only=True)
    assert not f.btn_save.isEnabled() and f.save() is None


def test_form_save_failure_and_cancel(qapp):
    f, sink = make_form(qapp, sink=Sink(fail=True))
    f.set_answers(_ok_answers())
    assert f.has_unsaved()
    assert f.save() is None and "저장 실패" in f.lbl_status.text()
    f.cancel()
    assert sink.cancels == 1 and not f.has_unsaved() and f.answers().confidence is None


def test_form_shortcut_helpers(qapp):
    f, _ = make_form(qapp)
    f.set_confidence("probably")
    assert f.answers().confidence == "probably"
    f.insert_time()
    assert f.rationale.text().startswith("00:01:40")
    f, _ = make_form(qapp, selection=mc.Selection((), {}))
    assert f.current_target() is None and not f.btn_save.isEnabled() and f.save() is None


def test_form_keeps_selection_on_reload(qapp):
    f, _ = make_form(qapp)
    f.select_chat(cid(3))
    f.load_context(selection=select())
    assert f.current_chat_id() == cid(3)
