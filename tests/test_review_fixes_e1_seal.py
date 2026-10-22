"""E 첫 묶음 검토 수정의 재현 시험 — 봉인 해제·일치도 보고서 쪽 (06 6-2·6-6 10번·7-1·7-2, 08 D-15 7).

- 채팅 파일이 없거나(또는 읽을 줄이 없거나) 판정이 모두 없는 chat_id 면 해제가 기록을 쓰지 않고 멈춘다(해제는 한 번뿐).
- 일치도 보고서도 계산된 노출(계획 취소 뒤 만든 재라벨)을 view_exposed 로 친다. 봉인 폴더는 열지 않는다.

합성 프로젝트(tests.test_agreement.proj)와 합성 엔진 결과만 쓴다.
"""
import os

import pytest

from app import agreement_report as ar
from app import jsonl, seal
from tests.test_agreement import MID_1, NOW, SID_A, SID_B, _lock, proj  # noqa: F401
from tests.test_seal import data_of, result_doc


def _pid(root):
    from app.project import open_project
    return open_project(root).project_id


def _write_unseal(root, at, run_id="run-synth-x"):
    d = seal.project_ai_dir(root, _pid(root))
    os.makedirs(d, exist_ok=True)
    jsonl.append(os.path.join(d, seal.SEAL_FILE), {"v": 1, "seq": 1, "kind": seal.K_UNSEALED, "at": at,
                                                   "sha256": "0" * 64, "run_id": run_id})


def _unseal_records(root):
    p = os.path.join(seal.project_ai_dir(root, _pid(root)), seal.SEAL_FILE)
    return [r for r in jsonl.read(p).records if r.get("kind") == seal.K_UNSEALED]


def test_unseal_refuses_without_chat_file_and_can_retry(proj):  # noqa: F811
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01")
    msgs = os.path.join(root, "chat", "messages.jsonl")
    os.replace(msgs, msgs + ".away")
    with pytest.raises(seal.SealError) as ei:
        seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험")
    assert "messages.jsonl" in str(ei.value)
    assert _unseal_records(root) == []
    assert not os.path.isdir(os.path.join(seal.project_ai_dir(root, _pid(root)), seal.RUNS_DIR))
    os.replace(msgs + ".away", msgs)
    res = seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험")
    assert len(seal.read_run(root, res.run_id)["reactions"]) == 1


def test_unseal_refuses_chat_file_without_readable_lines(proj):  # noqa: F811
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01")
    with open(os.path.join(root, "chat", "messages.jsonl"), "wb") as f:
        f.write(b"{broken\n")
    with pytest.raises(seal.SealError):
        seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험")
    assert _unseal_records(root) == []


def test_unseal_refuses_when_every_chat_id_unknown(proj):  # noqa: F811
    root = proj["root"]
    doc = result_doc(reactions=[{"chat_id": "c" + "f" * 20, "result": "unconfirmed", "grade": None}])
    e = seal.seal_bytes(root, data_of(doc), source="file", by="L01")
    with pytest.raises(seal.SealError) as ei:
        seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험")
    assert "chat_id" in str(ei.value)
    assert _unseal_records(root) == []


def test_unseal_events_reads_records_only(proj):  # noqa: F811
    root = proj["root"]
    assert seal.unseal_events(root) == ([], [])
    _write_unseal(root, "2026-10-21T09:30:00.000+09:00")
    ev, probs = seal.unseal_events(root, reader_kind="analysis")
    assert probs == [] and [x["run_id"] for x in ev] == ["run-synth-x"]
    with pytest.raises(seal.SealBlindError):
        seal.unseal_events(root, reader_kind="primary")


def test_agreement_report_view_exposed_from_cancel_not_seal_folder(proj):  # noqa: F811
    from tests.test_review_fixes_e1 import _write_cancel
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)                                   # 둘 다 09:00 에 만들고 10:00 잠금
    _write_unseal(root, "2026-10-21T09:30:00.000+09:00")       # 사람끼리 보고서는 봉인 폴더를 보지 않는다
    rep0 = ar.build_report(root, SID_A, SID_B, now=NOW).report
    assert rep0["header"]["view_exposed_sessions"] == []
    _write_cancel(root, "RP-20261021-zz22", "2026-10-21T08:00:00+09:00")   # 재라벨(SID_B)은 취소 뒤에 만듦
    rep = ar.build_report(root, SID_A, SID_B, now=NOW)
    assert rep.report["header"]["view_exposed_sessions"] == [SID_B]
    assert any(SID_B in w and "노출" in w for w in rep.warnings)
