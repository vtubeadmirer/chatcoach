"""엄격 JSON 읽기와 손 검증기 (docs/design/05-security.md S-29, 03-data-model.md 6-1·8-4).

- load_json_strict: BOM 을 바이트로 벗기고, NaN·Infinity·중복 키를 거부하고, 최상위가 object 인지 본다.
- validate_project: project.json 을 schemas/project.schema.json 과 같은 규칙으로 검사해 문제 목록을 돌려준다.
  jsonschema 패키지에 기대지 않는다. 스키마의 검사용 키만 옮긴 PROJECT_SPEC 을 작은 해석기(check)로 읽는다.
  PROJECT_SPEC 이 스키마 파일과 같은지는 tests/test_project.py 가 확인한다(스키마를 고치면 여기도 고쳐야 한다).
- canonical_json: 해시용 정규화 JSON (03 8-4: 키 정렬, 구분자 ',' ':', ensure_ascii=False, UTF-8, NaN 금지, 3.0 → 3).

core 층이라 파일·시계·난수를 쓰지 않는다. 바이트와 객체만 받는다.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

UTF8_BOM = b"\xef\xbb\xbf"
PROJECT_SCHEMA_VERSION = 1          # 이 앱이 아는 project.json 형식 버전
PROJECT_MAX_BYTES = 4 * 1024 * 1024  # project.json 크기 상한 (보정 앵커·기준서 스냅샷이 쌓여도 수백 KB)


class StrictJsonError(ValueError):
    """엄격 JSON 규칙 위반. 메시지에 본문을 넣지 않는다(위치·사유만)."""


def _reject_constant(name: str):
    raise StrictJsonError(f"허용하지 않는 상수 {name}")


def _no_duplicate_keys(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise StrictJsonError(f"중복 키 {k!r}")
        obj[k] = v
    return obj


def load_json_strict(data: bytes, max_bytes: int) -> dict:
    """바이트 → dict. 규칙: 크기 상한, 앞 BOM 벗김(utf-8-sig 코덱을 쓰지 않는다, S-30), UTF-8 엄격 디코드,
    NaN·Infinity·-Infinity 거부, 중복 키 거부, 최상위 object. 어기면 StrictJsonError."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 는 bytes 여야 합니다")
    if len(data) > max_bytes:
        raise StrictJsonError(f"파일이 너무 큽니다 ({len(data)} > {max_bytes} 바이트)")
    if data.startswith(UTF8_BOM):
        data = data[len(UTF8_BOM):]
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as e:
        raise StrictJsonError(f"UTF-8 이 아닙니다 (바이트 {e.start})") from None
    try:
        obj = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys)
    except StrictJsonError:
        raise
    except json.JSONDecodeError as e:
        raise StrictJsonError(f"JSON 문법 오류 ({e.lineno}행 {e.colno}열: {e.msg})") from None
    except RecursionError:
        raise StrictJsonError("중첩이 너무 깊습니다") from None
    if not isinstance(obj, dict):
        raise StrictJsonError("최상위가 object 가 아닙니다")
    return obj


def _canon(o):
    if isinstance(o, bool) or o is None or isinstance(o, (str, int)):
        return o
    if isinstance(o, float):
        if not math.isfinite(o):
            raise ValueError("NaN·Infinity 는 정규화할 수 없습니다")
        return int(o) if o.is_integer() else o
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    raise TypeError(f"JSON 으로 바꿀 수 없는 값: {type(o).__name__}")


def canonical_json(obj: Any) -> bytes:
    """해시용 정규화 JSON 바이트 (03 8-4)."""
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


# ---------------------------------------------------------------------------
# 작은 스키마 해석기. PROJECT_SPEC 이 쓰는 키만 안다:
# type · const · enum · pattern · minimum · maximum · minLength · maxLength · minItems
# required · properties · additionalProperties(false 또는 스키마) · items · oneOf · $ref
# 정수는 bool·float 를 받지 않는다(1.0 도 거부 — S-29 타입 혼동). 숫자는 bool 을 받지 않는다.

