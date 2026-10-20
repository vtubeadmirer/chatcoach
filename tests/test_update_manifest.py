"""업데이트 매니페스트의 서명·형식·버전·필드 규칙 (docs/design/04 5-4 · 6-2 · 12-4).

- M0 끝났다의 기준 4종: ① 서명 없음 ② 잘못된 서명(훼손·다른 키·trusted comment 버전 불일치)
  ③ 같거나 낮은 버전은 '최신' ④ 발행 시각이 설치본 빌드 시각보다 이르면 거부.
- 정상 경로: 경량/전체 선택, signed_by, download_size, needs_update(min_supported), 라벨 형식 경고,
  mandatory 와 '이 버전 건너뛰기', 공개키 없음·manifest_version 2·다른 앱·엄격 JSON·자산 규칙·타입 규칙.
- is_due(성공 24시간·실패 1시간), run_check(가짜 fetch 주입: 확인 시각을 쓰는 시점, 주기, 상태 저장),
  effective_pubkeys(채널별 환경 변수), allowed_base_url, rewrite_url, parse_version.
가짜 빌드 폴더 → package_release → make_manifest → 시험 키 서명 흐름은 tests/release_kit.py 에 있다.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import release_kit as rk
import sign_manifest as sm

from app import updater
from app.appspec import SPEC, PublicKey
from app.updater import UpdateError


@pytest.fixture(scope="module")
def signed_manifest(tmp_path_factory):
    """가짜 빌드(0.0.2) → zip 두 개 → latest.json(deps 는 가짜) → 시험 키 서명."""
    return rk.build_release(str(tmp_path_factory.mktemp("rel")))


@pytest.fixture()
def inst(signed_manifest):
    """설치본 0.0.1, 채널 test, 빌드 시각 어제(UTC), frozen, deps·ffmpeg·libmpv 일치 → 경량 대상."""
    return rk.installed(signed_manifest.dist_app)


def _eval(rel, inst, data=None, sig=None, keys=None, **kw):
    return updater.evaluate(rel.data if data is None else data, rel.sig if sig is None else sig, inst,
                            rel.keys if keys is None else keys, SPEC, **kw)


def _assert_failed(r, kind):
    assert r.state == "failed" and r.kind == kind and not r.ok and r.release is None, r


# --------------------------------------------------------------------------- M0 끝났다의 기준 4종
def test_m0_criterion_1_no_signature_is_failed_signature(signed_manifest, inst):
    r = updater.evaluate(signed_manifest.data, None, inst, signed_manifest.keys, SPEC)
    _assert_failed(r, "signature")
    assert "서명 파일" in r.reason


def test_m0_criterion_2_bad_signature_is_failed_signature(signed_manifest, inst):
    rel = signed_manifest
    # (a) 서명 바이트 1비트 훼손
    lines = rel.sig.decode("utf-8").split("\n")
    raw = bytearray(base64.b64decode(lines[1]))
    raw[30] ^= 0x01
    lines[1] = base64.b64encode(bytes(raw)).decode("ascii")
    r = _eval(rel, inst, sig="\n".join(lines).encode("utf-8"))
    _assert_failed(r, "signature")
    assert "서명이 맞지 않습니다" in r.reason
    # (b) 다른 키로 서명
    other = sm.generate_keypair()
    r = _eval(rel, inst, sig=rel.sign(rel.data, kp=other))
    _assert_failed(r, "signature")
    assert "모르는" in r.reason
    # (c) 같은 키지만 trusted comment 의 버전이 매니페스트와 다름 (다른 버전 서명의 재사용)
    r = _eval(rel, inst, sig=rel.sign(rel.data, comment_version="0.0.9"))
    _assert_failed(r, "signature")
    assert "trusted comment" in r.reason
    # (d) 서명 뒤에 내용이 바뀜
    r = _eval(rel, inst, data=rel.data.replace(b'"mandatory": false', b'"mandatory": true'))
    _assert_failed(r, "signature")
    # (e) trusted comment 줄만 바꿔치기 → 전역 서명 불일치
    lines = rel.sig.decode("utf-8").split("\n")
    lines[2] = "trusted comment: " + SPEC.trusted_comment("0.0.9")
    r = _eval(rel, inst, sig="\n".join(lines).encode("utf-8"))
    _assert_failed(r, "signature")
    assert "전역 서명" in r.reason
    # (f) 서명 파일 형식 오류 · 상한 초과
    _assert_failed(_eval(rel, inst, sig=b"garbage"), "signature")
    _assert_failed(_eval(rel, inst, sig=b"x" * (SPEC.max_sig_bytes + 1)), "signature")


@pytest.mark.parametrize("installed_version", ["0.0.2", "0.0.3"])
def test_m0_criterion_3_same_or_lower_version_is_latest(signed_manifest, installed_version):
    inst = rk.installed(signed_manifest.dist_app, version=installed_version)
    r = _eval(signed_manifest, inst)
    assert r.state == "latest" and r.ok and r.release is None and r.kind == "", r


def test_m0_criterion_4_published_before_built_is_failed_format(signed_manifest):
    rel = signed_manifest
    # 설치본 빌드 시각이 매니페스트 발행 시각보다 뒤
    inst = rk.installed(rel.dist_app, built_at=datetime.now(timezone.utc) + timedelta(days=1))
    r = _eval(rel, inst)
    _assert_failed(r, "format")
    assert "발행 시각" in r.reason
    # 2000년에 발행된 것으로 고쳐 다시 서명한 매니페스트 (재생 공격 흉내)
    data, sig = rel.variant(lambda m: m.update(published_at="2000-01-01T00:00:00Z"))
    _assert_failed(_eval(rel, rk.installed(rel.dist_app), data, sig), "format")
    # frozen 인데 빌드 시각을 모르면 하한을 판단할 수 없으므로 거부
    _assert_failed(_eval(rel, rk.installed(rel.dist_app, built_at=None)), "format")
    # 소스 실행(frozen False, built_at None)은 하한 검사 없이 newer
    assert _eval(rel, rk.installed(rel.dist_app, built_at=None, frozen=False)).state == "newer"


# --------------------------------------------------------------------------- 정상 경로
def test_newer_chooses_lite_when_deps_and_heavy_match(signed_manifest, inst):
    rel = signed_manifest
    r = _eval(rel, inst)
    assert r.state == "newer" and r.ok and not r.skipped and not r.needs_update
    assert r.release.version == "0.0.2" and r.release.chosen == "lite"
    assert r.release.signed_by == "TEST" and r.release.key_id == rel.kp.key_id_hex
    assert r.release.download_size == os.path.getsize(rel.lite_zip)
    assert r.release.warnings == ()
    m = r.release.manifest
    assert m.app == "ChatCoach" and m.min_supported == "0.0.1" and m.mandatory is False
    assert m.deps == rk.DEPS and m.guide_versions == ("0.1",)
    assert m.asset("full").size == os.path.getsize(rel.full_zip)
    assert m.published_at.tzinfo is not None


def test_newer_chooses_full_otherwise(signed_manifest):
    rel = signed_manifest
    cases = {
        "ffmpeg·libmpv 없음": rk.installed(rel.dist_app, has_heavy=False),
        "deps.txt 없음": rk.installed(rel.dist_app, deps={}),
        "태그 다름": rk.installed(rel.dist_app, deps=dict(rk.LOCAL_DEPS, ffmpeg="z")),
        "sha256 다름": rk.installed(rel.dist_app, deps=dict(rk.LOCAL_DEPS, libmpv_sha256="2" * 64)),
        "sha256 줄 없음": rk.installed(rel.dist_app, deps={"ffmpeg": "a", "libmpv": "b"}),
    }
    for why, inst in cases.items():
        r = _eval(rel, inst)
        assert r.state == "newer" and r.release.chosen == "full", why
        assert r.release.download_size == os.path.getsize(rel.full_zip), why


def test_needs_update_when_below_min_supported(signed_manifest, inst):
    rel = signed_manifest
    assert _eval(rel, inst).needs_update is False
    data, sig = rel.variant(lambda m: m.update(min_supported="0.0.5"))
    r = _eval(rel, inst, data, sig)
    assert r.state == "newer" and r.needs_update is True


def test_label_schema_version_change_warns(signed_manifest, inst):
    data, sig = signed_manifest.variant(lambda m: m.update(label_schema_version=SPEC.label_schema_version + 1))
    r = _eval(signed_manifest, inst, data, sig)
    assert r.state == "newer" and len(r.release.warnings) == 1 and "라벨 형식" in r.release.warnings[0]


def test_skip_version_and_mandatory(signed_manifest, inst):
    rel = signed_manifest
    assert _eval(rel, inst, skip_version="0.0.2").skipped is True
    assert _eval(rel, inst, skip_version="0.0.3").skipped is False
    assert _eval(rel, inst, skip_version=None).skipped is False
    data, sig = rel.variant(lambda m: m.update(mandatory=True))
    r = _eval(rel, inst, data, sig, skip_version="0.0.2")
    assert r.state == "newer" and r.skipped is False and r.release.manifest.mandatory is True


def test_no_pubkeys_is_failed_pubkeys(signed_manifest, inst):
    _assert_failed(_eval(signed_manifest, inst, keys=[]), "pubkeys")
    # 서명이 없어도 공개키 없음이 먼저다 (어느 경우든 업데이트하지 않는다)
    _assert_failed(updater.evaluate(signed_manifest.data, None, inst, [], SPEC), "pubkeys")


def test_manifest_version_2_is_manual(signed_manifest, inst):
    data, sig = signed_manifest.variant(lambda m: m.update(manifest_version=2))
    r = _eval(signed_manifest, inst, data, sig)
    _assert_failed(r, "manual")
    assert "수동 업데이트" in r.reason
    data, sig = signed_manifest.variant(lambda m: m.update(manifest_version=0))
    _assert_failed(_eval(signed_manifest, inst, data, sig), "format")


def test_other_app_is_format(signed_manifest, inst):
    data, sig = signed_manifest.variant(lambda m: m.update(app="ClipSlicer"))
    r = _eval(signed_manifest, inst, data, sig)
    _assert_failed(r, "format")
    assert "다른 앱" in r.reason


def _signed_raw(rel, data: bytes):
    return data, rel.sign(data)


def test_strict_json_rules(signed_manifest, inst):
    rel = signed_manifest
    bad = {
        "NaN": rel.data.replace(b'"mandatory": false', b'"mandatory": NaN'),
        "Infinity": rel.data.replace(b'"label_schema_version": 1', b'"label_schema_version": Infinity'),
        "-Infinity": rel.data.replace(b'"label_schema_version": 1', b'"label_schema_version": -Infinity'),
        "최상위 배열": b"[" + rel.data + b"]",
        "문법 오류": rel.data[:-5],
        "UTF-8 아님": rel.data.replace("시험 노트".encode("utf-8"), "시험 노트".encode("cp949")),
    }
    assert all(v != rel.data for v in bad.values())
    for why, data in bad.items():
        r = _eval(rel, inst, *_signed_raw(rel, data))
        _assert_failed(r, "format")
    # BOM 은 벗기고 통과
    r = _eval(rel, inst, *_signed_raw(rel, b"\xef\xbb\xbf" + rel.data))
    assert r.state == "newer", r
    # 64KB 초과는 서명과 상관없이 거부
    data, sig = rel.variant(lambda m: m.update(padding="x" * (SPEC.max_manifest_bytes + 10)))
    assert len(data) > SPEC.max_manifest_bytes
    r = _eval(rel, inst, data, sig)
    _assert_failed(r, "format")
    assert "KB" in r.reason


def _asset_of(m, kind):
    return next(a for a in m["assets"] if a["kind"] == kind)


def test_asset_rules(signed_manifest, inst):
    rel = signed_manifest

    def mut(fn):
        return rel.variant(fn)

    def set_asset(which, **kv):
        return lambda m: _asset_of(m, which).update(**kv)

    cases = {
        "lite name 규칙": set_asset("lite", name="ChatCoach-v0.0.2-win64-lite.zip"),
        "full name 버전 다름": set_asset("full", name=SPEC.asset_name("0.0.3", "full")),
        "url 다른 호스트": set_asset("full", url="https://example.com/ChatCoach-v0.0.2-win64.zip"),
        "url 다른 태그": set_asset("lite", url=SPEC.asset_url("0.0.3", SPEC.asset_name("0.0.2", "lite"))),
        "sha256 대문자": lambda m: _asset_of(m, "full").update(sha256=_asset_of(m, "full")["sha256"].upper()),
        "sha256 짧음": set_asset("full", sha256="ab" * 31),
        "size 0": set_asset("full", size=0),
        "size 음수": set_asset("lite", size=-1),
        "size 상한 초과": set_asset("full", size=SPEC.max_zip_bytes + 1),
        "unpacked_size 0": set_asset("full", unpacked_size=0),
        "file_count 상한 초과": set_asset("full", file_count=SPEC.max_zip_entries + 1),
        "kind 모름": set_asset("lite", kind="delta"),
        "full 중복": lambda m: m.update(assets=[_asset_of(m, "full"), dict(_asset_of(m, "full"))]),
        "lite 없음": lambda m: m.update(assets=[_asset_of(m, "full")]),
        "자산 3개": lambda m: m.update(assets=m["assets"] + [dict(_asset_of(m, "lite"))]),
        "자산이 object 아님": lambda m: m.update(assets=["x"]),
        "필드 없음": lambda m: _asset_of(m, "full").pop("sha256"),
        "deps sha 대문자": lambda m: m["deps"]["ffmpeg"].update(sha256="A" * 64),
        "deps 형식": lambda m: m.update(deps={"ffmpeg": "a"}),
        "guide_versions 숫자": lambda m: m.update(guide_versions=[1]),
        "version 형식": lambda m: m.update(version="v0.0.2"),
        "min_supported 형식": lambda m: m.update(min_supported="0.1"),
        "필수 필드 없음": lambda m: m.pop("notes_ko"),
    }
    for why, fn in cases.items():
        r = _eval(rel, inst, *mut(fn))
        _assert_failed(r, "format")


def test_notes_truncated_and_unknown_keys_ignored(signed_manifest, inst):
    rel = signed_manifest
    long_notes = "가" * (SPEC.max_notes_chars + 500)
    data, sig = rel.variant(lambda m: m.update(notes_ko=long_notes))
    r = _eval(rel, inst, data, sig)
    assert r.state == "newer"
    assert len(r.release.manifest.notes_ko) == SPEC.max_notes_chars

    def add_unknown(m):
        m["future_field"] = {"x": [1, 2, 3]}
        _asset_of(m, "lite")["future"] = True

    r = _eval(rel, inst, *rel.variant(add_unknown))
    assert r.state == "newer", r


def test_bool_is_not_int(signed_manifest, inst):
    rel = signed_manifest
    for fn in (lambda m: m.update(manifest_version=True),
               lambda m: m.update(label_schema_version=True),
               lambda m: _asset_of(m, "full").update(size=True),
               lambda m: _asset_of(m, "lite").update(file_count=True),
               lambda m: m.update(mandatory=0)):          # 반대로 int 가 bool 자리에
        _assert_failed(_eval(rel, inst, *rel.variant(fn)), "format")


def test_published_at_requires_timezone(signed_manifest, inst):
    rel = signed_manifest
    for bad in ("2026-10-20T00:00:00", "2026-10-20", "어제", ""):
        _assert_failed(_eval(rel, inst, *rel.variant(lambda m, b=bad: m.update(published_at=b))), "format")
    for good in ("2099-01-01T00:00:00Z", "2099-01-01T09:00:00+09:00"):
        assert _eval(rel, inst, *rel.variant(lambda m, g=good: m.update(published_at=g))).state == "newer"
    assert updater.parse_iso_utc("2026-10-20T09:00:00+09:00") == datetime(2026, 10, 20, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        updater.parse_iso_utc("2026-10-20T00:00:00")


def test_installed_version_unreadable_is_format(signed_manifest):
    r = _eval(signed_manifest, rk.installed(signed_manifest.dist_app, version="dev"))
    _assert_failed(r, "format")


# --------------------------------------------------------------------------- 확인 주기
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def test_is_due_first_time():
    assert updater.is_due(updater.UpdateState(), T0)


def test_is_due_after_success_24h():
    st = updater.UpdateState()
    st.record_ok(T0, "0.0.2")
    assert not updater.is_due(st, T0 + timedelta(hours=23))
    assert updater.is_due(st, T0 + timedelta(hours=25))


def test_is_due_after_failure_1h():
    st = updater.UpdateState()
    st.record_fail(T0, "x")
    assert not updater.is_due(st, T0 + timedelta(minutes=59))
    assert updater.is_due(st, T0 + timedelta(minutes=61))


def test_is_due_latest_event_wins():
    # 실패가 성공보다 최근이면 1시간 규칙
    st = updater.UpdateState()
    st.record_ok(T0, "0.0.2")
    st.record_fail(T0 + timedelta(hours=2), "x")
    assert not updater.is_due(st, T0 + timedelta(hours=2, minutes=59))
    assert updater.is_due(st, T0 + timedelta(hours=3, minutes=1))
    # 성공이 실패보다 최근이면 24시간 규칙
    st = updater.UpdateState()
    st.record_fail(T0, "x")
    st.record_ok(T0 + timedelta(hours=1), "0.0.2")
    assert not updater.is_due(st, T0 + timedelta(hours=3))
    assert updater.is_due(st, T0 + timedelta(hours=25, minutes=1))
    # 깨진 시각 문자열은 없는 것으로 본다
    assert updater.is_due(updater.UpdateState(last_ok_at="어제", last_fail_at="garbage"), T0)


# --------------------------------------------------------------------------- run_check (fetch 주입)
class FakeFetch:
    def __init__(self, result=None, exc=None, on_call=None):
        self.result, self.exc, self.on_call = result, exc, on_call
        self.calls: list[tuple] = []

    def __call__(self, spec, installed, base):
        self.calls.append((spec, installed, base))
        if self.on_call:
            self.on_call()
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture()
def state_path(tmp_path):
    return str(tmp_path / "data" / "update" / "state.json")


def test_run_check_writes_check_time_after_result(signed_manifest, inst, state_path):
    """확인 시각은 결과가 나온 뒤에 쓴다 (S-41): fetch 도중에는 이전 상태 그대로, 실패면 last_ok_at 은 그대로."""
    before = updater.UpdateState(last_ok_at=(T0 - timedelta(days=2)).isoformat(), last_seen_version="0.0.1")
    before.save(state_path)
    seen_during_fetch = []
    fetch = FakeFetch(exc=UpdateError("network", "연결 실패"),
                      on_call=lambda: seen_during_fetch.append(updater.UpdateState.load(state_path)))
    r = updater.run_check(SPEC, inst, state_path, now=T0, pubkeys=signed_manifest.keys, fetch=fetch)
    _assert_failed(r, "network")
    assert r.reason == "연결 실패"
    assert seen_during_fetch == [before]                       # fetch 중에는 아무것도 안 바뀜
    after = updater.UpdateState.load(state_path)
    assert after.last_ok_at == before.last_ok_at               # 성공 시각은 그대로
    assert after.last_fail_at == T0.isoformat() and after.last_fail_reason == "연결 실패"
    assert after.last_seen_version == "0.0.1"
    # 실패 기록 뒤에는 1시간 규칙
    assert updater.run_check(SPEC, inst, state_path, now=T0 + timedelta(minutes=30),
                             pubkeys=signed_manifest.keys, fetch=fetch) is None


def test_run_check_not_due_returns_none_without_fetch(signed_manifest, inst, state_path):
    st = updater.UpdateState()
    st.record_ok(T0 - timedelta(hours=1), "0.0.2")
    st.save(state_path)
    fetch = FakeFetch(result=(signed_manifest.data, signed_manifest.sig))
    assert updater.run_check(SPEC, inst, state_path, now=T0, force=False, pubkeys=signed_manifest.keys,
                             fetch=fetch) is None
    assert fetch.calls == []
    # force 면 때와 상관없이 확인한다
    r = updater.run_check(SPEC, inst, state_path, now=T0, force=True, pubkeys=signed_manifest.keys, fetch=fetch)
    assert r.state == "newer" and len(fetch.calls) == 1


def test_run_check_success_records_ok_and_seen_version(signed_manifest, inst, state_path):
    fetch = FakeFetch(result=(signed_manifest.data, signed_manifest.sig))
    r = updater.run_check(SPEC, inst, state_path, now=T0, pubkeys=signed_manifest.keys, fetch=fetch,
                          base_url="http://127.0.0.1:1234")
    assert r.state == "newer" and r.release.chosen == "lite"
    assert fetch.calls[0][2] == "http://127.0.0.1:1234/"      # allowed_base_url 을 거친 값이 fetch 로
    st = updater.UpdateState.load(state_path)
    assert st.last_ok_at == T0.isoformat() and st.last_seen_version == "0.0.2" and st.last_fail_at is None
    # 최신이면 설치본 버전을 본 버전으로 기록
    latest_inst = rk.installed(signed_manifest.dist_app, version="0.0.2")
    r = updater.run_check(SPEC, latest_inst, state_path, now=T0 + timedelta(days=2),
                          pubkeys=signed_manifest.keys, fetch=fetch)
    assert r.state == "latest"
    assert updater.UpdateState.load(state_path).last_seen_version == "0.0.2"


def test_run_check_evaluation_failure_records_fail(signed_manifest, inst, state_path):
    fetch = FakeFetch(result=(signed_manifest.data, None))
    r = updater.run_check(SPEC, inst, state_path, now=T0, pubkeys=signed_manifest.keys, fetch=fetch)
    _assert_failed(r, "signature")
    st = updater.UpdateState.load(state_path)
    assert st.last_fail_at == T0.isoformat() and st.last_ok_at is None and "서명" in st.last_fail_reason


def test_run_check_network_error_kind(signed_manifest, inst, state_path):
    fetch = FakeFetch(exc=UpdateError("network", "업데이트 확인 실패 — 1차: x / 2차: y"))
    r = updater.run_check(SPEC, inst, state_path, now=T0, force=True, pubkeys=signed_manifest.keys, fetch=fetch)
    assert r.state == "failed" and r.kind == "network" and "1차" in r.reason


def test_run_check_skip_version_from_state(signed_manifest, inst, state_path):
    updater.UpdateState(skip_version="0.0.2").save(state_path)
    fetch = FakeFetch(result=(signed_manifest.data, signed_manifest.sig))
    r = updater.run_check(SPEC, inst, state_path, now=T0, pubkeys=signed_manifest.keys, fetch=fetch)
    assert r.state == "newer" and r.skipped is True


def test_run_check_uses_env_test_key_when_pubkeys_none(signed_manifest, inst, state_path, monkeypatch):
    monkeypatch.setenv(updater.TEST_PUBKEY_ENV, signed_manifest.kp.public_line())
    fetch = FakeFetch(result=(signed_manifest.data, signed_manifest.sig))
    r = updater.run_check(SPEC, inst, state_path, now=T0, fetch=fetch)
    assert r.state == "newer" and r.release.signed_by == "TEST"
    # release 채널은 환경 변수를 읽지 않는다 → 내장 공개키(지금은 없음) → pubkeys
    rel_inst = rk.installed(signed_manifest.dist_app, channel="release")
    r = updater.run_check(SPEC, rel_inst, state_path, now=T0, force=True, fetch=fetch)
    expect = "pubkeys" if not SPEC.update_pubkeys else "signature"
    _assert_failed(r, expect)


def test_update_state_load_broken_file_is_empty(tmp_path):
    p = tmp_path / "state.json"
    for content in (b"{not json", b"[1, 2]", b"\xff\xfe\x00", b"", b'{"last_ok_at": 5, "skip_version": ["x"]}'):
        p.write_bytes(content)
        assert updater.UpdateState.load(str(p)) == updater.UpdateState()
    assert updater.UpdateState.load(str(tmp_path / "none.json")) == updater.UpdateState()


def test_run_check_with_broken_state_file(signed_manifest, inst, state_path):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "wb") as f:
        f.write(b"\x00garbage")
    fetch = FakeFetch(result=(signed_manifest.data, signed_manifest.sig))
    r = updater.run_check(SPEC, inst, state_path, now=T0, pubkeys=signed_manifest.keys, fetch=fetch)
    assert r.state == "newer" and len(fetch.calls) == 1
    with open(state_path, encoding="utf-8") as f:
        assert json.load(f)["last_ok_at"] == T0.isoformat()


# --------------------------------------------------------------------------- 공개키 · 시험 주소
def test_effective_pubkeys_by_channel(signed_manifest):
    line = signed_manifest.kp.public_line()
    spec = replace(SPEC, update_pubkeys=(PublicKey("K1", "BUILTIN-K1"), PublicKey("K2", "BUILTIN-K2")))
    builtin = [("K1", "BUILTIN-K1"), ("K2", "BUILTIN-K2")]
    env = {updater.TEST_PUBKEY_ENV: "  " + line + "\n"}
    app = signed_manifest.dist_app
    assert updater.effective_pubkeys(spec, rk.installed(app, channel="release"), env) == builtin
    assert updater.effective_pubkeys(spec, rk.installed(app, channel="test"), env) == [("TEST", line)]
    assert updater.effective_pubkeys(spec, rk.installed(app, channel="source"), env) == [("TEST", line)]
    assert updater.effective_pubkeys(spec, rk.installed(app, channel="test"), {}) == builtin
    assert updater.effective_pubkeys(spec, rk.installed(app, channel="test"), {updater.TEST_PUBKEY_ENV: " "}) == builtin
    # 알 수 없는 채널도 release 처럼 다룬다
    assert updater.effective_pubkeys(spec, rk.installed(app, channel="beta"), env) == builtin


def test_allowed_base_url(signed_manifest):
    app = signed_manifest.dist_app
    rel_inst = rk.installed(app, channel="release")
    test_inst = rk.installed(app, channel="test")
    src_inst = rk.installed(app, channel="source")
    assert updater.allowed_base_url(rel_inst, "http://127.0.0.1:1234/") is None
    assert updater.allowed_base_url(rel_inst, "https://example.com/") is None
    assert updater.allowed_base_url(test_inst, "http://127.0.0.1:1234") == "http://127.0.0.1:1234/"
    assert updater.allowed_base_url(test_inst, "http://127.0.0.1:1234/") == "http://127.0.0.1:1234/"
    assert updater.allowed_base_url(src_inst, "http://127.0.0.1:9/x") == "http://127.0.0.1:9/x/"
    assert updater.allowed_base_url(test_inst, "http://example.com/") is None
    assert updater.allowed_base_url(test_inst, "http://localhost:1234/") is None
    assert updater.allowed_base_url(test_inst, "ftp://127.0.0.1/") is None
    assert updater.allowed_base_url(test_inst, "https://example.com/mirror") == "https://example.com/mirror/"
    assert updater.allowed_base_url(test_inst, None) is None
    assert updater.allowed_base_url(test_inst, "") is None


def test_rewrite_url():
    url = SPEC.asset_url("0.0.2", SPEC.asset_name("0.0.2", "full"))
    base = "http://127.0.0.1:1234/"
    assert updater.rewrite_url(url, SPEC, base) == base + url[len("https://github.com/"):]
    assert updater.rewrite_url(url, SPEC, None) == url
    assert updater.rewrite_url("https://api.github.com/repos/x", SPEC, base) == "https://api.github.com/repos/x"
    assert updater.rewrite_url(SPEC.latest_download_url("latest.json"), SPEC, base) == \
        base + f"{SPEC.repo}/releases/latest/download/latest.json"


# --------------------------------------------------------------------------- 버전
def test_parse_version_strict():
    assert updater.parse_version("0.1.0") == (0, 1, 0)
    assert updater.parse_version("10.20.300") == (10, 20, 300)
    for bad in ("v0.1.0", "1.2", "1.2.3.4", " 0.1.0", "0.1.0 ", "0. 1.0", "", "a.b.c", "0.1.-1", None, 1):
        with pytest.raises(ValueError):
            updater.parse_version(bad)
    assert updater.is_newer("0.1.10", "0.1.9") and not updater.is_newer("0.1.0", "0.1.0")
    assert not updater.is_newer("0.0.9", "0.1.0")
    with pytest.raises(ValueError):
        updater.is_newer("v0.2.0", "0.1.0")


@pytest.mark.parametrize("bad", ["0.1.0\n", "０.1.0", "0.1.٣"],
                         ids=["trailing-newline", "fullwidth-digit", "arabic-indic-digit"])
def test_parse_version_rejects_newline_and_non_ascii_digits(bad):
    """VERSION_RE 의 `$` 는 끝 줄바꿈 앞에서도 맞고 `\\d` 는 유니코드 숫자도 맞는다 — 엄격 규칙이면 거부해야 한다."""
    with pytest.raises(ValueError):
        updater.parse_version(bad)
