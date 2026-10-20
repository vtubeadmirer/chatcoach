"""챗코치 엔진 도구 (개발 PC 전용, D-04 · ADR-18).

앱(app/)과 따로 돈다. 앱 zip 에 넣지 않고, 앱 .venv 가 아니라 .venv-engine 으로 실행한다.
  python -m engine asr <wav> --out <asr.json>
app/ 은 engine/ 을 import 하지 않는다. engine/ 은 app/ 가운데 순수 모듈인 app.core.*(등급 규칙·시각 변환을 앱과 한 벌로 쓰려고)
와 app.jsonl 만 import 한다. Qt·앱 설정·네트워크 모듈은 쓰지 않는다 (tests/test_layers.py 가 검사).

무거운 의존성(faster_whisper)은 함수 안에서 늦게 import 한다. 그래서 이 패키지를 import 하는 것만으로는
GPU·모델을 건드리지 않고, 앱 venv 의 테스트도 import 할 수 있다.
"""
from __future__ import annotations

ASR_FORMAT = "chatcoach.asr"
ASR_FORMAT_VERSION = 1

__all__ = ["ASR_FORMAT", "ASR_FORMAT_VERSION"]
