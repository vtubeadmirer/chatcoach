"""시범 해제 당일 리허설 도구 (tools/pilot_rehearsal.py, 08 D-23 3·4, 24 5-3 의 4) — 짧게: 도구가 돌아 종료 0.

도구는 합성 시범 3편만 만들고 %LOCALAPPDATA%\\ChatCoachSmoke 아래(시험 중에는 conftest 가 바꾼 LOCALAPPDATA 아래)에서만
쓰며 끝나면 지운다. 실제 시범 폴더·봉인 폴더는 열지 않는다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "pilot_rehearsal.py")


def test_rehearsal_attach_mode_exits_0_and_cleans_up():
    """'판정만 봉인 → 잠금 → attach-pairs → 해제' 가 종료 0, 짝 셋 모두 대조 ok, 사람 클릭 0, 자리를 지움."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    r = subprocess.run([sys.executable, TOOL, "--json"], capture_output=True, text=True, encoding="utf-8",
                       env=env, cwd=ROOT, timeout=600)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    rec = json.loads(r.stdout.strip().splitlines()[-1])
    assert rec["failed_checks"] == [] and rec["removed"] is True and rec["user_clicks"] == 0
    for k in ("attach_pairs_exit_0", "attach_keeps_presealed", "unseal_day_exit_0", "pairs_three_each_2_no_error",
              "verify_all_pairs_ok", "ai_seal_verify_ok", "pilot_folders_unchanged", "no_text_in_outputs"):
        assert rec["checks"][k] is True or rec["checks"][k]["ok"] is True, k
    assert all(p["pairs"] == [2, 2, 2] and p["presealed"] == 2 for p in rec["unseal_day"]["projects"])


def test_base_must_be_under_smoke_dir_and_not_real_data(tmp_path):
    from tools import pilot_rehearsal as pr
    from tools import pilot_unseal_day as day
    outside = os.path.join(os.path.splitdrive(str(tmp_path))[0] + os.sep, "cc-rehearsal-not-smoke", "x")  # 글자로만
    with pytest.raises(pr.RehearsalError):
        pr.check_base(outside)                                           # ChatCoachSmoke 밖
    with pytest.raises(pr.RehearsalError):
        pr.check_base(os.path.join(day.DATA_DIR, "ChatCoachSmoke", "x"))  # 실제 데이터 폴더 아래(글자로만)
    ok = tmp_path / "ChatCoachSmoke" / "rehearsal-x"
    assert pr.check_base(str(ok)) == str(ok) and not ok.exists()        # 만들지는 않는다
    assert pr.main(["--base", outside]) == 2 and not os.path.exists(outside)
