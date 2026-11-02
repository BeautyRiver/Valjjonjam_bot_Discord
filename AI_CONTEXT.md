# 발쫀봇 프로젝트 인수인계

작성 기준: 2026-11-02. 구현 기준 커밋: `a48a106`.
다른 AI에 이 파일을 먼저 전달하면 됩니다. 설명이 코드와 다르면 현재 코드·Git 이력을 확인하고 차이를 알리세요.
이 문서는 운영 서버의 실제 배포 버전을 보장하지 않습니다.

## 1. 프로젝트와 작업 규칙

- 발로란트 커뮤니티 **발쫀잼**의 Discord 운영 봇입니다. 멤버 등록, 역할·닉네임 관리, 파티·이벤트 모집을 담당합니다.
- 이 폴더는 독립 Git 저장소이며 기본 브랜치는 `main`입니다. 원격: `BeautyRiver/Valjjonjam_bot_Discord`.
- 연동 웹은 형제 폴더 `../valjjonjam-map-wheel`의 **발쫀잼 툴킷**입니다. 별도 저장소이므로 커밋·푸시도 따로 합니다.
- 상위 폴더에서 두 저장소의 변경을 한꺼번에 커밋하지 마세요. `git -C <프로젝트 폴더> ...` 또는 해당 폴더에서 작업합니다.
- 사용자에게는 **한국어**로 설명하고 커밋 메시지도 한국어로 작성합니다.
- 작업 완료 후 **“커밋·푸시 진행할까요?”**라고 묻습니다. 해당 작업에 사용자가 이미 명시적으로 승인했다면 반복 질문 없이 진행합니다. 이전 작업의 승인을 새 작업에 재사용하지 않습니다.
- 커밋·푸시 승인은 **GCP 배포, 서비스 재시작, DB 수정, Discord 실메시지 전송 승인과 다릅니다.**
- 시작할 때 `git status --short`, 브랜치, 관련 diff를 확인합니다. 사용자·다른 AI의 변경을 덮어쓰거나 일괄 정리하지 않습니다.
- 동시에 작업한다면 담당 파일을 나누고, 같은 파일 수정이 겹치면 먼저 조율합니다. 명시한 파일만 stage하고 강제 푸시·하드 리셋은 하지 않습니다.
- 요청된 범위만 수정하고 검증합니다. 기능 변경 후 이 문서와 필요시 상대 프로젝트 안내도 갱신합니다.

## 2. 기술 구조와 찾아볼 파일

- Python + `discord.py==2.7.1`, `firebase-admin==7.4.0`, `python-dotenv==1.2.2` (`requirements.txt` 기준).
- 주요 구현은 **`bot.py` 단일 파일**입니다. Firestore Admin SDK를 사용합니다.
- Discord Slash Command → Modal/Select/Button → 검증 → Firestore 저장 → 모집 메시지 갱신 구조입니다.
- 동기 DB 작업은 주로 `asyncio.to_thread`로 실행합니다. 동시 참가 처리는 Firestore transaction과 action lock을 사용합니다.

| 영역 | 코드에서 검색할 이름 |
| --- | --- |
| 로그인·시작 작업 | 파일 상단 Firebase 초기화, `on_ready`, 파일 끝 `bot.run` |
| 멤버 등록·인증 | `BasicSetupModal`, `BasicSetupView`, `TierRoleView`, `VERIFIED_ROLE` |
| 내부 티어 | `INTERNAL_TIER_LABELS`, `INTERNAL_TIER_SCORES`, `set_internal_tier` |
| 일반 파티 | `PartyView`, `PartyCreationView`, `PartyTimeEditView`, `join_party_transaction`, `leave_party_transaction` |
| 이벤트·티어 제한 | `EventFeeSelectionView`, `EventDetailsModal`, `EventCreationView`, `EventView`, `event_tier_*` |
| 웹 공개 데이터 동기화 | `build_toolkit_recruitment_data`, `*_recruitment_pair`, `backfill_toolkit_recruitments_sync` |
| 자동 정리 | `cleanup_parties`, `close_started_events`, `on_raw_message_delete` |
| 테스트 | `tests/test_event_tiers.py` |
| 서버 업데이트 | `Guide.txt`, `Update-Bot.cmd`, `Update-Bot.ps1` |

