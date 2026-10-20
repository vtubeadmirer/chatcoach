# 치지직 채팅 새 type_code·status 조사 — 2026-10-21

상태: 09 5-2 질문 3 에 대한 조사. 실제 수집 1건의 원문 페이지(보호 구역 `chat/protected/pages-*.jsonl.gz`)를 로컬에서 읽어 **구조만** 적는다.
본문·닉네임·userIdHash·금액·상품 이름 원문은 이 문서에 넣지 않는다.

---

## 1. 표본

| 항목 | 값 |
|---|---|
| 자료 | 실제 수집 프로젝트 `<P>` 의 원문 페이지 파일 1개 (`pages-F20261020-….jsonl.gz`) |
| 항목 수 | videoChats 29,793 개 |
| type_code 분포 | 1: 29,525 · 10: 230 · **12: 33** · 11: 4 · **15: 1** |
| status 분포 | NORMAL: 29,564 · CBOTBLIND: 228 · **BLIND: 1** |

조사 스크립트는 저장소에 넣지 않았다(키 목록·값 종류·길이만 세는 일회성 스크립트).

모든 항목의 최상위 키는 같다: `chatChannelId, content, extras, messageStatusType, messageTime, messageTypeCode, playerMessageTime, profile, userIdHash` (9개).

---

## 2. type_code 12 — 구독권 선물 (확인됨)

### 2-1. 실측 구조 (33건)

| 항목 | 관측 |
|---|---|
| content | 33건 모두 빈 문자열(길이 0) |
| profile | 33건 모두 `null` |
| userIdHash | 33건 모두 `"anonymous"` → `viewer_id = anon` |
| extras 키 | `giftId`(str), `giftTierName`(str), `giftTierNo`(int, 관측값 1), `giftType`(str, 관측값 `SUBSCRIPTION_GIFT_RECEIVER` 한 가지), `receiverNickname`(str), `receiverUserIdHash`(str), `receiverVerifiedMark`(bool), `selectionType`(str, 관측값 `MANUAL` 한 가지) |

즉 "누가 구독권을 받았다" 는 알림이고, 보낸 사람 신원은 항목에 없고(익명), 받은 사람 신원은 `extras.receiver*` 에 있다. 앱은 extras 를 레코드에 옮기지 않으므로 받은 사람 닉네임·해시는 messages.jsonl 에 들어가지 않는다(원문 페이지 파일에는 남는다 — 보호 구역).

### 2-2. 공개 자료

- chzzkpy(비공식 파이썬 라이브러리, MIT) `chzzkpy/unofficial/chat/enums.py` 의 `ChatType` 에 `SUBSCRIPTION_GIFT = 12` 가 있다. 같은 열거형에 `OPEN = 121` 도 있다(아직 관측 없음).
  출처: https://github.com/gunyu1019/chzzkpy/blob/main/chzzkpy/unofficial/chat/enums.py (조회 2026-10-21, 파일 마지막 커밋 b8bbed4, 2025-08-01)
- kimcore/chzzk(비공식 TS 라이브러리) `src/chat/types.ts`, chzzk4j `WsMessageTypes.java` 에는 12 가 없다(1~5·10·11·30 만).
  출처: https://github.com/kimcore/chzzk/blob/main/src/chat/types.ts , https://github.com/R2turnTrue/chzzk4j/blob/develop/src/main/java/xyz/r2turntrue/chzzk4j/chat/WsMessageTypes.java

공개 자료(이름)와 실측(`giftType = SUBSCRIPTION_GIFT_RECEIVER`)이 맞는다.

### 2-3. 결정 (이번 수정)

`app/chzzk_chat.py` 의 `KIND_BY_CODE` 에 **`12: "subscription"`** 을 더했다. 스키마 kind enum 안의 값이다.
11(구독)과 같이 판정·표본 모집단에서 빠진다(사유 코드 F). 본문이 비어 있어 원래도 반응 판정 대상이 될 수 없다.

---

## 3. type_code 15 — 상품 구매 알림으로 보임 (확인 안 됨)

### 3-1. 실측 구조 (1건)

