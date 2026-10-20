"""릴리스 태그 검사 (release.yml build 잡 2단계, docs/design/04-update-and-release.md 4-2).

사용: python tools/check_tag.py vX.Y.Z

확인하는 것 (하나라도 틀리면 사유를 출력하고 exit 1):
  1. 태그 형식이 ^v<숫자>.<숫자>.<숫자>$ 이다 (접미사 없음, 04 2-2)
  2. 태그의 X.Y.Z 가 VERSION 과 같다
  3. 생성 파일(app/__init__.py · assets/ChatCoach.manifest)이 VERSION 과 맞다 (sync_version.py --check 와 같은 검사)
  4. 태그 커밋이 지금 작업 트리의 커밋(HEAD)이다 — 아니면 2·3 의 비교가 그 태그의 내용이 아니다
  5. 태그 커밋이 origin/main(없으면 main)에 포함된다 (git merge-base --is-ancestor)
"""
import sys

# CI 러너처럼 콘솔 인코딩이 UTF-8 이 아닌 곳에서도 한글 출력이 죽지 않게
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import os
import re
import subprocess

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import sync_version  # noqa: E402

TAG_RE = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)", re.ASCII)


def _decode(data: bytes) -> str:
    """git 출력은 bytes 로 받아 utf-8 → cp949 순으로 푼다 (콘솔 코드 페이지와 어긋나는 문제)."""
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def git(*args: str) -> tuple[int, str]:
    """git 을 저장소 루트에서 실행해 (종료 코드, stdout+stderr 앞뒤 공백 제거) 를 돌려준다."""
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    except OSError as ex:
        return 127, f"git 을 실행하지 못했습니다: {ex}"
    out = _decode(p.stdout).strip()
    if p.returncode != 0:
        err = _decode(p.stderr).strip()
        out = (out + "\n" + err).strip()
    return p.returncode, out


def _commit_of(ref: str) -> str | None:
    code, out = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out if code == 0 and out else None


def check(tag: str) -> list[str]:
    """실패 사유 목록 (비면 통과)."""
    errors: list[str] = []
    m = TAG_RE.fullmatch(tag or "")
    if not m:
        return [f"태그 형식이 아닙니다 (vX.Y.Z, 숫자만): {tag!r}"]
    tag_version = m.group(1)

    try:
        version = sync_version.read_version()
    except (OSError, ValueError) as ex:
        return [f"VERSION 을 읽지 못했습니다: {ex}"]
    if version != tag_version:
        errors.append(f"태그({tag_version})와 VERSION({version})이 다릅니다")

    bad = sync_version.diff_files(version)
    if bad:
        errors.append(f"생성 파일이 VERSION({version})과 다릅니다: {', '.join(bad)} (python tools/sync_version.py 를 잊었나요?)")

    tag_commit = _commit_of(f"refs/tags/{tag}")
    if not tag_commit:
        errors.append(f"태그 {tag} 를 찾지 못했습니다 (fetch-depth: 0 · 태그 push 확인)")
        return errors
    head = _commit_of("HEAD")
    if head != tag_commit:
        errors.append(f"작업 트리(HEAD {head or '?'})가 태그 커밋({tag_commit})이 아닙니다 — 검사한 파일이 태그의 내용이 아닙니다")

    base = "origin/main" if _commit_of("refs/remotes/origin/main") else ("main" if _commit_of("refs/heads/main") else None)
    if base is None:
        errors.append("origin/main 도 main 도 없습니다 (checkout 에 fetch-depth: 0 이 필요합니다)")
        return errors
    code, out = git("merge-base", "--is-ancestor", tag_commit, base)
    if code == 1:
        errors.append(f"태그 커밋({tag_commit[:12]})이 {base} 에 포함되지 않습니다 — main 에 병합된 커밋에만 태그를 겁니다")
    elif code != 0:
        errors.append(f"git merge-base 실패 (exit {code}): {out}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("사용: python tools/check_tag.py vX.Y.Z", file=sys.stderr)
        return 1
    tag = argv[0]
    errors = check(tag)
    if errors:
        print(f"태그 검사 실패: {tag}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"태그 검사 통과: {tag} = VERSION {sync_version.read_version()}, 생성 파일 일치, main 에 포함")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
