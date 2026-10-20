# vendored from vtubeadmirer/clipslicer@07d13cc (ClipSlicer.spec) — 변경: 이름 ChatCoach, hiddenimports 없음(WebEngine·yt_dlp 제거), 안내서 v0.1·VERSION 번들, excludes 확대, upx=False 두 곳(S-17)
# -*- mode: python ; coding: utf-8 -*-
# build.ps1 이 `pyinstaller --noconfirm --clean --distpath <DistPath> ChatCoach.spec` 로 부른다.
# 경로는 이 파일이 있는 저장소 루트 기준이다.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('bin\\libmpv-2.dll', '.')],
    datas=[
        ('assets', 'assets'),
        ('bin\\ffmpeg', 'ffmpeg'),
        # 라벨링 안내서는 v0.1 파일 하나만 (폴더째가 아니라 — 검토본·개정안은 번들하지 않는다)
        ('docs\\labeling-guide\\v0.1.md', 'docs\\labeling-guide'),
        # 실행 중에 읽는 스키마: store(app/store.py SCHEMA_REL, 없으면 읽기 전용으로 연다)와
        # 내보내기(app/export.py SCHEMA_REL, 없으면 [라벨 내보내기] 가 실패한다).
        # project.json 검사는 app/core/validate.py 안의 사본으로 하므로 project.schema.json 은 번들하지 않는다.
        ('schemas\\label-event.schema.json', 'schemas'),
        ('schemas\\export-row.schema.json', 'schemas'),
        ('VERSION', '.'),
    ],
    # mpv(python-mpv)는 main.py 가 직접 import 하지 않고 selftest·player 가 importlib 로 늦게 부른다 → 명시
    hiddenimports=['mpv'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PIL·nacl 은 tools 전용(아이콘·서명), 나머지 Qt 모듈은 쓰지 않는다. build.ps1 이 DLL 화이트리스트로 한 번 더 정리한다.
    excludes=[
        'PIL', 'numpy', 'tkinter', 'yaml', 'psutil', 'nacl',
        'PySide6.QtPdf', 'PySide6.QtQuick3D', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtMultimedia',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ChatCoach',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 는 빌드 환경마다 결과가 다르고 백신 오탐을 늘린다 (S-17)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icon.ico'],
    manifest='assets\\ChatCoach.manifest',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # 위와 같은 이유
    upx_exclude=[],
    name='ChatCoach',
)