| 항목 | 관측 |
|---|---|
| content | 빈 문자열(길이 0) |
| profile | 있음. 키는 일반 채팅과 같다 (`activityBadges, badge, nickname, profileImageUrl, streamingProperty, title, userIdHash, userRoleCode, verifiedMark, verifiedMarkType, viewerBadges`) |
| userIdHash | 실제 해시(익명 아님) |
| extras 키 | `minorPurchasable`(bool), `nickname`(str), `orderAmount`(int), `productName`(str), `quantity`(int, 관측값 1), `thumbnailImageUrl`(str) |

### 3-2. 공개 자료

chzzkpy·kimcore/chzzk·chzzk4j 어디에도 15 가 없고, 웹 검색으로도 찾지 못했다.
키 이름(`productName`, `orderAmount`, `quantity`, `minorPurchasable`)으로 보아 **시청자가 상품을 샀다는 알림**(치지직 안 상품 판매 기능 계열)으로 추정한다. 추정일 뿐이다.

### 3-3. 결정

**`other` 유지.** 스키마 kind enum 에 맞는 값이 없다(`donation` 은 후원 금액 의미가 달라 섞으면 후원 건수·금액 집계가 틀어진다).
`type_code = 15` 원값은 레코드에 남으므로 나중에 다시 분류할 수 있다.

제안(결정 필요): 15 가 더 나오거나 뜻이 확인되면 kind enum 에 `purchase` 를 더하는 스키마 변경을 검토한다(chat-message·export-row 두 스키마의 enum 을 함께 바꿔야 한다). 그 전까지는 `other` 로 판정에서 빠지므로(본문도 비어 있음) 분석에 지장은 없다.

---

## 4. status BLIND (1건)

### 4-1. 실측 구조

| 항목 | 관측 |
|---|---|
| type_code | 1 (일반 채팅) |
| content | **빈 문자열(길이 0)** |
| profile | 있음(일반 채팅과 같은 키) |
| extras 키 | `chatType`(str, `STREAMING`), `emojis`(빈 dict), `extraToken`(str), `osType`(str, `AOS`), `streamingChannelId`(str) — 일반 채팅과 같은 모양 |

비교: CBOTBLIND 228건은 모두 type_code 1 이고 **content 가 비어 있지 않다**(클린봇이 가렸어도 다시보기 API 는 본문을 준다).
BLIND 는 본문 자체가 비어 온다.

### 4-2. 공개 자료

채팅 웹소켓 명령에 `BLIND = 94008` 이 있다(chzzkpy `ChatCmd`, kimcore/chzzk·chzzk4j 도 같음). kimcore/chzzk 의 `BlindEvent` 에는 `blindType`, `blindUserId` 필드가 있다 — 사람(스트리머·매니저)이 채팅을 가리는 기능으로 보인다.
다만 `messageStatusType = "BLIND"` 값 자체를 설명한 공개 자료는 찾지 못했다. **"사람이 가린 채팅, 다시보기에서 본문이 지워짐"** 으로 추정한다.

### 4-3. 결정

코드 수정 없음. status 는 원값 그대로 저장하고(스키마도 "다른 값은 그대로 저장"), kind 는 type_code 로만 정하므로 `text` 가 된다.
본문이 비어 있어 반응 판정에 쓸 내용이 없다. 표본 모집단에서 뺄지는 CBOTBLIND 와 함께 03 20장 질문 3 / 07 질문 41 에서 정한다.
제안: 스키마 `status` 설명의 "관측" 목록에 `BLIND`(본문 빈 문자열) 를 더한다 — 스키마 파일은 이번 담당이 아니라 고치지 않았다.

---

## 5. 정리

| 값 | 뜻 | 근거 | kind |
|---|---|---|---|
| type_code 12 | 구독권 선물(받은 사람 알림) | chzzkpy `SUBSCRIPTION_GIFT = 12` + 실측 `giftType` | `subscription` (이번에 추가) |
| type_code 15 | 상품 구매 알림(추정) | 실측 extras 키 이름뿐 | `other` 유지, `purchase` 추가 제안 |
| status BLIND | 사람이 가린 채팅(추정), 본문 빈 문자열 | 웹소켓 BLIND 명령 존재 + 실측 | 해당 없음(status 원값 저장) |

남은 일: 스키마 `type_code`·`status` 설명 문구 갱신(12 확인, 15·BLIND 관측 추가) — 스키마 담당이 할 일.
