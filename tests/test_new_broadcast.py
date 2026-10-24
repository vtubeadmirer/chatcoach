"""'파일 → 새 방송…' (08 D-31 2·3): 대화상자 번호 뽑기 · 진행기 단계 순서·건너뜀·실패 · derived 이름 · 시범 봉인 길 ·
메인 창 끝 화면 · main.py 인자 · find_anchors --json.

합성 데이터만(tmp_path), 네트워크 없음 — 채팅 받기(COLLECT·FETCH_INFO)·영상 확인(MEDIA_REF)·음성 뽑기(EXTRACT)·엔진 프로세스
(RUN_PROCESS)는 app.engine_run 의 바꿔 끼우는 함수 변수로 대신한다. 실제 프로젝트·시범 폴더·엔진은 건드리지 않는다.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import chzzk_chat, media  # noqa: E402
from app import engine_run as er  # noqa: E402
from app import project as project_mod  # noqa: E402

VNO = "12345678"
N_CHATS = 60
STEP = 10_000
VIDEO_MS = 600_000


# ---------------------------------------------------------------------------- 가짜들
def _cid(i):
    return f"c{i:020x}"


class Fakes:
    """바꿔 끼우는 함수 묶음 + 부른 기록(명령·인자)."""

    def __init__(self, tmp_path, *, vectors=True):
        self.calls: list[tuple] = []
        self.cmds: list[list[str]] = []
        self.envs: list[dict] = []
        self.collect_error = None
        self.collect_after = None              # 채팅을 받은 뒤 부를 함수(취소 시험)
        self.asr_rc = 0
        self.asr_problems: list[str] = []
        self.engine_version = "link-v0.3"
        self.pilot_ok = True
        self.tmp = tmp_path

    # 영상 정보 API
    def info(self, vno):
        self.calls.append(("info", vno))
        return {"duration_ms": VIDEO_MS, "title": "합성 방송 제목"}

    # 채팅 받기 (app.chzzk_chat.collect 와 같은 인자)
    def collect(self, vno, root, salt, *, duration_ms=None, on_progress=None, cancel=None, **kw):
        self.calls.append(("collect", vno, duration_ms, len(salt)))
        if self.collect_error is not None:
            raise self.collect_error
        os.makedirs(os.path.join(root, "chat", "protected"), exist_ok=True)
        with open(os.path.join(root, "chat", "messages.jsonl"), "wb") as f:
            for i in range(N_CHATS):
                m = {"chat_id": _cid(i), "t_ms": 1_000 + i * STEP, "server_time_ms": 1_760_000_000_000 + i * STEP,
                     "type_code": 1, "kind": "text", "status": "normal", "viewer_id": "vw_synthaaaaaa" + "abcdefg"[i % 7],
                     "is_anonymous": False, "nickname_masked": "시**", "role": "viewer", "text": f"합성 채팅 {i}?",
                     "raw_ref": {"fetch_id": "F20261024-test", "page": 0, "index": i}}
                f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
        if on_progress:
            on_progress(0.5, 1, N_CHATS // 2, 300_000)
            on_progress(0.99, 2, N_CHATS, 600_000)
        if self.collect_after:
            self.collect_after()
        return chzzk_chat.FetchSummary(fetch_id="F20261024-test", video_no=vno, pages=2, messages=N_CHATS,
                                       complete=True, stop_reason=chzzk_chat.STOP_NO_NEXT, duplicates_skipped=0,
                                       elapsed_sec=0.1, kind_counts={"text": N_CHATS}, viewers=7)

    # 영상 확인 (app.media.media_ref 모양, 지문은 진짜)
    def media_ref(self, path, root):
        self.calls.append(("media_ref",))
        size = os.path.getsize(path)
        return {"path_abs": os.path.normpath(path), "path_rel": None, "size": size, "mtime_ns": 0,
                "partial_fp": media.partial_fp(path, size), "full_sha256": None, "ntfs_file_id": None,
                "volume_serial": None,
                "probe": {"duration_ms": VIDEO_MS, "video_codec": "h264", "audio_codec": "aac", "width": 1920,
                          "height": 1080, "r_frame_rate": "60/1", "avg_frame_rate": "60/1", "vfr": False,
                          "nb_frames": None}}

    # 음성 뽑기 (app.analysis.extract_audio_wav 모양)
    def extract(self, video, wav, *, sample_rate=16000, start_ms=None, dur_ms=None, cancel=None):
        self.calls.append(("extract", start_ms, dur_ms))
        if cancel is not None:
            cancel()
        with open(wav, "wb") as f:
            f.write(b"RIFF0000WAVE")
        return {"path": wav, "sample_rate": sample_rate, "channels": 1, "duration_ms": dur_ms or VIDEO_MS,
                "elapsed_s": 0.01}

    # 엔진·도구 프로세스
    def run_process(self, cmd, *, cancelled=None, tick=None, timeout_s=None, env=None, cwd=None):
        cmd = list(cmd)
        self.cmds.append(cmd)
        self.envs.append(dict(env or {}))
        if tick:
            tick(0.1)

        def arg(flag):
            return cmd[cmd.index(flag) + 1] if flag in cmd else None
        if any(c.endswith("find_anchors.py") for c in cmd):
            return er.ProcResult(0, {"mode": "asr" if "--asr" in cmd else "question", "chats": N_CHATS,
                                     "candidates": 5, "consistent": 3 if "--asr" in cmd else None})
        if "-m" in cmd and "asr" in cmd:
            if self.asr_rc != 0 and not self.asr_problems:
                return er.ProcResult(self.asr_rc, {}, [], 0.1, None, "RuntimeError")
            segs = [{"id": i, "start_ms": i * 20_000, "end_ms": i * 20_000 + 3_000, "text": f"합성 발화 {i}",
                     "words": [{"word": "합성", "start_ms": i * 20_000, "end_ms": i * 20_000 + 500}]}
                    for i in range(4)]
            with open(arg("--out"), "w", encoding="utf-8") as f:
                json.dump({"format": "chatcoach.asr", "format_version": 1, "segments": segs,
                           "audio": {"duration_ms": VIDEO_MS}}, f, ensure_ascii=False)
            # engine asr: 형식 문제가 있어도 asr.json 은 다 쓰고 종료 1(engine/__main__.py)
            return er.ProcResult(1 if self.asr_problems else 0,
                                 {"out": arg("--out"), "device": "cuda", "fallback": None,
                                  "format_problems": list(self.asr_problems),
                                  "stats": {"segment_count": 4, "word_count": 4}, "timing": {"realtime_x": 40.0}})
        if any(c.endswith("engine_to_ai.py") for c in cmd):
            out = arg("--out")
            stem = os.path.splitext(out)[0]
            reactions = [
                {"chat_id": _cid(1), "result": "graded", "grade": "direct", "reaction_time_ms": 12_000,
                 "utterance_start_ms": 12_000, "utterance_end_ms": 14_000, "score": 3.0},
                {"chat_id": _cid(2), "result": "graded", "grade": "direct", "reaction_time_ms": 22_000,
                 "utterance_start_ms": 22_000, "utterance_end_ms": 24_000, "score": 3.0},
                {"chat_id": _cid(3), "result": "graded", "grade": "likely", "reaction_time_ms": 32_000,
                 "utterance_start_ms": 32_000, "utterance_end_ms": 34_000, "score": 2.0},
                {"chat_id": _cid(4), "result": "unconfirmed", "grade": None, "reaction_time_ms": None,
                 "utterance_start_ms": None, "utterance_end_ms": None, "score": 0.5},
            ]
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": "link-v0.3-test",
                           "time_axis": "vod_ms", "reactions": reactions}, f)
            for k in ("topics", "words") + (("vectors",) if "--vectors" in cmd else ()):
                with open(f"{stem}.{k}.json", "w", encoding="utf-8") as f:
                    json.dump({"kind": k}, f)
            return er.ProcResult(0, {"out": out, "reactions_in": 4, "reactions_out": 4, "topic_blocks": 2,
                                     "word_windows": 6, "vector_windows": 6, "engine_version": self.engine_version,
                                     "engine_fallback": self.engine_version != "link-v0.3"})
        if any(c.endswith("pilot_engine_run.py") for c in cmd):
            row = {"project": os.path.basename(arg("--project")), "ok": self.pilot_ok,
                   "asr": {"segments": 7}, "seals": [{"engine": "v0.2", "presealed": True},
                                                     {"engine": "v0.3", "presealed": True}], "removed": 3}
            if not self.pilot_ok:
                row["error"] = "labeling_started"
            summ = {"tool": "pilot_engine_run", "ok": 1 if self.pilot_ok else 0}
            return er.ProcResult(0 if self.pilot_ok else 1, summ, [row, summ])
        return er.ProcResult(2, {"error": "unknown_cmd"})

    def install(self, monkeypatch):
        monkeypatch.setattr(er, "FETCH_INFO", self.info)
        monkeypatch.setattr(er, "COLLECT", self.collect)
        monkeypatch.setattr(er, "MEDIA_REF", self.media_ref)
        monkeypatch.setattr(er, "EXTRACT", self.extract)
        monkeypatch.setattr(er, "RUN_PROCESS", self.run_process)
        monkeypatch.setattr(er.paths, "find_tool", lambda name: "C:\\fake\\" + name + ".exe")
        return self

    def kinds(self):
        out = []
        for c in self.cmds:
            if any(x.endswith("find_anchors.py") for x in c):
                out.append("anchors")
            elif "-m" in c and "asr" in c:
                out.append("asr")
            elif any(x.endswith("engine_to_ai.py") for x in c):
                out.append("link")
            elif any(x.endswith("pilot_engine_run.py") for x in c):
                out.append("pilot")
        return out


def _models(tmp_path, *, whisper=True, e5=True):
    m = tmp_path / "models"
    m.mkdir(parents=True, exist_ok=True)
    if whisper:
        p = m / er.WHISPER_CACHE_DIR / "snapshots" / "abc"
        p.mkdir(parents=True, exist_ok=True)
        (p / "model.bin").write_bytes(b"0")
    if e5:
        for parts in er.E5_FILES:
            f = m.joinpath(*parts)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"0")
    return str(m)


def _video(tmp_path, name="합성 영상.mp4"):
    p = tmp_path / "videos" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(3 * 1024 * 1024))
    return str(p)


def _engine_py(tmp_path):
    p = tmp_path / "venv-engine" / "python.exe"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return str(p)


def _req(tmp_path, video, *, vno=VNO, engine_python=None, models=None, limit=None):
    return er.Request(video_no=vno, video_path=video, labeler_id="L01", projects_root=str(tmp_path / "projects"),
                      asr_limit_ms=limit, engine_python=engine_python or _engine_py(tmp_path),
                      models_dir=models or _models(tmp_path))


# ---------------------------------------------------------------------------- 이름 뽑기
def test_video_no_from_url_and_number():
    assert chzzk_chat.parse_video_no("https://chzzk.naver.com/video/15767532") == "15767532"
    assert chzzk_chat.parse_video_no(" 15767532 ") == "15767532"
    assert chzzk_chat.parse_video_no("chzzk.naver.com/video/15767532?t=10") == "15767532"
    assert chzzk_chat.parse_video_no("https://example.com/video/1") is None


def test_parse_video_file_name_clipslicer():
    # ClipSlicer: '<제목> [<번호>] HHMMSS-HHMMSS_01.mp4' · 'MMmSSs' 꼴 · 시간 없는 이름 · 번호 없는 이름
    assert er.parse_video_file_name(r"C:\v\합성 [15767532] 000000-034900_01.mp4") == ("15767532", 0)
    assert er.parse_video_file_name(r"C:\v\합성 [15767532] 012000-034900.mp4") == ("15767532", 4_800_000)
    assert er.parse_video_file_name(r"C:\v\합성 [15632692] 00m00s-05m00s.mp4") == ("15632692", 0)
    assert er.parse_video_file_name(r"C:\v\합성 [15632692] 01h02m03s-02h00m00s.mp4") == ("15632692", 3_723_000)
    assert er.parse_video_file_name(r"C:\v\합성 [0015632692].mp4") == ("15632692", None)
    assert er.parse_video_file_name(r"C:\v\합성 재데뷔_02.mp4") == (None, None)
    assert er.parse_video_file_name(r"C:\v\합성 [0] 000000-000100.mp4") == (None, None)
    assert er.title_from_file_name(r"C:\v\합성 제목 [15767532] 000000-034900.mp4") == "합성 제목"
    assert er.title_from_file_name(r"C:\v\제목만.mp4") is None


# ---------------------------------------------------------------------------- 진행기
def test_run_steps_in_order_and_derived_names(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    video = _video(tmp_path)
    texts: list[tuple[float, str]] = []
    res = er.run(_req(tmp_path, video), progress=lambda f, t: texts.append((f, t)))
    assert res.ok, res.summary()
    assert [s.key for s in res.steps] == list(er.STEPS) == ["project", "chat", "media", "anchors", "engine"]
    assert [s.status for s in res.steps] == ["done"] * 5
    root = res.project_root
    assert os.path.basename(root) == f"합성_방송_제목-{VNO}" and not res.pilot
    # 단계 글이 차례로(단계 이름·경과 시간·숫자만 — 채팅 본문 없음)
    order = []
    for _f, t in texts:
        head = t.split(" · ")[0]
        if head not in order:
            order.append(head)
    assert order == [f"{i + 1}/5 {er.STEP_KO[k]}" for i, k in enumerate(er.STEPS)]
    assert all("합성 채팅" not in t and "합성 발화" not in t for _f, t in texts)
    assert any("(전체 " in t for _f, t in texts) and any("60건" in t for _f, t in texts)
    fr = [f for f, _t in texts]
    assert all(0 <= f <= 1 for f in fr) and fr == sorted(fr)                      # 막대는 뒤로 가지 않는다
    # 화면이 읽는 이름 그대로(분석 화면·방송 요약·시청자 훈련)
    from app import viewer_flow as vf
    from app.ui import workbench as wbm
    assert (er.ASR_REL, er.TOPICS_REL, er.WORDS_REL, er.VECTORS_REL) == (vf.ASR_REL, vf.TOPICS_REL, vf.WORDS_REL,
                                                                          vf.VECTORS_REL)
    d = os.path.join(root, "derived")
    for rel in (er.ASR_REL, er.TOPICS_REL, er.WORDS_REL, er.VECTORS_REL):
        assert os.path.isfile(os.path.join(root, *rel)), rel
    link = wbm.find_link_file(d)
    assert link is not None and os.path.basename(link) == "link-v0.3.json" == er.LINK_NAME["v0.3"]
    assert er.find_link(d) == link
    # 보호 구역 이름(derived/asr*) 밖에는 전사 낱말·벡터를 두지 않는다
    from app import backup
    for rel in (er.WORDS_REL, er.VECTORS_REL, er.TOPICS_REL, er.ASR_REL):
        assert backup.in_protected_zone("/".join(rel), backup.DEFAULT_PROTECTED_ZONE), rel
    # 중간 파일(wav·쓰던 asr·asr-link 단계 파일)은 남지 않는다
    left = sorted(os.listdir(d))
    assert left == sorted(["asr.json", "asr-topic-flow.json", "asr-words.json", "asr-topic-vectors.json",
                           "link-v0.3.json"]), left
    assert res.number("engine", "removed") == 1                 # wav 하나를 os.remove
    # 숫자
    assert (res.chats, res.utterances, res.direct) == (N_CHATS, 4, 2)
    assert res.line() == f"새 방송 끝: 채팅 {N_CHATS} · 발화 4 · 반응(direct) 2"
    eng = res.step("engine").numbers
    assert (eng["reactions"], eng["likely"], eng["unconfirmed"], eng["engine_version"]) == (3, 1, 1, "link-v0.3")
    assert res.step("anchors").numbers["candidates"] == 5 and res.step("anchors").numbers["mode"] == "question"
    # 엔진은 엔진 venv 를 따로 프로세스로, 허깅페이스에 닿지 않게, v0.3 + 주제·낱말·벡터, 보호 구역 단계 이름으로
    assert fk.kinds() == ["anchors", "asr", "link"]
    asr_cmd, link_cmd = fk.cmds[1], fk.cmds[2]
    assert asr_cmd[0] == link_cmd[0] and asr_cmd[0].endswith("python.exe")
    assert asr_cmd[1:4] == ["-m", "engine", "asr"] and "--models-dir" in asr_cmd
    assert all(fk.envs[i].get("HF_HUB_OFFLINE") == "1" for i in (1, 2))
    i = link_cmd.index("--out")
    assert os.path.basename(link_cmd[i + 1]) == er.STAGE_NAME and os.path.dirname(link_cmd[i + 1]) == d
    for flag in ("--from-project", "--engine", "--topics", "--words", "--vectors"):
        assert flag in link_cmd
    assert link_cmd[link_cmd.index("--engine") + 1] == "v0.3" and "--seal" not in link_cmd
    assert "--start-ms" not in link_cmd and "--asr-start-ms" not in link_cmd     # 전체 길이 · 영상 0초부터
    assert ("extract", None, None) in fk.calls
    # project.json: 영상 연결(media_rev 1), 보정은 오프셋 0 미확인 그대로, 목적·VOD 길이
    pj = project_mod.open_project(root).data
    assert pj["media"]["media_rev"] == 1 and pj["media"]["current"]["probe"]["duration_ms"] == VIDEO_MS
    assert pj["calibration"]["active_rev"] == 0 and pj["calibration"]["revs"][0]["offset_ms"] == 0
    assert pj["calibration"]["revs"][0]["status"] == "unverified"
    assert pj["broadcast"]["duration_ms"] == VIDEO_MS and pj["purpose"] == er.NEW_PURPOSE


def test_run_again_resumes_and_skips(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    video = _video(tmp_path)
    req = _req(tmp_path, video)
    first = er.run(req)
    assert first.ok
    n_collect = sum(1 for c in fk.calls if c[0] == "collect")
    second = er.run(req)
    assert second.ok and second.project_root == first.project_root
    assert [s.status for s in second.steps] == ["skipped", "skipped", "skipped", "done", "skipped"]
    assert sum(1 for c in fk.calls if c[0] == "collect") == n_collect == 1           # 채팅 있으면 건너뜀
    assert fk.kinds() == ["anchors", "asr", "link", "anchors"]                        # 엔진 결과 있으면 건너뜀
    assert "--asr" in fk.cmds[-1]                                                     # 다시 셀 때는 발화 기준 후보
    assert (second.chats, second.utterances, second.direct) == (N_CHATS, 4, 2)
    # 판정만 없어진 경우: 전사는 그대로 두고 판정·주제·낱말·벡터만 다시
    d = os.path.join(first.project_root, "derived")
    os.remove(os.path.join(d, "link-v0.3.json"))
    os.remove(os.path.join(d, "asr-topic-flow.json"))
    third = er.run(req)
    assert third.ok and third.step("engine").status == "done"
    assert fk.kinds()[-2:] == ["anchors", "link"] and third.step("engine").numbers.get("asr_kept") == 1
    assert sum(1 for c in fk.calls if c[0] == "extract") == 1


def test_failure_stops_with_code_and_keeps_project(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    fk.collect_error = chzzk_chat.ChatFetchError("HTTP 500")
    res = er.run(_req(tmp_path, _video(tmp_path)))
    assert not res.ok and res.stopped.key == "chat" and res.stopped.code == "chat_fetch"
    assert [s.key for s in res.steps] == ["project", "chat"]                          # 뒤 단계는 하지 않는다
    assert os.path.isfile(os.path.join(res.project_root, "project.json"))             # 만든 프로젝트는 남긴다
    assert res.line().startswith("새 방송 멈춤: 2/5 채팅 받기 — chat_fetch")
    # 다시 하면 같은 프로젝트로 이어서
    fk.collect_error = None
    again = er.run(_req(tmp_path, _video(tmp_path, "다른 이름.mp4")))
    assert again.ok and again.project_root == res.project_root and again.steps[0].status == "skipped"


def test_engine_venv_missing_and_model_missing(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    video = _video(tmp_path)
    res = er.run(_req(tmp_path, video, engine_python=str(tmp_path / "없음" / "python.exe")))
    assert res.stopped.key == "engine" and res.stopped.code == "engine_venv_missing"
    assert er.SETUP_HINT in res.line() and "tools\\setup_engine.ps1" in res.line()
    assert "asr" not in fk.kinds()
    res2 = er.run(_req(tmp_path, video, models=_models(tmp_path / "m2", whisper=False)))
    assert res2.stopped.code == "whisper_model_missing" and not any(c[0] == "extract" for c in fk.calls)
    # 전사가 실패하면 코드(종료 코드·예외 이름)만, 중간 파일은 지운다
    fk.asr_rc = 1
    res3 = er.run(_req(tmp_path, video))
    assert res3.stopped.code == "asr_exit_1:runtimeerror"
    d = os.path.join(res3.project_root, "derived")
    assert not [n for n in os.listdir(d) if n.endswith(".wav") or ".part" in n]


def test_asr_format_problems_order_only_is_tolerated(tmp_path, monkeypatch):
    # 리허설 1회차(2026-10-24): engine asr 가 asr.json 을 다 쓰고 형식 문제로 종료 1 → 'asr_exit_1' 로 멈췄다.
    # 구간 순서만 어긋난 것은 뒤 단계가 시각으로 다시 정렬하므로 경고(asr_order)로 진행, 시각·키 문제는 멈춘다.
    fk = Fakes(tmp_path).install(monkeypatch)
    video = _video(tmp_path)
    fk.asr_problems = ["segments[12] 시작이 앞 구간보다 이름", "segments[40] 시작이 앞 구간보다 이름"]
    res = er.run(_req(tmp_path, video))
    eng = res.step("engine")
    assert res.ok and eng.code == "asr_order" and eng.numbers["asr_problems"] == 2
    assert eng.numbers["asr_problem_kinds"] == "order" and os.path.isfile(os.path.join(res.project_root, *er.ASR_REL))
    assert er.asr_problem_kinds(["segments[3].words[0].prob 없음", "없는 키 audio", "format 다름", "x"]) == \
        {"missing", "format", "other"}
    # 시각 문제는 멈춘다(판정이 읽지 못함) — 쓰던 asr 는 지운다
    tmp2 = tmp_path / "t2"
    tmp2.mkdir()
    fk2 = Fakes(tmp2).install(monkeypatch)
    fk2.asr_problems = ["segments[5] 시각이 정수·순서가 아님"]
    bad = er.run(_req(tmp2, _video(tmp2)))
    assert bad.stopped.code == "asr_format:time" and not os.path.exists(os.path.join(bad.project_root, *er.ASR_REL))
    d = os.path.join(bad.project_root, "derived")
    assert not [n for n in os.listdir(d) if ".part" in n or n.endswith(".wav")]


def test_e5_missing_runs_without_vectors_and_v02_name(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    fk.engine_version = "0.1.0"                        # v0.3 가 엔진 안에서 v0.2 로 물러났다
    res = er.run(_req(tmp_path, _video(tmp_path), models=_models(tmp_path / "m", e5=False)))
    assert res.ok and res.step("engine").code == "e5_missing"
    link_cmd = fk.cmds[[k for k in fk.kinds()].index("link")]
    assert "--vectors" not in link_cmd
    d = os.path.join(res.project_root, "derived")
    assert os.path.isfile(os.path.join(d, "link-v0.2.json")) and not os.path.exists(os.path.join(d, "link-v0.3.json"))


def test_cancel_stops_at_that_step(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    flag = {"v": False}
    fk.collect_after = lambda: flag.__setitem__("v", True)
    res = er.run(_req(tmp_path, _video(tmp_path)), cancelled=lambda: flag["v"])
    assert res.cancelled and res.stopped.key == "media" and res.line().startswith("새 방송 취소: 3/5 영상 연결")
    assert res.step("chat").status == "done"


def test_media_other_stops(tmp_path, monkeypatch):
    Fakes(tmp_path).install(monkeypatch)
    first = er.run(_req(tmp_path, _video(tmp_path)))
    assert first.ok
    res = er.run(_req(tmp_path, _video(tmp_path, "다른 영상.mp4")))        # 다른 파일(지문 다름)
    assert res.stopped.key == "media" and res.stopped.code == "media_other"


def test_clip_start_from_name_and_limit_range(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    video = _video(tmp_path, f"합성 제목 [{VNO}] 001000-002000.mp4")             # 파일 0초 = VOD 10:00
    res = er.run(_req(tmp_path, video, limit=120_000))
    assert res.ok, res.summary()
    pj = project_mod.open_project(res.project_root).data
    cal = pj["calibration"]
    act = next(r for r in cal["revs"] if r["rev"] == cal["active_rev"])
    assert cal["active_rev"] == 1 and act["offset_ms"] == -600_000 and act["status"] == "unverified"
    assert act["media_rev"] == pj["media"]["media_rev"] == 1 and not act["anchors"]
    assert res.step("media").numbers["clip_start_s"] == 600
    anchors_cmd = fk.cmds[fk.kinds().index("anchors")]
    assert anchors_cmd[anchors_cmd.index("--file-start-ms") + 1] == "600000"
    assert ("extract", None, 120_000) in fk.calls                                    # 영상 처음 2분만
    link_cmd = fk.cmds[fk.kinds().index("link")]
    assert link_cmd[link_cmd.index("--start-ms") + 1] == "600000"
    assert link_cmd[link_cmd.index("--end-ms") + 1] == "720000"                       # 그 2분이 덮는 VOD 구간


def test_pilot_project_goes_seal_path(tmp_path, monkeypatch):
    fk = Fakes(tmp_path).install(monkeypatch)
    proj = project_mod.create(str(tmp_path / "projects"), f"pilot-{VNO}", VNO, labeler_id="L01",
                              created_app_version="0.0.0", engine_author=None, machine4="test")
    assert os.path.basename(proj.root) == f"pilot-{VNO}-{VNO}"
    video = _video(tmp_path)
    res = er.run(_req(tmp_path, video))
    assert res.ok and res.pilot and res.project_root == proj.root
    assert fk.kinds() == ["anchors", "pilot"]                                         # engine_to_ai --out 은 없다
    pilot_cmd = fk.cmds[-1]
    assert pilot_cmd[1].endswith("pilot_engine_run.py") and pilot_cmd[pilot_cmd.index("--project") + 1] == proj.root
    assert "--asr" not in fk.cmds[0]
    d = os.path.join(proj.root, "derived")
    assert not os.listdir(d)                                                          # 시범은 derived\ 에 쓰지 않는다
    assert res.line().endswith("반응(direct) 봉인됨") and res.utterances == 7
    fk.pilot_ok = False
    bad = er.run(_req(tmp_path, video))
    assert bad.stopped.key == "engine" and bad.stopped.code == "pilot_labeling_started"


def test_find_projects_matches_number_only(tmp_path):
    root = tmp_path / "projects"
    a = project_mod.create(str(root), "가", VNO, labeler_id="L01", created_app_version="0.0.0", machine4="test")
    project_mod.create(str(root), "나", "87654321", labeler_id="L01", created_app_version="0.0.0", machine4="test")
    (root / f"깨짐-{VNO}").mkdir()
    (root / f"깨짐-{VNO}" / "project.json").write_bytes(b"{not json")
    assert er.find_projects(str(root), VNO) == [a.root]
    assert er.find_projects(str(tmp_path / "없음"), VNO) == []


def test_default_projects_root_follows_data_dir(tmp_path):
    from app import paths
    paths.set_data_root(str(tmp_path / "rehearsal"))
    assert er.default_projects_root() == os.path.join(str(tmp_path / "rehearsal"), "projects")


def test_run_process_reads_json_and_kills_on_cancel(tmp_path):
    ok = er.run_process([sys.executable, "-c", "import json; print('x'); print(json.dumps({'a': 1}, indent=1))"],
                        cwd=str(tmp_path))
    assert ok.rc == 0 and ok.doc == {"a": 1}
    lines = er.run_process([sys.executable, "-c", "print('{\"a\": 1}'); print('{\"b\": 2}')"], cwd=str(tmp_path))
    assert [d for d in lines.docs] == [{"a": 1}, {"b": 2}] and lines.doc == {"b": 2}
    bad = er.run_process([sys.executable, "-c", "raise KeyError('x')"], cwd=str(tmp_path))
    assert bad.rc == 1 and bad.err_code == "KeyError"
    ticks = []
    gone = er.run_process([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path),
                          tick=ticks.append, cancelled=lambda: len(ticks) >= 2)
    assert gone.error == "cancelled" and gone.rc is None and gone.elapsed_s < 20


# ---------------------------------------------------------------------------- find_anchors --json
def test_find_anchors_json_numbers_only(tmp_path):
    root = tmp_path / "proj"
    Fakes(tmp_path).collect(VNO, str(root), b"0" * 32)
    sys.path.insert(0, os.path.join(er.repo_root(), "tools"))
    try:
        import find_anchors as fa
    finally:
        sys.path.pop(0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fa.main(["--project", str(root), "--json", "--top", "5"])
    out = buf.getvalue().strip().splitlines()
    assert rc == 0 and len(out) == 1
    doc = json.loads(out[0])
    assert doc["mode"] == "question" and doc["chats"] == N_CHATS and isinstance(doc["candidates"], int)
    assert "합성" not in out[0]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fa.main(["--project", str(root), "--json", "--asr", str(root / "없음.json")])
    assert rc == 1 and json.loads(buf.getvalue()) == {"error": "asr_missing"}


# ---------------------------------------------------------------------------- 대화상자
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication(sys.argv[:1])


def test_dialog_extracts_number_and_fills_from_file_name(qapp, tmp_path):
    from app.ui.new_broadcast_dialog import NewBroadcastDialog
    dlg = NewBroadcastDialog(None, settings=None, labeler_id="L01")
    assert dlg.ed_labeler is None                                   # 라벨러는 설정의 것 — 칸이 없다
    dlg.ed_vod.setText("https://chzzk.naver.com/video/15767532")
    dlg._normalize_vod()
    assert dlg.ed_vod.text() == "15767532" and dlg.video_no() == "15767532"
    dlg.ed_vod.setText("")
    video = _video(tmp_path, f"합성 제목 [{VNO}] 000000-001000_01.mp4")
    dlg.choose_file = lambda: video
    dlg.btn_pick.click()
    assert dlg.ed_vod.text() == VNO and dlg.video_path() == os.path.normpath(video)
    assert dlg.problems() == []
    dlg.btn_start.click()
    assert dlg.result() == NewBroadcastDialog.Accepted
    assert dlg.values() == {"video_no": VNO, "video_path": os.path.normpath(video), "labeler_id": "L01"}
    dlg.deleteLater()


def test_dialog_keeps_typed_number_and_asks_labeler_when_unknown(qapp, tmp_path):
    from app.ui.new_broadcast_dialog import NewBroadcastDialog
    dlg = NewBroadcastDialog(None, settings=None, labeler_id=None, video_no="11111111")
    assert dlg.ed_labeler is not None                               # 앱 설정에 라벨러가 없을 때만 한 칸 더
    video = _video(tmp_path, f"합성 [{VNO}] 000000-001000.mp4")
    dlg.set_video('"' + video + '"')                                 # 탐색기 '경로로 복사' 따옴표
    assert dlg.ed_vod.text() == "11111111" and dlg.video_path() == os.path.normpath(video)
    assert VNO in dlg.hint.text()                                    # 파일 이름의 번호와 다르다는 한 줄
    dlg.btn_start.click()
    assert dlg.result() != NewBroadcastDialog.Accepted and "라벨러" in dlg.hint.text()
    dlg.ed_labeler.setText("l02")
    dlg.btn_start.click()
    assert dlg.result() == NewBroadcastDialog.Accepted and dlg.values()["labeler_id"] == "L02"
    missing = NewBroadcastDialog(None, settings=None, labeler_id="L01", video_no="x",
                                 video_path=str(tmp_path / "없는 파일.mp4"))
    assert len(missing.problems()) == 2
    dlg.deleteLater()
    missing.deleteLater()


# ---------------------------------------------------------------------------- 메인 창
@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    from app.ui import workbench as wbm
    from tests.test_workbench_labeling import PARTS
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    data_root = str(tmp_path / "appdata")
    os.makedirs(data_root, exist_ok=True)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    w = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    w.check_updates = lambda force=False, on_start=False: None
    w.shown = []
    w.inform = lambda title, text: w.shown.append(("info", text))
    w.warn = lambda title, text: w.shown.append(("warn", text))
    yield w
    if w.workbench is not None:
        w.workbench.wait_jobs(15_000)
    w.close()
    w.deleteLater()


def test_menu_has_new_broadcast_first(win):
    file_menu = win.menuBar().actions()[0].menu()
    first = [a for a in file_menu.actions() if not a.isSeparator()][0]
    assert first is win.a_new_broadcast and first.text() == "새 방송…(&N)"


def test_mainwindow_new_broadcast_opens_engine_view(qapp, win, tmp_path, monkeypatch):
    from app import session as sess
    from app.ui import workbench as wbm
    fk = Fakes(tmp_path).install(monkeypatch)
    monkeypatch.setattr(er, "default_engine_python", lambda: _engine_py(tmp_path))
    monkeypatch.setattr(er, "default_models_dir", lambda: _models(tmp_path))
    video = _video(tmp_path)

    def run(dlg):
        assert dlg.ed_labeler is not None                           # 빈 데이터 폴더 — 라벨러 칸 하나 더
        dlg.ed_vod.setText(f"https://chzzk.naver.com/video/{VNO}")
        dlg.choose_file = lambda: video
        dlg.btn_pick.click()
        dlg.ed_labeler.setText("L01")
        dlg.btn_start.click()
        return dlg.result()
    res = win.new_broadcast_dialog(run=run)
    assert res is not None and res.ok, res.summary()
    line = f"새 방송 끝: 채팅 {N_CHATS} · 발화 4 · 반응(direct) 2"
    assert win.last_new_broadcast_line == line and win.statusBar().currentMessage() == line
    assert win.last_progress_text.startswith("5/5 엔진")
    assert os.path.dirname(res.project_root) == er.default_projects_root()     # 지금 데이터 폴더의 projects
    assert sess.load_labelers(win.data_root).get("L01") is not None
    assert win.settings.value("session/last_labeler") == "L01"
    wb = win.workbench
    assert wb is not None and wb.session is not None and wb.session.session_kind == "analysis"
    assert wb.analysis_action.isChecked()                            # 작업대 '분석 보기'(엔진 반응 띠)
    assert wb.wait_jobs(15_000)
    qapp.processEvents()
    assert wb.timeline.markers.get(wbm.ENGINE_TRACK)                 # derived\link-v0.3.json 의 판정이 띠에
    assert win.statusBar().currentMessage() == line                  # 작업대 일이 끝난 뒤에도 그 한 줄(리허설 2회차에서 덮임)
    assert not win.shown                                             # 경고·알림 없음
    assert fk.kinds() == ["anchors", "asr", "link"]


def test_mainwindow_new_broadcast_failure_keeps_project_in_recent(qapp, win, tmp_path, monkeypatch):
    from app.ui import project_view as pv
    fk = Fakes(tmp_path).install(monkeypatch)
    monkeypatch.setattr(er, "default_engine_python", lambda: str(tmp_path / "없음.exe"))
    monkeypatch.setattr(er, "default_models_dir", lambda: _models(tmp_path))
    win.settings.setValue("session/last_labeler", "L01")
    res = win.start_new_broadcast(VNO, _video(tmp_path))
    assert res is not None and res.stopped.code == "engine_venv_missing"
    assert win.workbench is None
    assert win.shown and win.shown[-1][0] == "warn" and er.SETUP_HINT in win.shown[-1][1]
    assert res.project_root in pv.load_recent(win.settings)
    assert win.statusBar().currentMessage().startswith("새 방송 멈춤: 5/5 엔진 — engine_venv_missing")
    assert fk.kinds() == ["anchors"]


def test_start_request_opens_prefilled_dialog(win, monkeypatch):
    got = []
    monkeypatch.setattr(win, "new_broadcast_dialog", lambda **kw: got.append(kw))
    assert win.run_start_request({"new_broadcast": {"video_no": VNO, "video_path": "C:\\v\\a.mp4"}}) is None
    assert got == [{"video_no": VNO, "video_path": "C:\\v\\a.mp4"}]


# ---------------------------------------------------------------------------- main.py 인자
def test_main_new_broadcast_args():
    import main
    a = main.parse_args(["--new-broadcast", VNO, "C:\\v\\합성 영상.mp4", "--data-dir", "D:\\d"])
    assert a["new_broadcast"] == [VNO, "C:\\v\\합성 영상.mp4"] and a["data_dir"] == "D:\\d"
    assert main.start_request(a) == {"new_broadcast": {"video_no": VNO,
                                                       "video_path": os.path.abspath("C:\\v\\합성 영상.mp4")}}
    assert main.parse_args(["--new-broadcast", VNO])["new_broadcast"] is None          # 값이 둘 다 있어야
    assert main.parse_args(["--new-broadcast", VNO, "--data-dir", "D:\\d"])["new_broadcast"] is None
    assert main.parse_args([])["new_broadcast"] is None and main.start_request(main.parse_args([])) is None
    b = main.parse_args(["--project", "C:\\p", "--new-broadcast", VNO, "C:\\v.mp4"])
    assert "new_broadcast" in main.start_request(b)                                    # --project 보다 앞선다
