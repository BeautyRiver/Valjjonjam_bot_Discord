import discord
from discord.ext import commands, tasks
from discord import app_commands
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import transactional
from google.cloud.firestore_v1.base_query import FieldFilter
from dotenv import load_dotenv
import os
import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

# 로깅 설정 (실패 원인을 콘솔에 자세히 남김)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("valjjonjam")

# 환경변수 로드
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Firebase 초기화
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# 봇 초기화
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
# 모든 슬래시 명령어는 서버 안에서만 노출 (봇 DM·그룹 DM에서는 숨김)
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
)

# 티어 목록 (실제 디스코드 역할명 = 저장값)
TIERS = [
    "언랭",
    "아이언 1", "아이언 2", "아이언 3",
    "브론즈 1", "브론즈 2", "브론즈 3",
    "실버 1", "실버 2", "실버 3",
    "골드 1", "골드 2", "골드 3",
    "플래티넘 1", "플래티넘 2", "플래티넘 3",
    "다이아 1", "다이아 2", "다이아 3",
    "초월자1", "초월자2", "초월자3",
    "불멸 1", "불멸 2", "불멸 3", "레디언트"
]

# 2단계 선택용: 큰 티어 + 세부등급 없는 티어
MAJOR_TIERS = ["언랭", "아이언", "브론즈", "실버", "골드", "플래티넘", "다이아", "초월자", "불멸", "레디언트"]
NO_DIVISION = {"언랭", "레디언트"}  # 세부등급(1/2/3)이 없는 티어

# 큰 티어 + 세부등급 → 실제 티어명 (기존 역할명 형식 유지: 초월자는 공백 없음)
def make_tier(major, division):
    if major in NO_DIVISION:
        return major
    if major == "초월자":
        return f"{major}{division}"
    return f"{major} {division}"

# View에 저장된 선택값으로 최종 티어 계산 → (티어, 에러메시지)
def resolve_tier(view):
    major = getattr(view, "major_tier", None)
    if major is None:
        return None, "최대 티어를 선택해주세요!"
    if major in NO_DIVISION:
        return major, None
    division = getattr(view, "division", None)
    if division is None:
        return None, f"`{major}` 의 세부 등급(1/2/3)을 선택해주세요!"
    return make_tier(major, division), None

# 역할군 목록
ROLES = ["타격대", "척후대", "전략가", "감시자", "플렉스"]

# 내전·5인큐 기록으로 정리한 발쫀잼 내부 티어
INTERNAL_TIER_LABELS = {
    "1": "1티어",
    "2상": "2티어 -상", "2중": "2티어 -중", "2하": "2티어 -하", "2?": "2티어 -?",
    "3상": "3티어 -상", "3중": "3티어 -중", "3하": "3티어 -하", "3?": "3티어 -?",
    "4상": "4티어 -상", "4중": "4티어 -중", "4하": "4티어 -하", "4?": "4티어 -?",
    "5상": "5티어 -상", "5중": "5티어 -중", "5하": "5티어 -하", "5?": "5티어 -?",
}
INTERNAL_TIER_SCORES = {
    "1": 17,
    "2상": 15, "2중": 14, "2하": 13, "2?": 13,
    "3상": 11, "3중": 10, "3하": 9, "3?": 9,
    "4상": 7, "4중": 6, "4하": 5, "4?": 5,
    "5상": 3, "5중": 2, "5하": 1, "5?": 1,
}
INTERNAL_TIER_CHOICES = [
    app_commands.Choice(name=label, value=tier)
    for tier, label in INTERNAL_TIER_LABELS.items()
] + [app_commands.Choice(name="미분류", value="unclassified")]

# 인증(기본설정 완료) 역할명 — 이 역할이 있어야 일반 채널이 열리도록 서버 권한을 설정
VERIFIED_ROLE = "인증됨"

# 파티 모집 설정
PARTY_COLLECTION = "parties"
EVENT_COLLECTION = "events"
TOOLKIT_RECRUITMENT_COLLECTION = "toolkit_recruitments"
GUILD_SETTINGS_COLLECTION = "guild_settings"
PARTY_MAX_MEMBERS = 5
INHOUSE_PARTY_MAX_MEMBERS = 10
SUPPORTED_PARTY_SIZES = {PARTY_MAX_MEMBERS, INHOUSE_PARTY_MAX_MEMBERS}
PARTY_TIMEZONE = timezone(timedelta(hours=9))
PARTY_MIN_LEAD = timedelta(minutes=10)
PARTY_MAX_LEAD = timedelta(hours=24)
PARTY_DELETE_DELAY = timedelta(hours=10)
EVENT_MAX_LEAD = timedelta(days=14)
EVENT_REFRESH_WINDOW = timedelta(days=1)
EVENT_PUBLIC_MEMBER_LIMIT = 20
EVENT_TEAM_SIZE = 5
# 홀수 팀이 생기지 않도록 2팀(10명) 단위로만 참가 확정
EVENT_CONFIRM_UNIT = EVENT_TEAM_SIZE * 2
EVENT_TIER_MODES = {
    "none": "제한 없음 (자유 참가)",
    "valorant": "발로란트 티어",
    "internal": "발쫀잼 내부 티어",
}
PARTY_PUBLIC_WAITLIST_LIMIT = 20
PARTY_PREFERRED_TIER_LABELS = {
    "free": "자유 모집",
    "unrated": "일반전",
    "iron_bronze": "아이언~브론즈",
    "bronze_silver": "브론즈~실버",
    "silver_gold": "실버~골드",
    "gold_platinum": "골드~플래티넘",
    "platinum_diamond": "플래티넘~다이아",
    "diamond_ascendant": "다이아~초월자",
    "ascendant_immortal": "초월자~불멸",
    "immortal_plus": "불멸 이상",
}

# 인증 역할을 가져오거나 없으면 생성
async def get_or_create_verified_role(guild):
    role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
    if role is None:
        role = await guild.create_role(
            name=VERIFIED_ROLE,
            permissions=discord.Permissions.none()
        )
    return role

# 인증(기본설정 완료) 역할 보유 여부
def is_verified(member):
    return any(r.name == VERIFIED_ROLE for r in member.roles)

# DM 등 서버 밖에서 쓰면 안내 후 차단 — 차단했으면 True 반환
async def block_if_not_in_guild(interaction):
    if interaction.guild is not None:
        return False
    await interaction.response.send_message(
        "⚠️ 이 명령어는 **서버 채널 안에서** 사용해주세요!\n"
        "봇과의 개인 DM(1:1 채팅)에서는 역할·닉네임을 설정할 수 없어요. 발쫀잼 서버로 가서 다시 입력해주세요.",
        ephemeral=True
    )
    return True

# 미인증자면 안내 후 차단 — 차단했으면 True 반환
async def block_if_unverified(interaction):
    if is_verified(interaction.user):
        return False
    await interaction.response.send_message(
        "🔒 먼저 `/기본설정` 으로 등록을 완료해주세요!", ephemeral=True
    )
    return True

# 관리자 대리 명령어 공통: 대상이 봇이면 안내 후 차단 — 차단했으면 True 반환
async def block_if_bot_target(interaction, 대상):
    if 대상.bot:
        await interaction.response.send_message(
            "❌ 봇은 설정 대상이 될 수 없어요!", ephemeral=True
        )
        return True
    return False

# 슬래시 명령어 공통 오류 처리 — 권한 부족은 안내, 그 밖의 오류는 로그를 남기고 실패를 알림
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ 어드민만 사용 가능한 명령어입니다!"
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "⚠️ 이 명령어는 **서버 채널 안에서** 사용해주세요!"
    else:
        command_name = getattr(interaction.command, "name", "알 수 없음")
        log.error("명령어 처리 실패 — /%s user=%s", command_name, interaction.user.id, exc_info=error)
        message = "❌ 명령어 처리 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        log.exception("명령어 오류 안내 전송 실패")

# 기본설정 안내 문구 (입장 안내 / 전체 안내 공용)
SETUP_GUIDE = (
    "👋 **발쫀잼 서버에 오신 걸 환영해요!**\n"
    "서버에서 `/기본설정` 을 입력해 **학번 · 이름 · 발로란트 닉네임 · 티어 · 역할군**을 등록해주세요.\n"
    "등록하면 역할과 닉네임이 자동으로 세팅됩니다! 🎮\n"    
)

# 필독-규칙 채널 ID (채널 우클릭 → ID 복사. 0이면 링크 생략)
RULES_CHANNEL_ID = 1478315682503327824

# 규칙 채널 링크를 붙인 안내 문구 생성
def build_setup_guide(guild=None):
    if RULES_CHANNEL_ID:
        return SETUP_GUIDE + f"\n\n📜 먼저 <#{RULES_CHANNEL_ID}> 을(를) 꼭 확인해주세요!"
    return SETUP_GUIDE

# 미인증자 재안내 문구 (서버에 있지만 아직 /기본설정 안 한 멤버용)
UNVERIFIED_NOTICE = (
    "📢 **발쫀잼 서버 이용 안내**\n"
    "아직 `/기본설정` 등록을 완료하지 않으셨어요!\n"
    "`/기본설정` 을 입력해 **학번 · 이름 · 발로란트 닉네임 · 티어 · 역할군**을 등록해주세요.\n"
    "등록을 마치면 역할과 닉네임이 자동으로 세팅돼요. 🎮\n"
)

# 규칙 채널 링크를 붙인 미인증자 안내 문구 생성
def build_unverified_notice(guild=None):
    if RULES_CHANNEL_ID:
        return UNVERIFIED_NOTICE + f"\n\n📜 먼저 <#{RULES_CHANNEL_ID}> 을(를) 꼭 확인해주세요!"
    return UNVERIFIED_NOTICE

@bot.event
async def on_ready():
    # 재시작 후에도 기존 모집글의 버튼이 계속 동작하도록 영구 View 등록
    if not getattr(bot, "party_view_registered", False):
        bot.add_view(PartyView())
        bot.party_view_registered = True
        await refresh_party_messages()
    if not getattr(bot, "event_view_registered", False):
        bot.add_view(EventView())
        bot.event_view_registered = True
        await refresh_event_messages()
    if not getattr(bot, "profile_check_button_registered", False):
        bot.add_dynamic_items(ProfileCheckButton)
        bot.profile_check_button_registered = True
    if not getattr(bot, "toolkit_recruitments_backfilled", False):
        if await backfill_toolkit_recruitments():
            bot.toolkit_recruitments_backfilled = True
    if not cleanup_parties.is_running():
        cleanup_parties.start()
    if not close_started_events.is_running():
        close_started_events.start()
    if not profile_check_auto_loop.is_running():
        profile_check_auto_loop.start()
    await bot.tree.sync()
    print(f"{bot.user} 온라인!")

# 새 멤버 입장 시 자동 안내
@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    guide = build_setup_guide(member.guild)
    try:
        await member.send(guide)
    except discord.HTTPException:
        # DM이 막혀있거나 전송에 실패하면 시스템 채널에 멘션으로 안내
        channel = member.guild.system_channel
        if channel and channel.permissions_for(member.guild.me).send_messages:
            try:
                await channel.send(f"{member.mention}\n{guide}")
            except discord.HTTPException:
                log.exception("입장 안내 전송 실패 — %s(%s)", member, member.id)

