"""작업대 오른쪽 칸의 편집 폼(02 7-3). 각 폼은 QWidget 이고 store 를 직접 쥐지 않는다.

공통 모양
- 생성자에 콜백 `save(draft: dict) -> dict` 와 `cancel()` 을 받는다. save 는 저장된 레코드(봉투의 `id` 포함)를
  돌려줘야 한다. 작업대가 store.append_label 등으로 이어 준다(연결은 통합 담당).
- 작업대 상태(영상 길이·현재 유효 레코드·프레임률·시작값 등)는 `load_context(...)` 로 받는다.
- draft 의 kind·필드는 schemas/label-event.schema.json 과 docs/design/03-data-model.md 6-5~6-10 을 따른다.
- 블라인드(안내서 원칙 3, 03 12장): 폼은 받은 자기 세션 레코드만 보인다. 엔진 결과·다른 세션 라벨·AI 제안을
  읽는 길이 없다.
"""
from .continuation_editor import ContinuationEditor
from .evidence_widget import EvidenceWidget
from .reaction_editor import ReactionEditor
from .segment_form import SegmentForm

__all__ = ["SegmentForm", "ReactionEditor", "ContinuationEditor", "EvidenceWidget"]
