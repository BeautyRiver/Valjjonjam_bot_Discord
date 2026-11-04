---
name: bot-check
description: 발쫀봇 패치 후 점검. bot.py를 실행·import하지 않고 문법 검사, 가짜 DB 회귀 테스트, 웹과 공유하는 필드·내부 티어 점수·버튼 custom_id 변경 여부를 보고한다. bot.py나 tests를 수정한 뒤, 또는 사용자가 /bot-check·점검·검증을 요청할 때 사용. 커밋·푸시·배포는 하지 않는다.
---

# bot-check — 발쫀봇 패치 후 점검

기준 문서는 저장소 루트의 `AI_CONTEXT.md`입니다. 이 파일은 점검 절차만 다룹니다.

## 하지 않는 것

- `bot.py` 실행·import, 실제 봇·Firebase 연결
- `.env`, `serviceAccountKey.json`, `valjjonjam-server-token.txt`, SSH 키 열기
- 커밋·푸시, GCP 배포·재시작, 운영 DB 수정, Discord 실메시지·DM 전송
- 웹 저장소(`../Valjjonjam-toolkit`) 수정 — 변경이 필요하면 전달 메모만 작성
- 사용자 승인 없는 패키지 설치 (pyright 포함)

## 1. 점검 스크립트 실행

저장소 루트에서 실행합니다.

```sh
python .agents/skills/bot-check/scripts/bot_check.py
```

- 기본 비교 기준은 `HEAD`입니다(커밋하지 않은 변경). 이미 커밋한 변경까지 보려면 `--base origin/main`.
- 스크립트가 하는 일:
  - ① `bot.py`·`tests/*.py` compile 문법 검사, `git diff --check`, pyright(설치된 경우만)
  - ② `python -m unittest discover -s tests -v`
  - ③ 공개 미러 필드와 비밀 필드 누출, 내부 티어 점수·이름, `EVENT_TIER_MODES`, 컬렉션 이름, 고정 버튼 `custom_id`의 변경 전후 비교, 웹 `script.js`의 `INTERNAL_TIER_INFO` 점수 일치 여부
- 종료 코드 1은 FAIL이 있다는 뜻입니다. WARN은 실패가 아니라 사람이 확인할 항목입니다.
- pyright는 처음에 기존 경고가 많을 수 있습니다. 사용자가 원하면 `--update-pyright-baseline`으로 현재 상태를 기준선(`pyright-baseline.json`)으로 저장하고, 이후에는 새 진단만 봅니다.

## 2. 스크립트가 못 보는 항목 — diff를 읽고 확인

변경이 해당 영역에 닿았을 때만 확인합니다.

- 일반 파티의 **희망 티어**는 안내일 뿐 참가를 막지 않는지, 발쫀전 **티어 제한**은 실제로 참가를 막는지
- 대기자 `waitlist_ids` FIFO 승격과 DM 시도 순서가 유지되는지
- `on_ready`의 persistent View 등록이 유지되어 재시작 후 기존 모집글 버튼이 작동하는지
- 이벤트 참가 시 조건과 `users` 문서를 같은 transaction에서 읽는지
- 원본 컬렉션과 `toolkit_recruitments` 미러를 함께 갱신·삭제하는지
- 기능 동작이 바뀌었다면 `AI_CONTEXT.md`를 갱신했는지

## 3. 보고

한국어로 아래를 구분해 보고합니다.

1. ①②③ 결과 (OK / WARN / FAIL / SKIP 그대로)
2. 2장 확인 항목 중 확인한 것과 확인하지 못한 것
3. ③에 WARN이 있으면 웹 담당자 전달 메모:
   - 바뀐 필드·값의 변경 전 → 후
   - 기존 Firestore 데이터 처리 방법 (그대로 둠 / 백필 필요 / 옛 값 호환)
   - 웹에서 확인할 위치 (예: `script.js`의 `INTERNAL_TIER_INFO`, `firestore.rules`)
4. 커밋·푸시 여부와 서버 배포 여부 — 이 점검은 둘 다 하지 않았다고 명시
