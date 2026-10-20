"""세션 격리·블라인드 정책 고정 (docs/design/03-data-model.md 12-1, 05 S-11, 08 D-08)."""
import pytest

from app.core import visibility as V

LABELING = (V.PRIMARY, V.RELABEL_SAME, V.RELABEL_SECOND)
ALL_PHASES = V.PHASES + (None, "unknown_phase")

# 03 12-1 표를 그대로 옮긴 기대값 (열 = 세션 종류). 바꾸려면 03 문서와 같이 바꾼다.
EXPECTED_12_1 = {
    "open_lock":     {V.PRIMARY: V.HOLD, V.RELABEL_SAME: V.HOLD, V.RELABEL_SECOND: V.HOLD,
                      V.ADJUDICATION: V.HOLD, V.ORIGIN_REVIEW: V.HOLD},
    "own_labels":    {V.PRIMARY: V.RW, V.RELABEL_SAME: V.RW, V.RELABEL_SECOND: V.RW,
                      V.ADJUDICATION: V.RW, V.ORIGIN_REVIEW: V.RW},
    "other_labels":  {V.PRIMARY: V.NONE, V.RELABEL_SAME: V.NONE, V.RELABEL_SECOND: V.NONE,
                      V.ADJUDICATION: V.LOCKED_ONLY, V.ORIGIN_REVIEW: V.NONE},
    "project_json":  {V.PRIMARY: V.READ_CAL_MEDIA, V.RELABEL_SAME: V.READ, V.RELABEL_SECOND: V.READ,
                      V.ADJUDICATION: V.READ, V.ORIGIN_REVIEW: V.READ},
    "messages":      {k: V.READ for k in (V.PRIMARY, V.RELABEL_SAME, V.RELABEL_SECOND, V.ADJUDICATION,
                                          V.ORIGIN_REVIEW)},
    "samples":       {V.PRIMARY: V.READ_DRAW, V.RELABEL_SAME: V.NONE, V.RELABEL_SECOND: V.NONE,
                      V.ADJUDICATION: V.READ, V.ORIGIN_REVIEW: V.NONE},
    "relabel_plans": {V.PRIMARY: V.NONE, V.RELABEL_SAME: V.OWN, V.RELABEL_SECOND: V.OWN,
                      V.ADJUDICATION: V.READ, V.ORIGIN_REVIEW: V.NONE},
    "identities":    {V.PRIMARY: V.TOGGLE, V.RELABEL_SAME: V.TOGGLE, V.RELABEL_SECOND: V.TOGGLE,
                      V.ADJUDICATION: V.TOGGLE, V.ORIGIN_REVIEW: V.NONE},
    "derived_asr":   {V.PRIMARY: V.TOGGLE, V.RELABEL_SAME: V.TOGGLE, V.RELABEL_SECOND: V.TOGGLE,
                      V.ADJUDICATION: V.READ, V.ORIGIN_REVIEW: V.NONE},
    "origin":        {V.PRIMARY: V.NONE, V.RELABEL_SAME: V.NONE, V.RELABEL_SECOND: V.NONE,
                      V.ADJUDICATION: V.READ, V.ORIGIN_REVIEW: V.RW},
    "ai_sealed":     {V.PRIMARY: V.NONE, V.RELABEL_SAME: V.NONE, V.RELABEL_SECOND: V.NONE,
                      V.ADJUDICATION: V.SEAL_ONLY, V.ORIGIN_REVIEW: V.NONE},
    "reports":       {V.PRIMARY: V.NONE, V.RELABEL_SAME: V.NONE, V.RELABEL_SECOND: V.NONE,
                      V.ADJUDICATION: V.RW, V.ORIGIN_REVIEW: V.NONE},
}


@pytest.mark.parametrize("field", sorted(EXPECTED_12_1))
def test_table_matches_03_12_1(field):
    for kind, want in EXPECTED_12_1[field].items():
        assert V.files_to_open(kind, V.REACTIONS).access(field) == want, (field, kind)


# --------------------------------------------------------------------------- 블라인드 핵심 규칙
BLIND_FILES = ("other_labels", "engine_results", "ai_sealed", "origin", "reports")
BLIND_ITEMS = (V.OTHER_SESSION_LABELS, V.AI_RESULTS, V.ENGINE_RESULTS, V.ORIGIN_INFO)


@pytest.mark.parametrize("kind", LABELING)
@pytest.mark.parametrize("phase", ALL_PHASES)
def test_labeling_sessions_never_see_engine_or_other_sessions(kind, phase):
    fs = V.files_to_open(kind, phase)
    for f in BLIND_FILES:
        assert fs.access(f) == V.NONE, f
        assert not fs.can_read(f) and not fs.can_toggle(f) and not fs.can_write(f)
    assert not fs.may_read_other_session(locked=True)
    assert not fs.may_read_other_session(locked=False)
    for item in BLIND_ITEMS:
        assert V.show_policy(item, phase, kind) == V.NEVER, item
        assert not V.can_show(item, phase, kind) and not V.can_reveal(item, phase, kind)


@pytest.mark.parametrize("kind", LABELING)
def test_labeling_toggles_are_hidden_by_default(kind):
    for item in (V.NICKNAME_RAW, V.ASR_TEXT, V.RAW_CHAT_PAGE):
        assert V.show_policy(item, V.REACTIONS, kind) == V.ON_TOGGLE
        assert not V.can_show(item, V.REACTIONS, kind)       # 기본 숨김
        assert V.can_reveal(item, V.REACTIONS, kind)         # 켜면 view.exposed 를 남기고 보임


