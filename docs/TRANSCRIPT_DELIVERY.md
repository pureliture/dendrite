# 전사 분할 전송과 재시도

긴 세션의 초과 내용을 버리지 않는다. source adapter는 전체 전사를 읽어
비식별화하고, thin shipper가 이를 여러 `conversation_chunk`로 나누어 기존
`POST /v1/ingest/enqueue` 계약으로 보낸다. provider별 의미 해석이나 서버 측
세션 조립·색인 책임은 Neurons에 있다.

## 분할 기준

- 기존 180,000 제한은 전체 전사를 자르는 기준에서 **조각별 JSON 직렬화 본문 예산**으로 바뀐다.
  Markdown/YAML 헤더와 JSON escape까지 예산에 포함한다. 실제 HTTP 요청에는 이 본문 외에
  기존 ingress envelope·metadata가 추가된다.
- 한글·이모지도 JSON escape 크기로 계산한다. 가능한 경우 줄 경계에서 나누고,
  한 줄이 예산보다 길면 Unicode 문자 경계에서 나눈다.
- 분할 전에 전체 전사를 비식별화한다. 따라서 조각 경계에 걸친 비밀값도 먼저 제거된다.
- `part_index`, `part_count`, `char_start`, `char_end`로 순서와 위치를 전달한다.
  문자 위치는 비식별화된 전사 기준이며 `char_end`는 포함하지 않는 끝 위치다.
- 각 조각은 같은 `capture_snapshot_hash`와 capture identity를 공유하고 별도 `chunk_id`를 갖는다.
  조각의 Transcript 부분을 순서대로 연결하면 source adapter가 반환한 전체 전사를 복원할 수 있다.
  JSONL을 읽을 때의 기존 줄바꿈 정규화와 비식별화 외에 공백·꼬리 내용을 제거하지 않는다.
  `turn_*`는 기존 opaque 전사의 비어 있지 않은 줄 위치이며 provider 의미상의 대화 턴 수가 아니다.

## 로컬 outbox와 완료 조건

drain은 spool별 파일 잠금을 잡아 동시에 두 consumer가 전송하지 않도록 한다.
잠금을 획득한 새 tick은 중단된 이전 tick의 processing 항목을 pending으로 회수하고,
저장된 snapshot과 진행 위치로 재개한다. 실행 중인 consumer가 있으면 새 consumer는 건너뛴다.

전송 시작 시 비식별화된 조각 묶음을 capture spool 아래 `outbox/snapshots`에
고정한다. 디렉터리 권한은 `0700`, 파일은 `0600`이다. provider hook은 여전히
locator-only capture request만 만든다. 본문은 drain이 만든 별도 outbox에만 저장한다.

한 tick의 `--max-items`는 capture 처리 수와 POST 시도 수를 모두 제한한다.
예산을 다 쓰면 다음 조각 위치를 capture request에 원자적으로 기록하고 pending으로
돌려놓는다. 전송 중 원본이 늘어나거나 삭제돼도 해당 capture의 조각은 바뀌지 않는다.
새 hook으로 생긴 다음 capture는 그 시점의 전체 전사를 별도 snapshot으로 만든다.
capture 사이의 delta 전송이나 서버 측 snapshot 선택 정책은 이번 변경에 포함하지 않는다.

각 조각 수락 직후 진행 위치를 기록한다. 중간 실패는 수락되지 않은 조각부터 재시도한다.
서버 수락 뒤 응답이나 checkpoint 기록을 잃으면 같은 본문·idempotency key로 재전송해
기존 ingress 멱등 처리를 사용한다. snapshot 손실·손상, 진행 위치 오류, target 변경은
완료로 표시하지 않고 격리한다.

**모든 조각이 ingress에서 수락된 뒤에만 capture를 ack한다.** 이 ack는 서버 ingress
수락을 뜻하며 최종 색인 완료를 뜻하지 않는다. ack 후 해당 snapshot은 제거한다.
ack와 snapshot 제거 사이에서 프로세스가 죽으면 snapshot 파일이 남을 수 있다.

기존 보고 필드 `queued_count`는 해당 tick에서 전체 전송을 마친 capture 수를 유지한다.
`queued_part_count`는 수락된 조각 수, `delivery_pending_count`는 POST 예산 때문에
다음 tick으로 넘긴 capture 수다. 일부 조각만 보낸 tick은 `queued_count=0`일 수 있다.

## 적용 범위와 기존 누락

이 변경은 새 runtime으로 처리되는 capture부터 적용된다. 예전 runtime이 잘라 보낸
acked 항목을 자동으로 재전송하지 않는다. 진행 중인 세션의 다음 hook은 전체 전사를
다시 읽으므로 그 요청부터 꼬리까지 분할해서 보낸다. 이미 종료된 세션의 누락 복구는
별도로 범위를 정한 재수집이 필요하다.

부분 전송 중에는 이 형식을 모르는 이전 drain과 같은 spool을 동시에 소비하게 해서는 안 된다.
새 runtime은 저장된 snapshot과 checkpoint를 함께 읽어 재개해야 한다.
