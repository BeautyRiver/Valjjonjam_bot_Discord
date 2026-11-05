# 발쫀봇 프로젝트 인수인계

작성 기준: 2026-11-05. 구현 기준 커밋: `20ba155` 이후 정보 확인 안내 추가.
다른 AI에 이 파일을 먼저 전달하면 됩니다. 설명이 코드와 다르면 현재 코드·Git 이력을 확인하고 차이를 알리세요.
이 문서는 운영 서버의 실제 배포 버전을 보장하지 않습니다.

## 1. 프로젝트와 작업 규칙

- 발로란트 커뮤니티 **발쫀잼**의 Discord 운영 봇입니다. 멤버 등록, 역할·닉네임 관리, 파티·이벤트 모집을 담당합니다.
- 이 폴더는 독립 Git 저장소이며 기본 브랜치는 `main`입니다. 원격: `BeautyRiver/Valjjonjam_bot_Discord`.
- 연동 웹은 형제 폴더 `../Valjjonjam-toolkit`의 **발쫀잼 툴킷**입니다(원격: `BeautyRiver/Valjjonjam-toolkit`, 이전 이름 `valjjonjam-map-wheel`). 별도 저장소이므로 커밋·푸시도 따로 합니다.
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
| 멤버 등록·인증 | `BasicSetupModal`, `BasicSetupView`, `TierRoleView`, `VERIFIED_ROLE`, `apply_tier_roles`, `apply_nickname`, `save_user_profile` |
| 정보 확인 안내 | `profile_check_targets`, `ProfileCheckButton`, `send_profile_checks`, `profile_check_command`, `configure_profile_check`, `run_profile_check_auto`, `profile_check_auto_loop` |
| 내부 티어 | `INTERNAL_TIER_LABELS`, `INTERNAL_TIER_SCORES`, `set_internal_tier`, `update_internal_tier` |
| 일반 파티 | `PartyView`, `PartyCreationView`, `PartyTimeEditView`, `join_party_transaction`, `leave_party_transaction`, `DeleteConfirmView` |
| 이벤트·티어 제한 | `EventFeeSelectionView`, `EventDetailsModal`, `EventCreationView`, `EventView`, `EventMemberRemoveView`, `event_tier_*` |
| 웹 공개 데이터 동기화 | `build_toolkit_recruitment_data`, `*_recruitment_pair`, `backfill_toolkit_recruitments_sync` |
| 자동 정리 | `cleanup_parties`, `close_started_events`, `on_raw_message_delete`, `is_recruitment_channel` |
| 명령어 공통 오류 | `on_app_command_error`, `commands.Bot(allowed_contexts=...)` |
| 테스트 | `tests/test_event_tiers.py` |
| 패치 후 점검 skill | `.agents/skills/bot-check/` (Claude는 `.claude/skills/bot-check/`가 이 파일을 가리킴) |
| 서버 업데이트 | `Guide.txt`, `Update-Bot.cmd`, `Update-Bot.ps1` |

`on_ready`는 영구 View 등록, 기존 모집글 갱신, 공개 미러 백필, 정리 루프 시작, 명령어 sync를 수행합니다.
재시작 후 기존 메시지 버튼이 작동해야 하므로 `custom_id`와 persistent View 동작을 보존하세요.
고정 `custom_id`: `party:join|leave|change_time|delete`, `event:join|leave|members|close|reopen|delete|remove_member`.
정보 확인 DM 버튼은 서버 ID를 담는 동적 `custom_id` `profile_check:keep|tier|nickname:{guildId}`이며 `on_ready`의 `add_dynamic_items`로 복원합니다. 이미 보낸 DM의 버튼이 동작해야 하므로 형식을 보존하세요.

## 3. 현재 기능과 유지해야 할 동작

### 멤버·운영

