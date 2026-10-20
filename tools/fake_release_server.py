"""127.0.0.1 전용 가짜 릴리스 서버 (docs/design/04-update-and-release.md 12-3) + 시험 릴리스 준비.

사용:
  python tools/fake_release_server.py keygen --out <폴더>                     # 암호 없는 시험 키쌍 (test.key · test.pub)
  python tools/fake_release_server.py prepare --dist dist_test --version 0.0.2 --out <폴더> --key <폴더>\\test.key
      → package_release 로 zip 두 개·SHA256SUMS·latest.json 을 만들고 시험 키로 서명한다
  python tools/fake_release_server.py serve --dir <폴더> [--port 0] [--mode normal] [--key <test.key>] [--port-file p.txt]

앱 쪽: -Channel test 빌드를 CHATCOACH_TEST_PUBKEY=<test.pub 둘째 줄> 환경 변수와
       --update-base-url http://127.0.0.1:<port>/ 로 실행한다 (release 채널은 둘 다 무시한다).

모드 (기대 결과):
  normal         정상 → 업데이트 성공
  no_sig         .minisig 404 → 거부(서명 없음)
  bad_sig        서명 바이트 훼손 → '서명 검증 실패'
  wrong_key      다른 키로 서명 → '모르는 키'
  old_version    버전을 0.0.0 으로 바꿔 재서명(--key 필요) → '최신'
  old_published  published_at 을 2000년으로 바꿔 재서명(--key 필요) → 거부(발행 시각)
  size_mismatch  zip 을 1바이트 짧게 보냄 → 중단
  primary_404    1차 경로 404 → 2차 경로(/api/releases/latest)로 성공
  slow           느리게 보냄 → 진행률·취소 동작 확인
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import base64
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.appspec import SPEC  # noqa: E402
import sign_manifest as sm  # noqa: E402

MODES = ("normal", "no_sig", "bad_sig", "wrong_key", "old_version", "old_published", "size_mismatch", "primary_404", "slow")


# --------------------------------------------------------------------------- 준비
def cmd_keygen(a) -> int:
    os.makedirs(a.out, exist_ok=True)
    kp = sm.generate_keypair()
    with open(os.path.join(a.out, "test.key"), "w", encoding="utf-8", newline="\n") as f:
        f.write(sm.secret_file_text(kp, None))
    with open(os.path.join(a.out, "test.pub"), "w", encoding="utf-8", newline="\n") as f:
        f.write(kp.public_file_text())
    print(f"시험 키쌍: {a.out}\\test.key / test.pub  (키 ID {kp.key_id_hex})")
    print(f"CHATCOACH_TEST_PUBKEY={kp.public_line()}")
    return 0


def _load_key(path: str) -> sm.KeyPair:
    with open(path, encoding="utf-8") as f:
        return sm.load_secret_key(f.read(), None)


def sign_file(manifest_path: str, kp: sm.KeyPair) -> str:
    with open(manifest_path, "rb") as f:
        data = f.read()
    version = sm.manifest_version(data)
    sig = sm.sign_bytes(data, kp, SPEC.trusted_comment(version))
    out = manifest_path + ".minisig"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(sig)
    return out


def cmd_prepare(a) -> int:
    import package_release
    rc = package_release.main(["--dist", a.dist, "--out", a.out, "--version", a.version] +
                              (["--notes", a.notes] if a.notes else []) +
                              (["--published-at", a.published_at] if a.published_at else []))
    if rc:
        return rc
    kp = _load_key(a.key)
    out = sign_file(os.path.join(a.out, "latest.json"), kp)
    print(f"서명: {out} (키 ID {kp.key_id_hex})")
    print(f"CHATCOACH_TEST_PUBKEY={kp.public_line()}")
    return 0


# --------------------------------------------------------------------------- 서버
class _State:
    def __init__(self, folder: str, mode: str, key: str | None):
        self.folder = folder
        self.mode = mode
        with open(os.path.join(folder, "latest.json"), "rb") as f:
            self.manifest = f.read()
        self.version = sm.manifest_version(self.manifest)
        sig_path = os.path.join(folder, "latest.json.minisig")
        self.sig = None
        if os.path.isfile(sig_path):
            with open(sig_path, "rb") as f:
                self.sig = f.read()
        self.kp = _load_key(key) if key else None
        if mode in ("old_version", "old_published"):
            if not self.kp:
                raise SystemExit(f"{mode} 모드는 --key 가 필요합니다 (재서명)")
            obj = json.loads(self.manifest.decode("utf-8"))
            if mode == "old_version":
                obj["version"] = "0.0.0"
                for asset in obj["assets"]:
                    asset["name"] = SPEC.asset_name("0.0.0", asset["kind"])
                    asset["url"] = SPEC.asset_url("0.0.0", asset["name"])
            else:
                obj["published_at"] = "2000-01-01T00:00:00Z"
            self.manifest = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            self.sig = sm.sign_bytes(self.manifest, self.kp, SPEC.trusted_comment(obj["version"])).encode("utf-8")
        elif mode == "wrong_key":
            other = sm.generate_keypair()
            self.sig = sm.sign_bytes(self.manifest, other, SPEC.trusted_comment(self.version)).encode("utf-8")
        elif mode == "bad_sig" and self.sig:
            lines = self.sig.decode("utf-8").split("\n")
            raw = bytearray(base64.b64decode(lines[1]))
            raw[20] ^= 0x01
            lines[1] = base64.b64encode(bytes(raw)).decode("ascii")
            self.sig = "\n".join(lines).encode("utf-8")
        elif mode == "no_sig":
            self.sig = None


class Handler(BaseHTTPRequestHandler):
    state: _State = None  # type: ignore[assignment]
    repo = SPEC.repo

    def log_message(self, fmt, *args):  # noqa: N802
        print(f"[{time.strftime('%H:%M:%S')}] {self.command} {self.path} → {fmt % args}")

    def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream", length: int | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.end_headers()
        if self.state.mode == "slow" and len(body) > 1 << 20:
            for i in range(0, len(body), 1 << 18):
                self.wfile.write(body[i:i + (1 << 18)])
                self.wfile.flush()
                time.sleep(0.05)
        else:
            self.wfile.write(body)

    def _asset(self, name: str):
        st = self.state
        if name == "latest.json":
            return self._send(200, st.manifest, "application/json")
        if name == "latest.json.minisig":
            if st.sig is None:
                return self._send(404, b"no signature")
            return self._send(200, st.sig, "text/plain")
        path = os.path.join(st.folder, os.path.basename(name))
        if not os.path.isfile(path):
            return self._send(404, b"not found")
        with open(path, "rb") as f:
            body = f.read()
        if st.mode == "size_mismatch" and name.endswith(".zip"):
            body = body[:-1]
        return self._send(200, body)

    def do_GET(self):  # noqa: N802
        p = self.path.split("?", 1)[0]
        m = re.match(rf"^/{re.escape(self.repo)}/releases/latest/download/([^/]+)$", p)
        if m:
            if self.state.mode == "primary_404":
                return self._send(404, b"primary disabled")
            return self._asset(m.group(1))
        m = re.match(rf"^/{re.escape(self.repo)}/releases/download/v([0-9.]+)/([^/]+)$", p)
        if m:
            return self._asset(m.group(2))
        if p == "/api/releases/latest":
            base = f"http://127.0.0.1:{self.server.server_address[1]}/{self.repo}/releases/download/v{self.state.version}/"
            names = ["latest.json"] + (["latest.json.minisig"] if self.state.sig is not None else []) + \
                [n for n in os.listdir(self.state.folder) if n.endswith(".zip")]
            obj = {"tag_name": f"v{self.state.version}", "assets": [{"name": n, "browser_download_url": base + n} for n in names]}
            return self._send(200, json.dumps(obj).encode("utf-8"), "application/json")
        return self._send(404, b"not found")


def cmd_serve(a) -> int:
    if a.mode not in MODES:
        raise SystemExit(f"모드는 {MODES} 중 하나")
    Handler.state = _State(a.dir, a.mode, a.key)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    if a.port_file:
        with open(a.port_file, "w", encoding="ascii") as f:
            f.write(str(port))
    print(f"가짜 릴리스 서버: {url}  모드={a.mode}  버전={Handler.state.version}  폴더={a.dir}")
    print(f"앱 인자: --update-base-url {url}")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    k.set_defaults(fn=cmd_keygen)
    pr = sub.add_parser("prepare")
    pr.add_argument("--dist", required=True)
    pr.add_argument("--version", required=True)
    pr.add_argument("--out", required=True)
    pr.add_argument("--key", required=True)
    pr.add_argument("--notes")
    pr.add_argument("--published-at")
    pr.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("serve")
    s.add_argument("--dir", required=True)
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--mode", default="normal")
    s.add_argument("--key")
    s.add_argument("--port-file")
    s.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