`on_ready`는 영구 View 등록, 기존 모집글 갱신, 공개 미러 백필, 정리 루프 시작, 명령어 sync를 수행합니다.
재시작 후 기존 메시지 버튼이 작동해야 하므로 `custom_id`와 persistent View 동작을 보존하세요.

## 3. 현재 기능과 유지해야 할 동작

### 멤버·운영

- `/기본설정`: 학번·이름·Riot ID·최대 인게임 티어·선호 역할군을 등록하고 Discord 역할·닉네임에 반영합니다.
- `/닉네임변경`, `/역할설정`: 본인 정보 변경. 선호 역할군은 최대 3개입니다.
- `/대리기본설정`, `/대리역할설정`, `/대리닉네임변경`: 관리자용 대리 수정.
- `/내부티어설정`: 관리자용 내부 티어 수정. `미분류` 선택도 지원하며 DB에 `null`을 저장합니다.
- `/내부티어초기화`: 내부 티어만 미분류로 초기화합니다. 다른 멤버 필드를 지우지 않습니다.
- `/역할생성`, `/메세지`, `/미인증안내`, `/탈퇴정리`: 운영 명령어. 권한 검사와 실제 부작용을 읽고 사용하세요.
- 새 멤버에게 기본설정 안내 DM을 보내고 실패 시 시스템 채널로 안내합니다. 역할이 없다는 이유로 자동 강퇴하지 않습니다.
- `/탈퇴정리`는 캐시와 Discord API로 부재를 확인한 계정의 `users` 문서를 삭제하고, 삭제·조회 보류 명단을 실행자에게 비공개로 전달합니다.

### 일반 파티

- `/파티채널설정`: 서버별 모집 채널 ID를 저장합니다. 새 채널을 만드는 명령이 아닙니다.
- `/파티생성`: 지정 채널에서 **5인큐 / 일반 내전 10명** 선택 → 날짜·시간 선택. 파티장은 자동 참가합니다.
- 여러 파티에 동시에 참가할 수 있습니다. 같은 파티 중복 참가는 차단합니다.
- 정원 초과자는 `waitlist_ids`에 등록하고 빈자리가 생기면 FIFO 순서로 참가 확정·DM 알림을 처리합니다.
- 5인큐만 희망 티어대 선택을 제공합니다. 이는 **안내일 뿐 참가 제한이 아닙니다.** 10명 내전에는 없습니다.
- 시작 시간 변경과 삭제는 파티장·관리자 권한을 검사합니다. 시간 변경 시 확정자와 대기자에게 DM을 시도하며 변경 실행자는 제외합니다.
- 시작 시간은 현재부터 최소 10분 이후, 최대 24시간 이내. 시작되면 참가 마감, **시작 시각 + 10시간** 후 삭제 대상입니다. 정리 루프는 5분 주기입니다.
- 멤버 표시는 `get_party_member_names`가 조회한 닉네임 텍스트를 사용합니다. 모바일에서 ID 숫자로 보였던 문제를 피하려고 멘션 문자열에만 의존하지 않습니다.

### 발쫀전 이벤트