_KNOWN_KEYS = {"type", "const", "enum", "pattern", "minimum", "maximum", "minLength", "maxLength",
               "minItems", "required", "properties", "additionalProperties", "items", "oneOf", "$ref", "$defs"}
_pattern_cache: dict[str, re.Pattern] = {}


def _compile(p: str) -> re.Pattern:
    r = _pattern_cache.get(p)
    if r is None:
        # JSON Schema 의 '$' 는 문자열 끝. 파이썬 '$' 는 끝 줄바꿈 앞에서도 맞으므로 \Z 로 바꾼다.
        src = p[:-1] + r"\Z" if p.endswith("$") and not p.endswith(r"\$") else p
        r = _pattern_cache[p] = re.compile(src)
    return r


def _type_ok(v, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v))
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    raise ValueError(f"모르는 type {t!r}")


def _show(v) -> str:
    """문제 메시지에 넣을 값 요약. 문자열 본문은 넣지 않는다(제목·메모에 개인정보가 있을 수 있다)."""
    if isinstance(v, str):
        return f"문자열({len(v)}자)"
    if isinstance(v, (dict, list)):
        return type(v).__name__
    return repr(v)


def check(value, spec: dict, root: dict, path: str = "$") -> list[str]:
    """value 를 spec(스키마 부분집합)으로 검사해 문제 목록을 돌려준다. 빈 목록이면 통과."""
    out: list[str] = []
    unknown = set(spec) - _KNOWN_KEYS
    if unknown:
        raise ValueError(f"해석기가 모르는 스키마 키: {sorted(unknown)}")
    if "$ref" in spec:
        ref = spec["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"지원하지 않는 $ref {ref}")
        return check(value, root["$defs"][ref[len("#/$defs/"):]], root, path)
    if "oneOf" in spec:
        hits = [check(value, s, root, path) for s in spec["oneOf"]]
        n_ok = sum(1 for h in hits if not h)
        if n_ok == 1:
            return []
        if n_ok > 1:
            return [f"{path}: 여러 형식에 동시에 맞습니다"]
        best = min(hits, key=len)
        return best if len(hits) == 1 else [f"{path}: 허용한 형식 어느 것에도 맞지 않습니다"] + best
    if "type" in spec:
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        if not any(_type_ok(value, t) for t in types):
            return [f"{path}: 형식이 {'/'.join(types)} 가 아닙니다 ({_show(value)})"]
    if "const" in spec:
        c = spec["const"]
        if not (value == c and type(value) is type(c)):
            out.append(f"{path}: 값은 {c!r} 이어야 합니다 ({_show(value)})")
    if "enum" in spec:
        if not any(value == e and type(value) is type(e) for e in spec["enum"]):
            out.append(f"{path}: 허용 값 {spec['enum']} 밖입니다 ({_show(value)})")
    if isinstance(value, str):
        if "minLength" in spec and len(value) < spec["minLength"]:
            out.append(f"{path}: {spec['minLength']}자 이상이어야 합니다")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            out.append(f"{path}: {spec['maxLength']}자 이하여야 합니다 ({len(value)}자)")
        if "pattern" in spec and not _compile(spec["pattern"]).search(value):
            out.append(f"{path}: 형식(정규식)에 맞지 않습니다 ({_show(value)})")
    if _type_ok(value, "number"):
        if "minimum" in spec and value < spec["minimum"]:
            out.append(f"{path}: {spec['minimum']} 이상이어야 합니다 ({value!r})")
        if "maximum" in spec and value > spec["maximum"]:
            out.append(f"{path}: {spec['maximum']} 이하여야 합니다 ({value!r})")
    if isinstance(value, list):
        if "minItems" in spec and len(value) < spec["minItems"]:
            out.append(f"{path}: 항목이 {spec['minItems']}개 이상이어야 합니다")
        if "items" in spec:
            for i, item in enumerate(value):
                out += check(item, spec["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for k in spec.get("required", []):
            if k not in value:
                out.append(f"{path}: 필수 필드 {k!r} 가 없습니다")
        props = spec.get("properties", {})
        extra = spec.get("additionalProperties", True)
        for k, v in value.items():
            sub = f"{path}.{k}"
            if k in props:
                out += check(v, props[k], root, sub)
            elif extra is False:
                out.append(f"{path}: 모르는 필드 {k!r}")
            elif isinstance(extra, dict):
                out += check(v, extra, root, sub)
    return out


def validate_project(obj) -> list[str]:
    """project.json 객체의 문제 목록. 빈 목록이면 통과.

    스키마 규칙(PROJECT_SPEC) 다음에 스키마로 적을 수 없는 관계를 본다:
    active_guide_version 이 guides[] 안에 있는지, calibration.active_rev 가 revs[] 안에 있는지,
    guides[].guide_version·calibration.revs[].rev 가 겹치지 않는지, slug 끝 번호가 video_no 와 같은지.
    schema_version 이 앱이 아는 값보다 크면 그 사실만 알리고 나머지는 검사하지 않는다(읽기 전용으로 연다).
    """
    if not isinstance(obj, dict):
        return ["$: 최상위가 object 가 아닙니다"]
    sv = obj.get("schema_version")
    if isinstance(sv, int) and not isinstance(sv, bool) and sv > PROJECT_SCHEMA_VERSION:
        return [f"$.schema_version: 이 앱이 아는 형식({PROJECT_SCHEMA_VERSION})보다 새 형식({sv})입니다"]
    out = check(obj, PROJECT_SPEC, PROJECT_SPEC)
    guides = obj.get("guides")
    if isinstance(guides, list):
        vers = [g.get("guide_version") for g in guides if isinstance(g, dict)]
        if len(vers) != len(set(map(str, vers))):
            out.append("$.guides: guide_version 이 겹칩니다")
        agv = obj.get("active_guide_version")
        if isinstance(agv, str) and agv not in vers:
            out.append("$.active_guide_version: guides[] 에 없는 버전입니다")
    cal = obj.get("calibration")
    if isinstance(cal, dict) and isinstance(cal.get("revs"), list):
        revs = [r.get("rev") for r in cal["revs"] if isinstance(r, dict)]
        if len(revs) != len(set(map(str, revs))):
            out.append("$.calibration.revs: rev 가 겹칩니다")
        if "active_rev" in cal and cal["active_rev"] not in revs:
            out.append("$.calibration.active_rev: revs[] 에 없는 번호입니다")
    slug = obj.get("slug")
    b = obj.get("broadcast")
    if isinstance(slug, str) and isinstance(b, dict) and isinstance(b.get("video_no"), str):
        if not slug.endswith("-" + b["video_no"]):
            out.append("$.slug: 끝 번호가 broadcast.video_no 와 다릅니다")
    return out


# 스키마 schemas/project.schema.json 에서 description·title·format·default·$schema·$id 만 뺀 것.
# 손으로 고치지 말고 스키마를 고친 뒤 같은 방식으로 다시 옮긴다(테스트가 어긋남을 잡는다).
PROJECT_SPEC: dict = {'type': 'object',
 'required': ['schema',
              'schema_version',
              'project_id',
              'rev',
              'slug',
              'created_at',
              'created_by',
              'created_app_version',
              'modified_at',
              'modified_by',
              'purpose',
              'retention_until',
              'broadcast',
              'media',
              'chat',
              'pseudonymization',
              'guides',
              'active_guide_version',
              'calibration',
              'sample_seed',
              'dataset_split',
              'labelers',
              'protected_zone'],
 'additionalProperties': False,
 'properties': {'schema': {'const': 'chatcoach/project'},
                'schema_version': {'const': 1},
                'project_id': {'$ref': '#/$defs/uuid'},
                'rev': {'type': 'integer', 'minimum': 1},
                'slug': {'type': 'string', 'pattern': '^[^\\\\/:*?"<>|]{1,30}-[0-9]{1,12}$'},
                'created_at': {'$ref': '#/$defs/datetime'},
                'created_by': {'$ref': '#/$defs/labelerId'},
                'created_app_version': {'$ref': '#/$defs/semver'},
                'modified_at': {'$ref': '#/$defs/datetime'},
                'modified_by': {'type': 'string', 'pattern': '^[A-Z][A-Z0-9]{1,7}@[a-z2-7]{4}$'},
                'purpose': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                'retention_until': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'},
                'consent_note': {'type': 'string', 'maxLength': 2000},
                'notes': {'type': 'string', 'maxLength': 5000},
                'broadcast': {'$ref': '#/$defs/broadcast'},
                'media': {'$ref': '#/$defs/media'},
                'chat': {'oneOf': [{'type': 'null'}, {'$ref': '#/$defs/chatSummary'}]},
                'pseudonymization': {'$ref': '#/$defs/pseudonymization'},
                'guides': {'type': 'array', 'minItems': 1, 'items': {'$ref': '#/$defs/guideEntry'}},
                'active_guide_version': {'$ref': '#/$defs/guideVersion'},
                'calibration': {'$ref': '#/$defs/calibration'},
                'sample_seed': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'},
                'dataset_split': {'enum': ['dev', 'test']},
                'labelers': {'type': 'array', 'items': {'$ref': '#/$defs/labelerEntry'}},
                'protected_zone': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
                'storage_warning_acknowledged': {'oneOf': [{'type': 'null'},
                                                           {'type': 'object',
                                                            'required': ['at', 'by', 'path_kind'],
                                                            'additionalProperties': False,
                                                            'properties': {'at': {'$ref': '#/$defs/datetime'},
                                                                           'by': {'$ref': '#/$defs/labelerId'},
                                                                           'path_kind': {'enum': ['onedrive',
                                                                                                  'unc',
                                                                                                  'removable',
                                                                                                  'other']}}}]},
                'x': {'type': 'object'}},
 '$defs': {'uuid': {'type': 'string',
                    'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'},
           'datetime': {'type': 'string',
                        'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'},
           'semver': {'type': 'string', 'pattern': '^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.]+)?$'},
           'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
           'labelerId': {'type': 'string', 'pattern': '^[A-Z][A-Z0-9]{1,7}$'},
           'guideVersion': {'type': 'string', 'pattern': '^[0-9]+\\.[0-9]+(-[a-z0-9]+)?$'},
           'ms': {'type': 'integer', 'minimum': -3600000, 'maximum': 172800000},
           'broadcast': {'type': 'object',
                         'required': ['platform', 'video_no', 'title', 'duration_ms'],
                         'additionalProperties': False,
                         'properties': {'platform': {'const': 'chzzk'},
                                        'video_no': {'type': 'string', 'pattern': '^[0-9]{1,12}$'},
                                        'channel_id': {'type': ['string', 'null'], 'maxLength': 64},
                                        'channel_name': {'type': ['string', 'null'], 'maxLength': 100},
                                        'title': {'type': 'string', 'maxLength': 300},
                                        'duration_ms': {'type': 'integer', 'minimum': 0},
                                        'live_open_at': {'oneOf': [{'type': 'null'},
                                                                   {'$ref': '#/$defs/datetime'}]},
                                        'publish_at': {'oneOf': [{'type': 'null'},
                                                                 {'$ref': '#/$defs/datetime'}]},
                                        'adult': {'type': ['boolean', 'null']}}},
           'probe': {'type': 'object',
                     'required': ['duration_ms', 'vfr'],
                     'additionalProperties': False,
                     'properties': {'duration_ms': {'type': 'integer', 'minimum': 0},
                                    'video_codec': {'type': ['string', 'null']},
                                    'audio_codec': {'type': ['string', 'null']},
                                    'width': {'type': ['integer', 'null'], 'minimum': 0},
                                    'height': {'type': ['integer', 'null'], 'minimum': 0},
                                    'r_frame_rate': {'type': ['string', 'null'],
                                                     'pattern': '^[0-9]+/[0-9]+$'},
                                    'avg_frame_rate': {'type': ['string', 'null'],
                                                       'pattern': '^[0-9]+/[0-9]+$'},
                                    'vfr': {'type': 'boolean'},
                                    'nb_frames': {'type': ['integer', 'null'], 'minimum': 0}}},
           'mediaRef': {'type': 'object',
                        'required': ['path_abs', 'size', 'mtime_ns', 'partial_fp', 'probe'],
                        'additionalProperties': False,
                        'properties': {'path_abs': {'type': 'string', 'minLength': 3},
                                       'path_rel': {'type': ['string', 'null']},
                                       'size': {'type': 'integer', 'minimum': 0},
                                       'mtime_ns': {'type': 'integer', 'minimum': 0},
                                       'partial_fp': {'$ref': '#/$defs/sha256'},
                                       'full_sha256': {'oneOf': [{'type': 'null'},
                                                                 {'$ref': '#/$defs/sha256'}]},
                                       'ntfs_file_id': {'type': ['string', 'null'], 'pattern': '^[0-9]+$'},
                                       'volume_serial': {'type': ['string', 'null'], 'pattern': '^[0-9]+$'},
                                       'probe': {'$ref': '#/$defs/probe'}}},
           'media': {'type': 'object',
                     'required': ['media_rev', 'current', 'history'],
                     'additionalProperties': False,
                     'properties': {'media_rev': {'type': 'integer', 'minimum': 0},
                                    'current': {'oneOf': [{'type': 'null'}, {'$ref': '#/$defs/mediaRef'}]},
                                    'history': {'type': 'array',
                                                'items': {'type': 'object',
                                                          'required': ['media_rev',
                                                                       'partial_fp',
                                                                       'size',
                                                                       'replaced_at',
                                                                       'replaced_by',
                                                                       'reason'],
                                                          'additionalProperties': False,
                                                          'properties': {'media_rev': {'type': 'integer',
                                                                                       'minimum': 1},
                                                                         'partial_fp': {'$ref': '#/$defs/sha256'},
                                                                         'size': {'type': 'integer',
                                                                                  'minimum': 0},
                                                                         'duration_ms': {'type': ['integer',
                                                                                                  'null'],
                                                                                         'minimum': 0},
                                                                         'replaced_at': {'$ref': '#/$defs/datetime'},
                                                                         'replaced_by': {'$ref': '#/$defs/labelerId'},
                                                                         'reason': {'type': 'string',
                                                                                    'minLength': 1,
                                                                                    'maxLength': 500}}}}}},
           'chatSummary': {'type': 'object',
                           'required': ['chat_rev',
                                        'fetch_id',
                                        'fetched_at',
                                        'source',
                                        'chat_id_algorithm',
                                        'message_count',
                                        'messages_sha256',
                                        'complete'],
                           'additionalProperties': False,
                           'properties': {'chat_rev': {'type': 'integer', 'minimum': 1},
                                          'fetch_id': {'type': 'string',
                                                       'pattern': '^F[0-9]{8}-[a-z0-9]{4}$'},
                                          'fetched_at': {'$ref': '#/$defs/datetime'},
                                          'source': {'enum': ['chzzk_vod_api_v1']},
                                          'chat_id_algorithm': {'const': 'sl-chat-id-v1'},
                                          'message_count': {'type': 'integer', 'minimum': 0},
                                          'messages_sha256': {'$ref': '#/$defs/sha256'},
                                          'complete': {'type': 'boolean'},
                                          'incomplete_reason': {'type': ['string', 'null'], 'maxLength': 300},
                                          'server_minus_player_ms': {'type': ['integer', 'null']},
                                          'anonymous_count': {'type': 'integer', 'minimum': 0},
                                          'kind_counts': {'type': 'object',
                                                          'additionalProperties': {'type': 'integer',
                                                                                   'minimum': 0}}}},
           'pseudonymization': {'type': 'object',
                                'required': ['method', 'salt_id', 'nickname_mask'],
                                'additionalProperties': False,
                                'properties': {'method': {'const': 'hmac-sha256-v1'},
                                               'salt_id': {'type': 'string', 'pattern': '^[0-9a-f]{12}$'},
                                               'nickname_mask': {'enum': ['first_char', 'hidden']}}},
           'guideEntry': {'type': 'object',
                          'required': ['guide_version',
                                       'doc_path',
                                       'doc_sha256',
                                       'thresholds',
                                       'thresholds_sha256',
                                       'added_at',
                                       'added_by'],
                          'additionalProperties': False,
                          'properties': {'guide_version': {'$ref': '#/$defs/guideVersion'},
                                         'doc_path': {'type': 'string'},
                                         'doc_sha256': {'$ref': '#/$defs/sha256'},
                                         'thresholds': {'type': 'object'},
                                         'thresholds_sha256': {'$ref': '#/$defs/sha256'},
                                         'added_at': {'$ref': '#/$defs/datetime'},
                                         'added_by': {'$ref': '#/$defs/labelerId'},
                                         'reason': {'type': ['string', 'null'], 'maxLength': 500}}},
           'anchor': {'type': 'object',
                      'required': ['anchor_id', 'kind', 'vod_ms', 'file_ms'],
                      'additionalProperties': False,
                      'properties': {'anchor_id': {'$ref': '#/$defs/uuid'},
                                     'kind': {'enum': ['nickname_call', 'text_read', 'other']},
                                     'chat_id': {'type': ['string', 'null'],
                                                 'pattern': '^c[0-9a-f]{20}(_[0-9]+)?$'},
                                     'vod_ms': {'$ref': '#/$defs/ms'},
                                     'file_ms': {'type': 'integer', 'minimum': 0},
                                     'note': {'type': 'string', 'maxLength': 300}}},
           'calibrationRev': {'type': 'object',
                              'required': ['rev',
                                           'media_rev',
                                           'method',
                                           'offset_ms',
                                           'anchors',
                                           'status',
                                           'created_at',
                                           'created_by'],
                              'additionalProperties': False,
                              'properties': {'rev': {'type': 'integer', 'minimum': 0},
                                             'media_rev': {'type': 'integer', 'minimum': 0},
                                             'method': {'enum': ['single_offset', 'piecewise_linear']},
                                             'offset_ms': {'type': 'integer',
                                                           'minimum': -86400000,
                                                           'maximum': 86400000},
                                             'anchors': {'type': 'array',
                                                         'items': {'$ref': '#/$defs/anchor'}},
                                             'residual_max_ms': {'type': ['integer', 'null'], 'minimum': 0},
                                             'status': {'enum': ['verified', 'unverified', 'skipped']},
                                             'skip_reason': {'type': ['string', 'null'], 'maxLength': 500},
                                             'broadcast_delay_ms': {'type': ['integer', 'null'],
                                                                    'minimum': 0,
                                                                    'maximum': 120000},
                                             'delay_anchors': {'type': 'array',
                                                               'items': {'type': 'object',
                                                                         'required': ['chat_id',
                                                                                      'chat_ms',
                                                                                      'utterance_ms'],
                                                                         'additionalProperties': False,
                                                                         'properties': {'chat_id': {'type': 'string',
                                                                                                    'pattern': '^c[0-9a-f]{20}(_[0-9]+)?$'},
                                                                                        'chat_ms': {'$ref': '#/$defs/ms'},
                                                                                        'utterance_ms': {'$ref': '#/$defs/ms'},
                                                                                        'note': {'type': 'string',
                                                                                                 'maxLength': 300}}}},
                                             'created_at': {'$ref': '#/$defs/datetime'},
                                             'created_by': {'$ref': '#/$defs/labelerId'},
                                             'note': {'type': 'string', 'maxLength': 500}}},
           'calibration': {'type': 'object',
                           'required': ['active_rev', 'revs'],
                           'additionalProperties': False,
                           'properties': {'active_rev': {'type': 'integer', 'minimum': 0},
                                          'revs': {'type': 'array',
                                                   'minItems': 1,
                                                   'items': {'$ref': '#/$defs/calibrationRev'}}}},
           'labelerEntry': {'type': 'object',
                            'required': ['labeler_id', 'role', 'assigned_session_kinds', 'engine_author'],
                            'additionalProperties': False,
                            'properties': {'labeler_id': {'$ref': '#/$defs/labelerId'},
                                           'engine_author': {'type': 'boolean'},
                                           'role': {'enum': ['primary', 'second', 'reviewer']},
                                           'assigned_session_kinds': {'type': 'array',
                                                                      'minItems': 1,
                                                                      'items': {'enum': ['primary',
                                                                                         'relabel_same',
                                                                                         'relabel_second',
                                                                                         'adjudication',
                                                                                         'origin_review']}}}}}}