- `/기본설정`: 학번·이름·Riot ID·최대 인게임 티어·선호 역할군을 등록하고 Discord 역할·닉네임에 반영합니다.
- `/닉네임변경`, `/역할설정`: 본인 정보 변경. 선호 역할군은 최대 3개입니다.
- `/기본설정`·`/역할설정`·`/닉네임변경`(대리 포함)은 같은 공용 함수로 역할(add/remove 방식)·닉네임을 적용하고 `users`에 병합 저장합니다. Discord 반영 → DB 저장 → 결과 안내 순서이며, 역할·닉네임 실패(권한 부족, 금지 단어 등)나 DB 저장 실패는 성공 안내 아래 경고로 표시하고 로그를 남깁니다. 역할이나 닉네임이 실패해도 입력 정보는 저장합니다.
- `/대리기본설정`, `/대리역할설정`, `/대리닉네임변경`: 관리자용 대리 수정.
- `/내부티어설정`: 관리자용 내부 티어 수정. `미분류` 선택도 지원하며 DB에 `null`을 저장합니다.
- `/내부티어초기화`: 내부 티어만 미분류로 초기화합니다. 다른 멤버 필드를 지우지 않습니다. `/내부티어설정`의 미분류 선택과 같은 `update_internal_tier`를 사용합니다.
- `/역할생성`, `/메세지`, `/미인증안내`, `/탈퇴정리`: 운영 명령어. 권한 검사와 실제 부작용을 읽고 사용하세요. `/미인증안내`는 일부 DM 전송이 실패해도 나머지 멤버에게 계속 보내고 실패 수를 보고합니다.
- `/메세지`는 명령어를 친 채널에 임베드를 보냅니다. 보내기 전에 제목 256자·내용 4,096자 제한과 그 채널의 봇 권한(`interaction.app_permissions`: 채널 보기·메시지 보내기(스레드는 스레드에서 메시지 보내기)·링크 첨부)을 확인해 빠진 권한 이름을 비공개로 안내합니다. 먼저 응답(defer)한 뒤 전송하며, 그래도 Discord가 권한 오류를 돌려주면 확인할 권한을 안내합니다. `미리보기`는 권한과 관계없이 본인에게만 보여줍니다.
- `/전체안내`: 관리자가 입력한 내용(`/메세지`와 같은 `build_notice_embed`)을 봇을 제외한 서버 멤버 전원에게 DM으로 보냅니다. 임베드 하단에 서버 이름을 표시합니다. `미리보기=True`는 본인에게만 보여주고, `False`면 받는 인원을 보여주는 확인 화면(`DmBroadcastConfirmView`, 60초)에서 `전송`을 눌러야 보냅니다. 스팸 제한을 피하려고 한 명당 1초(`DM_BROADCAST_INTERVAL`) 간격을 두며, 일부 DM이 실패해도 계속 보내고 실패 수를 보고합니다. `dm_broadcast_lock`으로 동시 전송을 막습니다. 완료 보고는 Discord 응답 제한(15분) 안에 끝나야 하므로 멤버가 약 800명을 넘으면 보고 방식을 다시 봐야 합니다.
- 정보 확인 안내: 멤버가 티어·닉네임을 잘 안 고치는 문제를 줄이는 리마인더입니다. 대상은 현재 서버에 있는 봇이 아닌 **인증된 등록 멤버** 중 `profile_checked_at`이 기준일 이상 지났거나 없는 사람입니다(미인증자는 `/미인증안내` 몫). 필드가 없는 기존 멤버는 처음에 모두 대상입니다.
  - DM에 현재 최대 티어·발로란트 ID와 `그대로예요`·`티어 변경`·`닉네임 변경` 버튼을 표시합니다. `그대로예요`는 확인 시각만 저장하고, 나머지는 `RoleSelectView`·`NicknameModal`을 DM에서 재사용합니다(역할군도 다시 선택). 슬래시 명령어와 달리 버튼은 DM에서도 동작하며, 서버를 나갔거나 미인증이면 안내만 합니다.
  - `save_user_profile`을 거치는 저장(기본설정·역할설정·닉네임변경, 대리 포함, DM 버튼)이 `profile_checked_at`을 갱신합니다. 내부 티어 저장은 갱신하지 않으므로 `updated_at`을 이 기준으로 쓰지 마세요.
  - `/정보확인안내`: 수동 발송. `기준일`(기본 60일, 1~365), `미리보기`. `미리보기=True`는 실행자 본인 DM으로만 실제 메시지를 보내고(DM이 막혀 있으면 비공개 표시) 발송 시각을 남기지 않습니다. `False`는 확인 화면에서 `전송`을 눌러야 보내며, 이때 대상을 다시 계산합니다. 실패한 멤버 이름을 실행자에게 비공개로 알려줍니다.
  - `/정보확인자동설정`: 자동 발송 켜기·끄기(`사용`)와 `기준일` 변경. 옵션 없이 실행하면 현재 설정만 보여줍니다. **기본은 꺼짐이며 설정한 서버에서만 동작**합니다. 켜져 있으면 매일 오후 8시(KST, `PROFILE_CHECK_AUTO_TIME`)에 대상자에게 보내고, 결과와 실패 명단은 설정을 마지막으로 바꾼 관리자에게 DM으로 보냅니다. 봇이 그 시각에 꺼져 있었으면 다음 날 보냅니다.
  - 자동 발송 대상은 `profile_check_sent_at`도 함께 봅니다. 답하지 않은 사람도 기준일이 다시 지나야 또 받으며, DM이 실패해도 발송 시각을 남겨 매일 재시도하지 않습니다. 수동 발송은 이 값을 무시하지만 값을 남기므로, 수동으로 보낸 사람에게 자동 DM이 바로 이어지지 않습니다. 보내는 중에 끄면 한 명 보내기 전마다 설정을 다시 읽어 남은 사람에게 보내지 않습니다(이미 보낸 DM은 되돌릴 수 없음). 처음 켜면 기존 멤버 전원이 대상입니다.
  - `/전체안내`와 같은 `dm_broadcast_lock`·1초 간격을 씁니다. 수동 전송은 다른 일괄 DM이 진행 중이면 거절하고, 자동 발송은 끝날 때까지 기다립니다.