# 티어 선택 1단계 - 큰 티어
class MajorTierSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=t) for t in MAJOR_TIERS]
        super().__init__(placeholder="최대 티어를 선택하세요", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.view.major_tier = self.values[0]
        for opt in self.options:
            opt.default = (opt.label == self.values[0])
        self.view.refresh_division()  # 언랭·레디언트면 세부등급 드롭다운 숨김
        await interaction.response.edit_message(view=self.view)

# 티어 선택 2단계 - 세부등급 (언랭·레디언트일 땐 표시 안 함)
class DivisionSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=str(d)) for d in (1, 2, 3)]
        super().__init__(placeholder="세부 등급 (1 / 2 / 3)", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        for opt in self.options:
            opt.default = (opt.label == self.values[0])
        self.view.division = self.values[0]
        await interaction.response.defer()

# 역할 선택 드롭다운 - 역할군
class RoleSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=role) for role in ROLES]
        super().__init__(
            placeholder="역할군을 선택하세요 (최대 3개)",
            options=options,
            min_values=1,
            max_values=3,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view.selected_roles = self.values

# 티어(2단계)+역할군 선택 공용 View — 확인 버튼만 갈아끼움
# target: 역할/닉네임을 적용할 대상 멤버(관리자가 대신 설정할 때). None이면 명령어 실행자 본인
class TierRoleView(discord.ui.View):
    def __init__(self, confirm_item, target=None, timeout=120):
        super().__init__(timeout=timeout)
        self.target = target
        self.major_tier = None
        self.division = None
        self.division_select = DivisionSelect()
        confirm_item.row = 3
        self.add_item(MajorTierSelect())
        self.add_item(RoleSelect())
        self.add_item(confirm_item)

    # 큰 티어에 따라 세부등급 드롭다운을 보였다 숨겼다
    def refresh_division(self):
        needs = self.major_tier is not None and self.major_tier not in NO_DIVISION
        present = self.division_select in self.children
        if needs and not present:
            self.add_item(self.division_select)
        elif not needs and present:
            self.remove_item(self.division_select)
            self.division = None
            for opt in self.division_select.options:
                opt.default = False

ROLE_FAILED_NOTICE = "\n⚠️ 일부 역할을 적용하지 못했어요. (봇에 '역할 관리' 권한을 주고 봇 역할을 위로 올려주세요)"
NICKNAME_FORBIDDEN_NOTICE = (
    "\n⚠️ 닉네임을 바꿀 권한이 없어요. (서버 주인 닉네임은 봇이 못 바꿔요 / "
    "그 외엔 '별명 관리하기' 권한 + 봇 역할을 위로 올려주세요)"
)
NICKNAME_FAILED_NOTICE = (
    "\n⚠️ 닉네임은 바꾸지 못했어요. (이름·발로 닉네임에 `@`, `discord`, `clyde`, `everyone`, `here` "
    "같은 단어가 들어가면 디스코드가 막아요. `/닉네임변경` 으로 다시 시도해보세요)"
)
DB_SAVE_FAILED_NOTICE = (
    "\n⚠️ 입력한 정보를 DB에 저장하지 못했어요. 잠시 후 다시 시도해주세요. "
    "(저장 전까지 이벤트 참가 조건과 웹에는 이전 정보가 적용돼요)"
)


# 티어·역할군 역할 교체 — add/remove로 처리해 봇보다 위 역할을 가진 멤버에게도 영향을 덜 받음
# 성공하면 None, 실패하면 Discord 예외를 반환
async def apply_tier_roles(guild, member, tier, selected_roles, extra_roles=()):
    to_add = []
    for role_name in [tier, *selected_roles]:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            to_add.append(role)
    to_add.extend(extra_roles)

    managed = set(TIERS + ROLES)
    to_remove = [r for r in member.roles if r.name in managed and r not in to_add]
    try:
        if to_remove:
            await member.remove_roles(*to_remove)
        await member.add_roles(*to_add)
    except discord.HTTPException as e:
        log.error(
            "역할 적용 실패 — %s(%s) / 부여시도=%s / status=%s code=%s: %s",
            member, member.id, [r.name for r in to_add],
            getattr(e, "status", "?"), getattr(e, "code", "?"), e,
        )
        return e
    return None


# 닉네임 변경 — 성공하면 "", 실패하면 사용자 안내 문구를 반환
async def apply_nickname(member, nickname):
    try:
        await member.edit(nick=nickname)
        return ""
    except discord.HTTPException as e:
        log.error(
            "닉네임 변경 실패 — %s(%s) / 닉='%s' / status=%s code=%s: %s",
            member, member.id, nickname,
            getattr(e, "status", "?"), getattr(e, "code", "?"), e,
        )
        if isinstance(e, discord.Forbidden):
            return NICKNAME_FORBIDDEN_NOTICE
        return NICKNAME_FAILED_NOTICE


def get_user_data(user_id):
    snapshot = db.collection("users").document(str(user_id)).get()
    return snapshot.to_dict() if snapshot.exists else {}


# users 문서에 병합 저장 (다른 필드는 유지) — 실패하면 로그를 남기고 False
async def save_user_profile(member, fields):
    try:
        await asyncio.to_thread(
            db.collection("users").document(str(member.id)).set,
            {
                "discord_id": str(member.id),
                "username": member.name,
                **fields,
                "updated_at": firestore.SERVER_TIMESTAMP,
                # 정보 확인 안내 기준 — 관리자가 내부 티어만 바꿀 때도 갱신되는 updated_at과 구분
                "profile_checked_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        return True
    except Exception:
        log.exception("멤버 정보 DB 저장 실패 — %s(%s)", member, member.id)
        return False


# 확인 버튼
class ConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="확인", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        tier, err = resolve_tier(view)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not hasattr(view, "selected_roles"):
            await interaction.response.send_message("역할군을 선택해주세요!", ephemeral=True)
            return

        await interaction.response.send_message("⏳ 적용 중...", ephemeral=True)
        member = view.target or interaction.user  # 관리자가 대신 설정 시 대상 멤버

        # 정보 확인 DM의 버튼에서 열면 interaction.guild가 없어 대상 멤버의 서버를 사용
        guild = interaction.guild or member.guild
        role_failed = await apply_tier_roles(guild, member, tier, view.selected_roles)
        saved = await save_user_profile(
            member,
            {"nickname": member.display_name, "tier": tier, "role": view.selected_roles},
        )

        msg = f"✅ **{member.display_name}** 님\n티어: `{tier}` | 역할군: `{', '.join(view.selected_roles)}` 설정 완료!"
        if role_failed:
            msg += ROLE_FAILED_NOTICE
        if not saved:
            msg += DB_SAVE_FAILED_NOTICE
        await interaction.edit_original_response(content=msg)

# 전체 View
class RoleSelectView(TierRoleView):
    def __init__(self, target=None):
        super().__init__(ConfirmButton(), target=target, timeout=60)

# 슬래시 명령어
@bot.tree.command(name="역할설정", description="역할군과 티어를 설정합니다")
async def set_role(interaction: discord.Interaction):
    if await block_if_not_in_guild(interaction):
        return
    if await block_if_unverified(interaction):
        return
    await interaction.response.send_message(
        "아래에서 역할군과 티어를 선택해주세요!",
        view=RoleSelectView(),
        ephemeral=True
    )

@bot.tree.command(name="역할생성", description="역할군&티어 역할을 자동으로 생성합니다")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def create_roles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    created = []
    existing = [r.name for r in guild.roles]

    # 티어별 색깔 지정
    tier_colors = {
        "언랭": 0x99AAB5,
        "아이언 1": 0x6B6B6B, "아이언 2": 0x6B6B6B, "아이언 3": 0x6B6B6B,
        "브론즈 1": 0xA0522D, "브론즈 2": 0xA0522D, "브론즈 3": 0xA0522D,
        "실버 1": 0xC0C0C0, "실버 2": 0xC0C0C0, "실버 3": 0xC0C0C0,
        "골드 1": 0xFFD700, "골드 2": 0xFFD700, "골드 3": 0xFFD700,
        "플래티넘 1": 0x00CED1, "플래티넘 2": 0x00CED1, "플래티넘 3": 0x00CED1,
        "다이아 1": 0x00BFFF, "다이아 2": 0x00BFFF, "다이아 3": 0x00BFFF,
        "초월자1": 0x9400D3, "초월자2": 0x9400D3, "초월자3": 0x9400D3,
        "불멸 1": 0xFF4500, "불멸 2": 0xFF4500, "불멸 3": 0xFF4500,
        "레디언트": 0xFFFF00,
    }

    # 역할군 색깔 지정
    role_colors = {
        "타격대": 0xFF4444,
        "척후대": 0x44FF44,
        "전략가": 0x4444FF,
        "감시자": 0xFFFF44,
        "플렉스": 0xFF44FF,
    }

    all_colors = {**tier_colors, **role_colors}

    for role_name, color in all_colors.items():
        if role_name not in existing:
            await guild.create_role(
                name=role_name,
                color=discord.Color(color),
                permissions=discord.Permissions.none()  # everyone 기본 권한
            )
            created.append(role_name)

    # 인증 역할도 함께 생성 (없을 때만)
    if VERIFIED_ROLE not in existing:
        await guild.create_role(name=VERIFIED_ROLE, permissions=discord.Permissions.none())
        created.append(VERIFIED_ROLE)

    if created:
        await interaction.followup.send(f"✅ {len(created)}개 역할 생성 완료!\n`{'`, `'.join(created)}`", ephemeral=True)
    else:
        await interaction.followup.send("이미 모든 역할이 존재합니다!", ephemeral=True)
        
# Discord 임베드 글자 수 제한
EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096


# 입력한 내용을 임베드로 변환 — (임베드, None) 또는 글자 수 초과 시 (None, 안내 문구) 반환
def build_notice_embed(내용, 제목=None):
    # 슬래시 입력은 줄바꿈을 못 넣으니 \n 표기를 실제 줄바꿈으로 변환
    description = 내용.replace("\\n", "\n")
    if len(description) > EMBED_DESCRIPTION_LIMIT or len(제목 or "") > EMBED_TITLE_LIMIT:
        return None, (
            f"❌ 제목은 {EMBED_TITLE_LIMIT}자, 내용은 {EMBED_DESCRIPTION_LIMIT:,}자까지 보낼 수 있어요. "
            f"(현재 제목 {len(제목 or '')}자 · 내용 {len(description):,}자)"
        )

    embed = discord.Embed(description=description, color=discord.Color.blurple())
    if 제목:
        embed.title = 제목
    return embed, None


# 채널에 임베드를 보내는 데 필요한데 봇에게 없는 권한 이름 목록 (스레드는 스레드 전송 권한 확인)
def missing_send_permissions(permissions, in_thread=False):
    required = [
        ("view_channel", "채널 보기"),
        ("send_messages_in_threads", "스레드에서 메시지 보내기")
        if in_thread
        else ("send_messages", "메시지 보내기"),
        ("embed_links", "링크 첨부"),
    ]
    return [label for name, label in required if not getattr(permissions, name)]


@bot.tree.command(name="메세지", description="봇이 현재 채널에 임베드 메세지를 보냅니다")
@app_commands.describe(
    내용="보낼 내용 (줄바꿈은 \\n 으로 입력)",
    제목="임베드 제목 (선택)",
    미리보기="True면 나에게만 보여요 (전송 전 확인용)",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def bot_say(interaction: discord.Interaction, 내용: str, 제목: str = None, 미리보기: bool = False):
    embed, error = build_notice_embed(내용, 제목)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    # 미리보기(디버그): 채널에 안 보내고 나에게만 보여줌
    if 미리보기:
        await interaction.response.send_message(
            content="🧪 **미리보기** — 아직 채널에 전송되지 않았어요!",
            embed=embed, ephemeral=True
        )
        return

    # 보내기 전에 이 채널에서 봇이 가진 권한(app_permissions)을 확인해 빠진 권한을 알려줌
    missing = missing_send_permissions(
        interaction.app_permissions, isinstance(interaction.channel, discord.Thread)
    )
    if missing:
        await interaction.response.send_message(
            f"❌ 봇에게 이 채널의 {', '.join(f'`{label}`' for label in missing)} 권한이 없어 보낼 수 없어요.\n"
            "채널 편집 → 권한에서 발쫀봇 역할에 허용해주세요. (카테고리 권한에 막혀 있을 수도 있어요)",
            ephemeral=True,
        )
        return

    # 먼저 응답해 두고 보내서, 전송이 늦어도 '응답 없음' 오류가 나지 않게 함
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(embed=embed)
    except discord.Forbidden as e:
        log.warning("메세지 전송 권한 부족 — channel=%s code=%s", interaction.channel_id, e.code)
        await interaction.followup.send(
            "❌ 권한 문제로 보내지 못했어요. 채널·카테고리 권한에서 발쫀봇의 "
            "`채널 보기`, `메시지 보내기`, `링크 첨부`를 확인해주세요.",
            ephemeral=True,
        )
        return
    await interaction.followup.send("✅ 메세지를 보냈어요!", ephemeral=True)


# 짧은 시간에 DM을 몰아 보내면 Discord 스팸 제한에 걸릴 수 있어 한 명씩 간격을 둠 (초)
DM_BROADCAST_INTERVAL = 1
dm_broadcast_lock = asyncio.Lock()


def dm_broadcast_recipients(guild):
    return [member for member in guild.members if not member.bot]


async def send_dm_broadcast(guild, embed):
    sent, failed = 0, 0
    for member in dm_broadcast_recipients(guild):
        try:
            await member.send(embed=embed)
            sent += 1
        except discord.HTTPException as e:
            # DM 차단 또는 일시적인 전송 실패 — 나머지 멤버에게는 계속 보냄
            failed += 1
            log.warning("전체 안내 DM 실패 — user=%s status=%s", member.id, e.status)
        await asyncio.sleep(DM_BROADCAST_INTERVAL)
    return sent, failed


class DmSendConfirmView(discord.ui.View):
    """보내면 되돌릴 수 없는 일괄 DM을 실행자에게 한 번 더 확인."""

    cancel_message = "전송을 취소했어요."

    def __init__(self):
        super().__init__(timeout=60)

    async def send_all(self, interaction):
        raise NotImplementedError

    @discord.ui.button(label="전송", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        # 일괄 DM이 겹치면 같은 사람이 중복으로 받거나 스팸 제한에 걸릴 수 있음
        if dm_broadcast_lock.locked():
            await interaction.response.edit_message(
                content="⚠️ 이미 다른 일괄 DM을 보내는 중이에요. 끝난 뒤 다시 시도해주세요.", view=None
            )
            return
        async with dm_broadcast_lock:
            await interaction.response.edit_message(content="⏳ DM을 보내는 중...", view=None)
            await self.send_all(interaction)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content=self.cancel_message, view=None)


class DmBroadcastConfirmView(DmSendConfirmView):
    cancel_message = "전체 안내를 취소했어요."

    def __init__(self, guild, embed):
        super().__init__()
        self.guild = guild
        self.embed = embed

    async def send_all(self, interaction):
        sent, failed = await send_dm_broadcast(self.guild, self.embed)
        await interaction.edit_original_response(
            content=f"📣 전체 안내 DM 전송 완료!\n✅ 보냄: {sent}명 | ❌ 전송 실패(DM 차단 등): {failed}명"
        )


@bot.tree.command(name="전체안내", description="봇을 제외한 서버 멤버 전원에게 입력한 내용을 DM으로 보냅니다")
@app_commands.describe(
    내용="보낼 내용 (줄바꿈은 \\n 으로 입력)",
    제목="임베드 제목 (선택)",
    미리보기="True면 나에게만 보여요 (전송 전 확인용)",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def dm_broadcast(interaction: discord.Interaction, 내용: str, 제목: str = None, 미리보기: bool = False):
    embed, error = build_notice_embed(내용, 제목)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    # DM에서는 어느 서버의 공지인지 알 수 없으니 서버 이름을 표시
    guild = interaction.guild
    embed.set_footer(text=f"{guild.name} 공지")
    count = len(dm_broadcast_recipients(guild))

    if 미리보기:
        await interaction.response.send_message(
            content=f"🧪 **미리보기** — 아직 아무에게도 전송되지 않았어요! (받는 사람 {count}명)",
            embed=embed, ephemeral=True
        )
        return

    await interaction.response.send_message(
        content=f"📨 봇을 제외한 멤버 **{count}명**에게 아래 내용을 DM으로 보낼까요?\n보낸 DM은 되돌릴 수 없어요.",
        embed=embed, view=DmBroadcastConfirmView(guild, embed), ephemeral=True
    )


@bot.tree.command(name="미인증안내", description="인증됨 역할이 없는 멤버 전원에게 기본설정 안내 DM을 보냅니다")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def announce_unverified(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    guide = build_unverified_notice(guild)

    sent, failed, skipped = 0, 0, 0
    for member in guild.members:
        if member.bot:
            continue
        if is_verified(member):  # 인증됨 역할 있으면 제외
            skipped += 1
            continue
        try:
            await member.send(guide)
            sent += 1
        except discord.HTTPException:
            failed += 1  # DM 차단 또는 일시적인 전송 실패 — 나머지 멤버에게는 계속 보냄

    await interaction.followup.send(
        f"📣 미인증자 안내 DM 전송 완료!\n"
        f"✅ 보냄: {sent}명 | ⏭️ 이미 인증: {skipped}명 | ❌ 전송 실패(DM 차단 등): {failed}명",
        ephemeral=True
    )


async def send_split_lines(send, title, lines):
    """Discord 2,000자 제한에 맞춰 여러 줄을 나눠 보냄."""
    message = title
    for line in lines:
        next_message = f"{message}\n{line}"
        if len(next_message) <= 1900:
            message = next_message
            continue
        await send(message)
        message = f"{title} **(계속)**\n{line}"
    await send(message)


async def send_ephemeral_log(interaction, title, lines):
    async def send(message):
        await interaction.followup.send(message, ephemeral=True)

    await send_split_lines(send, title, lines)


@bot.tree.command(name="탈퇴정리", description="서버에 없는 멤버를 확인해 DB에서 삭제하고 명단을 알려줍니다")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_db(interaction: discord.Interaction):
    if await block_if_not_in_guild(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    registered = await asyncio.to_thread(
        lambda: list(db.collection("users").stream())
    )
    gone = []
    lookup_failed = []

    # 캐시에 없으면 Discord API로 다시 확인하고, 실제로 없는 멤버만 삭제 대상으로 삼음
    for document in registered:
        user_id = document.id
        data = document.to_dict() or {}
        saved_name = data.get("name") or data.get("nickname") or data.get("username") or "이름 없음"
        saved_name = discord.utils.escape_markdown(discord.utils.escape_mentions(saved_name))
        label = f"{saved_name} (`{user_id}`)"

        try:
            member_id = int(user_id)
        except (TypeError, ValueError):
            lookup_failed.append(f"{label} — 잘못된 Discord ID")
            continue

        if guild.get_member(member_id) is not None:
            continue
        try:
            await guild.fetch_member(member_id)
        except discord.NotFound:
            gone.append((document, label))
        except (discord.Forbidden, discord.HTTPException):
            lookup_failed.append(f"{label} — Discord 조회 실패")

    def delete_docs():
        batch = db.batch()
        for document, _ in gone:
            batch.delete(document.reference)
        batch.commit()

    if gone:
        await asyncio.to_thread(delete_docs)

    await interaction.followup.send(
        "🧹 **탈퇴 멤버 DB 정리 완료**\n"
        "Discord 서버에 실제로 없는 계정만 Firebase `users`에서 삭제했습니다.\n"
        f"🗑️ 삭제: {len(gone)}명 | 👥 남은 등록: {len(registered) - len(gone)}명"
        f" | ⚠️ 조회 보류: {len(lookup_failed)}명",
        ephemeral=True
    )

    deleted_lines = [f"• {label}" for _, label in gone]
    if deleted_lines:
        await send_ephemeral_log(interaction, "🗑️ **삭제된 멤버**", deleted_lines)
    else:
        await interaction.followup.send("✅ 삭제할 탈퇴 멤버가 없었습니다.", ephemeral=True)

    if lookup_failed:
        await send_ephemeral_log(
            interaction,
            "⚠️ **삭제하지 않고 보류한 멤버**",
            [f"• {line}" for line in lookup_failed],
        )


# ===== 발쫀잼 내부 티어 관리 =====

@bot.tree.command(name="내부티어설정", description="(어드민) 멤버의 발쫀잼 내부 티어를 설정합니다")
@app_commands.describe(대상="내부 티어를 설정할 멤버", 티어="설정할 발쫀잼 내부 티어 (미분류 선택 시 기존 티어 해제)")
@app_commands.choices(티어=INTERNAL_TIER_CHOICES)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def set_internal_tier(
    interaction: discord.Interaction,
    대상: discord.Member,
    티어: app_commands.Choice[str],
):
    internal_tier = None if 티어.value == "unclassified" else 티어.value
    await update_internal_tier(interaction, 대상, internal_tier)


@bot.tree.command(name="내부티어초기화", description="(어드민) 멤버의 발쫀잼 내부 티어를 미분류로 초기화합니다")
@app_commands.describe(대상="내부 티어를 초기화할 멤버")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def clear_internal_tier(interaction: discord.Interaction, 대상: discord.Member):
    # /내부티어설정에서 미분류를 고른 것과 같은 동작
    await update_internal_tier(interaction, 대상, None)


# 내부 티어 저장 공통 — internal_tier가 None이면 미분류(DB null)
async def update_internal_tier(interaction, 대상, internal_tier):
    if await block_if_not_in_guild(interaction):
        return
    if await block_if_bot_target(interaction, 대상):
        return

    await interaction.response.defer(ephemeral=True)
    user_ref = db.collection("users").document(str(대상.id))
    snapshot = await asyncio.to_thread(user_ref.get)
    data = snapshot.to_dict() if snapshot.exists else {}
    if not data.get("name"):
        await interaction.followup.send(
            f"❌ **{escape_display_text(대상.display_name)}** 님의 기본설정 정보가 없어요. "
            "`/기본설정`을 먼저 진행해주세요.",
            ephemeral=True,
        )
        return

    await asyncio.to_thread(
        user_ref.set,
        {"internal_tier": internal_tier, "updated_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    name = escape_display_text(data["name"])
    if internal_tier is None:
        await interaction.followup.send(
            f"✅ **{name}** 님의 발쫀잼 내부 티어를 `미분류`로 설정했어요.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        f"✅ **{name}** 님의 발쫀잼 내부 티어 `{INTERNAL_TIER_LABELS[internal_tier]}` 설정을 완료했어요. "
        f"(밸런스 점수: `{INTERNAL_TIER_SCORES[internal_tier]:g}점`)",
        ephemeral=True,
    )


# ===== /기본설정 =====

# 닉네임 자동 생성: "학번 이름 / 발로닉#태그" (디스코드 최대 32자)
def build_nickname(student_id, name, riot_id):
    return f"{student_id} {name} / {riot_id}"[:32]

# 1단계: 학번/이름/발로란트 닉네임 입력 모달
class BasicSetupModal(discord.ui.Modal, title="기본 설정"):
    student_id = discord.ui.TextInput(label="학번", placeholder="예: 21", max_length=10)
    name = discord.ui.TextInput(label="이름", placeholder="예: 홍길동", max_length=20)
    riot_id = discord.ui.TextInput(
        label="발로 닉네임#태그 (Discord 닉네임 자동변경됨)",
        placeholder="예: test#kr1",
        max_length=30
    )

    def __init__(self, target=None):
        super().__init__()
        self.target = target  # 관리자가 대신 설정할 대상 멤버

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "마지막으로 티어와 역할군을 선택한 뒤 확인을 눌러주세요!",
            view=BasicSetupView(str(self.student_id), str(self.name), str(self.riot_id), self.target),
            ephemeral=True
        )

# 2단계: 티어/역할군 선택 후 확인 버튼
class BasicSetupConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="확인", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        tier, err = resolve_tier(view)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not hasattr(view, "selected_roles"):
            await interaction.response.send_message("역할군을 선택해주세요!", ephemeral=True)
            return

        await interaction.response.send_message("⏳ 적용 중...", ephemeral=True)
        guild = interaction.guild
        member = view.target or interaction.user  # 관리자가 대신 설정 시 대상 멤버

        # ① 기본설정 완료 → 인증 역할도 함께 부여 (없으면 생성)
        #    역할을 먼저·따로 부여 — 닉네임이 막혀도 인증은 무조건 되게
        verified = await get_or_create_verified_role(guild)
        role_failed = await apply_tier_roles(
            guild, member, tier, view.selected_roles, extra_roles=[verified]
        )

        # ② 닉네임은 별도로 — 실패해도 인증은 통과
        nick_notice = await apply_nickname(
            member, build_nickname(view.student_id, view.name, view.riot_id)
        )

        saved = await save_user_profile(
            member,
            {
                "student_id": view.student_id,
                "name": view.name,
                "riot_id": view.riot_id,
                "tier": tier,
                "role": view.selected_roles,
            },
        )

        msg = (
            f"✅ **{view.name}** 님 설정 완료!\n"
            f"학번: `{view.student_id}` | 발로ID: `{view.riot_id}`\n"
            f"티어: `{tier}` | 역할군: `{', '.join(view.selected_roles)}`"
        )
        if role_failed:
            msg += ROLE_FAILED_NOTICE
        msg += nick_notice
        if not saved:
            msg += DB_SAVE_FAILED_NOTICE
        await interaction.edit_original_response(content=msg)

# 2단계 View (모달 입력값을 들고 다님)
class BasicSetupView(TierRoleView):
    def __init__(self, student_id, name, riot_id, target=None):
        super().__init__(BasicSetupConfirmButton(), target=target, timeout=120)
        self.student_id = student_id
        self.name = name
        self.riot_id = riot_id

@bot.tree.command(name="기본설정", description="학번/이름/발로란트 닉네임과 티어·역할군을 설정합니다")
async def basic_setup(interaction: discord.Interaction):
    if await block_if_not_in_guild(interaction):
        return
    await interaction.response.send_modal(BasicSetupModal())


# ===== /닉네임변경 =====

# 학번/이름/발로닉만 받아 디스코드 닉네임을 "학번 이름 / 발로닉" 으로 변경
class NicknameModal(discord.ui.Modal):
    def __init__(self, target, student_id="", name="", riot_id=""):
        super().__init__(title="닉네임 변경")
        self.target = target  # 닉네임을 바꿀 대상 멤버(관리자가 대신 변경 시)
        self.student_id = discord.ui.TextInput(
            label="학번", placeholder="예: 21", default=student_id, max_length=10
        )
        self.name = discord.ui.TextInput(
            label="이름", placeholder="예: 홍길동", default=name, max_length=20
        )
        self.riot_id = discord.ui.TextInput(
            label="발로란트 닉네임#태그", placeholder="예: test#kr1", default=riot_id, max_length=30
        )
        self.add_item(self.student_id)
        self.add_item(self.name)
        self.add_item(self.riot_id)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ 적용 중...", ephemeral=True)
        member = self.target  # 관리자가 대신 변경 시 대상 멤버
        sid, nm, rid = str(self.student_id), str(self.name), str(self.riot_id)
        new_nick = build_nickname(sid, nm, rid)
        nick_notice = await apply_nickname(member, new_nick)

        # 입력한 정보도 DB에 반영 (기존 티어/역할군 등은 유지)
        saved = await save_user_profile(member, {"student_id": sid, "name": nm, "riot_id": rid})

        if not nick_notice:
            msg = f"✅ 닉네임을 `{new_nick}` (으)로 변경했어요!"
        else:
            msg = nick_notice.lstrip("\n")
            if saved:
                msg += "\n정보는 저장했어요."
        if not saved:
            msg += DB_SAVE_FAILED_NOTICE
        await interaction.edit_original_response(content=msg)

@bot.tree.command(name="닉네임변경", description="디스코드 닉네임을 '학번 이름 / 발로닉' 형식으로 변경합니다")
async def change_nickname(interaction: discord.Interaction):
    if await block_if_not_in_guild(interaction):
        return
    if await block_if_unverified(interaction):
        return
    # 기존에 저장된 정보가 있으면 모달에 미리 채워줌
    data = await asyncio.to_thread(get_user_data, interaction.user.id)
    await interaction.response.send_modal(NicknameModal(
        interaction.user,
        data.get("student_id", ""),
        data.get("name", ""),
        data.get("riot_id", ""),
    ))


# ===== 관리자 대리 설정 =====
# 본인 명령어(기본설정/역할설정/닉네임변경)와 동일한 View/Modal을 쓰되
# 대상 멤버를 넣어 그 사람에게 적용. 어드민에게만 디스코드 UI에 노출됨.

@bot.tree.command(name="대리기본설정", description="(어드민) 다른 멤버의 학번·이름·발로닉·티어·역할군을 대신 등록합니다")
@app_commands.describe(대상="설정해줄 멤버")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def admin_basic_setup(interaction: discord.Interaction, 대상: discord.Member):
    if await block_if_bot_target(interaction, 대상):
        return
    await interaction.response.send_modal(BasicSetupModal(대상))


@bot.tree.command(name="대리역할설정", description="(어드민) 다른 멤버의 역할군·티어를 대신 설정합니다")
@app_commands.describe(대상="설정해줄 멤버")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def admin_set_role(interaction: discord.Interaction, 대상: discord.Member):
    if await block_if_bot_target(interaction, 대상):
        return
    await interaction.response.send_message(
        f"**{대상.display_name}** 님의 역할군과 티어를 선택해주세요!",
        view=RoleSelectView(대상),
        ephemeral=True
    )


@bot.tree.command(name="대리닉네임변경", description="(어드민) 다른 멤버의 닉네임을 '학번 이름 / 발로닉' 형식으로 변경합니다")
@app_commands.describe(대상="닉네임을 바꿔줄 멤버")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def admin_change_nickname(interaction: discord.Interaction, 대상: discord.Member):
    if await block_if_bot_target(interaction, 대상):
        return
    # 대상에게 저장된 정보가 있으면 모달에 미리 채워줌
    data = await asyncio.to_thread(get_user_data, 대상.id)
    await interaction.response.send_modal(NicknameModal(
        대상,
        data.get("student_id", ""),
        data.get("name", ""),
        data.get("riot_id", ""),
    ))


# ===== 정보 확인 안내 =====
# 티어·닉네임을 오래 확인하지 않은 멤버에게 DM으로 확인을 요청. 바뀌었으면 DM 버튼으로 바로 수정.

PROFILE_CHECK_DEFAULT_DAYS = 60
PROFILE_CHECK_AUTO_TIME = time(hour=20, tzinfo=PARTY_TIMEZONE)
PROFILE_CHECK_BUTTONS = {
    "keep": ("그대로예요", "✅", discord.ButtonStyle.green),
    "tier": ("티어 변경", "🎯", discord.ButtonStyle.secondary),
    "nickname": ("닉네임 변경", "✏️", discord.ButtonStyle.secondary),
}


def list_registered_users():
    return {document.id: document.to_dict() or {} for document in db.collection("users").stream()}


def get_guild_settings(guild_id):
    snapshot = db.collection(GUILD_SETTINGS_COLLECTION).document(str(guild_id)).get()
    return (snapshot.to_dict() or {}) if snapshot.exists else {}


# respect_sent: 자동 발송용 — 답하지 않은 사람도 기준일이 다시 지나야 또 받음
def profile_check_due(data, days, now, respect_sent=False):
    times = [as_utc(data.get("profile_checked_at"))]
    if respect_sent:
        times.append(as_utc(data.get("profile_check_sent_at")))
    times = [value for value in times if value is not None]
    return not times or now - max(times) >= timedelta(days=days)


# 현재 서버의 인증된 등록 멤버만 대상 (users는 서버 구분이 없는 공용 컬렉션, 미인증자는 /미인증안내 대상)
def profile_check_targets(guild, users, days, now, respect_sent=False):
    targets = []
    for user_id, data in users.items():
        member = guild.get_member(int(user_id)) if user_id.isdigit() else None
        if member is None or member.bot or not is_verified(member) or not data.get("name"):
            continue
        if profile_check_due(data, days, now, respect_sent):
            targets.append((member, data))
    return targets


def build_profile_check_embed(guild, data):
    embed = discord.Embed(
        title="📋 내 정보 확인",
        description="등록된 정보가 지금도 맞는지 확인해주세요!\n바뀐 내용이 있으면 아래 버튼으로 바로 수정할 수 있어요.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="최대 티어", value=escape_display_text(data.get("tier")) or "미등록")
    embed.add_field(name="발로란트 ID", value=escape_display_text(data.get("riot_id")) or "미등록")
    embed.set_footer(text=f"{guild.name} 공지")
    return embed


# DM에는 서버 정보가 없으니 custom_id에 서버 ID를 담음. 재시작 후에도 동작하도록 on_ready에서 등록
class ProfileCheckButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"profile_check:(?P<action>keep|tier|nickname):(?P<guild_id>[0-9]+)",
):
    def __init__(self, action, guild_id):
        label, emoji, style = PROFILE_CHECK_BUTTONS[action]
        super().__init__(discord.ui.Button(
            label=label, emoji=emoji, style=style, custom_id=f"profile_check:{action}:{guild_id}"
        ))
        self.action = action
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction):
        guild = bot.get_guild(self.guild_id)
        member = guild.get_member(interaction.user.id) if guild else None
        if member is None:
            await interaction.response.send_message(
                "❌ 서버에서 멤버 정보를 찾지 못했어요. 서버에 계속 있는지 확인해주세요.", ephemeral=True
            )
            return
        if not is_verified(member):
            await interaction.response.send_message(
                "🔒 먼저 서버에서 `/기본설정` 으로 등록을 완료해주세요!", ephemeral=True
            )
            return

        if self.action == "tier":
            await interaction.response.send_message(
                "아래에서 역할군과 티어를 선택해주세요!", view=RoleSelectView(member), ephemeral=True
            )
            return
        if self.action == "nickname":
            data = await asyncio.to_thread(get_user_data, member.id)
            await interaction.response.send_modal(NicknameModal(
                member,
                data.get("student_id", ""),
                data.get("name", ""),
                data.get("riot_id", ""),
            ))
            return

        await interaction.response.defer()
        if await save_user_profile(member, {}):
            await interaction.edit_original_response(content="✅ 확인했어요! 감사합니다.", view=None)
        else:
            await interaction.followup.send(DB_SAVE_FAILED_NOTICE.strip(), ephemeral=True)


def profile_check_view(guild_id):
    view = discord.ui.View(timeout=None)
    for action in PROFILE_CHECK_BUTTONS:
        view.add_item(ProfileCheckButton(action, guild_id))
    return view


async def mark_profile_check_sent(user_id):
    try:
        await asyncio.to_thread(
            db.collection("users").document(str(user_id)).set,
            {"profile_check_sent_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
    except Exception:
        log.exception("정보 확인 안내 발송 시각 저장 실패 — user=%s", user_id)


# should_continue: 자동 발송 중 꺼졌는지 확인 — 꺼지면 남은 사람에게는 보내지 않음
# 반환: (보낸 수, 실패 명단, 보내지 않은 수)
async def send_profile_checks(guild, targets, should_continue=None):
    sent, failed_names = 0, []
    for index, (member, data) in enumerate(targets):
        if should_continue is not None and not await should_continue():
            return sent, failed_names, len(targets) - index
        try:
            await member.send(embed=build_profile_check_embed(guild, data), view=profile_check_view(guild.id))
            sent += 1
        except discord.HTTPException as e:
            failed_names.append(escape_display_text(member.display_name))
            log.warning("정보 확인 안내 DM 실패 — user=%s status=%s", member.id, e.status)
        # DM을 막아 둔 사람에게 매일 다시 시도하지 않도록 실패해도 발송 시각을 남김
        await mark_profile_check_sent(member.id)
        await asyncio.sleep(DM_BROADCAST_INTERVAL)
    return sent, failed_names, 0


def profile_check_summary(sent, failed_names):
    return f"✅ 보냄: {sent}명 | ❌ 전송 실패(DM 차단 등): {len(failed_names)}명"


class ProfileCheckConfirmView(DmSendConfirmView):
    cancel_message = "정보 확인 안내를 취소했어요."

    def __init__(self, guild, days):
        super().__init__()
        self.guild = guild
        self.days = days

    async def send_all(self, interaction):
        # 확인을 누르기 전에 답한 사람이 빠지도록 대상을 다시 계산
        try:
            users = await asyncio.to_thread(list_registered_users)
        except Exception:
            log.exception("정보 확인 안내 대상 조회 실패")
            await interaction.edit_original_response(
                content="❌ 멤버 정보를 불러오지 못해 보내지 않았어요. 잠시 후 다시 시도해주세요."
            )
            return
        targets = profile_check_targets(self.guild, users, self.days, utc_now())
        sent, failed_names, _ = await send_profile_checks(self.guild, targets)
        await interaction.edit_original_response(
            content=f"📣 정보 확인 안내 DM 전송 완료!\n{profile_check_summary(sent, failed_names)}"
        )
        if failed_names:
            await send_ephemeral_log(
                interaction, "❌ **DM을 받지 못한 멤버**", [f"• {name}" for name in failed_names]
            )


@bot.tree.command(name="정보확인안내", description="티어·닉네임을 오래 확인하지 않은 멤버에게 확인 DM을 보냅니다")
@app_commands.describe(
    기준일=f"이 일수 이상 확인하지 않은 멤버에게 보내요 (기본 {PROFILE_CHECK_DEFAULT_DAYS}일)",
    미리보기="True면 내 DM으로만 보내요 (전송 전 확인용)",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def profile_check_command(
    interaction: discord.Interaction,
    기준일: app_commands.Range[int, 1, 365] = PROFILE_CHECK_DEFAULT_DAYS,
    미리보기: bool = False,
):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    users = await asyncio.to_thread(list_registered_users)
    count = len(profile_check_targets(guild, users, 기준일, utc_now()))

    # 미리보기: 버튼까지 직접 눌러볼 수 있게 실제 DM을 나에게만 보냄 (발송 시각은 남기지 않음)
    if 미리보기:
        embed = build_profile_check_embed(guild, users.get(str(interaction.user.id), {}))
        try:
            await interaction.user.send(embed=embed, view=profile_check_view(guild.id))
            note = "내 DM으로 실제 메시지를 보냈어요. 버튼도 눌러볼 수 있어요."
        except discord.HTTPException:
            note = "내 DM이 막혀 있어 여기에만 보여드려요."
        await interaction.followup.send(
            content=f"🧪 **미리보기** — 다른 멤버에게는 보내지 않았어요! (기준 {기준일}일, 대상 {count}명)\n{note}",
            embed=embed, ephemeral=True,
        )
        return

    if not count:
        await interaction.followup.send(f"✅ {기준일}일 기준으로 보낼 대상이 없어요.", ephemeral=True)
        return

    await interaction.followup.send(
        f"📨 {기준일}일 이상 정보를 확인하지 않은 멤버 **{count}명**에게 확인 DM을 보낼까요?\n"
        "보낸 DM은 되돌릴 수 없어요.",
        view=ProfileCheckConfirmView(guild, 기준일), ephemeral=True,
    )


@bot.tree.command(name="정보확인자동설정", description="정보 확인 DM 자동 발송을 켜거나 끄고 기준일을 바꿉니다")
@app_commands.describe(
    사용="자동 발송 켜기/끄기 (비워두면 그대로)",
    기준일="이 일수가 지나면 다시 보내요 (비워두면 그대로)",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def configure_profile_check(
    interaction: discord.Interaction,
    사용: bool = None,
    기준일: app_commands.Range[int, 1, 365] = None,
):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    settings = await asyncio.to_thread(get_guild_settings, guild.id)
    enabled = bool(settings.get("profile_check_auto_enabled")) if 사용 is None else 사용
    days = 기준일 or settings.get("profile_check_days") or PROFILE_CHECK_DEFAULT_DAYS

    # 옵션 없이 실행하면 현재 설정만 보여줌
    if 사용 is not None or 기준일 is not None:
        await asyncio.to_thread(
            db.collection(GUILD_SETTINGS_COLLECTION).document(str(guild.id)).set,
            {
                "profile_check_auto_enabled": enabled,
                "profile_check_days": days,
                "profile_check_report_to": str(interaction.user.id),
                "updated_by": str(interaction.user.id),
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    if not enabled:
        await interaction.followup.send(
            f"⚪ 정보 확인 자동 발송: **꺼짐** (기준 {days}일)\n"
            "보내는 중이었다면 아직 받지 않은 멤버에게는 보내지 않아요.",
            ephemeral=True,
        )
        return

    users = await asyncio.to_thread(list_registered_users)
    count = len(profile_check_targets(guild, users, days, utc_now(), respect_sent=True))
    await interaction.followup.send(
        f"🟢 정보 확인 자동 발송: **켜짐** (기준 {days}일)\n"
        f"매일 오후 {PROFILE_CHECK_AUTO_TIME.hour - 12}시(한국 시간)에 대상자에게 DM을 보내요. "
        f"지금 기준 다음 발송 대상은 **{count}명**이에요.\n"
        "결과와 실패 명단은 이 설정을 마지막으로 바꾼 관리자에게 DM으로 알려드려요.",
        ephemeral=True,
    )


async def report_profile_check_auto(guild, report_to, sent, failed_names, skipped):
    admin = guild.get_member(int(report_to)) if str(report_to or "").isdigit() else None
    if admin is None:
        log.warning("정보 확인 자동 안내 결과를 받을 관리자 없음 — guild=%s", guild.id)
        return
    title = f"📣 **{guild.name}** 정보 확인 자동 안내 결과\n{profile_check_summary(sent, failed_names)}"
    if skipped:
        title += f"\n⏹️ 자동 발송이 꺼져 {skipped}명에게는 보내지 않았어요."
    try:
        await send_split_lines(admin.send, title, [f"• {name}" for name in failed_names])
    except discord.HTTPException:
        log.warning("정보 확인 자동 안내 결과 DM 실패 — admin=%s", admin.id)


async def run_profile_check_auto(guild):
    settings = await asyncio.to_thread(get_guild_settings, guild.id)
    if not settings.get("profile_check_auto_enabled"):
        return
    days = settings.get("profile_check_days") or PROFILE_CHECK_DEFAULT_DAYS

    async def still_enabled():
        try:
            current = await asyncio.to_thread(get_guild_settings, guild.id)
        except Exception:
            log.exception("정보 확인 자동 설정 조회 실패 — guild=%s", guild.id)
            return False
        return bool(current.get("profile_check_auto_enabled"))

    async with dm_broadcast_lock:
        users = await asyncio.to_thread(list_registered_users)
        targets = profile_check_targets(guild, users, days, utc_now(), respect_sent=True)
        if not targets:
            return
        sent, failed_names, skipped = await send_profile_checks(guild, targets, still_enabled)
    await report_profile_check_auto(guild, settings.get("profile_check_report_to"), sent, failed_names, skipped)


# ===== 파티 모집 =====

party_action_lock = asyncio.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# 한국 시간 "YYYY-MM-DD HH:MM" → UTC (최소 10분 이후 ~ max_lead 이내)
def parse_start_time(value, max_lead, too_late_message):
    try:
        local_time = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=PARTY_TIMEZONE)
    except ValueError:
        return None, "❌ 선택한 날짜 또는 시간이 올바르지 않아요. 다시 선택해주세요."

    now_local = datetime.now(PARTY_TIMEZONE)
    if local_time < now_local + PARTY_MIN_LEAD:
        return None, "❌ 시작시간은 현재 시각보다 최소 10분 이후여야 해요."
    if local_time > now_local + max_lead:
        return None, too_late_message
    return local_time.astimezone(timezone.utc), None


def parse_party_time(value):
    return parse_start_time(value, PARTY_MAX_LEAD, "❌ 시작시간은 현재부터 24시간 이내로 정해주세요.")


def parse_event_time(value):
    return parse_start_time(value, EVENT_MAX_LEAD, "❌ 이벤트는 현재부터 14일 이내로 정해주세요.")


def party_max_members(data):
    max_members = data.get("max_members", PARTY_MAX_MEMBERS)
    # 지원 종료된 모집도 기존 정원은 보존하고, party_status에서 마감한다.
    if isinstance(max_members, int) and max_members > 0:
        return max_members
    return PARTY_MAX_MEMBERS


def party_type_name(max_members):
    if max_members not in SUPPORTED_PARTY_SIZES:
        return "지원 종료 파티"
    return "내전 파티 (10명)" if max_members == INHOUSE_PARTY_MAX_MEMBERS else "5인 파티"


def party_preferred_tier_name(data):
    return PARTY_PREFERRED_TIER_LABELS.get(data.get("preferred_tier"), "자유 모집")


def party_status(data, now=None):
    if party_max_members(data) not in SUPPORTED_PARTY_SIZES:
        return "closed"
    now = now or utc_now()
    scheduled_at = as_utc(data.get("scheduled_at"))
    if scheduled_at is None or now >= scheduled_at:
        return "closed"
    if len(data.get("member_ids", [])) >= party_max_members(data):
        return "full"
    return "open"


# 사용자가 입력한 파티·이벤트 이름 등을 표시할 때 마크다운·멘션을 무력화
def escape_display_text(value):
    text = str(value or "").strip()
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


async def get_party_member_names(guild, user_ids):
    names = {}
    for user_id in dict.fromkeys(user_ids):
        member = None
        try:
            member_id = int(user_id)
            member = guild.get_member(member_id)
            if member is None:
                member = await guild.fetch_member(member_id)
        except (TypeError, ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        if member is None:
            names[user_id] = "알 수 없는 사용자"
            continue

        display_name = discord.utils.escape_mentions(member.display_name)
        names[user_id] = discord.utils.escape_markdown(display_name)
    return names


async def send_party_dm(guild, user_id, content):
    try:
        member_id = int(user_id)
        member = guild.get_member(member_id)
        if member is None:
            member = await guild.fetch_member(member_id)
        await member.send(content)
        return True
    except (TypeError, ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.info("파티 알림 DM 전송 실패 — guild=%s user=%s", guild.id, user_id)
        return False


async def try_edit_recruitment_message(message, embed, view, kind, recruitment_id):
    try:
        await message.edit(embed=embed, view=view)
        return True
    except discord.HTTPException:
        log.exception("%s 모집글 표시 갱신 실패 — id=%s", kind, recruitment_id)
        return False


# 모집글과 DB(원본·공개 미러)를 함께 삭제하고 결과 안내 문구를 반환
async def delete_recruitment(message, kind, recruitment_id, lock):
    collection = PARTY_COLLECTION if kind == "party" else EVENT_COLLECTION
    label = "파티" if kind == "party" else "발쫀전"
    try:
        async with lock:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            await asyncio.to_thread(
                delete_recruitment_pair,
                db.collection(collection).document(recruitment_id),
                kind,
                recruitment_id,
            )
        return f"🗑️ {label} 모집글을 삭제했어요."
    except Exception:
        log.exception("%s 삭제 실패 — id=%s", label, recruitment_id)
        return f"❌ {label} 삭제 중 오류가 발생했어요."


class DeleteConfirmView(discord.ui.View):
    """삭제 버튼을 잘못 눌러도 바로 지워지지 않도록 실행자에게만 한 번 더 확인."""

    def __init__(self, owner_id, message, kind, recruitment_id, lock):
        super().__init__(timeout=60)
        self.owner_id = str(owner_id)
        self.message = message
        self.kind = kind
        self.recruitment_id = str(recruitment_id)
        self.lock = lock

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message("❌ 삭제를 요청한 사람만 확인할 수 있어요.", ephemeral=True)
        return False

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="⏳ 삭제하는 중...", view=None)
        result = await delete_recruitment(self.message, self.kind, self.recruitment_id, self.lock)
        await interaction.edit_original_response(content=result)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="삭제를 취소했어요.", view=None)


async def build_party_embed(data, guild):
    status = party_status(data)
    max_members = party_max_members(data)
    status_text = {
        "open": "🟢 모집 중",
        "full": "🟡 정원 마감 · 대기 접수 중",
        "closed": "⚫ 모집 마감",
    }[status]
    if max_members not in SUPPORTED_PARTY_SIZES:
        status_text = "⚫ 지원 종료"
    color = {
        "open": discord.Color.green(),
        "full": discord.Color.gold(),
        "closed": discord.Color.dark_grey(),
    }[status]

    member_ids = [str(uid) for uid in data.get("member_ids", [])]
    waitlist_ids = [
        str(uid) for uid in data.get("waitlist_ids", []) if str(uid) not in member_ids
    ]
    creator_id = str(data.get("creator_id", ""))
    public_waitlist_ids = waitlist_ids[:PARTY_PUBLIC_WAITLIST_LIMIT]
    member_names = await get_party_member_names(
        guild, member_ids + public_waitlist_ids + [creator_id]
    )
    member_lines = []
    for index in range(max_members):
        if index < len(member_ids):
            user_id = member_ids[index]
            owner = " 👑" if user_id == creator_id else ""
            member_lines.append(f"{index + 1}. {member_names[user_id]}{owner}")
        else:
            slot_text = "모집 마감" if status == "closed" else "모집 중"
            member_lines.append(f"{index + 1}. *{slot_text}*")

    scheduled_at = as_utc(data["scheduled_at"])
    delete_at = as_utc(data.get("delete_at")) or scheduled_at + PARTY_DELETE_DELAY
    scheduled_timestamp = int(scheduled_at.timestamp())
    delete_timestamp = int(delete_at.timestamp())

    embed = discord.Embed(
        title=f"🎮 {party_type_name(max_members)} · {escape_display_text(data['name'])}",
        description="\n".join(member_lines),
        color=color,
    )
    embed.add_field(name="상태", value=status_text, inline=True)
    embed.add_field(name="모집 유형", value=party_type_name(max_members), inline=True)
    embed.add_field(
        name="참가 확정",
        value=f"**{len(member_ids)} / {max_members}**",
        inline=True,
    )
    if max_members == PARTY_MAX_MEMBERS:
        embed.add_field(
            name="희망 티어대",
            value=f"**{party_preferred_tier_name(data)}**\n*참가 제한이 아닌 희망 조건입니다.*",
            inline=False,
        )
    if waitlist_ids:
        waitlist_lines = [
            f"{index}. {member_names[user_id]}"
            for index, user_id in enumerate(public_waitlist_ids, 1)
        ]
        hidden_count = len(waitlist_ids) - len(public_waitlist_ids)
        if hidden_count:
            waitlist_lines.append(f"… 외 {hidden_count}명")
        embed.add_field(
            name=f"🕒 대기 명단 ({len(waitlist_ids)}명)",
            value="\n".join(waitlist_lines),
            inline=False,
        )
    embed.add_field(name="파티장", value=member_names[creator_id], inline=True)
    embed.add_field(
        name="파티 시작",
        value=f"<t:{scheduled_timestamp}:F>\n<t:{scheduled_timestamp}:R>",
        inline=False,
    )
    embed.add_field(
        name="자동 삭제",
        value=f"<t:{delete_timestamp}:F>\n파티 시작 10시간 후 자동으로 삭제됩니다.",
        inline=False,
    )
    embed.set_footer(
        text="다른 파티에도 동시에 참가할 수 있습니다. 빈자리가 생기면 대기 순서대로 참가가 확정됩니다."
    )
    return embed


class PartyView(discord.ui.View):
    def __init__(self, data=None):
        super().__init__(timeout=None)
        if data is not None:
            status = party_status(data)
            self.join_party.disabled = status == "closed"
            self.join_party.label = "대기 신청" if status == "full" else "참가하기"
            self.leave_party.disabled = status == "closed"
            self.change_party_time.disabled = status == "closed"

    @discord.ui.button(
        label="참가하기",
        style=discord.ButtonStyle.success,
        custom_id="party:join",
    )
    async def join_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return
        if await block_if_unverified(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        party_id = str(interaction.message.id)
        user_id = str(interaction.user.id)

        try:
            async with party_action_lock:
                party_ref = db.collection(PARTY_COLLECTION).document(party_id)
                mirror_ref = get_toolkit_recruitment_ref("party", party_id)
                transaction = db.transaction()
                result, data, waitlist_position = await asyncio.to_thread(
                    join_party_transaction,
                    transaction,
                    party_ref,
                    mirror_ref,
                    user_id,
                    utc_now(),
                )

            messages = {
                "missing": "❌ 이미 삭제된 파티예요.",
                "closed": "❌ 모집이 마감된 파티예요.",
                "already_member": "ℹ️ 이미 참가가 확정된 파티예요.",
                "already_waitlist": f"ℹ️ 이미 이 파티의 대기 명단 {waitlist_position}번에 있어요.",
                "joined": "✅ 파티 참가가 확정됐어요!",
                "waitlisted": f"🕒 대기 명단 {waitlist_position}번으로 등록됐어요. 빈자리가 생기면 자동으로 참가가 확정됩니다.",
            }
            refresh_warning = ""
            if result in {"joined", "waitlisted"}:
                embed = await build_party_embed(data, interaction.guild)
                refreshed = await try_edit_recruitment_message(
                    interaction.message, embed, PartyView(data), "파티", party_id
                )
                if not refreshed:
                    refresh_warning = "\n⚠️ 참가는 저장됐지만 모집글 표시 갱신이 지연되고 있어요."
            await interaction.followup.send(messages[result] + refresh_warning, ephemeral=True)
        except Exception:
            log.exception("파티 참가 처리 실패 — party=%s user=%s", party_id, user_id)
            await interaction.followup.send("❌ 파티 참가 처리 중 오류가 발생했어요.", ephemeral=True)

    @discord.ui.button(
        label="참가 취소",
        style=discord.ButtonStyle.secondary,
        custom_id="party:leave",
    )
    async def leave_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        party_id = str(interaction.message.id)
        user_id = str(interaction.user.id)

        try:
            async with party_action_lock:
                party_ref = db.collection(PARTY_COLLECTION).document(party_id)
                mirror_ref = get_toolkit_recruitment_ref("party", party_id)
                transaction = db.transaction()
                result, data, promoted_id = await asyncio.to_thread(
                    leave_party_transaction,
                    transaction,
                    party_ref,
                    mirror_ref,
                    user_id,
                    utc_now(),
                )

            messages = {
                "missing": "❌ 이미 삭제된 파티예요.",
                "closed": "❌ 이미 모집이 마감됐어요.",
                "creator": "❌ 파티장은 참가를 취소할 수 없어요. `파티 삭제`를 이용해주세요.",
                "not_member": "ℹ️ 이 파티에 참가하고 있지 않아요.",
                "left": "✅ 참가 확정을 취소했어요.",
                "left_promoted": "✅ 참가 확정을 취소했어요. 대기 1번의 참가가 자동으로 확정됐습니다.",
                "waitlist_left": "✅ 대기 신청을 취소했어요.",
            }
            refresh_warning = ""
            if result in {"left", "left_promoted", "waitlist_left"}:
                embed = await build_party_embed(data, interaction.guild)
                refreshed = await try_edit_recruitment_message(
                    interaction.message, embed, PartyView(data), "파티", party_id
                )
                if not refreshed:
                    refresh_warning = "\n⚠️ 취소는 저장됐지만 모집글 표시 갱신이 지연되고 있어요."
            if result == "left_promoted" and promoted_id:
                tier_text = (
                    f"• 희망 티어대: **{party_preferred_tier_name(data)}**\n"
                    if party_max_members(data) == PARTY_MAX_MEMBERS
                    else ""
                )
                promoted = await send_party_dm(
                    interaction.guild,
                    promoted_id,
                    (
                        "🎉 **파티 참가가 확정됐어요!**\n"
                        f"대기 중이던 **{discord.utils.escape_markdown(discord.utils.escape_mentions(data['name']))}** 파티에 빈자리가 생겨 참가 확정으로 변경됐습니다.\n"
                        f"• 모집 유형: **{party_type_name(party_max_members(data))}**\n"
                        f"{tier_text}"
                        f"• 시작: <t:{int(as_utc(data['scheduled_at']).timestamp())}:F>\n\n"
                        "참여가 어렵다면 모집글에서 `참가 취소`를 눌러주세요.\n"
                        f"{interaction.message.jump_url}"
                    ),
                )
                if not promoted:
                    try:
                        await interaction.channel.send(
                            f"<@{promoted_id}> 대기 명단에서 **참가 확정**으로 변경됐어요! {interaction.message.jump_url}",
                            allowed_mentions=discord.AllowedMentions(users=True),
                        )
                    except discord.HTTPException:
                        log.exception(
                            "파티 승격 채널 알림 실패 — party=%s user=%s",
                            party_id,
                            promoted_id,
                        )
            await interaction.followup.send(messages[result] + refresh_warning, ephemeral=True)
        except Exception:
            log.exception("파티 참가 취소 실패 — party=%s user=%s", party_id, user_id)
            await interaction.followup.send("❌ 참가 취소 처리 중 오류가 발생했어요.", ephemeral=True)

    @discord.ui.button(
        label="시간 변경",
        style=discord.ButtonStyle.primary,
        custom_id="party:change_time",
    )
    async def change_party_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        party_id = str(interaction.message.id)

        try:
            data = await asyncio.to_thread(get_party_data, party_id)
            if data is None:
                await interaction.followup.send("❌ 이미 삭제된 파티예요.", ephemeral=True)
                return

            is_creator = str(interaction.user.id) == str(data.get("creator_id"))
            is_admin = interaction.user.guild_permissions.administrator
            if not is_creator and not is_admin:
                await interaction.followup.send(
                    "❌ 파티장 또는 관리자만 시작 시간을 변경할 수 있어요.",
                    ephemeral=True,
                )
                return
            if party_status(data) == "closed":
                await interaction.followup.send(
                    "❌ 이미 시작한 파티의 시간은 변경할 수 없어요.", ephemeral=True
                )
                return

            view = PartyTimeEditView(
                interaction.user.id,
                party_id,
                interaction.message,
                data,
            )
            await interaction.followup.send(
                view.render_content(),
                view=view,
                ephemeral=True,
            )
        except Exception:
            log.exception("파티 시간 변경 화면 열기 실패 — party=%s", party_id)
            await interaction.followup.send(
                "❌ 시간 변경 화면을 여는 중 오류가 발생했어요.", ephemeral=True
            )

    @discord.ui.button(
        label="파티 삭제",
        style=discord.ButtonStyle.danger,
        custom_id="party:delete",
    )
    async def delete_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        party_id = str(interaction.message.id)

        try:
            data = await asyncio.to_thread(get_party_data, party_id)
            if data is None:
                await interaction.followup.send("❌ 이미 삭제된 파티예요.", ephemeral=True)
                return

            is_creator = str(interaction.user.id) == str(data.get("creator_id"))
            is_admin = interaction.user.guild_permissions.administrator
            if not is_creator and not is_admin:
                await interaction.followup.send(
                    "❌ 파티장 또는 관리자만 이 파티를 삭제할 수 있어요.", ephemeral=True
                )
                return

            await interaction.followup.send(
                f"🗑️ **{escape_display_text(data.get('name'))}** 파티 모집글을 삭제할까요?\n"
                "참가자·대기 명단도 함께 삭제되며 되돌릴 수 없어요.",
                view=DeleteConfirmView(
                    interaction.user.id, interaction.message, "party", party_id, party_action_lock
                ),
                ephemeral=True,
            )
        except Exception:
            log.exception("파티 삭제 확인 화면 열기 실패 — party=%s", party_id)
            await interaction.followup.send("❌ 파티 삭제 중 오류가 발생했어요.", ephemeral=True)


def get_party_data(party_id):
    snapshot = db.collection(PARTY_COLLECTION).document(str(party_id)).get()
    return snapshot.to_dict() if snapshot.exists else None


def list_parties():
    return [(doc.id, doc.to_dict()) for doc in db.collection(PARTY_COLLECTION).stream()]


@transactional
def join_party_transaction(transaction, party_ref, mirror_ref, user_id, now):
    snapshot = party_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None, None

    data = snapshot.to_dict()
    if party_status(data, now) == "closed":
        return "closed", data, None

    member_ids = [str(uid) for uid in data.get("member_ids", [])]
    waitlist_ids = [
        str(uid) for uid in data.get("waitlist_ids", []) if str(uid) not in member_ids
    ]
    if user_id in member_ids:
        return "already_member", data, None
    if user_id in waitlist_ids:
        return "already_waitlist", data, waitlist_ids.index(user_id) + 1

    max_members = party_max_members(data)
    if len(member_ids) >= max_members:
        waitlist_ids.append(user_id)
        result = "waitlisted"
        waitlist_position = len(waitlist_ids)
    else:
        member_ids.append(user_id)
        result = "joined"
        waitlist_position = None

    data["member_ids"] = member_ids
    data["waitlist_ids"] = waitlist_ids
    data["status"] = "full" if len(member_ids) >= max_members else "open"
    data["updated_at"] = now
    transaction.update(
        party_ref,
        {
            "member_ids": member_ids,
            "waitlist_ids": waitlist_ids,
            "status": data["status"],
            "updated_at": now,
        },
    )
    transaction.set(
        mirror_ref,
        build_toolkit_recruitment_data("party", party_ref.id, data, now),
    )
    return result, data, waitlist_position


@transactional
def leave_party_transaction(transaction, party_ref, mirror_ref, user_id, now):
    snapshot = party_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None, None

    data = snapshot.to_dict()
    if party_status(data, now) == "closed":
        return "closed", data, None
    if str(data.get("creator_id")) == user_id:
        return "creator", data, None

    member_ids = [str(uid) for uid in data.get("member_ids", [])]
    waitlist_ids = [
        str(uid) for uid in data.get("waitlist_ids", []) if str(uid) not in member_ids
    ]
    promoted_id = None

    if user_id in waitlist_ids:
        waitlist_ids.remove(user_id)
        result = "waitlist_left"
    elif user_id in member_ids:
        member_ids.remove(user_id)
        if waitlist_ids:
            promoted_id = waitlist_ids.pop(0)
            member_ids.append(promoted_id)
            result = "left_promoted"
        else:
            result = "left"
    else:
        return "not_member", data, None

    data["member_ids"] = member_ids
    data["waitlist_ids"] = waitlist_ids
    data["status"] = "full" if len(member_ids) >= party_max_members(data) else "open"
    data["updated_at"] = now
    transaction.update(
        party_ref,
        {
            "member_ids": member_ids,
            "waitlist_ids": waitlist_ids,
            "status": data["status"],
            "updated_at": now,
        },
    )
    transaction.set(
        mirror_ref,
        build_toolkit_recruitment_data("party", party_ref.id, data, now),
    )
    return result, data, promoted_id


@transactional
def update_party_time_transaction(transaction, party_ref, mirror_ref, scheduled_at, now):
    snapshot = party_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None

    data = snapshot.to_dict()
    if party_status(data, now) == "closed":
        return "closed", data

    delete_at = scheduled_at + PARTY_DELETE_DELAY
    member_count = len(data.get("member_ids", []))
    status = "full" if member_count >= party_max_members(data) else "open"
    data.update(
        {
            "scheduled_at": scheduled_at,
            "delete_at": delete_at,
            "status": status,
            "updated_at": now,
        }
    )
    transaction.update(
        party_ref,
        {
            "scheduled_at": scheduled_at,
            "delete_at": delete_at,
            "status": status,
            "updated_at": now,
        },
    )
    transaction.set(
        mirror_ref,
        build_toolkit_recruitment_data("party", party_ref.id, data, now),
    )
    return "updated", data


def get_party_channel_id(guild_id):
    snapshot = db.collection(GUILD_SETTINGS_COLLECTION).document(str(guild_id)).get()
    if not snapshot.exists:
        return None
    return snapshot.to_dict().get("party_channel_id")


# 모집글이 올라간 적 있는 채널 — 메시지 삭제 이벤트에서 이 채널만 DB를 확인 (실행 중 메모리에만 유지)
recruitment_channel_ids = set()
recruitment_channel_loaded_guilds = set()


def remember_recruitment_channel(channel_id):
    if channel_id:
        recruitment_channel_ids.add(str(channel_id))


@bot.tree.command(name="파티채널설정", description="(어드민) 파티·발쫀전 모집 명령어를 사용할 채널을 지정합니다")
@app_commands.describe(채널="파티·발쫀전 모집 전용 채널")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def configure_party_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    if await block_if_not_in_guild(interaction):
        return

    permissions = 채널.permissions_for(interaction.guild.me)
    required = (
        permissions.view_channel
        and permissions.send_messages
        and permissions.embed_links
        and permissions.read_message_history
    )
    if not required:
        await interaction.response.send_message(
            "❌ 봇에게 선택한 채널의 `채널 보기`, `메시지 보내기`, `링크 첨부`, "
            "`메시지 기록 보기` 권한을 모두 주세요.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    await asyncio.to_thread(
        db.collection(GUILD_SETTINGS_COLLECTION).document(str(interaction.guild.id)).set,
        {
            "party_channel_id": str(채널.id),
            "updated_by": str(interaction.user.id),
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    remember_recruitment_channel(채널.id)
    await interaction.followup.send(
        f"✅ 모집 채널을 {채널.mention}(으)로 설정했어요.\n"
        "이제 `/파티생성`과 `/이벤트생성`은 이 채널에서만 사용할 수 있습니다.",
        ephemeral=True,
    )


def make_party_date_options():
    today = datetime.now(PARTY_TIMEZONE).date()
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    options = []
    for offset, prefix in ((0, "오늘"), (1, "내일")):
        target = today + timedelta(days=offset)
        options.append(
            discord.SelectOption(
                label=f"{prefix} · {target.month}월 {target.day}일 ({weekdays[target.weekday()]})",
                value=target.isoformat(),
            )
        )
    return options


def make_event_date_options():
    today = datetime.now(PARTY_TIMEZONE).date()
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    options = []
    for offset in range(14):
        target = today + timedelta(days=offset)
        if offset == 0:
            prefix = "오늘 · "
        elif offset == 1:
            prefix = "내일 · "
        else:
            prefix = ""
        options.append(
            discord.SelectOption(
                label=f"{prefix}{target.month}월 {target.day}일 ({weekdays[target.weekday()]})",
                value=target.isoformat(),
            )
        )
    return options


def make_party_hour_options():
    options = []
    for hour in range(24):
        period = "오전" if hour < 12 else "오후"
        display_hour = hour % 12 or 12
        options.append(
            discord.SelectOption(
                label=f"{period} {display_hour}시",
                value=f"{hour:02d}",
            )
        )
    return options


def make_party_minute_options(extra_minute=None):
    options = [
        discord.SelectOption(label="정각 (00분)", value="00"),
        discord.SelectOption(label="30분", value="30"),
    ]
    if extra_minute not in {None, "00", "30"}:
        options.append(discord.SelectOption(label=f"{extra_minute}분 (현재)", value=extra_minute))
    return options


class PartyCreationSelect(discord.ui.Select):
    def __init__(self, parent_view, kind, placeholder, options, row):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1,
            row=row,
        )
        self.parent_view = parent_view
        self.kind = kind
        current_value = getattr(parent_view, kind, None)
        for option in self.options:
            option.default = option.value == current_value

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        setattr(self.parent_view, self.kind, value)
        for option in self.options:
            option.default = option.value == value
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class PartyCreationView(discord.ui.View):
    def __init__(self, owner_id, name, channel, max_members):
        super().__init__(timeout=300)
        self.owner_id = str(owner_id)
        self.name = name
        self.channel = channel
        self.max_members = max_members
        self.preferred_tier = "free"
        self.selected_date = None
        self.selected_hour = None
        self.selected_minute = None
        if self.max_members == PARTY_MAX_MEMBERS:
            self.add_item(
                PartyCreationSelect(
                    self,
                    "preferred_tier",
                    "희망 티어대 선택",
                    [
                        discord.SelectOption(label=label, value=value)
                        for value, label in PARTY_PREFERRED_TIER_LABELS.items()
                    ],
                    row=0,
                )
            )
        self.add_item(
            PartyCreationSelect(
                self,
                "selected_date",
                "날짜 선택",
                make_party_date_options(),
                row=1,
            )
        )
        self.add_item(
            PartyCreationSelect(
                self,
                "selected_hour",
                "시간 선택",
                make_party_hour_options(),
                row=2,
            )
        )
        self.add_item(
            PartyCreationSelect(
                self,
                "selected_minute",
                "분 선택",
                make_party_minute_options(),
                row=3,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "❌ 이 파티 생성 화면은 명령어를 실행한 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    def render_content(self):
        date_text = "선택 전"
        if self.selected_date:
            target = datetime.strptime(self.selected_date, "%Y-%m-%d")
            date_text = f"{target.month}월 {target.day}일"

        time_text = "선택 전"
        if self.selected_hour is not None and self.selected_minute is not None:
            time_text = f"{self.selected_hour}:{self.selected_minute}"

        tier_text = ""
        tier_note = ""
        if self.max_members == PARTY_MAX_MEMBERS:
            tier_text = f"• 희망 티어대: **{PARTY_PREFERRED_TIER_LABELS[self.preferred_tier]}**\n"
            tier_note = "희망 티어대는 참가 제한이 아닌 모집 안내로만 표시됩니다.\n"

        return (
            f"🎮 **{party_type_name(self.max_members)} · {escape_display_text(self.name)}**의 시작 시간을 선택해주세요.\n"
            f"{tier_text}"
            f"• 날짜: **{date_text}**\n"
            f"• 시간: **{time_text}**\n\n"
            f"{tier_note}"
            "날짜·시간을 모두 선택한 뒤 `파티 생성`을 눌러주세요. "
            "모집글은 파티 시작 10시간 후 자동으로 삭제됩니다."
        )

    @discord.ui.button(label="파티 생성", style=discord.ButtonStyle.success, row=4)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not all((self.selected_date, self.selected_hour, self.selected_minute)):
            await interaction.response.send_message(
                "❌ 날짜와 시간을 모두 선택해주세요.", ephemeral=True
            )
            return

        scheduled_at, error = parse_party_time(
            f"{self.selected_date} {self.selected_hour}:{self.selected_minute}"
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.defer()
        message = await publish_party(
            interaction,
            self.channel,
            self.name,
            scheduled_at,
            self.max_members,
            self.preferred_tier,
        )
        if message is None:
            return

        self.stop()
        await interaction.edit_original_response(
            content=(
                f"✅ 파티 모집글을 만들었어요! {message.jump_url}\n"
                "모집은 파티 시작 시간에 마감되고, 파티 시작 10시간 후 자동 삭제됩니다."
            ),
            view=None,
        )

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=4)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="파티 생성을 취소했어요.", view=None)


class PartyTimeEditView(discord.ui.View):
    def __init__(self, owner_id, party_id, party_message, data):
        super().__init__(timeout=300)
        self.owner_id = str(owner_id)
        self.party_id = str(party_id)
        self.party_message = party_message
        self.name = data["name"]

        current_time = as_utc(data["scheduled_at"]).astimezone(PARTY_TIMEZONE)
        self.current_timestamp = int(current_time.timestamp())
        self.selected_date = current_time.date().isoformat()
        self.selected_hour = f"{current_time.hour:02d}"
        self.selected_minute = f"{current_time.minute:02d}"

        self.add_item(
            PartyCreationSelect(
                self,
                "selected_date",
                "날짜 선택",
                make_party_date_options(),
                row=0,
            )
        )
        self.add_item(
            PartyCreationSelect(
                self,
                "selected_hour",
                "시간 선택",
                make_party_hour_options(),
                row=1,
            )
        )
        self.add_item(
            PartyCreationSelect(
                self,
                "selected_minute",
                "분 선택",
                make_party_minute_options(self.selected_minute),
                row=2,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "❌ 이 시간 변경 화면을 연 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    def render_content(self):
        target = datetime.strptime(self.selected_date, "%Y-%m-%d")
        date_text = f"{target.month}월 {target.day}일"
        return (
            f"⏰ **{escape_display_text(self.name)}** 파티의 시작 시간을 변경합니다.\n"
            f"• 현재: <t:{self.current_timestamp}:F>\n"
            f"• 변경: **{date_text} {self.selected_hour}:{self.selected_minute}**\n\n"
            "날짜·시간을 확인한 뒤 `시간 변경`을 눌러주세요. "
            "자동 삭제 시각도 새 시작 시간의 10시간 후로 바뀝니다."
        )

    @discord.ui.button(label="시간 변경", style=discord.ButtonStyle.success, row=3)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        scheduled_at, error = parse_party_time(
            f"{self.selected_date} {self.selected_hour}:{self.selected_minute}"
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        new_timestamp = int(scheduled_at.timestamp())
        if new_timestamp == self.current_timestamp:
            self.stop()
            await interaction.response.edit_message(
                content="ℹ️ 기존 시작 시간과 같아서 변경하지 않았어요.", view=None
            )
            return

        await interaction.response.defer()
        party_ref = db.collection(PARTY_COLLECTION).document(self.party_id)
        mirror_ref = get_toolkit_recruitment_ref("party", self.party_id)

        try:
            async with party_action_lock:
                transaction = db.transaction()
                result, data = await asyncio.to_thread(
                    update_party_time_transaction,
                    transaction,
                    party_ref,
                    mirror_ref,
                    scheduled_at,
                    utc_now(),
                )

            if result == "missing":
                self.stop()
                await interaction.edit_original_response(
                    content="❌ 이미 삭제된 파티예요.", view=None
                )
                return
            if result == "closed":
                self.stop()
                await interaction.edit_original_response(
                    content="❌ 선택 중 파티가 시작되어 시간을 변경할 수 없어요.",
                    view=None,
                )
                return

            embed = await build_party_embed(data, self.party_message.guild)
            refresh_warning = ""
            try:
                await self.party_message.edit(embed=embed, view=PartyView(data))
            except discord.NotFound:
                await asyncio.to_thread(
                    delete_recruitment_pair, party_ref, "party", self.party_id
                )
                self.stop()
                await interaction.edit_original_response(
                    content="❌ 파티 모집글이 삭제되어 변경을 저장하지 않았어요.",
                    view=None,
                )
                return
            except discord.HTTPException:
                log.exception("파티 시간 변경 후 모집글 갱신 실패 — party=%s", self.party_id)
                refresh_warning = "\n⚠️ 시간은 저장됐지만 모집글 표시 갱신이 지연되고 있어요."

            notification_ids = [
                str(user_id)
                for user_id in (
                    list(data.get("member_ids") or [])
                    + list(data.get("waitlist_ids") or [])
                )
                if str(user_id) != str(interaction.user.id)
            ]
            dm_content = (
                "⏰ **파티 시작 시간이 변경됐어요**\n"
                f"**{discord.utils.escape_markdown(discord.utils.escape_mentions(data['name']))}** 파티의 일정이 변경되었습니다.\n"
                f"• 이전: <t:{self.current_timestamp}:F>\n"
                f"• 변경: <t:{new_timestamp}:F>\n\n"
                "참여가 어렵다면 모집글에서 `참가 취소`를 눌러주세요.\n"
                f"{self.party_message.jump_url}"
            )
            dm_results = await asyncio.gather(
                *(
                    send_party_dm(self.party_message.guild, user_id, dm_content)
                    for user_id in dict.fromkeys(notification_ids)
                )
            )
            dm_success_count = sum(dm_results)
            dm_failure_count = len(dm_results) - dm_success_count
            dm_summary = f"\n알림 DM: {dm_success_count}명 전송"
            if dm_failure_count:
                dm_summary += f" · {dm_failure_count}명 실패(DM 차단 등)"

            self.stop()
            await interaction.edit_original_response(
                content=(
                    f"✅ 파티 시작 시간을 <t:{new_timestamp}:F>로 변경했어요.\n"
                    "자동 삭제 시각도 함께 갱신했습니다."
                    f"{dm_summary}"
                    f"{refresh_warning}"
                ),
                view=None,
            )
        except Exception:
            log.exception("파티 시간 변경 실패 — party=%s", self.party_id)
            self.stop()
            await interaction.edit_original_response(
                content="❌ 파티 시간 변경 중 오류가 발생했어요.", view=None
            )

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=3)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="시간 변경을 취소했어요.", view=None)


class PartyTypeSelectionView(discord.ui.View):
    def __init__(self, owner_id, name, channel):
        super().__init__(timeout=300)
        self.owner_id = str(owner_id)
        self.name = name
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "❌ 이 파티 생성 화면은 명령어를 실행한 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    async def select_type(self, interaction, max_members):
        self.stop()
        view = PartyCreationView(self.owner_id, self.name, self.channel, max_members)
        await interaction.response.edit_message(
            content=view.render_content(),
            view=view,
        )

    @discord.ui.button(label="5인 파티", style=discord.ButtonStyle.primary)
    async def five_member_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_type(interaction, PARTY_MAX_MEMBERS)

    @discord.ui.button(label="내전 파티 (10명)", style=discord.ButtonStyle.danger)
    async def inhouse_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_type(interaction, INHOUSE_PARTY_MAX_MEMBERS)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="파티 생성을 취소했어요.", view=None)


async def publish_party(
    interaction, channel, name, scheduled_at, max_members, preferred_tier="free"
):
    if max_members not in SUPPORTED_PARTY_SIZES:
        await interaction.followup.send("❌ 지원하지 않는 파티 유형이에요.", ephemeral=True)
        return None
    creator_id = str(interaction.user.id)

    try:
        async with party_action_lock:
            now = utc_now()
            data = {
                "name": name,
                "creator_id": creator_id,
                "member_ids": [creator_id],
                "waitlist_ids": [],
                "guild_id": str(interaction.guild.id),
                "channel_id": str(channel.id),
                "max_members": max_members,
                "preferred_tier": preferred_tier,
                "scheduled_at": scheduled_at,
                "delete_at": scheduled_at + PARTY_DELETE_DELAY,
                "created_at": now,
                "updated_at": now,
                "status": "open",
            }

            embed = await build_party_embed(data, interaction.guild)
            message = await channel.send(embed=embed)
            remember_recruitment_channel(channel.id)
            data["message_id"] = str(message.id)
            party_ref = db.collection(PARTY_COLLECTION).document(str(message.id))
            try:
                await asyncio.to_thread(
                    set_recruitment_pair,
                    party_ref,
                    "party",
                    str(message.id),
                    data,
                )
                await message.edit(embed=embed, view=PartyView(data))
            except Exception:
                await asyncio.to_thread(
                    delete_recruitment_pair,
                    party_ref,
                    "party",
                    str(message.id),
                )
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                raise
        return message
    except Exception:
        log.exception("파티 생성 실패 — creator=%s", creator_id)
        await interaction.followup.send("❌ 파티 생성 중 오류가 발생했어요.", ephemeral=True)
        return None


@bot.tree.command(name="파티생성", description="5인큐·일반 내전(10명) 모집글을 생성합니다")
@app_commands.describe(
    파티이름="파티 이름 (최대 30자)",
)
async def create_party(
    interaction: discord.Interaction,
    파티이름: app_commands.Range[str, 1, 30],
):
    if await block_if_not_in_guild(interaction):
        return
    if await block_if_unverified(interaction):
        return

    name = 파티이름.strip()
    if not name:
        await interaction.response.send_message("❌ 파티 이름을 입력해주세요.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    channel_id = await asyncio.to_thread(get_party_channel_id, interaction.guild.id)
    if channel_id is None:
        await interaction.followup.send(
            "❌ 파티 모집 채널이 아직 설정되지 않았어요. "
            "관리자가 `/파티채널설정`을 먼저 실행해주세요.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send(
            "❌ 설정된 파티 모집 채널을 찾을 수 없어요. "
            "관리자가 `/파티채널설정`으로 다시 지정해주세요.",
            ephemeral=True,
        )
        return

    if interaction.channel_id != channel.id:
        await interaction.followup.send(
            f"❌ `/파티생성`은 지정된 파티 모집 채널 {channel.mention}에서만 사용할 수 있어요.",
            ephemeral=True,
        )
        return

    permissions = channel.permissions_for(interaction.guild.me)
    if not permissions.send_messages or not permissions.embed_links:
        await interaction.followup.send(
            f"❌ 봇에게 {channel.mention} 채널의 메시지 전송 및 링크 첨부 권한을 주세요.",
            ephemeral=True,
        )
        return

    view = PartyTypeSelectionView(interaction.user.id, name, channel)
    await interaction.followup.send(
        f"🎮 **{escape_display_text(name)}** 파티의 모집 유형을 선택해주세요.",
        view=view,
        ephemeral=True,
    )


# ===== 발쫀전 이벤트 모집 =====

event_action_lock = asyncio.Lock()


def event_tier_choices(mode):
    if mode == "valorant":
        return {tier: tier for tier in MAJOR_TIERS if tier != "언랭"}
    if mode == "internal":
        return {
            tier: INTERNAL_TIER_LABELS[tier]
            for tier in sorted(INTERNAL_TIER_SCORES, key=INTERNAL_TIER_SCORES.get)
            if not tier.endswith("?")
        }
    return {}


def event_tier_score(mode, tier):
    if not isinstance(tier, str):
        return None
    if mode == "internal":
        return INTERNAL_TIER_SCORES.get(tier)
    if mode == "valorant":
        normalized = "".join(tier.split())
        valid_tiers = {"".join(value.split()) for value in TIERS + MAJOR_TIERS}
        if normalized not in valid_tiers or normalized == "언랭":
            return None
        major = normalized.rstrip("123")
        return MAJOR_TIERS.index(major)
    return None


def validate_event_tier_restriction(restriction):
    # 기존 이벤트에는 이 필드가 없으므로 자유 참가로 유지합니다.
    if restriction is None:
        return None
    if not isinstance(restriction, dict) or restriction.get("mode") not in EVENT_TIER_MODES:
        return "❌ 참가 티어 조건이 올바르지 않아요. 운영진에게 문의해주세요."
    mode = restriction["mode"]
    if mode == "none":
        return None
    choices = event_tier_choices(mode)
    minimum, maximum = restriction.get("min"), restriction.get("max")
    if (
        not isinstance(minimum, str) or minimum not in choices
        or not isinstance(maximum, str) or maximum not in choices
    ):
        return "❌ 참가 가능한 최저·최고 티어를 모두 선택해주세요."
    if event_tier_score(mode, minimum) > event_tier_score(mode, maximum):
        return "❌ 최저 티어가 최고 티어보다 높아요. 참가 범위를 다시 선택해주세요."
    return None


def event_tier_restriction_text(restriction):
    if validate_event_tier_restriction(restriction):
        return "⚠️ 참가 티어 조건 설정 오류 · 운영진 확인 필요"
    if restriction is None or restriction["mode"] == "none":
        return EVENT_TIER_MODES["none"]
    mode = restriction["mode"]
    choices = event_tier_choices(mode)
    text = f"{EVENT_TIER_MODES[mode]} **{choices[restriction['min']]} ~ {choices[restriction['max']]}**"
    if mode == "valorant":
        return text + "\n봇에 등록된 최대 티어 기준 · 세부 등급 1~3 모두 포함\n언랭·미등록은 티어 등록 후 참가할 수 있어요."
    return text + "\n최저·최고 등급 포함 · ?는 해당 티어의 하와 동일하게 판정\n미분류·미등록은 내부 티어 등록 후 참가할 수 있어요."


def event_tier_join_error(restriction, user_data):
    if validate_event_tier_restriction(restriction):
        return "❌ 이벤트의 참가 티어 조건이 올바르지 않아요. 운영진에게 문의해주세요."
    if restriction is None or restriction["mode"] == "none":
        return None
    mode = restriction["mode"]
    field = "internal_tier" if mode == "internal" else "tier"
    tier = (user_data or {}).get(field)
    score = event_tier_score(mode, tier)
    if score is None:
        if mode == "internal":
            return (
                "❌ 내부 티어가 미분류이거나 등록되지 않아 참가할 수 없어요.\n"
                "운영진에게 `/내부티어설정`을 요청한 뒤 다시 참가해주세요."
            )
        return (
            "❌ 발로란트 티어가 언랭이거나 등록되지 않아 참가할 수 없어요.\n"
            "`/기본설정` 또는 `/역할설정`으로 최대 티어를 등록한 뒤 다시 참가해주세요."
        )
    if not event_tier_score(mode, restriction["min"]) <= score <= event_tier_score(mode, restriction["max"]):
        tier_label = INTERNAL_TIER_LABELS[tier] if mode == "internal" else tier
        return (
            "❌ 이 이벤트의 참가 티어 범위에 해당하지 않아요.\n"
            f"{event_tier_restriction_text(restriction)}\n"
            f"내 등록 티어: **{escape_display_text(tier_label)}**"
        )
    return None


def event_status(data, now=None):
    now = now or utc_now()
    scheduled_at = as_utc(data.get("scheduled_at"))
    if data.get("status") == "closed" or scheduled_at is None or now >= scheduled_at:
        return "closed"
    return "open"


def get_toolkit_recruitment_ref(kind, source_id):
    document_id = f"{kind}_{source_id}"
    return db.collection(TOOLKIT_RECRUITMENT_COLLECTION).document(document_id)


def build_toolkit_recruitment_data(kind, source_id, data, now=None):
    member_ids = [str(member_id) for member_id in data.get("member_ids", [])]
    if kind == "party":
        status = party_status(data, now)
        max_members = party_max_members(data)
        waitlist_ids = [str(member_id) for member_id in data.get("waitlist_ids", [])]
    elif kind == "event":
        status = event_status(data, now)
        max_members = None
        # 이벤트는 원본에 신청 순서만 저장하고, 미러에는 확정·대기로 나눠 웹 팀 편성에 전달
        member_ids, waitlist_ids = split_event_members(member_ids)
    else:
        raise ValueError(f"지원하지 않는 모집 유형입니다: {kind}")

    return {
        "kind": kind,
        "source_id": str(source_id),
        "guild_id": str(data.get("guild_id", "")),
        "name": str(data.get("name", "")),
        "scheduled_at": as_utc(data.get("scheduled_at")),
        "status": status,
        "max_members": max_members,
        "member_ids": member_ids,
        "waitlist_ids": waitlist_ids,
        "preferred_tier": data.get("preferred_tier", "free") if kind == "party" else None,
        "tier_restriction": data.get("tier_restriction") if kind == "event" else None,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def set_recruitment_pair(source_ref, kind, source_id, data):
    batch = db.batch()
    batch.set(source_ref, data)
    batch.set(
        get_toolkit_recruitment_ref(kind, source_id),
        build_toolkit_recruitment_data(kind, source_id, data),
    )
    batch.commit()


def update_recruitment_pair(source_ref, updates, kind, source_id, data):
    batch = db.batch()
    batch.update(source_ref, updates)
    batch.set(
        get_toolkit_recruitment_ref(kind, source_id),
        build_toolkit_recruitment_data(kind, source_id, data),
    )
    batch.commit()


def delete_recruitment_pair(source_ref, kind, source_id):
    batch = db.batch()
    batch.delete(source_ref)
    batch.delete(get_toolkit_recruitment_ref(kind, source_id))
    batch.commit()


def split_event_members(member_ids):
    """신청 순서대로 10명 단위까지 참가 확정, 나머지는 대기로 나눔 (DB에는 전체 순서만 저장)."""
    confirmed_count = len(member_ids) // EVENT_CONFIRM_UNIT * EVENT_CONFIRM_UNIT
    return member_ids[:confirmed_count], member_ids[confirmed_count:]


def event_member_state_text(member_ids, user_id):
    confirmed_ids, waitlist_ids = split_event_members(member_ids)
    if user_id in confirmed_ids:
        return "현재 **참가 확정**이에요."
    if user_id in waitlist_ids:
        return (
            f"현재 **대기 {waitlist_ids.index(user_id) + 1}번**이에요. "
            f"{EVENT_CONFIRM_UNIT}명 단위로 인원이 차면 참가가 확정돼요."
        )
    return ""


def event_team_progress(member_count):
    confirmed_count = member_count // EVENT_CONFIRM_UNIT * EVENT_CONFIRM_UNIT
    waiting_count = member_count - confirmed_count
    needed = EVENT_CONFIRM_UNIT - waiting_count
    team_count = confirmed_count // EVENT_TEAM_SIZE
    if team_count == 0:
        return f"확정된 팀 없음 · 2팀 확정까지 {needed}명 남음"

    text = f"{team_count}팀 확정 ({confirmed_count}명)"
    if waiting_count:
        text += f" · 대기 {waiting_count}명"
    text += f" · {team_count + 2}팀까지 {needed}명 남음"
    return text


def event_member_lines(member_ids, names, limit):
    lines = []
    for index, member_id in enumerate(member_ids[:limit], 1):
        line = f"{index}. {names[member_id]}"
        if len("\n".join(lines + [line])) > 900:
            break
        lines.append(line)
    hidden_count = len(member_ids) - len(lines)
    if hidden_count:
        lines.append(f"… 외 {hidden_count}명 (`참가자 보기`에서 전체 확인)")
    return lines


async def build_event_embed(data, guild):
    status = event_status(data)
    status_text = "🟢 모집 중" if status == "open" else "🔴 모집 마감"
    color = discord.Color(0xF2C94C) if status == "open" else discord.Color.dark_grey()
    member_ids = [str(user_id) for user_id in data.get("member_ids", [])]
    confirmed_ids, waitlist_ids = split_event_members(member_ids)
    creator_id = str(data.get("creator_id", ""))
    member_names = await get_party_member_names(
        guild, confirmed_ids[:EVENT_PUBLIC_MEMBER_LIMIT] + waitlist_ids + [creator_id]
    )
    scheduled_at = as_utc(data["scheduled_at"])
    scheduled_timestamp = int(scheduled_at.timestamp())

    rules = escape_display_text(data.get("rules")) or "상세 안내는 운영진 공지를 확인해주세요."
    prize = escape_display_text(data.get("prize"))
    member_count = len(member_ids)

    embed = discord.Embed(
        title=f"🏆 발쫀전 · {escape_display_text(data['name'])}",
        description=rules,
        color=color,
    )
    embed.set_author(name="VALJJONJAM SPECIAL EVENT")
    embed.add_field(name="모집 상태", value=status_text, inline=True)
    embed.add_field(
        name="현재 참가",
        value=f"**{member_count}명**\n확정 {len(confirmed_ids)} · 대기 {len(waitlist_ids)}",
        inline=True,
    )
    embed.add_field(name="주최", value=member_names.get(creator_id, "알 수 없는 사용자"), inline=True)
    embed.add_field(
        name="🎯 참가 티어 조건",
        value=event_tier_restriction_text(data.get("tier_restriction")),
        inline=False,
    )
    embed.add_field(
        name="경기 시작",
        value=f"<t:{scheduled_timestamp}:F>\n<t:{scheduled_timestamp}:R>",
        inline=False,
    )
    embed.add_field(name="🎁 상품", value=prize, inline=False)

    if data.get("has_entry_fee"):
        entry_fee = escape_display_text(data.get("entry_fee"))
        bank_holder = escape_display_text(data.get("bank_holder"))
        account_number = escape_display_text(data.get("account_number"))
        fee_text = (
            "☑️ **있음**　☐ 없음\n"
            f"금액: **{entry_fee}**\n"
            f"은행 / 예금주: **{bank_holder}**\n"
            f"계좌번호: `{account_number}`\n\n"
            "⚠️ **참가 확정 명단에 있는 분만, 경기가 확정되면 입금 부탁드립니다.**"
        )
    else:
        fee_text = "☐ 있음　☑️ **없음**"
    embed.add_field(name="💳 참가비", value=fee_text, inline=False)

    confirmed_lines = event_member_lines(confirmed_ids, member_names, EVENT_PUBLIC_MEMBER_LIMIT)
    embed.add_field(
        name=f"✅ 참가 확정 ({len(confirmed_ids)}명)",
        value=(
            "\n".join(confirmed_lines)
            if confirmed_lines
            else f"*{EVENT_CONFIRM_UNIT}명이 모이면 2팀 참가가 확정돼요.*"
        ),
        inline=False,
    )
    if waitlist_ids:
        embed.add_field(
            name=f"⏳ 대기 ({len(waitlist_ids)}명)",
            value="\n".join(event_member_lines(waitlist_ids, member_names, EVENT_CONFIRM_UNIT)),
            inline=False,
        )

    embed.add_field(
        name="팀 구성 현황",
        value=event_team_progress(member_count),
        inline=False,
    )
    embed.set_footer(
        text=f"{EVENT_CONFIRM_UNIT}명(2팀) 단위로 신청 순서대로 참가가 확정되며, 나머지 인원은 대기로 표시됩니다."
    )
    return embed


def get_event_data(event_id):
    snapshot = db.collection(EVENT_COLLECTION).document(str(event_id)).get()
    return snapshot.to_dict() if snapshot.exists else None


# 이벤트는 자동 삭제되지 않아 문서가 계속 쌓이므로, 전체가 아닌 필요한 범위만 조회
def list_open_events():
    query = db.collection(EVENT_COLLECTION).where(filter=FieldFilter("status", "==", "open"))
    return [(doc.id, doc.to_dict()) for doc in query.stream()]


# 재시작 시 갱신 대상: 아직 시작 전(수동 마감 포함)이거나 최근에 시작한 이벤트
def list_recent_events(now):
    query = db.collection(EVENT_COLLECTION).where(
        filter=FieldFilter("scheduled_at", ">=", now - EVENT_REFRESH_WINDOW)
    )
    return [(doc.id, doc.to_dict()) for doc in query.stream()]


@transactional
def join_event_transaction(transaction, event_ref, mirror_ref, user_id, now):
    snapshot = event_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None, None

    data = snapshot.to_dict()
    if event_status(data, now) == "closed":
        return "closed", data, None

    member_ids = [str(member_id) for member_id in data.get("member_ids", [])]
    if user_id in member_ids:
        return "already", data, None

    restriction = data.get("tier_restriction")
    if validate_event_tier_restriction(restriction):
        return "tier_restricted", data, event_tier_join_error(restriction, None)
    if restriction is not None and restriction["mode"] != "none":
        # 참가 조건과 멤버 티어를 같은 트랜잭션에서 읽어 검사한 뒤에만 저장합니다.
        user_snapshot = db.collection("users").document(user_id).get(transaction=transaction)
        error = event_tier_join_error(
            restriction, user_snapshot.to_dict() if user_snapshot.exists else None
        )
        if error:
            return "tier_restricted", data, error

    member_ids.append(user_id)
    data["member_ids"] = member_ids
    data["updated_at"] = now
    transaction.update(event_ref, {"member_ids": member_ids, "updated_at": now})
    transaction.set(
        mirror_ref,
        build_toolkit_recruitment_data("event", event_ref.id, data, now),
    )
    return "joined", data, None


@transactional
def leave_event_transaction(transaction, event_ref, mirror_ref, user_id, now, ignore_closed=False):
    # ignore_closed: 관리자의 참가자 제외는 모집 마감·시작 후에도 허용
    snapshot = event_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None

    data = snapshot.to_dict()
    if not ignore_closed and event_status(data, now) == "closed":
        return "closed", data

    member_ids = [str(member_id) for member_id in data.get("member_ids", [])]
    if user_id not in member_ids:
        return "not_member", data

    member_ids.remove(user_id)
    data["member_ids"] = member_ids
    data["updated_at"] = now
    transaction.update(event_ref, {"member_ids": member_ids, "updated_at": now})
    transaction.set(
        mirror_ref,
        build_toolkit_recruitment_data("event", event_ref.id, data, now),
    )
    return "left", data


@transactional
def set_event_registration_transaction(
    transaction, event_ref, mirror_ref, target_status, now
):
    snapshot = event_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None

    data = snapshot.to_dict()
    scheduled_at = as_utc(data.get("scheduled_at"))
    if scheduled_at is None or now >= scheduled_at:
        return "started", data

    if data.get("status") == target_status:
        return f"already_{target_status}", data

    data["status"] = target_status
    data["updated_at"] = now
    transaction.update(event_ref, {"status": target_status, "updated_at": now})
    transaction.set(
        mirror_ref,
        build_toolkit_recruitment_data("event", event_ref.id, data, now),
    )
    return target_status, data


class EventView(discord.ui.View):
    def __init__(self, data=None):
        super().__init__(timeout=None)
        if data is not None:
            status = event_status(data)
            scheduled_at = as_utc(data.get("scheduled_at"))
            self.join_event.disabled = status != "open"
            self.leave_event.disabled = status != "open"
            started = scheduled_at is None or scheduled_at <= utc_now()
            manually_closed = data.get("status") == "closed"
            self.close_registration.disabled = started or manually_closed
            self.reopen_registration.disabled = started or not manually_closed

    @discord.ui.button(
        label="참가하기",
        style=discord.ButtonStyle.success,
        custom_id="event:join",
        row=0,
    )
    async def join_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return
        if await block_if_unverified(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        event_id = str(interaction.message.id)
        user_id = str(interaction.user.id)

        try:
            async with event_action_lock:
                event_ref = db.collection(EVENT_COLLECTION).document(event_id)
                mirror_ref = get_toolkit_recruitment_ref("event", event_id)
                transaction = db.transaction()
                result, data, tier_error = await asyncio.to_thread(
                    join_event_transaction,
                    transaction,
                    event_ref,
                    mirror_ref,
                    user_id,
                    utc_now(),
                )

            state_text = ""
            if result in {"joined", "already"}:
                member_ids = [str(member_id) for member_id in data.get("member_ids", [])]
                state_text = " " + event_member_state_text(member_ids, user_id)
            messages = {
                "missing": "❌ 이미 삭제된 이벤트예요.",
                "closed": "❌ 이미 모집이 마감된 이벤트예요.",
                "already": "ℹ️ 이미 이벤트에 참가하고 있어요." + state_text,
                "joined": "🏆 발쫀전 참가 신청을 완료했어요!" + state_text,
                "tier_restricted": tier_error,
            }
            refresh_warning = ""
            if result in {"joined", "closed"} and data is not None:
                embed = await build_event_embed(data, interaction.guild)
                refreshed = await try_edit_recruitment_message(
                    interaction.message, embed, EventView(data), "이벤트", event_id
                )
                if result == "joined" and not refreshed:
                    refresh_warning = "\n⚠️ 참가는 저장됐지만 모집글 표시 갱신이 지연되고 있어요."
            await interaction.followup.send(messages[result] + refresh_warning, ephemeral=True)
        except Exception:
            log.exception("이벤트 참가 처리 실패 — event=%s user=%s", event_id, user_id)
            await interaction.followup.send("❌ 이벤트 참가 처리 중 오류가 발생했어요.", ephemeral=True)

    @discord.ui.button(
        label="참가 취소",
        style=discord.ButtonStyle.secondary,
        custom_id="event:leave",
        row=0,
    )
    async def leave_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        event_id = str(interaction.message.id)
        user_id = str(interaction.user.id)

        try:
            async with event_action_lock:
                event_ref = db.collection(EVENT_COLLECTION).document(event_id)
                mirror_ref = get_toolkit_recruitment_ref("event", event_id)
                transaction = db.transaction()
                result, data = await asyncio.to_thread(
                    leave_event_transaction,
                    transaction,
                    event_ref,
                    mirror_ref,
                    user_id,
                    utc_now(),
                )

            messages = {
                "missing": "❌ 이미 삭제된 이벤트예요.",
                "closed": "❌ 모집이 마감된 후에는 참가를 취소할 수 없어요.",
                "not_member": "ℹ️ 이 이벤트에 참가하고 있지 않아요.",
                "left": "✅ 이벤트 참가를 취소했어요.",
            }
            refresh_warning = ""
            if result in {"left", "closed"} and data is not None:
                embed = await build_event_embed(data, interaction.guild)
                refreshed = await try_edit_recruitment_message(
                    interaction.message, embed, EventView(data), "이벤트", event_id
                )
                if result == "left" and not refreshed:
                    refresh_warning = "\n⚠️ 취소는 저장됐지만 모집글 표시 갱신이 지연되고 있어요."
            await interaction.followup.send(messages[result] + refresh_warning, ephemeral=True)
        except Exception:
            log.exception("이벤트 참가 취소 실패 — event=%s user=%s", event_id, user_id)
            await interaction.followup.send("❌ 이벤트 참가 취소 중 오류가 발생했어요.", ephemeral=True)

    @discord.ui.button(
        label="참가자 보기",
        style=discord.ButtonStyle.primary,
        custom_id="event:members",
        row=0,
    )
    async def show_event_members(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        event_id = str(interaction.message.id)
        try:
            data = await asyncio.to_thread(get_event_data, event_id)
            if data is None:
                await interaction.followup.send("❌ 이미 삭제된 이벤트예요.", ephemeral=True)
                return

            member_ids = [str(member_id) for member_id in data.get("member_ids", [])]
            if not member_ids:
                await interaction.followup.send("아직 참가자가 없어요.", ephemeral=True)
                return

            names = await get_party_member_names(interaction.guild, member_ids)
            confirmed_ids, waitlist_ids = split_event_members(member_ids)
            lines = [f"**✅ 참가 확정 ({len(confirmed_ids)}명)**"]
            lines += [f"{index}. {names[member_id]}" for index, member_id in enumerate(confirmed_ids, 1)]
            if not confirmed_ids:
                lines.append(f"{EVENT_CONFIRM_UNIT}명이 모이면 2팀 참가가 확정돼요.")
            if waitlist_ids:
                lines.append(f"**⏳ 대기 ({len(waitlist_ids)}명)**")
                lines += [f"{index}. {names[member_id]}" for index, member_id in enumerate(waitlist_ids, 1)]
            await send_ephemeral_log(
                interaction,
                f"🏆 **{escape_display_text(data['name'])} 참가자 · {len(member_ids)}명**",
                lines,
            )
        except Exception:
            log.exception("이벤트 참가자 조회 실패 — event=%s", event_id)
            await interaction.followup.send("❌ 참가자 명단을 불러오지 못했어요.", ephemeral=True)

    async def set_registration_status(self, interaction, target_status):
        if await block_if_not_in_guild(interaction):
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 관리자만 모집 상태를 변경할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        event_id = str(interaction.message.id)
        try:
            async with event_action_lock:
                event_ref = db.collection(EVENT_COLLECTION).document(event_id)
                mirror_ref = get_toolkit_recruitment_ref("event", event_id)
                transaction = db.transaction()
                result, data = await asyncio.to_thread(
                    set_event_registration_transaction,
                    transaction,
                    event_ref,
                    mirror_ref,
                    target_status,
                    utc_now(),
                )

            messages = {
                "missing": "❌ 이미 삭제된 이벤트예요.",
                "started": "❌ 이미 경기 시각이 지나 모집 상태를 변경할 수 없어요.",
                "open": "✅ 이벤트 모집을 재개했어요.",
                "closed": "🔒 이벤트 모집을 마감했어요.",
                "already_open": "ℹ️ 이미 모집 중인 이벤트예요.",
                "already_closed": "ℹ️ 이미 모집이 마감된 이벤트예요.",
            }
            refresh_warning = ""
            if result in {"open", "closed"}:
                embed = await build_event_embed(data, interaction.guild)
                refreshed = await try_edit_recruitment_message(
                    interaction.message, embed, EventView(data), "이벤트", event_id
                )
                if not refreshed:
                    refresh_warning = "\n⚠️ DB에는 반영됐지만 모집글 표시 갱신이 지연되고 있어요."
            await interaction.followup.send(messages[result] + refresh_warning, ephemeral=True)
        except Exception:
            log.exception("이벤트 모집 상태 변경 실패 — event=%s", event_id)
            await interaction.followup.send("❌ 모집 상태 변경 중 오류가 발생했어요.", ephemeral=True)

    @discord.ui.button(
        label="모집 마감",
        style=discord.ButtonStyle.danger,
        custom_id="event:close",
        row=1,
    )
    async def close_registration(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.set_registration_status(interaction, "closed")

    @discord.ui.button(
        label="모집 재개",
        style=discord.ButtonStyle.success,
        custom_id="event:reopen",
        row=1,
    )
    async def reopen_registration(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.set_registration_status(interaction, "open")

    @discord.ui.button(
        label="이벤트 삭제",
        style=discord.ButtonStyle.danger,
        custom_id="event:delete",
        row=1,
    )
    async def delete_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 관리자만 이벤트를 삭제할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        event_id = str(interaction.message.id)
        try:
            data = await asyncio.to_thread(get_event_data, event_id)
            if data is None:
                await interaction.followup.send("❌ 이미 삭제된 이벤트예요.", ephemeral=True)
                return
            await interaction.followup.send(
                f"🗑️ **발쫀전 · {escape_display_text(data.get('name'))}** 모집글을 삭제할까요?\n"
                f"참가 신청 {len(data.get('member_ids', []))}명의 명단도 함께 삭제되며 되돌릴 수 없어요.",
                view=DeleteConfirmView(
                    interaction.user.id, interaction.message, "event", event_id, event_action_lock
                ),
                ephemeral=True,
            )
        except Exception:
            log.exception("이벤트 삭제 확인 화면 열기 실패 — event=%s", event_id)
            await interaction.followup.send("❌ 이벤트 삭제 중 오류가 발생했어요.", ephemeral=True)

    @discord.ui.button(
        label="참가자 제외",
        style=discord.ButtonStyle.secondary,
        custom_id="event:remove_member",
        row=1,
    )
    async def remove_event_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await block_if_not_in_guild(interaction):
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 관리자만 참가자를 제외할 수 있어요.", ephemeral=True)
            return
        view = EventMemberRemoveView(interaction.user.id, interaction.message)
        await interaction.response.send_message(view.render_content(), view=view, ephemeral=True)


class EventMemberRemoveView(discord.ui.View):
    """관리자가 노쇼 등으로 참가자를 명단에서 빼는 화면 — 모집 마감 후에도 사용 가능."""

    def __init__(self, owner_id, event_message):
        super().__init__(timeout=120)
        self.owner_id = str(owner_id)
        self.event_message = event_message
        self.target = None
        self.remove.disabled = True

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message("❌ 이 화면을 연 관리자만 사용할 수 있어요.", ephemeral=True)
        return False

    def render_content(self):
        if self.target is None:
            return "👤 명단에서 제외할 참가자를 선택해주세요.\n제외된 자리는 다음 신청자가 신청 순서대로 채웁니다."
        return (
            f"👤 **{escape_display_text(self.target.display_name)}** 님을 이 발쫀전 명단에서 제외할까요?\n"
            "제외된 자리는 다음 신청자가 신청 순서대로 채우며, 본인에게 따로 알림은 가지 않아요."
        )

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="제외할 참가자 선택", row=0)
    async def select_target(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.target = select.values[0]
        self.remove.disabled = False
        await interaction.response.edit_message(content=self.render_content(), view=self)

    @discord.ui.button(label="제외", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="⏳ 제외하는 중...", view=None)
        event_id = str(self.event_message.id)
        user_id = str(self.target.id)
        try:
            async with event_action_lock:
                result, data = await asyncio.to_thread(
                    leave_event_transaction,
                    db.transaction(),
                    db.collection(EVENT_COLLECTION).document(event_id),
                    get_toolkit_recruitment_ref("event", event_id),
                    user_id,
                    utc_now(),
                    ignore_closed=True,
                )
            name = escape_display_text(self.target.display_name)
            messages = {
                "missing": "❌ 이미 삭제된 이벤트예요.",
                "not_member": f"ℹ️ **{name}** 님은 이 이벤트에 참가하고 있지 않아요.",
                "left": f"✅ **{name}** 님을 명단에서 제외했어요.",
            }
            refresh_warning = ""
            if result == "left":
                embed = await build_event_embed(data, self.event_message.guild)
                refreshed = await try_edit_recruitment_message(
                    self.event_message, embed, EventView(data), "이벤트", event_id
                )
                if not refreshed:
                    refresh_warning = "\n⚠️ 제외는 저장됐지만 모집글 표시 갱신이 지연되고 있어요."
            await interaction.edit_original_response(content=messages[result] + refresh_warning)
        except Exception:
            log.exception("이벤트 참가자 제외 실패 — event=%s user=%s", event_id, user_id)
            await interaction.edit_original_response(content="❌ 참가자 제외 중 오류가 발생했어요.")

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="참가자 제외를 취소했어요.", view=None)


class EventTierSelect(PartyCreationSelect):
    async def callback(self, interaction: discord.Interaction):
        setattr(self.parent_view, self.kind, self.values[0])
        if self.kind == "tier_mode":
            self.parent_view.tier_min = None
            self.parent_view.tier_max = None
        self.parent_view.refresh_tier_selects()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(), view=self.parent_view
        )


class EventFeeSelectionView(discord.ui.View):
    def __init__(self, owner_id, name, channel):
        super().__init__(timeout=300)
        self.owner_id = str(owner_id)
        self.name = name
        self.channel = channel
        self.started = False
        self.tier_mode = "none"
        self.tier_min = None
        self.tier_max = None
        self.refresh_tier_selects()

    def tier_restriction(self):
        return {"mode": self.tier_mode, "min": self.tier_min, "max": self.tier_max}

    def refresh_tier_selects(self):
        for item in list(self.children):
            if isinstance(item, EventTierSelect):
                self.remove_item(item)
        self.add_item(EventTierSelect(
            self, "tier_mode", "참가 제한 기준", [
                discord.SelectOption(label=label, value=mode)
                for mode, label in EVENT_TIER_MODES.items()
            ], row=0,
        ))
        if self.tier_mode != "none":
            for field, placeholder, row in (
                ("tier_min", "최저 티어 (포함)", 1),
                ("tier_max", "최고 티어 (포함)", 2),
            ):
                self.add_item(EventTierSelect(
                    self, field, placeholder, [
                        discord.SelectOption(label=label, value=tier)
                        for tier, label in event_tier_choices(self.tier_mode).items()
                    ], row=row,
                ))

    def render_content(self):
        restriction = self.tier_restriction()
        error = validate_event_tier_restriction(restriction)
        condition = (
            f"{EVENT_TIER_MODES[self.tier_mode]}\n{error}"
            if error else event_tier_restriction_text(restriction)
        )
        return (
            f"🏆 **발쫀전 · {escape_display_text(self.name)}** 생성\n"
            f"🎯 참가 조건: {condition}\n\n"
            "참가 조건을 확인한 뒤 아래에서 참가비 여부를 선택해주세요.\n"
            "참가비가 있으면 입력한 은행·예금주·계좌번호가 모집글에 공개됩니다."
        )

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message(
                "❌ 이 이벤트 생성 화면을 연 관리자만 사용할 수 있어요.", ephemeral=True
            )
            return False
        if self.started:
            await interaction.response.send_message(
                "ℹ️ 이미 상세 정보 입력창을 열었어요. 다시 시작하려면 `/이벤트생성`을 실행해주세요.",
                ephemeral=True,
            )
            return False
        return True

    async def open_details(self, interaction, has_entry_fee):
        restriction = self.tier_restriction()
        error = validate_event_tier_restriction(restriction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        self.started = True
        await interaction.response.send_modal(EventDetailsModal(
            self.owner_id, self.name, self.channel, has_entry_fee,
            tier_restriction=restriction,
        ))

    @discord.ui.button(label="참가비 없음", style=discord.ButtonStyle.secondary, row=3)
    async def no_entry_fee(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_details(interaction, has_entry_fee=False)

    @discord.ui.button(label="참가비 있음", style=discord.ButtonStyle.success, row=3)
    async def has_entry_fee(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_details(interaction, has_entry_fee=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, row=3)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="이벤트 생성을 취소했어요.", view=None)


class EventDetailsModal(discord.ui.Modal):
    def __init__(self, owner_id, name, channel, has_entry_fee, tier_restriction=None):
        super().__init__(title="발쫀전 상세 정보", timeout=300)
        self.owner_id = str(owner_id)
        self.name = name
        self.channel = channel
        self.has_entry_fee = has_entry_fee
        self.tier_restriction = tier_restriction

        self.prize = discord.ui.TextInput(
            label="상품",
            placeholder="예: 우승팀 치킨 기프티콘",
            max_length=200,
        )
        self.add_item(self.prize)

        if has_entry_fee:
            self.entry_fee = discord.ui.TextInput(
                label="참가비",
                placeholder="예: 1인 5,000원",
                max_length=100,
            )
            self.bank_holder = discord.ui.TextInput(
                label="은행 / 예금주",
                placeholder="예: 카카오뱅크 / 홍길동",
                max_length=100,
            )
            self.account_number = discord.ui.TextInput(
                label="계좌번호",
                placeholder="예: 3333-00-0000000",
                max_length=100,
            )
            self.add_item(self.entry_fee)
            self.add_item(self.bank_holder)
            self.add_item(self.account_number)
        else:
            self.entry_fee = None
            self.bank_holder = None
            self.account_number = None

        self.rules = discord.ui.TextInput(
            label="안내 / 규칙 (선택)",
            placeholder="예: 팀은 모집 마감 후 공개합니다.",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.rules)

    async def on_submit(self, interaction: discord.Interaction):
        details = {
            "prize": self.prize.value.strip(),
            "has_entry_fee": self.has_entry_fee,
            "entry_fee": self.entry_fee.value.strip() if self.entry_fee else None,
            "bank_holder": self.bank_holder.value.strip() if self.bank_holder else None,
            "account_number": self.account_number.value.strip() if self.account_number else None,
            "rules": self.rules.value.strip(),
            "tier_restriction": self.tier_restriction,
        }
        required_values = [details["prize"]]
        if self.has_entry_fee:
            required_values.extend(
                [details["entry_fee"], details["bank_holder"], details["account_number"]]
            )
        if not all(required_values):
            await interaction.response.send_message(
                "❌ 필수 항목은 공백으로만 입력할 수 없어요. `/이벤트생성`부터 다시 진행해주세요.",
                ephemeral=True,
            )
            return
        view = EventCreationView(
            self.owner_id,
            self.name,
            self.channel,
            details,
        )
        await interaction.response.send_message(
            view.render_content(),
            view=view,
            ephemeral=True,
        )


class EventCreationView(discord.ui.View):
    def __init__(self, owner_id, name, channel, details):
        super().__init__(timeout=300)
        self.owner_id = str(owner_id)
        self.name = name
        self.channel = channel
        self.details = details
        self.selected_date = None
        self.selected_hour = None
        self.selected_minute = None
        self.submitting = False
        self.add_item(
            PartyCreationSelect(self, "selected_date", "날짜 선택", make_event_date_options(), row=0)
        )
        self.add_item(
            PartyCreationSelect(self, "selected_hour", "시간 선택", make_party_hour_options(), row=1)
        )
        self.add_item(
            PartyCreationSelect(
                self,
                "selected_minute",
                "분 선택",
                make_party_minute_options(),
                row=2,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "❌ 이 이벤트 생성 화면을 연 관리자만 사용할 수 있어요.", ephemeral=True
        )
        return False

    def render_content(self):
        date_text = "선택 전"
        if self.selected_date:
            target = datetime.strptime(self.selected_date, "%Y-%m-%d")
            date_text = f"{target.month}월 {target.day}일"
        time_text = "선택 전"
        if self.selected_hour is not None and self.selected_minute is not None:
            time_text = f"{self.selected_hour}:{self.selected_minute}"
        fee_text = (
            "☑️ 있음　☐ 없음"
            if self.details["has_entry_fee"]
            else "☐ 있음　☑️ 없음"
        )
        return (
            f"🏆 **발쫀전 · {escape_display_text(self.name)}** 생성\n"
            f"• 상품: **{escape_display_text(self.details['prize'])}**\n"
            f"• 참가비: **{fee_text}**\n"
            f"• 참가 조건: {event_tier_restriction_text(self.details.get('tier_restriction'))}\n"
            f"• 날짜: **{date_text}**\n"
            f"• 시간: **{time_text}**\n\n"
            "날짜·시간을 선택한 뒤 `이벤트 생성`을 눌러주세요."
        )

    @discord.ui.button(label="이벤트 생성", style=discord.ButtonStyle.success, row=3)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.submitting:
            await interaction.response.send_message(
                "ℹ️ 이벤트 모집글을 이미 생성하고 있어요.", ephemeral=True
            )
            return
        error = validate_event_tier_restriction(self.details.get("tier_restriction"))
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        if not all((self.selected_date, self.selected_hour, self.selected_minute)):
            await interaction.response.send_message(
                "❌ 날짜와 시간을 모두 선택해주세요.", ephemeral=True
            )
            return

        scheduled_at, error = parse_event_time(
            f"{self.selected_date} {self.selected_hour}:{self.selected_minute}"
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        self.submitting = True
        await interaction.response.defer()
        message = await publish_event(
            interaction,
            self.channel,
            self.name,
            scheduled_at,
            self.details,
        )
        if message is None:
            self.submitting = False
            return

        self.stop()
        await interaction.edit_original_response(
            content=f"✅ 발쫀전 모집글을 만들었어요! {message.jump_url}",
            view=None,
        )

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=3)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="이벤트 생성을 취소했어요.", view=None)


async def publish_event(interaction, channel, name, scheduled_at, details):
    error = validate_event_tier_restriction(details.get("tier_restriction"))
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return None
    creator_id = str(interaction.user.id)
    event_ref = None
    try:
        async with event_action_lock:
            now = utc_now()
            data = {
                "name": name,
                "creator_id": creator_id,
                "member_ids": [],
                "guild_id": str(interaction.guild.id),
                "channel_id": str(channel.id),
                "scheduled_at": scheduled_at,
                "prize": details["prize"],
                "has_entry_fee": details["has_entry_fee"],
                "entry_fee": details["entry_fee"],
                "bank_holder": details["bank_holder"],
                "account_number": details["account_number"],
                "rules": details["rules"],
                "tier_restriction": details.get("tier_restriction"),
                "created_at": now,
                "updated_at": now,
                "status": "open",
            }
            embed = await build_event_embed(data, interaction.guild)
            message = await channel.send(embed=embed)
            remember_recruitment_channel(channel.id)
            data["message_id"] = str(message.id)
            event_ref = db.collection(EVENT_COLLECTION).document(str(message.id))
            try:
                await asyncio.to_thread(
                    set_recruitment_pair,
                    event_ref,
                    "event",
                    str(message.id),
                    data,
                )
                await message.edit(embed=embed, view=EventView(data))
            except Exception:
                await asyncio.to_thread(
                    delete_recruitment_pair,
                    event_ref,
                    "event",
                    str(message.id),
                )
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                raise
        return message
    except Exception:
        log.exception("이벤트 생성 실패 — creator=%s", creator_id)
        await interaction.followup.send("❌ 이벤트 생성 중 오류가 발생했어요.", ephemeral=True)
        return None


@bot.tree.command(name="이벤트생성", description="(어드민) 상품이 걸린 발쫀전 모집글을 생성합니다")
@app_commands.describe(이벤트이름="이벤트 이름 (최대 50자)")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def create_event(
    interaction: discord.Interaction,
    이벤트이름: app_commands.Range[str, 1, 50],
):
    if await block_if_not_in_guild(interaction):
        return

    name = 이벤트이름.strip()
    if not name:
        await interaction.response.send_message("❌ 이벤트 이름을 입력해주세요.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    channel_id = await asyncio.to_thread(get_party_channel_id, interaction.guild.id)
    if channel_id is None:
        await interaction.followup.send(
            "❌ 모집 채널이 아직 설정되지 않았어요. "
            "`/파티채널설정`을 먼저 실행해주세요.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send(
            "❌ 설정된 모집 채널을 찾을 수 없어요. 재설정해주세요.", ephemeral=True
        )
        return
    if interaction.channel_id != channel.id:
        await interaction.followup.send(
            f"❌ `/이벤트생성`은 지정된 모집 채널 {channel.mention}에서만 사용할 수 있어요.",
            ephemeral=True,
        )
        return

    permissions = channel.permissions_for(interaction.guild.me)
    if not permissions.send_messages or not permissions.embed_links:
        await interaction.followup.send(
            f"❌ 봇에게 {channel.mention} 채널의 메시지 전송 및 링크 첨부 권한을 주세요.",
            ephemeral=True,
        )
        return

    view = EventFeeSelectionView(interaction.user.id, name, channel)
    await interaction.followup.send(
        view.render_content(),
        view=view,
        ephemeral=True,
    )


def backfill_toolkit_recruitments_sync():
    now = utc_now()
    batch = db.batch()
    pending = 0
    total = 0

    for collection_name, kind in (
        (PARTY_COLLECTION, "party"),
        (EVENT_COLLECTION, "event"),
    ):
        for document in db.collection(collection_name).stream():
            batch.set(
                get_toolkit_recruitment_ref(kind, document.id),
                build_toolkit_recruitment_data(kind, document.id, document.to_dict(), now),
            )
            pending += 1
            total += 1
            if pending >= 400:
                batch.commit()
                batch = db.batch()
                pending = 0

    if pending:
        batch.commit()
    return total


async def backfill_toolkit_recruitments():
    try:
        total = await asyncio.to_thread(backfill_toolkit_recruitments_sync)
        log.info("툴킷 모집 미러 백필 완료 — %s건", total)
        return True
    except Exception:
        log.exception("툴킷 모집 미러 백필 실패")
        return False


async def delete_recruitment_documents(message_id):
    def delete_existing_documents():
        existing = []
        for collection_name, kind in (
            (PARTY_COLLECTION, "party"),
            (EVENT_COLLECTION, "event"),
        ):
            source_ref = db.collection(collection_name).document(str(message_id))
            if source_ref.get().exists:
                existing.append((source_ref, kind))

        if not existing:
            return

        batch = db.batch()
        for source_ref, kind in existing:
            batch.delete(source_ref)
            batch.delete(get_toolkit_recruitment_ref(kind, message_id))
        batch.commit()

    try:
        await asyncio.to_thread(delete_existing_documents)
    except Exception:
        log.exception("삭제된 모집글 DB 정리 실패 — message=%s", message_id)


async def is_recruitment_channel(guild_id, channel_id):
    """모집글이 있을 수 있는 채널인지 — 서버마다 설정 채널을 한 번만 읽어 캐시."""
    if guild_id is None:
        return False
    guild_key = str(guild_id)
    if guild_key not in recruitment_channel_loaded_guilds:
        try:
            configured = await asyncio.to_thread(get_party_channel_id, guild_key)
        except Exception:
            log.exception("모집 채널 설정 조회 실패 — guild=%s", guild_key)
            return True  # 확인하지 못하면 놓치지 않도록 DB를 확인
        remember_recruitment_channel(configured)
        recruitment_channel_loaded_guilds.add(guild_key)
    return str(channel_id) in recruitment_channel_ids


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    # 관리자 등이 모집글을 직접 삭제한 경우 남은 DB 데이터도 함께 정리
    # 모든 채널의 삭제마다 DB를 읽지 않도록 모집 채널의 삭제만 확인
    if await is_recruitment_channel(payload.guild_id, payload.channel_id):
        await delete_recruitment_documents(payload.message_id)


@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    if not await is_recruitment_channel(payload.guild_id, payload.channel_id):
        return
    for message_id in payload.message_ids:
        await delete_recruitment_documents(message_id)


async def get_recruitment_message(data):
    guild = bot.get_guild(int(data["guild_id"]))
    if guild is None:
        return None, "guild_missing"
    channel = guild.get_channel(int(data["channel_id"]))
    if channel is None:
        return None, "channel_missing"
    try:
        return await channel.fetch_message(int(data["message_id"])), None
    except discord.NotFound:
        return None, "message_missing"


async def refresh_party_messages():
    try:
        parties = await asyncio.to_thread(list_parties)
    except Exception:
        log.exception("기존 파티 모집글 목록 조회 실패")
        return

    for party_id, data in parties:
        try:
            remember_recruitment_channel(data.get("channel_id"))
            message, reason = await get_recruitment_message(data)
            if message is not None:
                embed = await build_party_embed(data, message.guild)
                await message.edit(embed=embed, view=PartyView(data))
            elif reason in {"channel_missing", "message_missing"}:
                await asyncio.to_thread(
                    delete_recruitment_pair,
                    db.collection(PARTY_COLLECTION).document(party_id),
                    "party",
                    party_id,
                )
        except discord.Forbidden:
            log.error("기존 파티 모집글 갱신 권한 없음 — party=%s", party_id)
        except Exception:
            log.exception("기존 파티 모집글 갱신 실패 — party=%s", party_id)


async def refresh_event_messages():
    try:
        events = await asyncio.to_thread(list_recent_events, utc_now())
    except Exception:
        log.exception("기존 이벤트 모집글 목록 조회 실패")
        return

    for event_id, data in events:
        try:
            remember_recruitment_channel(data.get("channel_id"))
            message, reason = await get_recruitment_message(data)
            if message is not None:
                embed = await build_event_embed(data, message.guild)
                await message.edit(embed=embed, view=EventView(data))
            elif reason in {"channel_missing", "message_missing"}:
                await asyncio.to_thread(
                    delete_recruitment_pair,
                    db.collection(EVENT_COLLECTION).document(event_id),
                    "event",
                    event_id,
                )
        except discord.Forbidden:
            log.error("기존 이벤트 모집글 갱신 권한 없음 — event=%s", event_id)
        except Exception:
            log.exception("기존 이벤트 모집글 갱신 실패 — event=%s", event_id)


@tasks.loop(minutes=5)
async def cleanup_parties():
    try:
        parties = await asyncio.to_thread(list_parties)
    except Exception:
        log.exception("파티 자동 정리 목록 조회 실패")
        return

    now = utc_now()
    for party_id, data in parties:
        try:
            scheduled_at = as_utc(data.get("scheduled_at"))
            delete_at = as_utc(data.get("delete_at"))
            if scheduled_at is None:
                continue
            delete_at = delete_at or scheduled_at + PARTY_DELETE_DELAY
            party_ref = db.collection(PARTY_COLLECTION).document(party_id)

            if now >= delete_at:
                message, reason = await get_recruitment_message(data)
                if message is not None:
                    await message.delete()
                if reason != "guild_missing":
                    await asyncio.to_thread(
                        delete_recruitment_pair, party_ref, "party", party_id
                    )
                continue

            if party_status(data, now) == "closed" and data.get("status") != "closed":
                data["status"] = "closed"
                data["updated_at"] = now
                updates = {"status": "closed", "updated_at": now}
                await asyncio.to_thread(
                    update_recruitment_pair,
                    party_ref,
                    updates,
                    "party",
                    party_id,
                    data,
                )
                message, reason = await get_recruitment_message(data)
                if message is not None:
                    embed = await build_party_embed(data, message.guild)
                    await message.edit(embed=embed, view=PartyView(data))
                elif reason in {"channel_missing", "message_missing"}:
                    await asyncio.to_thread(
                        delete_recruitment_pair, party_ref, "party", party_id
                    )
        except discord.Forbidden:
            log.error("파티 모집글 정리 권한 없음 — party=%s", party_id)
        except Exception:
            log.exception("파티 자동 정리 실패 — party=%s", party_id)


@tasks.loop(minutes=1)
async def close_started_events():
    try:
        events = await asyncio.to_thread(list_open_events)
    except Exception:
        log.exception("이벤트 자동 마감 목록 조회 실패")
        return

    now = utc_now()
    for event_id, data in events:
        try:
            scheduled_at = as_utc(data.get("scheduled_at"))
            if (
                scheduled_at is None
                or now < scheduled_at
                or data.get("status") == "closed"
            ):
                continue

            data["status"] = "closed"
            data["updated_at"] = now
            event_ref = db.collection(EVENT_COLLECTION).document(event_id)
            updates = {"status": "closed", "updated_at": now}
            await asyncio.to_thread(
                update_recruitment_pair,
                event_ref,
                updates,
                "event",
                event_id,
                data,
            )

            message, reason = await get_recruitment_message(data)
            if message is not None:
                embed = await build_event_embed(data, message.guild)
                await message.edit(embed=embed, view=EventView(data))
            elif reason in {"channel_missing", "message_missing"}:
                await asyncio.to_thread(
                    delete_recruitment_pair, event_ref, "event", event_id
                )
        except discord.Forbidden:
            log.error("이벤트 자동 마감 권한 없음 — event=%s", event_id)
        except Exception:
            log.exception("이벤트 자동 마감 실패 — event=%s", event_id)


@tasks.loop(time=PROFILE_CHECK_AUTO_TIME)
async def profile_check_auto_loop():
    for guild in bot.guilds:
        try:
            await run_profile_check_auto(guild)
        except Exception:
            log.exception("정보 확인 자동 안내 실패 — guild=%s", guild.id)


@profile_check_auto_loop.before_loop
async def before_profile_check_auto_loop():
    await bot.wait_until_ready()


@cleanup_parties.before_loop
async def before_cleanup_parties():
    await bot.wait_until_ready()


@close_started_events.before_loop
async def before_close_started_events():
    await bot.wait_until_ready()


bot.run(TOKEN)
