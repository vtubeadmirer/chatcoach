"""시간 정렬(보정) 확정 (08 D-32): app.calibration · 새 방송 진행기의 자동 위치 추정과 판정 도장 · 확정 화면 · 메인 창 흐름.

합성 데이터만(tmp_path), 네트워크·엔진 없음 — 진행기의 바꿔 끼우는 함수는 tests.test_new_broadcast.Fakes 를 넓혀 쓴다
(find_anchors --locate/--list 도 가짜). 실제 프로젝트·시범 폴더는 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import calibration as calib  # noqa: E402
from app import engine_run as er  # noqa: E402
from app import project as project_mod  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from tests import test_new_broadcast as nb  # noqa: E402
from tests.test_new_broadcast import _cid, _engine_py, _models, _req, _video, qapp, win  # noqa: E402,F401

VNO = nb.VNO
CLIP_FS = 3_600_000          # 합성 정답: 영상 0초 = VOD 1:00:00
LAG = 6000                   # 합성 후보의 읽기 지연


# ---------------------------------------------------------------------------- 가짜
class CalFakes(nb.Fakes):
    """VOD 길이가 영상과 달라(위치 모름) 자동 위치 추정을 타게 하고, find_anchors --locate/--list 에 합성 후보를 낸다.
    후보 i 는 채팅 _cid(i)(시각 1_000 + i*STEP) 를 영상 시각 (채팅 − CLIP_FS + LAG)에 읽은 것. bad 는 엉뚱한 후보 수."""

    def __init__(self, tmp_path, *, vod_ms=nb.VIDEO_MS * 20, located=True, consistent=4, bad=1):
        super().__init__(tmp_path)
        self.vod_ms, self.located, self.consistent, self.bad = vod_ms, located, consistent, bad
        self.chat_base = CLIP_FS                     # 채팅 시각을 클립 안에 둔다(collect 를 덮음)

    def info(self, vno):
        self.calls.append(("info", vno))
        return {"duration_ms": self.vod_ms, "title": "합성 방송 제목"}

    def collect(self, vno, root, salt, **kw):
        out = super().collect(vno, root, salt, **kw)
        p = os.path.join(root, "chat", "messages.jsonl")
        rows = [json.loads(x) for x in open(p, encoding="utf-8")]
        with open(p, "w", encoding="utf-8") as f:
            for m in rows:
                m["t_ms"] += self.chat_base
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        return out

    def rows(self, fs):
        out = []
        for i in range(1, 1 + max(self.consistent, 0)):
            chat = self.chat_base + 1_000 + i * nb.STEP
            v = chat - CLIP_FS + LAG + (i % 3) * 300
            out.append({"chat_id": _cid(i), "chat_ms": chat, "video_ms": v, "video_end_ms": v + 2500, "basis": "text",
                        "score": 0.9, "diff_ms": v - (chat - fs)})
        for k in range(self.bad):
            i = 40 + k
            chat = self.chat_base + 1_000 + i * nb.STEP
            v = chat - CLIP_FS + 60_000
            out.append({"chat_id": _cid(i), "chat_ms": chat, "video_ms": v, "video_end_ms": None, "basis": "name?",
                        "score": 0.4, "diff_ms": v - (chat - fs)})
        return out

    def run_process(self, cmd, *, cancelled=None, tick=None, timeout_s=None, env=None, cwd=None):
        cmd = list(cmd)
        if any(c.endswith("find_anchors.py") for c in cmd) and ("--locate" in cmd or "--list" in cmd):
            self.cmds.append(cmd)
            self.envs.append(dict(env or {}))
            if "--locate" in cmd and not self.located:
                return er.ProcResult(0, {"mode": "asr", "located": None, "candidates": 0, "consistent": 0,
                                         "median_diff_ms": None, "file_start_ms": None, "rows": []})
            fs = CLIP_FS - LAG if "--locate" in cmd else int(cmd[cmd.index("--file-start-ms") + 1])
            rows = self.rows(fs)
            cons = [r["diff_ms"] for r in rows[:max(self.consistent, 0)]]
            med = sorted(cons)[(len(cons) - 1) // 2] if cons else None
            doc = {"mode": "asr", "chats": nb.N_CHATS, "utterances": 4, "candidates": len(rows),
                   "consistent": self.consistent, "median_diff_ms": med, "file_start_ms": fs, "rows": rows,
                   "elapsed_s": 0.1}
            if "--locate" in cmd:
                doc["located"] = {"file_start_ms": fs, "diff_ms": -fs, "peak": 9.0, "second": 1.0, "votes": 12,
                                  "peak_votes": 9, "peak_utts": 9, "peak_chats": 9, "utts_used": 4, "clamped": False}
            return er.ProcResult(0, doc)
        return super().run_process(cmd, cancelled=cancelled, tick=tick, timeout_s=timeout_s, env=env, cwd=cwd)

    def locate_cmds(self):
        return [c for c in self.cmds if "--locate" in c]


def _pj(root):
    return project_mod.open_project(root).data


def _active(root):
    return calib.active_rev(_pj(root)["calibration"])


def _add_verified(root, offset_ms):
    p = project_mod.open_project(root)

    def build(rev, media_rev, _d):
        cal = tc.Calibration(offset_ms=offset_ms, rev=rev, media_rev=media_rev, status="verified",
                             created_at="2026-10-24T12:00:00+09:00", created_by="L01")
        return tc.calibration_to_json(cal)
    return calib.add_rev(lambda m: p.update(m, by="L01"), build)


# ---------------------------------------------------------------------------- 위치 판단
def _data(*, revs=None, active=0, media_ms=600_000, vod_ms=600_000, path="C:\\v\\합성.mp4", vno=VNO):
    revs = revs or [{"rev": 0, "offset_ms": 0, "anchors": [], "status": "unverified", "media_rev": 0}]
    return {"calibration": {"active_rev": active, "revs": revs}, "broadcast": {"video_no": vno, "duration_ms": vod_ms},
            "media": {"media_rev": 1, "current": {"path_abs": path, "probe": {"duration_ms": media_ms}}}}


def test_media_position_order():
    assert calib.media_position(_data()) == "full"
    assert calib.media_position(_data(media_ms=600_000 + 2_500)) == "unknown"            # 2초 넘게 다름
    assert calib.media_position(_data(vod_ms=0)) == "unknown"                             # VOD 길이 모름
    clip = f"C:\\v\\합성 [{VNO}] 00m00s-05m00s.mp4"
    assert calib.media_position(_data(media_ms=300_000, path=clip)) == "clip"             # 시작 0 클립도 안다
    other = f"C:\\v\\합성 [23456789] 00m00s-05m00s.mp4"
    assert calib.media_position(_data(media_ms=300_000, path=other)) == "unknown"         # 다른 방송 번호
    auto = {"rev": 1, "offset_ms": -5, "anchors": [], "status": "unverified", "note": calib.AUTO_NOTE + "(D-32)"}
    two = [{"rev": 0, "offset_ms": 0, "anchors": [], "status": "unverified"}]
    assert calib.media_position(_data(revs=two + [auto], active=1, media_ms=1)) == "estimated"
    man = dict(auto, note="손으로")
    assert calib.media_position(_data(revs=two + [man], active=1, media_ms=1)) == "manual"
    for st in ("verified", "skipped"):
        assert calib.media_position(_data(revs=two + [dict(auto, status=st)], active=1, media_ms=1)) == "confirmed"
    # 다시 돌려도 같은 답(판 수를 보지 않는다 — D-32 검토 2)
    assert calib.media_position(_data(revs=two + [dict(auto, rev=2)], active=2, media_ms=1)) == "estimated"
    assert calib.approx_file_start_ms(_data(revs=two + [dict(auto, offset_ms=-3_600_000)], active=1)) == 3_600_000
    assert calib.approx_file_start_ms(_data(revs=two + [dict(auto, offset_ms=4_000)], active=1)) == 0


# ---------------------------------------------------------------------------- 후보·맞춤·판
def _cands(n_yes=3, spread=(0, 300, 600), extra=None):
    out = []
    for i in range(n_yes):
        chat = CLIP_FS + 100_000 + i * 50_000
        out.append(calib.Candidate(_cid(i), chat, chat - CLIP_FS + LAG + spread[i % len(spread)], None, "text", 0.9,
                                   calib.YES))
    for c in extra or ():
        out.append(c)
    return out


def test_parse_found_drops_bad_rows_and_keeps_offset_axis():
    doc = {"mode": "asr", "consistent": 2, "median_diff_ms": 6500, "file_start_ms": 1_000_000, "rows": [
        {"chat_id": "c1", "chat_ms": 1_010_000, "video_ms": 16_500, "video_end_ms": 19_000, "basis": "text", "score": 0.8},
        {"chat_id": "c2", "chat_ms": 1_020_000, "video_ms": 26_400, "video_end_ms": 1, "basis": "name", "score": 1},
        {"chat_id": "", "chat_ms": 1, "video_ms": 1}, {"chat_ms": True, "video_ms": 1}, "x"]}
    f = calib.parse_found(doc)
    assert [c.chat_id for c in f.candidates] == ["c1", "c2"]
    assert f.candidates[0].video_end_ms == 19_000 and f.candidates[1].video_end_ms is None   # 끝 < 시작은 버림
    assert f.median_offset_ms == 6500 - 1_000_000 and f.consistent == 2
    assert f.candidates[0].offset_ms == 16_500 - 1_010_000
    assert f.candidates[1].kind == "nickname_call" and f.candidates[0].kind == "text_read"
    assert calib.parse_found({"error": "asr_missing"}).error == "asr_missing"
    assert calib.parse_found(None).error == "no_output"


def test_fit_ready_and_lag():
    assert calib.fit_yes([]) is None and not calib.ready(None)
    cs = _cands()
    f = calib.fit_yes(cs)
    assert calib.ready(f) and f.offset_ms == -CLIP_FS + LAG + 300
    assert calib.lag_corrected(f.offset_ms) == f.offset_ms - calib.READ_LAG_MS
    two = _cands(n_yes=2)
    assert not calib.ready(calib.fit_yes(two))                                   # 3개 미만
    wide = _cands(spread=(0, 300, 5_000))
    assert not calib.ready(calib.fit_yes(wide))                                  # 흩어짐 3초 넘음
    cs[0].verdict = calib.NO
    assert calib.counts(cs) == {"candidates": 3, "yes": 2, "no": 1, "unsure": 0, "pending": 0}


def test_rev_builders_pass_project_schema(tmp_path):
    p = project_mod.create(str(tmp_path / "projects"), "합성", VNO, labeler_id="L01", created_app_version="0.0.0",
                           machine4="test")
    cs = _cands(extra=[calib.Candidate("cx", 1, 2, None, "text", 0.1, calib.NO)])
    f = calib.fit_yes(cs)
    rj = calib.add_rev(lambda m: p.update(m, by="L01"),
                       lambda rev, mr, _d: calib.verified_rev_json(f, cs, rev=rev, media_rev=mr, by="L01",
                                                                   now_iso="2026-10-24T12:00:00+09:00"))
    act = _active(p.root)
    assert act == rj and act["rev"] == 1 and act["status"] == "verified"
    assert act["offset_ms"] == f.offset_ms - calib.READ_LAG_MS                    # 읽기 지연을 뺀 축(D-32)
    assert [a["file_ms"] for a in act["anchors"]] == [c.video_ms for c in cs[:3]]   # 들은 시각 그대로
    assert [a["chat_id"] for a in act["anchors"]] == [c.chat_id for c in cs[:3]]
    assert {a["kind"] for a in act["anchors"]} == {"text_read"} and act["residual_max_ms"] == f.residual_max_ms
    assert "읽기 지연 6700ms" in act["note"] and "아님 1" in act["note"]
    # 자동 추정 판 → 건너뛰기(오프셋 그대로, note 에 자동 추정)
    calib.add_rev(lambda m: p.update(m, by="L01"),
                  lambda rev, mr, _d: calib.auto_rev_json(-3_594_000, rev=rev, media_rev=mr, by="L01",
                                                          now_iso="2026-10-24T12:00:00+09:00", consistent=4,
                                                          peak=9.0, second=1.0))
    auto = _active(p.root)
    assert auto["status"] == "unverified" and auto["anchors"] == [] and calib.is_auto_rev(auto)
    assert auto["offset_ms"] == -3_594_000 - calib.READ_LAG_MS and "창 합 9.00/1.00" in auto["note"]
    calib.add_rev(lambda m: p.update(m, by="L01"),
                  lambda rev, mr, d: calib.skipped_rev_json(d, rev=rev, media_rev=mr, by="L01",
                                                            now_iso="2026-10-24T12:00:00+09:00", reason="합성 사유"))
    sk = _active(p.root)
    assert sk["status"] == "skipped" and sk["offset_ms"] == auto["offset_ms"] and sk["skip_reason"] == "합성 사유"
    assert "자동 위치 추정 그대로" in sk["note"] and calib.media_position(_pj(p.root)) == "confirmed"
    assert [r["rev"] for r in _pj(p.root)["calibration"]["revs"]] == [0, 1, 2, 3]   # 판은 더하기만


# ---------------------------------------------------------------------------- 진행기 (자동 위치 추정·도장)
def test_run_unknown_position_auto_locates_then_links(tmp_path, monkeypatch):
    fk = CalFakes(tmp_path).install(monkeypatch)
    video = _video(tmp_path)
    res = er.run(_req(tmp_path, video))
    assert res.ok and res.calibration == "auto", res.summary()
    eng = res.step("engine").numbers
    assert eng["position"] == "unknown" and eng["located"] == 1 and eng["located_consistent"] == 4
    assert fk.kinds() == ["anchors", "asr", "anchors", "link"] and len(fk.locate_cmds()) == 1
    loc = fk.locate_cmds()[0]
    assert "--json" in loc and "--list" in loc and "--asr" in loc and "-I" in loc
    act = _active(res.project_root)
    med = sorted(r["diff_ms"] for r in fk.rows(CLIP_FS - LAG)[:4])[1]
    assert act["rev"] == 1 and act["status"] == "unverified" and act["anchors"] == [] and calib.is_auto_rev(act)
    assert act["offset_ms"] == (med - (CLIP_FS - LAG)) - calib.READ_LAG_MS
    assert abs(act["offset_ms"] - (-CLIP_FS)) <= 1500                          # 합성 참값과 1.5초 안
    assert act["media_rev"] == _pj(res.project_root)["media"]["media_rev"] == 1
    assert res.line().endswith("· 보정: 자동 추정(확인 전)")
    stamp = json.load(open(os.path.join(res.project_root, *er.STAMP_REL), encoding="utf-8"))
    assert (stamp["calibration_rev"], stamp["offset_ms"]) == (1, act["offset_ms"])
    # 판정 범위(D-33)는 자동 추정 판이 놓은 자리: 영상이 덮는 VOD 구간, 앞은 반응 창 20초만큼
    link = fk.cmds[fk.kinds().index("link")]
    a, b = int(link[link.index("--start-ms") + 1]), int(link[link.index("--end-ms") + 1])
    assert (a, b) == (-act["offset_ms"] - er.JUDGE_LEAD_MS, -act["offset_ms"] + nb.VIDEO_MS)
    assert stamp["range"] == [a, b] and a > 0 and b < fk.vod_ms                 # VOD(3시간 20분) 전체가 아니다
    # 다시 돌리면: 도장이 같아 판정은 건너뛰지만 자동 추정은 그대로 — 끝 화면 전에 다시 확인을 권한다
    again = er.run(_req(tmp_path, video))
    assert again.ok and again.step("engine").status == "skipped" and again.calibration == "auto"
    assert again.step("engine").numbers["position"] == "estimated"
    anc = fk.cmds[-1]
    assert anc[anc.index("--file-start-ms") + 1] == str(-act["offset_ms"])     # 앵커 수 세기도 그 자리 근처
    # 사람이 확정한 판 → 도장이 달라 판정만 다시(전사·위치 찾기 없음)
    n_extract = sum(1 for c in fk.calls if c[0] == "extract")
    _add_verified(res.project_root, -CLIP_FS)
    third = er.run(_req(tmp_path, video))
    assert third.ok and third.calibration is None and third.step("engine").status == "done"
    assert fk.kinds()[-2:] == ["anchors", "link"] and len(fk.locate_cmds()) == 1
    assert third.step("engine").numbers.get("asr_kept") == 1
    assert sum(1 for c in fk.calls if c[0] == "extract") == n_extract
    stamp = json.load(open(os.path.join(res.project_root, *er.STAMP_REL), encoding="utf-8"))
    assert (stamp["calibration_rev"], stamp["offset_ms"]) == (2, -CLIP_FS)
    link = fk.cmds[-1]                                                         # 확정 판으로 범위도 옮긴다
    assert (link[link.index("--start-ms") + 1], link[link.index("--end-ms") + 1]) ==         (str(CLIP_FS - er.JUDGE_LEAD_MS), str(CLIP_FS + nb.VIDEO_MS))
    assert stamp["range"] == [CLIP_FS - er.JUDGE_LEAD_MS, CLIP_FS + nb.VIDEO_MS]


@pytest.mark.parametrize("located,consistent", [(False, 0), (True, 2)])
def test_run_locate_fails_marks_needed_without_link(tmp_path, monkeypatch, located, consistent):
    fk = CalFakes(tmp_path, located=located, consistent=consistent, bad=0).install(monkeypatch)
    video = _video(tmp_path)
    res = er.run(_req(tmp_path, video))
    assert res.ok and res.calibration == "needed"
    st = res.step("engine")
    assert st.code == "calibration_needed" and st.status == "done"
    assert "link" not in fk.kinds() and er.find_link(os.path.join(res.project_root, "derived")) is None
    assert not os.path.exists(os.path.join(res.project_root, *er.STAMP_REL))
    assert res.line().startswith("새 방송: 판정 전 — 영상이 VOD 의 어디인지 정해야 합니다")
    assert calib.is_default_calibration(_pj(res.project_root)["calibration"])     # 판을 더하지 않았다
    # 사람이 정하면(확정) 다음 실행이 판정한다
    _add_verified(res.project_root, -CLIP_FS)
    again = er.run(_req(tmp_path, video))
    assert again.ok and again.calibration is None and fk.kinds()[-1] == "link"


def test_run_clip_name_and_full_skip_locate(tmp_path, monkeypatch):
    fk = CalFakes(tmp_path).install(monkeypatch)
    clip = _video(tmp_path, f"합성 제목 [{VNO}] 000000-001000.mp4")
    res = er.run(_req(tmp_path, clip))
    assert res.ok and res.calibration is None and res.step("engine").numbers["position"] == "clip"
    assert not fk.locate_cmds()


def test_stamp_mismatch_rules(tmp_path, monkeypatch):
    nb.Fakes(tmp_path).install(monkeypatch)
    res = er.run(_req(tmp_path, _video(tmp_path)))
    root = res.project_root
    data = _pj(root)
    assert er.stamp_matches(root, data)
    with open(os.path.join(root, *er.ASR_REL), "a", encoding="utf-8") as f:
        f.write(" ")                                                          # 전사가 바뀜
    assert not er.stamp_matches(root, data)
    er.write_stamp(root, data, "link-v0.3.json")
    assert er.stamp_matches(root, data)
    d2 = json.loads(json.dumps(data))
    d2["media"]["media_rev"] = 9
    assert not er.stamp_matches(root, d2)
    d3 = json.loads(json.dumps(data))
    d3["media"]["current"]["probe"]["duration_ms"] -= 60_000                   # 판정 범위가 바뀜(D-33)
    assert not er.stamp_matches(root, d3)
    os.remove(os.path.join(root, "derived", "link-v0.3.json"))
    assert not er.stamp_matches(root, data)                                    # 판정 파일이 없음
    os.remove(os.path.join(root, *er.STAMP_REL))
    assert not er.stamp_matches(root, data)                                    # 도장 없음(옛 프로젝트) → 판정만 다시


# ---------------------------------------------------------------------------- 확정 화면
from PySide6.QtWidgets import QWidget  # noqa: E402


class FakePlayer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = []

    def load(self, path):
        self.calls.append(("load", os.path.basename(path)))
        return True

    def seek(self, ms, exact=True):
        self.calls.append(("seek", ms))

    def play(self):
        self.calls.append(("play",))

    def pause(self):
        self.calls.append(("pause",))

    def set_ab_loop(self, a, b):
        self.calls.append(("ab", a, b))

    def close_player(self):
        self.calls.append(("close",))


def _found(n_yes=4, bad=1):
    fk = SimpleNamespace(consistent=n_yes, bad=bad, chat_base=CLIP_FS)
    rows = CalFakes.rows(fk, CLIP_FS - LAG)
    cons = sorted(r["diff_ms"] for r in rows[:n_yes])
    return calib.parse_found({"mode": "asr", "consistent": n_yes, "median_diff_ms": cons[(len(cons) - 1) // 2],
                              "file_start_ms": CLIP_FS - LAG, "rows": rows,
                              "located": {"file_start_ms": CLIP_FS - LAG}})


def _view(qapp, tmp_path, *, found=None, data=None, provider=None):
    from app.ui.calibration_view import CalibrationView
    p = project_mod.create(str(tmp_path / "projects"), "합성", VNO, labeler_id="L01", created_app_version="0.0.0",
                           machine4="test")
    v = CalibrationView(None, parts={"player": FakePlayer})
    writes = []

    def write(m):
        writes.append(1)
        p.update(m, by="L01")
    calls = []

    def prov(locate, fs):
        calls.append((locate, fs))
        return found or _found()
    v.start(p.root, data if data is not None else p.data, media_path=str(tmp_path / "합성.mp4"), labeler_id="L01",
            write=write, lookup=lambda cid: (1, "시청자 0001", "합성 채팅") if cid == _cid(1) else None,
            provider=provider or prov)
    return v, p, writes, calls


def test_view_finds_on_start_and_plays_first(qapp, tmp_path):
    v, p, writes, calls = _view(qapp, tmp_path)
    assert calls == [(True, 0)]                                     # 만든 그대로 + 위치 모름 → --locate
    assert v.table.rowCount() == 5 and v.current == 0
    pl = v.player.calls
    assert pl[0] == ("load", "합성.mp4")
    w = v.loop_window(0)
    c = v.cands[0]
    assert w == (c.video_ms - 3000, c.video_ms + 2500 + 1500) and ("ab", *w) in pl and pl[-1] == ("play",)
    assert "시청자 0001" in v.chat_label.text() and "합성 채팅" in v.chat_label.text()
    assert not v.btn_confirm.isEnabled() and "후보 5 · 서로 맞는 4" in v.top_label.text()
    assert writes == []
    v.deleteLater()


def test_view_keys_confirm_writes_verified(qapp, tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest
    v, p, writes, _ = _view(qapp, tmp_path)
    out = []
    v.finished.connect(out.append)
    v.show()
    v.setFocus()
    QTest.keyClick(v, Qt.Key_1)                                      # 맞음 → 다음 대기 건
    assert v.cands[0].verdict == calib.YES and v.current == 1
    # 한글 입력 상태처럼 key 값이 없어도 키 위치(숫자 패드 1)로 받는다
    ev = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.NoModifier, 0, 0x61, 0, "")
    qapp.sendEvent(v.table, ev)
    assert v.cands[1].verdict == calib.YES and v.current == 2
    QTest.keyClick(v, Qt.Key_3)                                      # 모름
    assert v.cands[2].verdict == calib.UNSURE
    assert not v.btn_confirm.isEnabled()                             # 맞음 2
    QTest.keyClick(v, Qt.Key_1)
    assert v.cands[3].verdict == calib.YES and v.btn_confirm.isEnabled() and v.can_confirm()
    assert "확정하면 영상 0초 = VOD" in v.fit_label.text() and "읽기 지연 6.7초" in v.fit_label.text()
    QTest.keyClick(v, Qt.Key_Return)                                 # Enter = 확정
    assert out == ["verified"] and writes == [1]
    act = _active(p.root)
    yes = [c for c in v.cands if c.verdict == calib.YES]
    assert act["status"] == "verified" and [a["chat_id"] for a in act["anchors"]] == [c.chat_id for c in yes]
    assert act["offset_ms"] == calib.fit_yes(v.cands).offset_ms - calib.READ_LAG_MS
    assert abs(act["offset_ms"] + CLIP_FS) <= 1500
    assert ("close",) in v.player.calls
    v.deleteLater()


def test_view_outlier_blocks_confirm_and_skip_needs_reason(qapp, tmp_path):
    v, p, writes, _ = _view(qapp, tmp_path)
    for _ in range(4):
        v.judge(calib.YES)                                           # 서로 맞는 4개
    assert v.can_confirm()
    v.select(4)
    v.judge(calib.YES)                                               # 60초 어긋난 후보까지 맞음 → 흩어짐·이상치
    assert not v.can_confirm() and "어긋" in v.fit_label.text()
    assert v.table.item(4, 5).text() == "맞음(어긋남)"
    assert v.skip("  ") is False and writes == []                    # 사유가 없으면 쓰지 않는다
    out = []
    v.finished.connect(out.append)
    assert v.skip("합성 사유") and out == ["skipped"]
    act = _active(p.root)
    assert act["status"] == "skipped" and act["offset_ms"] == 0 and act["skip_reason"] == "합성 사유"
    v.deleteLater()


def test_view_enter_in_start_field_does_not_confirm(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    v, p, writes, calls = _view(qapp, tmp_path)
    for _ in range(3):
        v.judge(calib.YES)
    assert v.can_confirm()
    v.show()
    v.ed_start.setFocus()
    QTest.keyClicks(v.ed_start, "1:0x")
    QTest.keyClick(v.ed_start, Qt.Key_Return)                      # 칸의 Enter = 다시 찾기 — 확정으로 번지지 않는다
    assert writes == [] and v.outcome is None and "읽지 못했습니다" in v.fit_label.text()
    QTest.keyClick(v.ed_start, Qt.Key_1)                           # 칸 안의 숫자는 글자다
    assert v.ed_start.text().endswith("1") and v.cands[3].verdict is None
    v.deleteLater()


def test_view_refind_with_known_start_and_close(qapp, tmp_path):
    v, p, writes, calls = _view(qapp, tmp_path)
    v.judge(calib.YES)
    assert v.refind("x:y") is False and "읽지 못했습니다" in v.fit_label.text()
    assert v.refind("1:00:00")
    assert calls[-1] == (False, 3_600_000) and all(c.verdict is None for c in v.cands)   # 판정은 새로
    out = []
    v.finished.connect(out.append)
    v._on_close_btn()
    assert out == ["closed"] and writes == [] and v.outcome == "closed"
    v.deleteLater()


def test_view_no_candidates_message(qapp, tmp_path):
    v, p, writes, calls = _view(qapp, tmp_path, found=calib.Found(error="asr_missing"))
    assert v.table.rowCount() == 0 and "후보를 찾지 못했습니다 (asr_missing)" in v.top_label.text()
    assert not v.btn_confirm.isEnabled() and v.btn_skip.isEnabled()
    v.deleteLater()


# ---------------------------------------------------------------------------- 메인 창 흐름
def _setup_win(win, tmp_path, monkeypatch, **kw):
    fk = CalFakes(tmp_path, **kw).install(monkeypatch)
    monkeypatch.setattr(er, "default_engine_python", lambda: _engine_py(tmp_path))
    monkeypatch.setattr(er, "default_models_dir", lambda: _models(tmp_path))
    win.settings.setValue("session/last_labeler", "L01")
    win.calibration_parts = {"player": FakePlayer}
    win.calibration_bg = lambda fn, done: done(fn(), "")
    return fk


def _drain(qapp, n=20):
    for _ in range(n):
        qapp.processEvents()


def test_mainwindow_new_broadcast_auto_then_confirm_relinks(qapp, win, tmp_path, monkeypatch):
    fk = _setup_win(win, tmp_path, monkeypatch)
    res = win.start_new_broadcast(VNO, _video(tmp_path))
    assert res is not None and res.ok and res.calibration == "auto"
    view = win.calibration_view_widget
    assert view is not None and win.stack.currentWidget() is view
    wb = win.workbench
    assert wb is not None and wb.session is None                     # 보기 전용 작업대(잠금) 위에 쌓음
    assert "시간 정렬을 확인하세요" in win.last_new_broadcast_line
    assert view.table.rowCount() == 5 and fk.kinds().count("link") == 1
    for _ in range(3):
        view.judge(calib.YES)
    assert view.confirm()
    _drain(qapp)                                                     # 한 바퀴 뒤 진행기(판정만 다시)
    assert win.last_calibration_outcome == "verified" and win.calibration_view_widget is None
    assert fk.kinds().count("link") == 2 and fk.kinds().count("asr") == 1 and len(fk.locate_cmds()) == 1
    act = _active(res.project_root)
    assert act["status"] == "verified" and act["rev"] == 2
    again = win.last_new_broadcast
    assert again is not res and again.ok and again.calibration is None
    assert win.last_new_broadcast_line.startswith("새 방송 끝:") and "자동 추정" not in win.last_new_broadcast_line
    wb = win.workbench
    assert wb is not None and wb.session is not None and wb.session.session_kind == "analysis"
    assert wb.analysis_action.isChecked() and wb.timemap.status == "verified"


def test_mainwindow_close_keeps_auto_and_opens_engine_view(qapp, win, tmp_path, monkeypatch):
    fk = _setup_win(win, tmp_path, monkeypatch)
    res = win.start_new_broadcast(VNO, _video(tmp_path))
    view = win.calibration_view_widget
    assert view is not None
    view._on_close_btn()
    _drain(qapp)
    assert win.last_calibration_outcome == "closed" and fk.kinds().count("link") == 1
    wb = win.workbench
    assert wb is not None and wb.session is not None and wb.session.session_kind == "analysis"
    assert calib.is_auto_rev(_active(res.project_root)) and "보정 확인 안 됨" in wb.timemap.describe()


def test_mainwindow_needed_close_shows_line(qapp, win, tmp_path, monkeypatch):
    _setup_win(win, tmp_path, monkeypatch, located=False, consistent=0, bad=0)
    res = win.start_new_broadcast(VNO, _video(tmp_path))
    assert res.calibration == "needed" and win.calibration_view_widget is not None
    win.calibration_view_widget._on_close_btn()
    _drain(qapp)
    assert "정하면 판정합니다" in win.statusBar().currentMessage()


def test_calibration_menu_blocks(qapp, win, tmp_path, monkeypatch):
    from app import session as sess
    assert win.a_calibrate.text() == "시간 정렬(보정)…(&A)"
    assert win.calibration_dialog() is None and "프로젝트를 먼저" in win.last_open_block
    _setup_win(win, tmp_path, monkeypatch, located=False, consistent=0, bad=0)
    res = win.start_new_broadcast(VNO, _video(tmp_path))
    win.close_calibration()
    root = res.project_root
    with monkeypatch.context() as m:
        m.setattr(sess, "survey_project", lambda r, d: SimpleNamespace(
            session_ids=("L01-P-20261024-ab12",), locked_ids=frozenset({"L01-P-20261024-ab12"}), relabel_plans=0))
        assert "라벨링이 진행된" in (win.calibration_block_reason(root) or "")
    assert win.calibration_block_reason(root) is None
    with monkeypatch.context() as m:
        m.setattr(er, "is_pilot", lambda r: True)
        assert "시범" in win.calibration_block_reason(root)
    os.remove(os.path.join(root, *er.ASR_REL))
    assert "전사" in win.calibration_block_reason(root)


def test_mainwindow_menu_after_needed_confirms_and_links(qapp, win, tmp_path, monkeypatch):
    """새 방송이 'needed'(판정 없음)로 끝나 [닫기] 한 뒤 메뉴로 열어 확정하면 판정을 만든다(라벨링 세션 없음)."""
    fk = _setup_win(win, tmp_path, monkeypatch, located=False, consistent=0, bad=0)
    video = _video(tmp_path)
    res = win.start_new_broadcast(VNO, video)
    win.calibration_view_widget._on_close_btn()
    _drain(qapp)
    assert "link" not in fk.kinds()
    fk.consistent, fk.bad = 4, 1                                   # 이제 아는 시각 근처에서는 후보가 나온다
    view = win.calibration_dialog()
    assert view is not None and win.workbench.session is None
    assert view.refind("1:00:00")
    for _ in range(3):
        view.judge(calib.YES)
    assert view.confirm()
    _drain(qapp)
    assert fk.kinds().count("link") == 1 and _active(res.project_root)["status"] == "verified"
    assert win.workbench is not None and win.workbench.session.session_kind == "analysis"


def test_close_calibration_cancels_and_waits_workers(win):
    class W:
        def __init__(self):
            self.log = []

        def cancel(self):
            self.log.append("cancel")

        def wait(self, ms):
            self.log.append(("wait", ms))
    w = W()
    win._calib_workers.append(w)
    win._wait_calib_workers(123)
    assert w.log == ["cancel", ("wait", 123)]
    win._calib_workers.clear()