def test_relabel_sessions_do_not_open_samples():
    for kind in (V.RELABEL_SAME, V.RELABEL_SECOND):
        fs = V.files_to_open(kind, V.SAMPLES)
        assert not fs.can_read("samples") and fs.relabel_plans == V.OWN


def test_origin_review_sees_no_nicknames_or_asr():
    for item in (V.NICKNAME_RAW, V.ASR_TEXT, V.RAW_CHAT_PAGE, V.OTHER_SESSION_LABELS, V.AI_RESULTS):
        assert V.show_policy(item, V.ORIGIN, V.ORIGIN_REVIEW) == V.NEVER
    assert V.can_show(V.ORIGIN_INFO, V.ORIGIN, V.ORIGIN_REVIEW)
    assert V.files_to_open(V.ORIGIN_REVIEW).can_write("origin")


def test_adjudication_ai_only_after_unseal():
    for ph in V.PHASES:
        if ph == V.COMPARISON:
            continue
        assert V.files_to_open(V.ADJUDICATION, ph).ai_sealed == V.SEAL_ONLY
        assert not V.can_show(V.AI_RESULTS, ph, V.ADJUDICATION)
    assert V.files_to_open(V.ADJUDICATION, V.COMPARISON).ai_sealed == V.READ
    assert V.can_show(V.AI_RESULTS, V.COMPARISON, V.ADJUDICATION)
    fs = V.files_to_open(V.ADJUDICATION, V.REACTIONS)
    assert fs.may_read_other_session(locked=True) and not fs.may_read_other_session(locked=False)
    assert not V.can_show(V.ENGINE_RESULTS, V.COMPARISON, V.ADJUDICATION)


# --------------------------------------------------------------------------- 분석 세션 (D-08 새 값 후보)
def test_analysis_session_sees_engine_and_labels_but_writes_no_labels():
    fs = V.files_to_open(V.ANALYSIS, V.REACTIONS)
    assert fs.can_read("engine_results") and fs.can_read("other_labels") and fs.can_read("derived_asr")
    assert fs.may_read_other_session(locked=False)
    assert fs.writable_label_kinds == frozenset()
    for k in ("segment", "reaction", "utterance", "retract", "adjudication", "origin"):
        assert not fs.can_write_label(k)
    assert fs.own_labels == V.OPS_ONLY
    assert fs.ai_sealed == V.NONE                    # 봉인된 평가 결과는 분석 화면도 열지 않는다
    assert not V.can_show(V.AI_RESULTS, V.COMPARISON, V.ANALYSIS)
    assert V.can_show(V.ENGINE_RESULTS, V.REACTIONS, V.ANALYSIS)
    assert V.can_show(V.OTHER_SESSION_LABELS, V.REACTIONS, V.ANALYSIS)
    assert not V.can_show(V.NICKNAME_RAW, V.REACTIONS, V.ANALYSIS)   # 원 닉네임은 여전히 토글
    assert not V.is_labeling_session(V.ANALYSIS)
    assert V.SESSION_CODES[V.ANALYSIS] == "AN"


def test_only_analysis_reads_engine_results():
    for kind in V.SESSION_KINDS:
        for ph in ALL_PHASES:
            readable = V.files_to_open(kind, ph).can_read("engine_results")
            assert readable == (kind == V.ANALYSIS), (kind, ph)


# --------------------------------------------------------------------------- 기본은 막힘
@pytest.mark.parametrize("kind", ["", "Primary", "unknown", None, 3, "analysis "])
def test_unknown_session_kind_is_blocked(kind):
    fs = V.files_to_open(kind, V.REACTIONS)
    assert fs == V.BLOCKED
    for name in V.FileScope.__dataclass_fields__:
        if name != "writable_label_kinds":
            assert fs.access(name) == V.NONE
    for item in V.ITEMS:
        assert V.show_policy(item, V.REACTIONS, kind) == V.NEVER


def test_unknown_item_and_field_never():
    for kind in V.SESSION_KINDS:
        assert V.show_policy("whatever", V.REACTIONS, kind) == V.NEVER
        assert not V.can_reveal(None, V.REACTIONS, kind)
        fs = V.files_to_open(kind)
        assert fs.access("not_a_field") == V.NONE and fs.access("writable_label_kinds") == V.NONE


def test_labeling_writable_kinds():
    assert V.files_to_open(V.PRIMARY).can_write_label("reaction")
    assert not V.files_to_open(V.PRIMARY).can_write_label("origin")
    assert not V.files_to_open(V.PRIMARY).can_write_label("adjudication")
    assert V.files_to_open(V.ADJUDICATION).can_write_label("adjudication")
    assert not V.files_to_open(V.ADJUDICATION).can_write_label("reaction")
    assert V.files_to_open(V.ORIGIN_REVIEW).writable_label_kinds == frozenset({"origin", "retract"})


def test_every_access_value_known_and_scope_frozen():
    for kind in V.SESSION_KINDS:
        for ph in ALL_PHASES:
            fs = V.files_to_open(kind, ph)
            for name in V.FileScope.__dataclass_fields__:
                if name != "writable_label_kinds":
                    assert fs.access(name) in V.ACCESS_VALUES
    with pytest.raises(Exception):
        V.files_to_open(V.PRIMARY).other_labels = V.READ   # 표를 밖에서 바꿀 수 없다