- 이름은 **발쫀전**입니다. 과거 이름인 발쫀컵과 제거된 건틀릿(16명) 모집 기능을 임의로 복구하지 마세요.
- `/이벤트생성`은 관리자·지정 채널 전용입니다. 정원 제한 없이 모집하고 주최자는 자동 참가하지 않습니다.
- 상품, 안내/규칙, 참가비 여부를 입력합니다. 참가비가 있으면 금액·은행/예금주·계좌번호를 받습니다. 봇이 입금·결제 처리까지 하는 것은 아닙니다.
- 5명씩 팀 구성에 사용할 참가자 명단을 보관하고 나머지는 대기로 안내합니다. 봇이 직접 밸런스 팀을 확정하는 것은 아닙니다.
- 참가·취소·전체 참가자 보기, 관리자 모집 마감·재개·삭제를 제공합니다.
- 시작 시각은 최소 10분 이후~14일 이내. 시작 이벤트는 1분 주기로 마감하며, 일반 파티의 10시간 자동 삭제 정책을 이벤트에 적용하지 않습니다.
- 참가 제한은 `none` / `valorant` / `internal` 중 **하나**입니다. 첫 생성 화면에서 조건 선택 후 참가비·상세 정보·시간 입력으로 이어집니다.
- 발로란트 제한은 **직접 등록한 최대 티어** 기준입니다. Riot API의 실시간 현재 랭크가 아닙니다. 범위에 속한 티어의 1~3을 모두 포함하며 언랭·미등록은 등록 안내 후 차단합니다.
- 내부 티어 제한은 아래 점수표로 비교합니다. 미분류·미등록은 등록 안내 후 차단하고 `?`는 같은 티어의 하로 판정합니다. 경계 선택지는 중복되는 `?` 대신 하를 사용합니다.
- 양끝 포함, 역전·미선택 범위 생성 차단. 참가 시 이벤트 조건과 `users` 문서를 **같은 transaction에서 읽은 후** 명단을 기록합니다. 실패 사유는 참가자 본인에게만 표시합니다.
- 참가 후 티어 변경에 따른 자동 제외·자동 재심사는 없습니다. 필드 없는 기존 이벤트는 자유 참가입니다.
- `@everyone` 알림 옵션, 시작 10분 전 알림은 현재 구현된 기능으로 간주하지 마세요.

## 4. Firestore 데이터 계약 — 웹과 함께 유지

Discord ID와 메시지 ID는 **문자열**로 저장·비교합니다. 시간은 UTC datetime/Timestamp로 저장하고 생성 UI는 한국 시간(UTC+9)을 사용합니다.

| 컬렉션 / 문서 ID | 주요 필드와 의미 |
| --- | --- |
| `users/{discordId}` | `discord_id`, `username`, `student_id`, `name`, `riot_id`, `tier`, `role`(배열), `internal_tier`, `updated_at` |
| `guild_settings/{guildId}` | `party_channel_id`, `updated_by`, `updated_at` |
| `parties/{messageId}` | `name`, `creator_id`, `guild_id`, `channel_id`, `message_id`, `member_ids`, `waitlist_ids`, `max_members`(5/10), `preferred_tier`, `scheduled_at`, `delete_at`, `status`, 생성/수정 시각 |
| `events/{messageId}` | 공통 모집 정보와 `member_ids`, `prize`, `has_entry_fee`, `entry_fee`, `bank_holder`, `account_number`, `rules`, `tier_restriction`, `scheduled_at`, `status`, 생성/수정 시각 |
| `toolkit_recruitments/{party또는event}_{messageId}` | 팀 편성용 공개 미러. 원본 컬렉션과 함께 갱신/삭제합니다. |

`tier_restriction` 예시: `{"mode":"internal","min":"5하","max":"3상"}` 또는 `{"mode":"valorant","min":"아이언","max":"다이아"}`. 자유 참가 값은 `null`, 필드 없음 또는 `mode: "none"`입니다.

공개 미러의 필드: `kind`, `source_id`, `guild_id`, `name`, `scheduled_at`, `status`, `max_members`, `member_ids`, `waitlist_ids`, `preferred_tier`, `tier_restriction`, `created_at`, `updated_at`.
이벤트 미러는 `max_members: null`, `waitlist_ids: []`입니다. **상품·규칙·참가비·예금주·계좌번호를 미러에 추가하지 마세요.**
현재 웹은 `tier_restriction`을 아직 읽어 표시하거나 참가 자격을 재검사하지 않습니다. 참가 차단은 봇에서 수행합니다.

### 내부 티어 점수 (양쪽 코드가 일치해야 함)

| 내부 티어 | 하 / ? | 중 | 상 |
| --- | ---: | ---: | ---: |
| 5티어 | 1 | 2 | 3 |
| 4티어 | 5 | 6 | 7 |
| 3티어 | 9 | 10 | 11 |
| 2티어 | 13 | 14 | 15 |
| 1티어 | 단일 17점 | 해당 없음 | 해당 없음 |