- 모든 슬래시 명령어는 `allowed_contexts`로 **서버 안에서만** 노출됩니다. 권한 부족·그 밖의 명령어 오류는 `@bot.tree.error` 공통 처리기가 비공개로 안내하고 로그를 남깁니다. 명령어별 `.error` 처리기를 다시 추가하지 마세요(공통 처리기와 중복 응답).
- `/닉네임변경`·`/대리닉네임변경`의 기존 정보 조회는 `asyncio.to_thread`로 실행합니다.
- 새 멤버에게 기본설정 안내 DM을 보내고 실패 시 시스템 채널로 안내합니다. 역할이 없다는 이유로 자동 강퇴하지 않습니다.
- `/탈퇴정리`는 캐시와 Discord API로 부재를 확인한 계정의 `users` 문서를 삭제하고, 삭제·조회 보류 명단을 실행자에게 비공개로 전달합니다.

### 일반 파티

- `/파티채널설정`: 서버별 모집 채널 ID를 저장합니다(파티·발쫀전 공용). 새 채널을 만드는 명령이 아닙니다.
- `/파티생성`: 지정 채널에서 **5인큐 / 일반 내전 10명** 선택 → 날짜·시간 선택. 파티장은 자동 참가합니다.
- 여러 파티에 동시에 참가할 수 있습니다. 같은 파티 중복 참가는 차단합니다.
- 정원 초과자는 `waitlist_ids`에 등록하고 빈자리가 생기면 FIFO 순서로 참가 확정·DM 알림을 처리합니다.
- 5인큐만 희망 티어대 선택을 제공합니다. 이는 **안내일 뿐 참가 제한이 아닙니다.** 10명 내전에는 없습니다.
- 시작 시간 변경과 삭제는 파티장·관리자 권한을 검사합니다. 시간 변경 시 확정자와 대기자에게 DM을 시도하며 변경 실행자는 제외합니다.
- `파티 삭제`·`이벤트 삭제`는 바로 지우지 않고 실행자에게만 보이는 확인 화면(`DeleteConfirmView`, 60초)에서 한 번 더 눌러야 삭제됩니다.
- 이미 대기 중인 사람이 다시 참가를 누르면 현재 대기 순번을 알려줍니다. 사용자가 입력한 파티 이름은 `escape_display_text`로 마크다운·멘션을 무력화해 표시합니다.
- 시작 시간은 현재부터 최소 10분 이후, 최대 24시간 이내. 시작되면 참가 마감, **시작 시각 + 10시간** 후 삭제 대상입니다. 정리 루프는 5분 주기입니다.
- 멤버 표시는 `get_party_member_names`가 조회한 닉네임 텍스트를 사용합니다. 모바일에서 ID 숫자로 보였던 문제를 피하려고 멘션 문자열에만 의존하지 않습니다.

