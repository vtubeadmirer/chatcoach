"""릴리스 한 번에 — release.cmd 더블클릭으로 실행한다.

  버전 정하기 → 릴리스 노트 → VERSION·생성 파일 → 전체 테스트 → 커밋·push → 태그 push
  → GitHub Actions 빌드 대기 → 초안 확인(해시 재계산) → 서명(암호 1회) → 서명 업로드 → 게시 → latest 전환

사용: python tools/release_oneclick.py [--version X.Y.Z] [--dry-run]
  --version 을 안 주면 지금 VERSION 의 패치 번호 +1 을 제안한다 (Enter 로 수락).
  --dry-run 은 검사·테스트까지만 하고 커밋·push·태그를 하지 않는다.

서명 키는 %LOCALAPPDATA%\\StreamLabeler-signing\\streamlabeler-K1.key. 같은 폴더에
streamlabeler-K1.password.txt 가 있으면 그 암호를 쓰고, 없으면 한 번 묻는다.
설계 문서 04 4-2·13장의 순서를 그대로 따른다. 중간에 실패하면 거기서 멈추고, 다시 실행하면 끝난 단계는 건너뛴다.
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
import shutil
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.appspec import SPEC  # noqa: E402

KEY_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StreamLabeler-signing")
KEY = os.path.join(KEY_DIR, "streamlabeler-K1.key")
PW_FILE = os.path.join(KEY_DIR, "streamlabeler-K1.password.txt")
GENERATED = ["VERSION", "app/__init__.py", "assets/StreamLabeler.manifest"]


def say(msg: str) -> None:
    print(f"\n▶ {msg}", flush=True)


def fail(msg: str) -> int:
    print(f"\n✗ 중단: {msg}", flush=True)
    return 1


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=capture, text=capture, encoding="utf-8" if capture else None,
                       errors="replace" if capture else None)
    if check and r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "") if capture else ""
        raise SystemExit(f"\n✗ 중단: 명령 실패 (exit {r.returncode}): {' '.join(cmd)}\n{out}")
    return r


def git(*args: str) -> str:
    return run(["git", *args], capture=True).stdout.strip()


def gh_exe() -> str:
    exe = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    if not os.path.isfile(exe):
        raise SystemExit("✗ gh CLI 가 없습니다: winget install --id GitHub.cli")
    return exe


def read_version() -> str:
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8-sig") as f:
        return f.read().strip()


def bump_patch(v: str) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + 1}"


def ensure_notes(version: str) -> str:
    path = os.path.join(ROOT, "docs", "release-notes", f"v{version}.md")
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("min_supported: 0.0.1\nmandatory: false\n---\n여기에 바뀐 점을 한국어 일반 텍스트로 적고 저장한 뒤 메모장을 닫으세요.\n")
        say(f"릴리스 노트를 메모장으로 엽니다. 적고 저장한 뒤 닫으세요: {os.path.relpath(path, ROOT)}")
        subprocess.run(["notepad.exe", path])
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    if "여기에 바뀐 점을" in text:
        raise SystemExit("✗ 중단: 릴리스 노트를 고치지 않았습니다. 다시 실행하세요")
    return path


def wait_for_release_run(gh: str, tag: str) -> None:
    say(f"GitHub Actions 빌드를 기다립니다 ({tag}, 보통 10분 안팎)")
    run_id = None
    for _ in range(60):
        out = run([gh, "run", "list", "-R", SPEC.repo, "--workflow", "release.yml", "--limit", "20",
                   "--json", "databaseId,headBranch,event"], capture=True).stdout
        for r in json.loads(out or "[]"):
            if r.get("headBranch") == tag and r.get("event") == "push":
                run_id = r["databaseId"]
                break
        if run_id:
            break
        time.sleep(5)
    if not run_id:
        raise SystemExit(f"✗ 중단: {tag} 의 Release 워크플로 실행을 찾지 못했습니다")
    print(f"  https://github.com/{SPEC.repo}/actions/runs/{run_id}")
    r = run([gh, "run", "watch", str(run_id), "-R", SPEC.repo, "--exit-status", "--interval", "15"], check=False)
    if r.returncode != 0:
        raise SystemExit("✗ 중단: 빌드가 실패했습니다. 원인을 고친 뒤 '다음 번호' 로 다시 내세요 (같은 번호는 다시 쓰지 않습니다)")


def release_state(gh: str, tag: str) -> dict | None:
    r = run([gh, "release", "view", tag, "-R", SPEC.repo, "--json", "isDraft,isPrerelease,assets"], check=False, capture=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    py = sys.executable
    os.chdir(ROOT)

    # 0. 사전 검사
    say("사전 검사")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        return fail("main 브랜치가 아닙니다")
    gh = gh_exe()
    if run([gh, "auth", "status"], check=False, capture=True).returncode != 0:
        return fail("gh 로그인이 필요합니다: gh auth login")
    if not os.path.isfile(KEY):
        return fail(f"서명 키가 없습니다: {KEY}")
    run(["git", "fetch", "-q", "origin", "--tags"])
    current = read_version()
    resume_tag = None
    if git("tag", "-l", f"v{current}") and git("rev-parse", "HEAD") == git("rev-list", "-n", "1", f"v{current}"):
        st = release_state(gh, f"v{current}")
        if st is None or st["isDraft"] or st["isPrerelease"]:
            resume_tag = f"v{current}"
            print(f"  v{current} 태그가 이미 있고 릴리스가 끝나지 않았습니다 → 이어서 진행")

    if resume_tag:
        version, tag = current, resume_tag
    else:
        version = a.version or input(f"  새 버전 [{bump_patch(current)}]: ").strip() or bump_patch(current)
        try:
            SPEC._check_version(version)
        except ValueError as ex:
            return fail(str(ex))
        tag = f"v{version}"
        # 이번 버전의 릴리스 노트는 미리 써 둬도 된다. 그 밖의 변경이 있으면 멈춘다
        own_notes = f"docs/release-notes/v{version}.md"
        dirty = [ln for ln in git("status", "--porcelain", "--untracked-files=all").splitlines()
                 if ln[3:].strip().strip('"') != own_notes]
        if dirty:
            return fail("커밋하지 않은 변경이 있습니다. 먼저 정리하세요:\n" + "\n".join(dirty))
        if tuple(map(int, version.split("."))) <= tuple(map(int, current.split("."))):
            return fail(f"{version} 은 지금 버전 {current} 보다 커야 합니다")
        if git("tag", "-l", tag) or git("ls-remote", "--tags", "origin", f"refs/tags/{tag}"):
            return fail(f"{tag} 태그가 이미 있습니다. 번호는 다시 쓰지 않습니다")
        notes = ensure_notes(version)

        # 1. VERSION · 생성 파일 · 테스트
        say(f"버전 {current} → {version}")
        with open(os.path.join(ROOT, "VERSION"), "w", encoding="utf-8", newline="\n") as f:
            f.write(version + "\n")
        run([py, "tools/sync_version.py"])
        say("전체 테스트 (실제 robocopy 교체 시험 포함, 1~2분)")
        r = run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider"], check=False)
        if r.returncode != 0:
            run(["git", "checkout", "--", *GENERATED])
            return fail("테스트 실패 — 버전 변경은 되돌렸습니다")
        if a.dry_run:
            run(["git", "checkout", "--", *GENERATED])
            if os.path.basename(notes) not in git("ls-files", "docs/release-notes"):
                print(f"  (시험 실행) 릴리스 노트 {os.path.relpath(notes, ROOT)} 는 남겨 둡니다")
            say("시험 실행 끝: 검사·테스트 통과. 커밋·push·태그는 하지 않았습니다")
            return 0

        # 2. 커밋 · push · 태그 (커밋과 push 가 성공했을 때만 태그)
        say("커밋 · push · 태그")
        run(["git", "add", "-u"])
        run(["git", "add", *GENERATED, os.path.relpath(notes, ROOT)])
        left = [ln for ln in git("status", "--porcelain").splitlines() if ln.startswith("??")]
        if left:
            return fail("추적하지 않은 파일이 남아 있습니다 (개인정보가 딸려 올라가지 않게 멈춤):\n" + "\n".join(left))
        with open(notes, encoding="utf-8-sig") as f:
            first = f.read().split("\n---\n", 1)[-1].strip().splitlines()[0][:72]
        run(["git", "commit", "-q", "-m", f"{tag}: {first}"])
        run(["git", "push", "-q", "origin", "main"])
        run(["git", "tag", tag, "-m", f"{SPEC.name} {version}"])
        run(["git", "push", "-q", "origin", tag])

    # 3. 빌드 대기
    st = release_state(gh, tag)
    if st is None:
        wait_for_release_run(gh, tag)
        st = release_state(gh, tag)
        if st is None:
            return fail("빌드는 끝났는데 초안 릴리스가 없습니다")
    names = {x["name"] for x in st.get("assets", [])}

    import publish_release
    import sign_manifest
    out_dir = os.path.join(ROOT, "out", tag)
    # 4. 초안 확인 → 서명 → 업로드
    if "latest.json.minisig" not in names:
        say("초안 확인 (zip 을 받아 해시를 다시 계산)")
        if publish_release.main(["check", tag, "--out", out_dir]) != 0:
            return fail("초안 확인 실패 — 서명하지 않았습니다")
        say("서명 (K1)")
        sign_args = ["sign", os.path.join(out_dir, "latest.json"), "--key", KEY]
        if os.path.isfile(PW_FILE):
            sign_args += ["--password-file", PW_FILE]
        if sign_manifest.main(sign_args) != 0:
            return fail("서명 실패")
        if publish_release.main(["upload-sig", tag, os.path.join(out_dir, "latest.json.minisig")]) != 0:
            return fail("서명 업로드 실패")
    # 5. 게시 → latest
    st = release_state(gh, tag) or {}
    if st.get("isDraft"):
        say("게시 (prerelease)")
        if publish_release.main(["publish", tag]) != 0:
            return fail("게시 실패")
    say("latest 로 전환하고 공개 링크의 서명을 확인")
    if publish_release.main(["make-latest", tag]) != 0:
        print("  공개 링크 확인이 아직 실패합니다 (CDN 캐시일 수 있음). 몇 분 뒤 다음으로 다시 확인하세요:")
        print(f"  python tools\\sign_manifest.py verify --url {SPEC.latest_download_url('latest.json')}")
    say(f"완료: https://github.com/{SPEC.repo}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    code = main()
    if os.environ.get("RELEASE_PAUSE") == "1":   # 더블클릭으로 연 창이 결과를 보기 전에 닫히지 않게
        try:
            input("\nEnter 를 누르면 창을 닫습니다…")
        except EOFError:
            pass
    sys.exit(code)
