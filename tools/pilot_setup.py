"""시범 영상 폴더 → 프로젝트 준비 (08 D-14 5·6).

시범 영상은 C:\\Users\\<사용자>\\Videos\\ChatCoach-pilot\\ 에 ClipSlicer 기본 이름으로 둔다(D-14 6). 이 도구는 파일 이름에서
VOD 번호와 구간을 읽어, 영상마다 기존 도구를 차례로 부른다.

  1. tools/new_project.py  — 프로젝트 만들기 (<데이터>\\projects\\<slug>)
  2. tools/fetch_chat.py   — 그 VOD 의 채팅 전량 받기
  3. 영상 연결             — app.media.media_ref 로 project.json 의 media.current 를 채움(비어 있을 때만)
  4. tools/find_anchors.py — 시각 보정 앵커 '후보' (구간 안 채팅만, 본문은 찍지 않음)

보정은 **제안까지만** 한다. 앵커는 사람이 다시보기에서 듣고 확인해야 하므로 calibrate.py 를 부르지 않고, 채워 넣을
명령 모양(--file-start-ms 포함)만 찍는다.

읽는 이름 (확장자 .mp4 .mkv .webm .ts .mov .m4v, 폴더 바로 아래만)
  (가) ClipSlicer '구간만 받기' 기본 이름: '<제목> [<VOD번호>] <시작>-<끝>.mp4'
       시각은 ClipSlicer segments.fmt_time_file 꼴 — 1시간 이상이면 'HHhMMmSSs', 아니면 'MMmSSs' (초 단위, 버림).
       같은 이름이 있을 때 ClipSlicer 가 붙이는 '_N' 꼬리도 받는다(경고).
  (나) '<VOD번호>_<시작>-<끝>' — 시각은 위와 같은 꼴 또는 초(정수).
  클립 파일 0초 = VOD 의 <시작> 이므로 file_start_ms = 시작(ms). 채팅 시각 축(messages.t_ms)이 VOD 시각이다.

사용
  python tools/pilot_setup.py --dry-run                  이름 해석만 (아무것도 만들지 않음)
  python tools/pilot_setup.py [--videos <폴더>] [--projects-root <폴더>] [--labeler L01] [--engine-author yes|no]
                              [--top 20] [--show-text] [--no-link-media] [--skip-chat]

이미 있는 프로젝트(같은 video_no)는 **이 도구가 만든 것만**(<데이터>\\pilot\\setup.json 에 적힌 것) 이어서 채운다.
다른 프로젝트(손으로 만든 실제 프로젝트 등)에는 쓰지 않고 건너뛴다.
출력에는 파일 이름·번호·시각·개수만 찍는다. 채팅 본문·닉네임은 찍지 않는다(--show-text 를 주면 find_anchors 가 본문을 화면에 찍는다).
종료 코드: 0 정상 · 1 입력 오류(폴더 없음·읽을 이름 없음) · 2 일부 단계 실패
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import timecode as tc  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(ROOT), "chatcoach-data")
DEFAULT_VIDEOS = os.path.join(os.path.expanduser("~"), "Videos", "ChatCoach-pilot")
DEFAULT_PROJECTS = os.path.join(DATA_DIR, "projects")
DEFAULT_PILOT_DIR = os.path.join(DATA_DIR, "pilot")
STATE_NAME = "setup.json"
STATE_SCHEMA = "chatcoach/pilot-setup"
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".ts", ".mov", ".m4v")
PILOT_MIN_MS, PILOT_MAX_MS = 30 * 60_000, 40 * 60_000     # D-14 5: 한 편 30~40분
PILOT_COUNT = 3                                          # D-14 5: 3편

# ClipSlicer segments.fmt_time_file: f"{h:02d}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"
_T = r"(?:\d{1,3}h)?\d{1,2}m\d{1,2}s"
_T_OR_SEC = rf"(?:{_T}|\d{{1,6}})"
# 실제 시범 폴더의 ClipSlicer 이름은 'HHMMSS-HHMMSS' (여섯 자리, 예: 000000-034900 = 0초~3시간 49분)도 있다.
# (가) 규칙에서만 여섯 자리를 시분초로 읽는다 — (나) 규칙의 숫자는 초다.
_T_CS = rf"(?:{_T}|\d{{6}})"
CLIPSLICER_RE = re.compile(rf"\A(?P<title>.*)\s*\[(?P<vno>\d{{1,12}})\]\s+(?P<start>{_T_CS})-(?P<end>{_T_CS})(?:_(?P<dup>\d+))?\Z")
PLAIN_RE = re.compile(rf"\A(?P<vno>\d{{1,12}})_(?P<start>{_T_OR_SEC})-(?P<end>{_T_OR_SEC})(?:_(?P<dup>\d+))?\Z")
_HMS_RE = re.compile(r"\A(?:(\d+)h)?(\d+)m(\d+)s\Z")


@dataclass
class ClipName:
    """파일 이름 하나를 읽은 결과."""
    file_name: str
    video_no: str
    start_ms: int
    end_ms: int
    title: str | None          # (가) 이름의 제목 부분. (나) 이면 None
    pattern: str               # 'clipslicer' | 'plain'
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def parse_time_token(tok: str) -> int | None:
    """'01h20m00s' · '20m00s' · '4800'(초) → ms. 분·초는 0~59 만."""
    m = _HMS_RE.match(tok)
    if m:
        h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if mi > 59 or s > 59:
            return None
        return ((h * 60 + mi) * 60 + s) * 1000
    if tok.isdigit():
        return int(tok) * 1000
    return None


def parse_hhmmss(tok: str) -> int | None:
    """'034900' (HHMMSS 여섯 자리) → ms. 분·초는 0~59 만. 여섯 자리가 아니면 None."""
    if len(tok) != 6 or not tok.isdigit():
        return None
    h, mi, s = int(tok[:2]), int(tok[2:4]), int(tok[4:])
    if mi > 59 or s > 59:
        return None
    return ((h * 60 + mi) * 60 + s) * 1000


def parse_clip_name(file_name: str) -> ClipName | None:
    """파일 이름(경로 아님) → ClipName. 영상 확장자가 아니거나 두 규칙에 맞지 않으면 None."""
    stem, ext = os.path.splitext(os.path.basename(file_name))
    if ext.lower() not in VIDEO_EXTS:
        return None
    for pattern, rx in (("clipslicer", CLIPSLICER_RE), ("plain", PLAIN_RE)):
        m = rx.match(stem)
        if not m:
            continue
        if pattern == "clipslicer":
            a, b = ((parse_hhmmss(t) if len(t) == 6 and t.isdigit() else parse_time_token(t))
                    for t in (m.group("start"), m.group("end")))
        else:
            a, b = parse_time_token(m.group("start")), parse_time_token(m.group("end"))
        if a is None or b is None or b <= a:
            return None
        vno = str(int(m.group("vno")))          # 앞의 0 을 떼어 치지직 번호 꼴로
        if vno == "0":
            return None
        title = m.groupdict().get("title")
        title = title.strip() if title is not None else None
        c = ClipName(os.path.basename(file_name), vno, a, b, title or None, pattern)
        if m.group("dup"):
            c.warnings.append(f"이름 끝에 '_{m.group('dup')}' 이 있습니다. 같은 이름 충돌 사본이면 그대로 쓰면 되지만, "
                              "받은 파일을 다시 자른 조각이면 시작 시각이 이름과 다릅니다")
        if not PILOT_MIN_MS <= c.duration_ms <= PILOT_MAX_MS:
            c.warnings.append(f"길이 {c.duration_ms // 60000}분 — 시범 기준 30~40분(D-14 5) 밖입니다")
        return c
    return None


def scan(folder: str) -> tuple[list[ClipName], list[str]]:
    """폴더 바로 아래 파일 → (읽은 것, 읽지 못한 영상 파일 이름). 이름 순."""
    clips, skipped = [], []
    for name in sorted(os.listdir(folder)):
        if not os.path.isfile(os.path.join(folder, name)):
            continue
        c = parse_clip_name(name)
        if c is not None:
            clips.append(c)
        elif os.path.splitext(name)[1].lower() in VIDEO_EXTS:
            skipped.append(name)
    seen: dict[str, str] = {}
    for c in clips:
        if c.video_no in seen:
            c.warnings.append(f"같은 VOD 번호의 다른 파일이 있습니다({seen[c.video_no]}). 프로젝트는 VOD 하나에 하나라 "
                              "먼저 나온 파일만 영상으로 연결합니다")
        else:
            seen[c.video_no] = c.file_name
    return clips, skipped


def anchor_plan(clip: ClipName) -> dict:
    """find_anchors·calibrate 에 줄 값. 채팅 시각 범위 = 클립이 덮는 VOD 구간 (find_anchors 는 정수를 ms 로 읽는다)."""
    return {"file_start_ms": clip.start_ms, "chat_from": f"{clip.start_ms}ms", "chat_to": f"{clip.end_ms}ms"}


def calibrate_hint(project_root: str, clip: ClipName) -> str:
    return (f".venv\\Scripts\\python tools\\calibrate.py --project \"{project_root}\" --file-start-ms {clip.start_ms} "
            "--anchor <영상시각>=<채팅시각> --anchor <영상시각>=<채팅시각> --anchor <영상시각>=<채팅시각> --dry-run")


# ---------------------------------------------------------------------------------------------- 상태 파일

def load_state(pilot_dir: str) -> dict:
    p = os.path.join(pilot_dir, STATE_NAME)
    if not os.path.exists(p):
        return {"schema": STATE_SCHEMA, "version": 1, "items": []}
    with open(p, "rb") as f:
        d = json.loads(f.read().decode("utf-8"))
    if not isinstance(d, dict) or d.get("schema") != STATE_SCHEMA or not isinstance(d.get("items"), list):
        raise ValueError(f"{STATE_NAME} 형식이 아닙니다")
    return d


def save_state(pilot_dir: str, state: dict) -> None:
    from app.project import atomic_write
    os.makedirs(pilot_dir, exist_ok=True)
    atomic_write(os.path.join(pilot_dir, STATE_NAME),
                 (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _state_item(state: dict, video_no: str) -> dict | None:
    return next((it for it in state["items"] if it.get("video_no") == video_no), None)


def find_projects_for(projects_root: str, video_no: str) -> list[str]:
    """projects_root 바로 아래에서 broadcast.video_no 가 같은 프로젝트 폴더들 (읽기만)."""
    out = []
    if not os.path.isdir(projects_root):
        return out
    for name in sorted(os.listdir(projects_root)):
        pj = os.path.join(projects_root, name, "project.json")
        try:
            with open(pj, "rb") as f:
                d = json.loads(f.read().decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        if str(((d.get("broadcast") or {}) if isinstance(d, dict) else {}).get("video_no")) == video_no:
            out.append(os.path.join(projects_root, name))
    return out


# ---------------------------------------------------------------------------------------------- 도구 부르기

def run_tool(name: str, args: list[str], *, capture: bool) -> tuple[int, str]:
    """tools/<name>.py 를 같은 파이썬으로 부른다. capture=False 면 출력이 그대로 화면에 나온다(진행률)."""
    cmd = [sys.executable, os.path.join(TOOLS, f"{name}.py"), *args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if capture:
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", errors="replace")
        if r.stderr:
            sys.stderr.write(r.stderr)
        return r.returncode, r.stdout
    r = subprocess.run(cmd, cwd=ROOT, env=env)
    return r.returncode, ""


def link_media(project_root: str, video_path: str, by: str) -> str:
    """project.json media.current 가 비어 있으면 이 영상을 연결한다. 결과 한 마디('linked'·'same'·'other')."""
    from app import media, project
    ref = media.media_ref(video_path, project_root)
    p = project.open_project(project_root)
    if p.read_only:
        raise RuntimeError("읽기 전용 프로젝트: " + "; ".join(p.problems[:2]))
    cur = ((p.data or {}).get("media") or {}).get("current")
    if cur:
        same = cur.get("partial_fp") == ref["partial_fp"] and cur.get("size") == ref["size"]
        return "same" if same else "other"

    def mutate(d: dict) -> None:
        m = d.setdefault("media", {"media_rev": 0, "current": None, "history": []})
        if m.get("current") is None:
            m["media_rev"] = int(m.get("media_rev", 0)) + 1
            m["current"] = ref
    p.update(mutate, by=by)
    return "linked"


# ---------------------------------------------------------------------------------------------- 한 편

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def setup_one(clip: ClipName, *, videos: str, projects_root: str, pilot_dir: str, state: dict, labeler: str,
              engine_author: str | None, top: int, show_text: bool, do_media: bool, do_chat: bool,
              primary_file: bool) -> bool:
    """한 편 준비. 실패한 단계가 있으면 False. 상태는 단계마다 저장한다."""
    ok = True
    item = _state_item(state, clip.video_no)
    existing = find_projects_for(projects_root, clip.video_no)
    if item is None:
        if existing:
            print(f"  건너뜀: 같은 VOD 의 프로젝트가 이미 있습니다({len(existing)}개) — 이 도구가 만든 것이 아니라 쓰지 않습니다")
            return True
        argv = ["--root", projects_root, "--title", clip.title or f"시범 {clip.video_no}", "--video-no", clip.video_no,
                "--labeler", labeler]
        if engine_author:
            argv += ["--engine-author", engine_author]
        code, out = run_tool("new_project", argv, capture=True)
        root = out.strip().splitlines()[0].strip() if out.strip() else ""
        if code != 0 or not root:
            print(f"  실패: 프로젝트 만들기 (종료 코드 {code})")
            return False
        pid = None
        try:
            with open(os.path.join(root, "project.json"), "rb") as f:
                pid = json.loads(f.read().decode("utf-8")).get("project_id")
        except (OSError, ValueError):
            pass
        item = {"video_no": clip.video_no, "file_name": clip.file_name, "start_ms": clip.start_ms,
                "end_ms": clip.end_ms, "project_root": root, "project_id": pid, "created_at": _now_iso(),
                "steps": {"project": "done"}}
        state["items"].append(item)
        save_state(pilot_dir, state)
        print(f"  프로젝트: 새로 만듦 ({os.path.basename(root)})")
    else:
        root = item["project_root"]
        if os.path.normcase(os.path.abspath(root)) not in {os.path.normcase(os.path.abspath(e)) for e in existing}:
            print("  실패: setup.json 에 적힌 프로젝트 폴더가 없습니다 (옮겼거나 지웠으면 setup.json 의 그 항목을 지우세요)")
            return False
        print(f"  프로젝트: 이 도구가 만든 것 이어서 ({os.path.basename(root)})")
    steps = item.setdefault("steps", {})

    if do_chat and steps.get("chat") != "done":
        code, _ = run_tool("fetch_chat", [clip.video_no, "--project", root], capture=False)
        steps["chat"] = "done" if code == 0 else f"failed:{code}"
        save_state(pilot_dir, state)
        if code != 0:
            print(f"  실패: 채팅 받기 (종료 코드 {code}; 3 이면 끝까지 받지 못함). 다시 돌리면 이어서 시도합니다")
            ok = False
    elif steps.get("chat") == "done":
        print("  채팅: 이미 받음")

    if do_media and primary_file and steps.get("media") not in ("linked", "same"):
        try:
            res = link_media(root, os.path.join(videos, clip.file_name), labeler)
        except Exception as e:  # noqa: BLE001 — ffprobe 없음·경로 문제·저장 충돌: 알리고 다음 단계로
            steps["media"] = f"failed:{type(e).__name__}"
            print(f"  실패: 영상 연결 ({type(e).__name__}). 작업대에서 직접 연결해도 됩니다")
            ok = False
        else:
            steps["media"] = res
            print({"linked": "  영상: 연결함", "same": "  영상: 이미 같은 파일",
                   "other": "  영상: 다른 파일이 이미 연결돼 있어 바꾸지 않았습니다"}[res])
        save_state(pilot_dir, state)

    chat_ready = os.path.exists(os.path.join(root, "chat", "messages.jsonl"))
    if not chat_ready:
        print("  앵커 후보: 채팅이 없어 건너뜀")
        return ok
    ap = anchor_plan(clip)
    argv = ["--project", root, "--file-start-ms", str(ap["file_start_ms"]), "--from", ap["chat_from"],
            "--to", ap["chat_to"], "--top", str(top)]
    if not show_text:
        argv.append("--no-text")
    code, out = run_tool("find_anchors", argv, capture=True)
    if code != 0:
        steps["anchors"] = f"failed:{code}"
        save_state(pilot_dir, state)
        print(f"  실패: 앵커 후보 (종료 코드 {code})")
        return False
    sys.stdout.write(out)
    if not show_text:                 # 본문이 없는 출력만 파일로 남긴다
        adir = os.path.join(pilot_dir, "anchors")
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, f"{clip.video_no}.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
    steps["anchors"] = "suggested"
    save_state(pilot_dir, state)
    print("  보정(제안만, 저장하지 않음): 다시보기에서 3개 이상 확인한 뒤 아래 명령의 <영상시각> 을 채워 돌린다.")
    print("    영상시각 = 클립 파일 0초부터의 시각, 채팅시각 = VOD 시각(위 표의 채팅 시각)")
    print("    " + calibrate_hint(root, clip))
    return ok


# ---------------------------------------------------------------------------------------------- main

def print_plan(clips: list[ClipName], skipped: list[str]) -> None:
    print(f"읽은 영상 {len(clips)}개 · 이름을 읽지 못한 영상 {len(skipped)}개")
    print(" #  VOD 번호      시작(VOD)       끝(VOD)         길이     규칙        file_start_ms  파일")
    for i, c in enumerate(clips, 1):
        print(f"{i:>2}  {c.video_no:<12}  {tc.fmt_display(c.start_ms):<14}  {tc.fmt_display(c.end_ms):<14}  "
              f"{c.duration_ms // 60000:>3}분   {c.pattern:<10}  {c.start_ms:<13}  {c.file_name}")
        for w in c.warnings:
            print(f"      경고: {w}")
    for s in skipped:
        print(f"    읽지 못함: {s}")
    if len(clips) != PILOT_COUNT:
        print(f"참고: 시범은 {PILOT_COUNT}편입니다(D-14 5). 지금 {len(clips)}편")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시범 영상 폴더 → 프로젝트·채팅·앵커 후보 (보정은 제안만)")
    ap.add_argument("--videos", default=DEFAULT_VIDEOS, help="시범 영상 폴더 (기본 ~\\Videos\\ChatCoach-pilot)")
    ap.add_argument("--projects-root", default=DEFAULT_PROJECTS, help="프로젝트를 만들 폴더")
    ap.add_argument("--pilot-dir", default=DEFAULT_PILOT_DIR, help="setup.json·앵커 후보를 둘 폴더")
    ap.add_argument("--labeler", default="L01")
    ap.add_argument("--engine-author", choices=("yes", "no"), default=None)
    ap.add_argument("--top", type=int, default=20, help="앵커 후보 개수")
    ap.add_argument("--show-text", action="store_true", help="find_anchors 가 채팅 본문도 화면에 찍게 한다(파일로는 남기지 않음)")
    ap.add_argument("--no-link-media", action="store_true", help="영상을 project.json 에 연결하지 않는다")
    ap.add_argument("--skip-chat", action="store_true", help="채팅 받기를 건너뛴다(이미 받았을 때)")
    ap.add_argument("--dry-run", action="store_true", help="이름 해석만 한다")
    a = ap.parse_args(argv)

    if not os.path.isdir(a.videos):
        print(f"오류: 시범 영상 폴더가 없습니다: {a.videos}", file=sys.stderr)
        return 1
    if a.top <= 0:
        print("오류: --top 은 1 이상", file=sys.stderr)
        return 1
    clips, skipped = scan(a.videos)
    print_plan(clips, skipped)
    if not clips:
        print("오류: 읽을 수 있는 이름의 영상이 없습니다", file=sys.stderr)
        return 1
    if a.dry_run:
        print("--dry-run: 아무것도 만들지 않았습니다")
        return 0

    try:
        state = load_state(a.pilot_dir)
    except (OSError, ValueError) as e:
        print(f"오류: 상태 파일을 읽지 못했습니다: {e}", file=sys.stderr)
        return 1
    all_ok = True
    first_of: dict[str, str] = {}
    for c in clips:
        first_of.setdefault(c.video_no, c.file_name)
    for i, c in enumerate(clips, 1):
        print(f"[{i}/{len(clips)}] VOD {c.video_no} · {tc.fmt_display(c.start_ms)}~{tc.fmt_display(c.end_ms)}")
        primary = first_of[c.video_no] == c.file_name
        if not primary:
            print("  같은 VOD 의 두 번째 파일이라 영상 연결은 하지 않습니다")
        all_ok &= setup_one(c, videos=a.videos, projects_root=a.projects_root, pilot_dir=a.pilot_dir, state=state,
                            labeler=a.labeler, engine_author=a.engine_author, top=a.top, show_text=a.show_text,
                            do_media=not a.no_link_media, do_chat=not a.skip_chat, primary_file=primary)
    print("끝: " + ("모든 단계 정상" if all_ok else "실패한 단계가 있습니다 — 다시 돌리면 남은 단계만 합니다"))
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
