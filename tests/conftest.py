# vendored from vtubeadmirer/clipslicer@07d13cc (tests/conftest.py) — 변경: 저장소 루트를 sys.path 에 넣음, 테스트마다 app.paths.reset_for_tests()
"""테스트 공통 설정."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True, scope="session")
def _isolated_appdata(tmp_path_factory):
    """백업·데이터 폴백은 %LOCALAPPDATA%\\StreamLabeler 에 쌓인다 → 테스트는 임시 폴더로 (사용자 데이터와 섞이지 않게)."""
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(tmp_path_factory.mktemp("localappdata"))
    yield
    if old is None:
        os.environ.pop("LOCALAPPDATA", None)
    else:
        os.environ["LOCALAPPDATA"] = old


@pytest.fixture(autouse=True)
def _reset_paths():
    """paths 모듈의 캐시(데이터·백업 폴더, --data-dir 지정)가 테스트 사이에 새지 않게 앞뒤로 비운다."""
    from app import paths
    paths.reset_for_tests()
    yield
    paths.reset_for_tests()


@pytest.fixture(autouse=True)
def _isolated_data_root(_reset_paths, tmp_path):
    """테스트가 저장소의 data\\ 에 쓰지 않게 데이터 폴더를 임시 폴더로 지정한다.

    _reset_paths 에 기대어 그 뒤에 지정하고, 끝나면 _reset_paths 가 지정을 푼다.
    set_data_root 자체는 폴더를 만들지 않는다 (data_root() 를 처음 부를 때 만들고, 쓸 수 없으면 OSError).
    """
    from app import paths
    paths.set_data_root(str(tmp_path / "data"))
    yield