DB 코드는 `1`, `2상`, `2중`, `2하`, `2?` … `5?`입니다. `1?`는 없습니다.
미분류는 `internal_tier: null` 또는 필드 부재이며 `?`와 다릅니다. 멤버 이름별 하드코딩 티어·fallback은 다시 넣지 마세요.
점수 변경 시 웹 `script.js`의 `INTERNAL_TIER_INFO`와 양쪽 테스트도 함께 확인합니다.

## 5. 안전과 알려진 제약

- **`bot.py`를 확인 목적으로 실행하거나 import하지 마세요.** 모듈 상단에서 `.env`와 서비스 계정 파일을 로드하고 Firebase를 초기화하며, 마지막에 실제 봇을 실행합니다.
- `.env`, `serviceAccountKey.json`, `valjjonjam-server-token.txt`, SSH 개인키는 읽어 출력하거나 첨부·복사·커밋하지 마세요.
- `valjjonjam-server-token.txt`는 작성 시점에 이미 추적 중인 파일입니다. 내용은 열지 않았으며, 보안 정리가 필요하면 별도 작업으로 소유자와 상의하세요. 임의의 이력 삭제·키 교체는 금지합니다.
- `users`는 서버별 하위 컬렉션이 아닌 공용 컬렉션입니다. `/탈퇴정리`는 명령을 실행한 서버 기준으로 전체 `users`를 검사합니다. **같은 운영 DB에 연결된 개인 테스트 서버에서 실행하면 운영 멤버를 삭제할 수 있습니다.**
- 웹의 `firestore.rules`는 `users` 문서 전체와 공개 미러의 읽기를 허용합니다. 비밀 필드를 `users`에 넣지 마세요. Admin SDK의 권한과 웹 클라이언트 규칙은 별개입니다.
- 로컬 수정만으로 운영 봇에 반영되지 않습니다. 현재 배포 여부는 사용자 확인이나 승인된 서버 조회 없이는 알 수 없습니다.

## 6. 검증과 배포

먼저 유효한 Python 실행 환경을 확인하세요. Windows의 `python`이 Microsoft Store 실행 별칭일 수 있습니다.
필요한 패키지는 분리된 가상환경에서 설치합니다. 실제 실행은 테스트 서버·테스트 자격증명 또는 별도 운영 승인이 있을 때만 수행합니다.

```sh
python -m py_compile bot.py tests/test_event_tiers.py
python -m unittest discover -s tests -v
git diff --check
```

테스트는 `discord.py`가 필요합니다. AST로 허용한 정의만 가져오고 실제 Discord UI 클래스와 가짜 DB/응답으로 검사합니다. 토큰·서비스 계정 파일을 읽거나 운영 네트워크에 연결하지 않습니다.
현재 회귀 테스트 15개는 이벤트 티어 조합·미등록·? 처리·transaction·참가비 양쪽 생성 흐름·비공개 참가 안내를 다룹니다. 봇의 모든 기능을 검증하는 테스트는 아닙니다.

배포는 `Guide.txt`를 기준으로 별도 승인 후 수행합니다.
- 로컬 `Update-Bot.cmd` → `Update-Bot.ps1`이 GCP SSH로 서버의 `~/bot`에서 `git pull --ff-only origin main` 후 `systemctl restart valjjonjam`, 실행 상태를 확인합니다.
- 로컬 커밋·푸시, 패키지 설치, 환경변수 변경, 중지 VM 켜기는 하지 않는 도구입니다.
- 전용 SSH 키 `.ssh/valjjonjam_deploy`와 `--no-force-key-file-overwrite`를 사용합니다. 기존 `google_compute_engine` 키를 덮어쓰지 마세요.
- 서비스 상태: `sudo systemctl status valjjonjam`, 로그: `sudo journalctl -u valjjonjam -f`. 로그 공유 시 개인정보·자격증명을 가리세요.
- GCP 비용이 무조건 0원이라고 보장하지 마세요. 인프라·과금 변경은 코드 작업 범위에 자동 포함되지 않습니다.

작업 인계에는 **변경 파일 / 이유 / 실행한 테스트와 미검증 항목 / 커밋·푸시 여부 / 서버 배포 여부**를 구분해 남기세요.