### 발쫀전 이벤트

- 이름은 **발쫀전**입니다. 과거 이름인 발쫀컵과 제거된 건틀릿(16명) 모집 기능을 임의로 복구하지 마세요.
- `/이벤트생성`은 관리자·지정 채널 전용입니다. 정원 제한 없이 모집하고 주최자는 자동 참가하지 않습니다.
- 상품, 안내/규칙, 참가비 여부를 입력합니다. 참가비가 있으면 금액·은행/예금주·계좌번호를 받습니다. 봇이 입금·결제 처리까지 하는 것은 아닙니다.
- 홀수 팀을 만들지 않도록 신청 순서대로 **10명(2팀) 단위로만 참가 확정**하고 나머지는 대기로 표시합니다(13명 → 확정 10·대기 3, 20명 → 확정 20). `split_event_members`가 화면에서 계산할 뿐 원본 `member_ids`에는 신청 순서 전체를 그대로 저장합니다. 확정자가 취소하면 다음 신청자가 자동으로 확정으로 올라가며, 이에 대한 DM 알림은 없습니다. 10명 단위가 깨지면 그 단위 전체가 대기로 돌아갑니다(10명 중 1명 취소 → 9명 모두 대기).
- 모집글에는 참가 확정·대기 명단과 팀 구성 현황을 표시하고, 참가 시 본인에게 확정 또는 대기 순번을 비공개로 안내합니다. 참가비 안내는 참가 확정자만 입금하도록 표시합니다. 과거의 "권장 진행 방식" 안내는 제거했습니다. 봇이 직접 밸런스 팀을 확정하는 것은 아닙니다.
- 참가·취소·전체 참가자 보기, 관리자 모집 마감·재개·삭제·**참가자 제외**를 제공합니다. 본인 취소는 모집 마감 후 불가하지만, 관리자 `참가자 제외`(`EventMemberRemoveView`, 사용자 선택 후 확인)는 마감·시작 후에도 가능하고 비운 자리는 다음 신청자가 순서대로 채웁니다. 제외 대상에게 DM은 보내지 않습니다.
- 시작 시각은 최소 10분 이후~14일 이내. 시작 이벤트는 1분 주기로 마감하며, 일반 파티의 10시간 자동 삭제 정책을 이벤트에 적용하지 않습니다.
- 이벤트 문서가 계속 쌓이므로 자동 마감 루프는 `status == "open"` 이벤트만, 재시작 시 모집글 갱신은 `scheduled_at`이 현재 기준 1일 전 이후인 이벤트만 조회합니다(`list_open_events`, `list_recent_events`, 단일 필드 자동 색인 사용). 그보다 오래된 이벤트의 모집글·DB는 재시작 때 다시 정리하지 않습니다.
- 모집글을 Discord에서 직접 지우면 DB(원본·미러)도 정리합니다. 메시지 삭제마다 DB를 읽지 않도록 설정된 모집 채널과 모집글이 올라간 채널(실행 중 메모리 캐시, `is_recruitment_channel`)에서 지워진 메시지만 확인합니다. 재시작 때 모집글이 사라진 파티·최근 이벤트는 DB도 정리합니다.
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
| `users/{discordId}` | `discord_id`, `username`, `student_id`, `name`, `riot_id`, `tier`, `role`(배열), `internal_tier`, `nickname`(`/역할설정` 때 표시 이름), `updated_at`, `profile_checked_at`(본인·대리 정보 확인 시각), `profile_check_sent_at`(마지막 정보 확인 DM 시도 시각). 시각 두 개는 봇 전용이며 웹은 읽지 않습니다. |
| `guild_settings/{guildId}` | `party_channel_id`, `profile_check_auto_enabled`, `profile_check_days`, `profile_check_report_to`, `updated_by`, `updated_at` |
| `parties/{messageId}` | `name`, `creator_id`, `guild_id`, `channel_id`, `message_id`, `member_ids`, `waitlist_ids`, `max_members`(5/10), `preferred_tier`, `scheduled_at`, `delete_at`, `status`, 생성/수정 시각 |
| `events/{messageId}` | 공통 모집 정보와 `member_ids`, `prize`, `has_entry_fee`, `entry_fee`, `bank_holder`, `account_number`, `rules`, `tier_restriction`, `scheduled_at`, `status`, 생성/수정 시각 |
| `toolkit_recruitments/{party또는event}_{messageId}` | 팀 편성용 공개 미러. 원본 컬렉션과 함께 갱신/삭제합니다. |

