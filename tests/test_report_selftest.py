"""문제 보고(비밀 값 가리기 · build_report) 와 자체 점검(main.py --selftest, 소스 실행).

- redact: ClipSlicer 의 비밀 값(토큰·서명 쿼리·Authorization·Cookie·Bearer·access/refresh_token)에 더해
  네이버 로그인 쿠키(NID_AUT·NID_SES), 40자 이상 16진수, 사용자 홈 경로를 가린다.
- build_report: 'ChatCoach ' 로 시작, 최근 로그 N줄, 폴더 경로를 넣지 않는다.
- --selftest: 결과 JSON 의 점검 항목 집합, 실패 허용은 ffmpeg·ffprobe·libmpv 뿐, --data-dir 존중,
  --expect-version 이 틀리면 version 실패와 exit 1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from app import paths
from app.logging_setup import redact
from app.report import build_report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = {"version", "channel", "pubkeys", "ffmpeg", "ffprobe", "libmpv", "qt_plugins", "settings", "korean_path", "updater"}
MAY_FAIL = {"ffmpeg", "ffprobe", "libmpv"}


# --------------------------------------------------------------------------- redact
def test_redact_hides_clipslicer_secrets():
    s = redact("GET https://cdn.x/a.m3u8?sig=abc123&x=1&token=zzz&hdnts=exp~acl~hmac\n"
               "Authorization: Bearer eyJhbGciOi.xx\nCookie: sess=ok; a=b\nx-auth-token: tok-777\n"
               '{"refresh_token": "r-123", "access_token":"a-9", "client_secret": "cs-1"}\nbearer QWERTY==')
    for secret in ("abc123", "zzz", "exp~acl~hmac", "eyJhbGciOi", "sess=ok", "tok-777", "r-123", "a-9", "cs-1", "QWERTY"):
        assert secret not in s, (secret, s)
    assert "x=1" in s and "cdn.x/a.m3u8" in s


def test_redact_naver_cookies():
    s = redact('NID_AUT=AbCdEf123+/=; NID_SES=AAABBBccc\n{"NID_SES": "sesvalue", "nid_aut": \'autvalue\'}\n'
               "set NID_AUT : spaced-value&next=1")
    for secret in ("AbCdEf123", "AAABBBccc", "sesvalue", "autvalue", "spaced-value"):
        assert secret not in s, (secret, s)
    assert "NID_AUT" in s and "NID_SES" in s and "next=1" in s


def test_redact_long_hex_ids():
    h40 = "0123456789abcdef0123456789ABCDEF01234567"
    h64 = "f" * 64
    s = redact(f"userIdHash={h40} channel {h64} short=0123456789abcdef0123456789abcdef0123456")
    assert h40 not in s and h64 not in s and s.count("<hex>") == 2
    assert "0123456789abcdef0123456789abcdef0123456" in s          # 39자는 그대로


def test_redact_user_home_paths():
    cases = {
        r"C:\Users\홍길동\AppData\Local\ChatCoach\data\x.log": r"%USERPROFILE%\AppData\Local\ChatCoach\data\x.log",
        "C:/Users/john doe/Documents/a.txt": "%USERPROFILE%/Documents/a.txt",
        "'C:\\\\Users\\\\bob\\\\x'": "'%USERPROFILE%\\\\x'",
        r"d:\users\Kim": "%USERPROFILE%",
        r"경로 C:\Users\alice 끝": r"경로 %USERPROFILE% 끝",
    }
    for raw, want in cases.items():
        assert redact(raw) == want, (raw, redact(raw))
    for name in ("홍길동", "john doe", "bob", "alice"):
        assert name not in redact(" ".join(cases))
    assert redact(r"C:\Program Files\ChatCoach") == r"C:\Program Files\ChatCoach"


# --------------------------------------------------------------------------- build_report
def test_build_report(tmp_path):
    log = tmp_path / "chatcoach.log"
    log.write_text("\n".join(f"line {i}" for i in range(300)) +
                   "\nurl?token=SECRET\nNID_SES=COOKIEVAL\n" + r"open C:\Users\someone\Videos\a.mp4" + "\n",
                   encoding="utf-8")
    r = build_report(str(log), {"수집 상태": "대기 중"}, lines=50)
    assert r.startswith("ChatCoach ") and r.endswith("\n")
    assert "수집 상태: 대기 중" in r and "채널: source" in r and "데이터: 지정(--data-dir)" in r
    assert "line 299" in r and "line 200" not in r
    assert "SECRET" not in r and "COOKIEVAL" not in r and "someone" not in r
    assert "최근 로그 50줄 (chatcoach.log)" in r
    # 폴더 경로를 넣지 않는다 (로그 파일 이름만)
    for folder in (str(tmp_path), paths.data_root(), paths.install_root(), os.path.basename(str(tmp_path))):
        assert folder not in r, folder
    assert "최근 로그 0줄" in build_report(str(tmp_path / "none.log"))
    assert "최근 로그 0줄 (-)" in build_report("")


# --------------------------------------------------------------------------- --selftest (소스 실행)
def _run_selftest(tmp_path, *extra):
    out = tmp_path / "st.json"
    data = tmp_path / "selftest-data"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    env.pop("CHATCOACH_TEST_PUBKEY", None)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "--selftest", str(out), "--data-dir", str(data),
                        *extra],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env, cwd=ROOT)
    assert out.exists(), (r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    return r, json.loads(out.read_text(encoding="utf-8")), data


def test_selftest_from_source(tmp_path):
    r, rep, data = _run_selftest(tmp_path)
    assert set(rep["checks"]) == CHECKS
    failed = {k: v["detail"] for k, v in rep["checks"].items() if not v["ok"]}
    assert set(failed) <= MAY_FAIL, failed                    # bin\ 이 있으면 ffmpeg·libmpv 도 통과한다
    assert r.returncode == (0 if not failed else 1), (r.returncode, failed)
    assert rep["ok"] is (not failed) and rep["frozen"] is False
    # --data-dir 를 따랐다 (저장소 data\ 를 쓰지 않음)
    assert os.path.normcase(rep["data"]) == os.path.normcase(str(data))
    assert rep["checks"]["settings"]["ok"] and str(data) in rep["checks"]["settings"]["detail"]
    assert rep["checks"]["channel"]["detail"].startswith("source")


def test_selftest_expect_version_mismatch(tmp_path):
    r, rep, _ = _run_selftest(tmp_path, "--expect-version", "9.9.9")
    assert r.returncode == 1
    assert rep["ok"] is False and rep["checks"]["version"]["ok"] is False
    assert "9.9.9" in rep["checks"]["version"]["detail"]