`tier_restriction` 예시: `{"mode":"internal","min":"5하","max":"3상"}` 또는 `{"mode":"valorant","min":"아이언","max":"다이아"}`. 자유 참가 값은 `null`, 필드 없음 또는 `mode: "none"`입니다.

공개 미러의 필드: `kind`, `source_id`, `guild_id`, `name`, `scheduled_at`, `status`, `max_members`, `member_ids`, `waitlist_ids`, `preferred_tier`, `tier_restriction`, `created_at`, `updated_at`.
이벤트 미러는 `max_members: null`이며, `member_ids`에는 참가 확정자(10명 단위)만, `waitlist_ids`에는 대기자를 신청 순서대로 담습니다. 원본 `events.member_ids`는 확정·대기를 나누지 않은 신청 순서 전체입니다. 기존 이벤트 미러는 봇 재시작 시 `on_ready` 백필로 이 형식으로 다시 기록됩니다. **상품·규칙·참가비·예금주·계좌번호를 미러에 추가하지 마세요.**
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
현재 회귀 테스트 67개는 이벤트 티어 조합·미등록·? 처리·transaction·참가비 양쪽 생성 흐름·비공개 참가 안내, 이벤트 10명 단위 확정·대기 표시·미러 분리, 기본설정·역할설정·닉네임변경의 역할·닉네임·DB 저장 실패 안내, 이벤트 조회 범위, 일반 파티 참가·대기·FIFO 승격·마감, 삭제 확인, 관리자 참가자 제외, 모집 채널 삭제 감지 캐시, 공통 명령어 오류 처리, 내부 티어 저장, `/메세지` 권한·글자 수 확인, `/전체안내` 미리보기·확인·전송 실패·동시 전송 차단, 정보 확인 안내의 대상 선정·확인 시각 갱신·DM 버튼·자동 설정·자동 발송·발송 중 끄기를 다룹니다. 봇의 모든 기능을 검증하는 테스트는 아닙니다.

패치 후에는 `bot-check` skill(Claude `/bot-check`, Codex는 `.agents/skills`에서 인식)로 위 검사를 한 번에 실행할 수 있습니다.
`python .agents/skills/bot-check/scripts/bot_check.py [--base origin/main]`은 `bot.py`를 실행·import하지 않고 문법·`git diff --check`·회귀 테스트를 돌린 뒤, 공개 미러의 비밀 필드 누출, 내부 티어 점수·이름, `EVENT_TIER_MODES`, 컬렉션 이름, 고정 버튼 `custom_id`의 변경 전후와 웹 `INTERNAL_TIER_INFO` 점수 일치 여부를 보고합니다.
pyright는 설치된 경우에만 실행하며, 기준선(`pyright-baseline.json`) 저장 후에는 새 진단만 보고합니다. 커밋·푸시·배포는 하지 않습니다.

배포는 `Guide.txt`를 기준으로 별도 승인 후 수행합니다.
- 로컬 `Update-Bot.cmd` → `Update-Bot.ps1`이 GCP SSH로 서버의 `~/bot`에서 `git pull --ff-only origin main` 후 `systemctl restart valjjonjam`, 실행 상태를 확인합니다.
- 로컬 커밋·푸시, 패키지 설치, 환경변수 변경, 중지 VM 켜기는 하지 않는 도구입니다.
- 전용 SSH 키 `.ssh/valjjonjam_deploy`와 `--no-force-key-file-overwrite`를 사용합니다. 기존 `google_compute_engine` 키를 덮어쓰지 마세요.
- 서비스 상태: `sudo systemctl status valjjonjam`, 로그: `sudo journalctl -u valjjonjam -f`. 로그 공유 시 개인정보·자격증명을 가리세요.
- GCP 비용이 무조건 0원이라고 보장하지 마세요. 인프라·과금 변경은 코드 작업 범위에 자동 포함되지 않습니다.

작업 인계에는 **변경 파일 / 이유 / 실행한 테스트와 미검증 항목 / 커밋·푸시 여부 / 서버 배포 여부**를 구분해 남기세요.
