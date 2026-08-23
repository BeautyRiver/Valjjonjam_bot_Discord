import discord
from discord.ext import commands, tasks
from discord import app_commands
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import transactional
from dotenv import load_dotenv
import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

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
bot = commands.Bot(command_prefix="!", intents=intents)

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
    "2상": "2티어 -상", "2하": "2티어 -하",
    "3상": "3티어 -상", "3하": "3티어 -하",
    "4상": "4티어 -상", "4하": "4티어 -하", "4?": "4티어 -?",
    "5상": "5티어 -상", "5하": "5티어 -하", "5?": "5티어 -?",
}
INTERNAL_TIER_CHOICES = [
    app_commands.Choice(name=label, value=tier)
    for tier, label in INTERNAL_TIER_LABELS.items()
]

# 인증(기본설정 완료) 역할명 — 이 역할이 있어야 일반 채널이 열리도록 서버 권한을 설정
VERIFIED_ROLE = "인증됨"

# 파티 모집 설정
PARTY_COLLECTION = "parties"
GUILD_SETTINGS_COLLECTION = "guild_settings"
PARTY_MAX_MEMBERS = 5
INHOUSE_PARTY_MAX_MEMBERS = 10
PARTY_TIMEZONE = timezone(timedelta(hours=9))
PARTY_MIN_LEAD = timedelta(minutes=10)
PARTY_MAX_LEAD = timedelta(hours=24)
PARTY_DELETE_DELAY = timedelta(hours=10)

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
    # 재시작 후에도 기존 파티 모집글의 버튼이 계속 동작하도록 영구 View 등록
    if not getattr(bot, "party_view_registered", False):
        bot.add_view(PartyView())
        bot.party_view_registered = True
        await refresh_party_messages()
    if not cleanup_parties.is_running():
        cleanup_parties.start()
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
    except discord.Forbidden:
        # DM이 막혀있으면 시스템 채널에 멘션으로 안내
        channel = member.guild.system_channel
        if channel and channel.permissions_for(member.guild.me).send_messages:
            await channel.send(f"{member.mention}\n{guide}")

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
        guild = interaction.guild
        member = view.target or interaction.user  # 관리자가 대신 설정 시 대상 멤버

        # 새로 부여할 티어/역할군 역할
        managed = set(TIERS + ROLES)
        to_add = []
        tier_role = discord.utils.get(guild.roles, name=tier)
        if tier_role:
            to_add.append(tier_role)
        for role_name in view.selected_roles:
            role_role = discord.utils.get(guild.roles, name=role_name)
            if role_role:
                to_add.append(role_role)

        # 기존 티어/역할군은 빼고 나머지는 유지 + 새 역할 → 한 번의 호출로 적용
        keep = [r for r in member.roles if r.name not in managed and not r.is_default()]
        await member.edit(roles=keep + to_add)

        await interaction.edit_original_response(
            content=f"✅ **{member.display_name}** 님\n티어: `{tier}` | 역할군: `{', '.join(view.selected_roles)}` 설정 완료!"
        )

        # Firestore 저장은 응답 후 백그라운드에서 (이벤트 루프 차단 방지)
        await asyncio.to_thread(
            db.collection("users").document(str(member.id)).set,
            {
                "discord_id": str(member.id),
                "username": member.name,
                "nickname": member.display_name,
                "tier": tier,
                "role": view.selected_roles,
                "updated_at": firestore.SERVER_TIMESTAMP
            },
            merge=True
        )

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
        
@bot.tree.command(name="메세지", description="봇이 현재 채널에 임베드 메세지를 보냅니다")
@app_commands.describe(
    내용="보낼 내용 (줄바꿈은 \\n 으로 입력)",
    제목="임베드 제목 (선택)",
    미리보기="True면 나에게만 보여요 (전송 전 확인용)",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def bot_say(interaction: discord.Interaction, 내용: str, 제목: str = None, 미리보기: bool = False):
    # 슬래시 입력은 줄바꿈을 못 넣으니 \n 표기를 실제 줄바꿈으로 변환
    embed = discord.Embed(description=내용.replace("\\n", "\n"), color=discord.Color.blurple())
    if 제목:
        embed.title = 제목

    # 미리보기(디버그): 채널에 안 보내고 나에게만 보여줌
    if 미리보기:
        await interaction.response.send_message(
            content="🧪 **미리보기** — 아직 채널에 전송되지 않았어요!",
            embed=embed, ephemeral=True
        )
        return

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ 메세지를 보냈어요!", ephemeral=True)

@bot_say.error
async def bot_say_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


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
        except discord.Forbidden:
            failed += 1  # DM 차단된 멤버

    await interaction.followup.send(
        f"📣 미인증자 안내 DM 전송 완료!\n"
        f"✅ 보냄: {sent}명 | ⏭️ 이미 인증: {skipped}명 | ❌ DM 차단: {failed}명",
        ephemeral=True
    )

@announce_unverified.error
async def announce_unverified_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


async def send_ephemeral_log(interaction, title, lines):
    """Discord 2,000자 제한에 맞춰 비공개 로그를 나눠 보냄."""
    message = title
    for line in lines:
        next_message = f"{message}\n{line}"
        if len(next_message) <= 1900:
            message = next_message
            continue
        await interaction.followup.send(message, ephemeral=True)
        message = f"{title} **(계속)**\n{line}"
    await interaction.followup.send(message, ephemeral=True)


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

@cleanup_db.error
async def cleanup_db_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


# ===== 발쫀잼 내부 티어 관리 =====

@bot.tree.command(name="내부티어설정", description="(어드민) 멤버의 발쫀잼 내부 티어를 설정합니다")
@app_commands.describe(대상="내부 티어를 설정할 멤버", 티어="설정할 발쫀잼 내부 티어")
@app_commands.choices(티어=INTERNAL_TIER_CHOICES)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def set_internal_tier(
    interaction: discord.Interaction,
    대상: discord.Member,
    티어: app_commands.Choice[str],
):
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
            f"❌ **{대상.display_name}** 님의 기본설정 정보가 없어요. `/기본설정`을 먼저 진행해주세요.",
            ephemeral=True,
        )
        return

    await asyncio.to_thread(
        user_ref.set,
        {"internal_tier": 티어.value, "updated_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    await interaction.followup.send(
        f"✅ **{data['name']}** 님의 발쫀잼 내부 티어를 `{INTERNAL_TIER_LABELS[티어.value]}`(으)로 설정했어요.",
        ephemeral=True,
    )


@set_internal_tier.error
async def set_internal_tier_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


@bot.tree.command(name="내부티어초기화", description="(어드민) 멤버의 발쫀잼 내부 티어를 미분류로 초기화합니다")
@app_commands.describe(대상="내부 티어를 초기화할 멤버")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def clear_internal_tier(interaction: discord.Interaction, 대상: discord.Member):
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
            f"❌ **{대상.display_name}** 님의 기본설정 정보가 없어요.",
            ephemeral=True,
        )
        return

    await asyncio.to_thread(
        user_ref.set,
        {"internal_tier": None, "updated_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    await interaction.followup.send(
        f"✅ **{data['name']}** 님의 발쫀잼 내부 티어를 `미분류`로 초기화했어요.",
        ephemeral=True,
    )


@clear_internal_tier.error
async def clear_internal_tier_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


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

        # 새로 부여할 티어/역할군 역할
        managed = set(TIERS + ROLES)
        to_add = []
        tier_role = discord.utils.get(guild.roles, name=tier)
        if tier_role:
            to_add.append(tier_role)
        for role_name in view.selected_roles:
            role_role = discord.utils.get(guild.roles, name=role_name)
            if role_role:
                to_add.append(role_role)

        # 기본설정 완료 → 인증 역할도 함께 부여 (없으면 생성)
        verified = await get_or_create_verified_role(guild)
        to_add.append(verified)

        # ① 역할(인증)을 먼저·따로 부여 — 닉네임이 막혀도 인증은 무조건 되게
        #    add/remove로 처리하면 봇보다 위 역할을 가진 멤버에게도 영향을 덜 받음
        managed_all = managed | {VERIFIED_ROLE}
        to_remove = [r for r in member.roles if r.name in managed_all and r not in to_add]
        role_failed = None
        try:
            if to_remove:
                await member.remove_roles(*to_remove)
            await member.add_roles(*to_add)
        except discord.HTTPException as e:
            role_failed = e
            log.error(
                "역할 적용 실패 — %s(%s) / 부여시도=%s / status=%s code=%s: %s",
                member, member.id, [r.name for r in to_add],
                getattr(e, "status", "?"), getattr(e, "code", "?"), e,
            )

        # ② 닉네임은 별도로 — 실패해도 인증은 통과 (Forbidden/HTTPException 모두 처리)
        new_nick = build_nickname(view.student_id, view.name, view.riot_id)
        nick_failed = None
        try:
            await member.edit(nick=new_nick)
        except discord.HTTPException as e:
            nick_failed = e
            log.error(
                "닉네임 변경 실패 — %s(%s) / 닉='%s' / status=%s code=%s: %s",
                member, member.id, new_nick,
                getattr(e, "status", "?"), getattr(e, "code", "?"), e,
            )

        msg = (
            f"✅ **{view.name}** 님 설정 완료!\n"
            f"학번: `{view.student_id}` | 발로ID: `{view.riot_id}`\n"
            f"티어: `{tier}` | 역할군: `{', '.join(view.selected_roles)}`"
        )
        if role_failed:
            msg += "\n⚠️ 일부 역할을 적용하지 못했어요. (봇에 '역할 관리' 권한을 주고 봇 역할을 위로 올려주세요)"
        if nick_failed:
            msg += "\n⚠️ 닉네임은 바꾸지 못했어요. (이름·발로 닉네임에 `@`, `discord`, `clyde`, `everyone`, `here` 같은 단어가 들어가면 디스코드가 막아요. `/닉네임변경` 으로 다시 시도해보세요)"
        await interaction.edit_original_response(content=msg)

        # Firestore 저장은 응답 후 백그라운드에서 (이벤트 루프 차단 방지)
        await asyncio.to_thread(
            db.collection("users").document(str(member.id)).set,
            {
                "discord_id": str(member.id),
                "username": member.name,
                "student_id": view.student_id,
                "name": view.name,
                "riot_id": view.riot_id,
                "tier": tier,
                "role": view.selected_roles,
                "updated_at": firestore.SERVER_TIMESTAMP
            },
            merge=True
        )

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

        nick_failed = False
        try:
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            nick_failed = True

        # 입력한 정보도 DB에 반영 (기존 티어/역할군 등은 유지)
        await asyncio.to_thread(
            db.collection("users").document(str(member.id)).set,
            {
                "discord_id": str(member.id),
                "username": member.name,
                "student_id": sid,
                "name": nm,
                "riot_id": rid,
                "updated_at": firestore.SERVER_TIMESTAMP
            },
            merge=True
        )

        if nick_failed:
            await interaction.edit_original_response(
                content="⚠️ 닉네임을 바꿀 권한이 없어요. (서버 주인 닉네임은 봇이 못 바꿔요 / 그 외엔 '별명 관리하기' 권한 + 봇 역할을 위로 올려주세요)\n정보는 저장했어요."
            )
        else:
            await interaction.edit_original_response(content=f"✅ 닉네임을 `{new_nick}` (으)로 변경했어요!")

@bot.tree.command(name="닉네임변경", description="디스코드 닉네임을 '학번 이름 / 발로닉' 형식으로 변경합니다")
async def change_nickname(interaction: discord.Interaction):
    if await block_if_not_in_guild(interaction):
        return
    if await block_if_unverified(interaction):
        return
    # 기존에 저장된 정보가 있으면 모달에 미리 채워줌
    doc = db.collection("users").document(str(interaction.user.id)).get()
    data = doc.to_dict() if doc.exists else {}
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

@admin_basic_setup.error
async def admin_basic_setup_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


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

@admin_set_role.error
async def admin_set_role_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


@bot.tree.command(name="대리닉네임변경", description="(어드민) 다른 멤버의 닉네임을 '학번 이름 / 발로닉' 형식으로 변경합니다")
@app_commands.describe(대상="닉네임을 바꿔줄 멤버")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def admin_change_nickname(interaction: discord.Interaction, 대상: discord.Member):
    if await block_if_bot_target(interaction, 대상):
        return
    # 대상에게 저장된 정보가 있으면 모달에 미리 채워줌
    doc = db.collection("users").document(str(대상.id)).get()
    data = doc.to_dict() if doc.exists else {}
    await interaction.response.send_modal(NicknameModal(
        대상,
        data.get("student_id", ""),
        data.get("name", ""),
        data.get("riot_id", ""),
    ))

@admin_change_nickname.error
async def admin_change_nickname_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


# ===== 5인 파티 모집 =====

party_action_lock = asyncio.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_party_time(value):
    try:
        local_time = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=PARTY_TIMEZONE)
    except ValueError:
        return None, "❌ 선택한 날짜 또는 시간이 올바르지 않아요. 다시 선택해주세요."

    now_local = datetime.now(PARTY_TIMEZONE)
    if local_time < now_local + PARTY_MIN_LEAD:
        return None, "❌ 시작시간은 현재 시각보다 최소 10분 이후여야 해요."
    if local_time > now_local + PARTY_MAX_LEAD:
        return None, "❌ 시작시간은 현재부터 24시간 이내로 정해주세요."
    return local_time.astimezone(timezone.utc), None


def party_max_members(data):
    max_members = data.get("max_members", PARTY_MAX_MEMBERS)
    return max_members if max_members in {PARTY_MAX_MEMBERS, INHOUSE_PARTY_MAX_MEMBERS} else PARTY_MAX_MEMBERS


def party_type_name(max_members):
    return "내전 파티 (10명)" if max_members == INHOUSE_PARTY_MAX_MEMBERS else "5인 파티"


def party_status(data, now=None):
    now = now or utc_now()
    scheduled_at = as_utc(data.get("scheduled_at"))
    if scheduled_at is None or now >= scheduled_at:
        return "closed"
    if len(data.get("member_ids", [])) >= party_max_members(data):
        return "full"
    return "open"


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


async def build_party_embed(data, guild):
    status = party_status(data)
    max_members = party_max_members(data)
    status_text = {
        "open": "🟢 모집 중",
        "full": "🔴 모집 완료",
        "closed": "⚫ 모집 마감",
    }[status]
    color = {
        "open": discord.Color.green(),
        "full": discord.Color.red(),
        "closed": discord.Color.dark_grey(),
    }[status]

    member_ids = [str(uid) for uid in data.get("member_ids", [])]
    creator_id = str(data.get("creator_id", ""))
    member_names = await get_party_member_names(guild, member_ids + [creator_id])
    member_lines = []
    for index in range(max_members):
        if index < len(member_ids):
            user_id = member_ids[index]
            owner = " 👑" if user_id == creator_id else ""
            member_lines.append(f"{index + 1}. {member_names[user_id]}{owner}")
        else:
            member_lines.append(f"{index + 1}. *모집 중*")

    scheduled_at = as_utc(data["scheduled_at"])
    delete_at = as_utc(data.get("delete_at")) or scheduled_at + PARTY_DELETE_DELAY
    scheduled_timestamp = int(scheduled_at.timestamp())
    delete_timestamp = int(delete_at.timestamp())

    embed = discord.Embed(
        title=f"🎮 {party_type_name(max_members)} · {data['name']}",
        description="\n".join(member_lines),
        color=color,
    )
    embed.add_field(name="상태", value=status_text, inline=True)
    embed.add_field(name="모집 유형", value=party_type_name(max_members), inline=True)
    embed.add_field(
        name="현재 인원",
        value=f"**{len(member_ids)} / {max_members}**",
        inline=True,
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
    embed.set_footer(text="한 사람은 동시에 하나의 파티에만 참가할 수 있습니다.")
    return embed


class PartyView(discord.ui.View):
    def __init__(self, data=None):
        super().__init__(timeout=None)
        if data is not None:
            status = party_status(data)
            self.join_party.disabled = status != "open"
            self.leave_party.disabled = status == "closed"

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
                other = await find_active_party_for_member(
                    str(interaction.guild.id), user_id, exclude_party_id=party_id
                )
                if other is not None:
                    await interaction.followup.send(
                        "❌ 이미 다른 파티에 참가 중이에요. 기존 파티에서 참가 취소 후 다시 시도해주세요.",
                        ephemeral=True,
                    )
                    return

                party_ref = db.collection(PARTY_COLLECTION).document(party_id)
                transaction = db.transaction()
                result, data = await asyncio.to_thread(
                    join_party_transaction, transaction, party_ref, user_id, utc_now()
                )

            messages = {
                "missing": "❌ 이미 삭제된 파티예요.",
                "closed": "❌ 모집이 마감된 파티예요.",
                "full": f"❌ 이미 {party_max_members(data)}명이 모두 모였어요.",
                "already": "ℹ️ 이미 이 파티에 참가하고 있어요.",
                "joined": "✅ 파티에 참가했어요!",
            }
            if result == "joined":
                embed = await build_party_embed(data, interaction.guild)
                await interaction.message.edit(embed=embed, view=PartyView(data))
            await interaction.followup.send(messages[result], ephemeral=True)
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
                transaction = db.transaction()
                result, data = await asyncio.to_thread(
                    leave_party_transaction, transaction, party_ref, user_id, utc_now()
                )

            messages = {
                "missing": "❌ 이미 삭제된 파티예요.",
                "closed": "❌ 이미 모집이 마감됐어요.",
                "creator": "❌ 파티장은 참가를 취소할 수 없어요. `파티 삭제`를 이용해주세요.",
                "not_member": "ℹ️ 이 파티에 참가하고 있지 않아요.",
                "left": "✅ 파티 참가를 취소했어요.",
            }
            if result == "left":
                embed = await build_party_embed(data, interaction.guild)
                await interaction.message.edit(embed=embed, view=PartyView(data))
            await interaction.followup.send(messages[result], ephemeral=True)
        except Exception:
            log.exception("파티 참가 취소 실패 — party=%s user=%s", party_id, user_id)
            await interaction.followup.send("❌ 참가 취소 처리 중 오류가 발생했어요.", ephemeral=True)

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
        party_ref = db.collection(PARTY_COLLECTION).document(party_id)

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

            async with party_action_lock:
                try:
                    await interaction.message.delete()
                except discord.NotFound:
                    pass
                await asyncio.to_thread(party_ref.delete)
            await interaction.followup.send("🗑️ 파티 모집글을 삭제했어요.", ephemeral=True)
        except Exception:
            log.exception("파티 삭제 실패 — party=%s", party_id)
            await interaction.followup.send("❌ 파티 삭제 중 오류가 발생했어요.", ephemeral=True)


def get_party_data(party_id):
    snapshot = db.collection(PARTY_COLLECTION).document(str(party_id)).get()
    return snapshot.to_dict() if snapshot.exists else None


def list_parties():
    return [(doc.id, doc.to_dict()) for doc in db.collection(PARTY_COLLECTION).stream()]


async def find_active_party_for_member(guild_id, user_id, exclude_party_id=None):
    parties = await asyncio.to_thread(list_parties)
    now = utc_now()
    for party_id, data in parties:
        if party_id == exclude_party_id:
            continue
        if str(data.get("guild_id")) != str(guild_id):
            continue
        if str(user_id) not in [str(uid) for uid in data.get("member_ids", [])]:
            continue
        scheduled_at = as_utc(data.get("scheduled_at"))
        if scheduled_at is not None and now < scheduled_at:
            return party_id, data
    return None


@transactional
def join_party_transaction(transaction, party_ref, user_id, now):
    snapshot = party_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None

    data = snapshot.to_dict()
    if party_status(data, now) == "closed":
        return "closed", data

    member_ids = [str(uid) for uid in data.get("member_ids", [])]
    if user_id in member_ids:
        return "already", data
    max_members = party_max_members(data)
    if len(member_ids) >= max_members:
        return "full", data

    member_ids.append(user_id)
    data["member_ids"] = member_ids
    data["status"] = "full" if len(member_ids) >= max_members else "open"
    data["updated_at"] = now
    transaction.update(
        party_ref,
        {
            "member_ids": member_ids,
            "status": data["status"],
            "updated_at": now,
        },
    )
    return "joined", data


@transactional
def leave_party_transaction(transaction, party_ref, user_id, now):
    snapshot = party_ref.get(transaction=transaction)
    if not snapshot.exists:
        return "missing", None

    data = snapshot.to_dict()
    if party_status(data, now) == "closed":
        return "closed", data
    if str(data.get("creator_id")) == user_id:
        return "creator", data

    member_ids = [str(uid) for uid in data.get("member_ids", [])]
    if user_id not in member_ids:
        return "not_member", data

    member_ids.remove(user_id)
    data["member_ids"] = member_ids
    data["status"] = "open"
    data["updated_at"] = now
    transaction.update(
        party_ref,
        {
            "member_ids": member_ids,
            "status": "open",
            "updated_at": now,
        },
    )
    return "left", data


def get_party_channel_id(guild_id):
    snapshot = db.collection(GUILD_SETTINGS_COLLECTION).document(str(guild_id)).get()
    if not snapshot.exists:
        return None
    return snapshot.to_dict().get("party_channel_id")


@bot.tree.command(name="파티채널설정", description="(어드민) 파티 모집 명령어를 사용할 채널을 지정합니다")
@app_commands.describe(채널="파티 모집 전용 채널")
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
    await interaction.followup.send(
        f"✅ 파티 모집 채널을 {채널.mention}(으)로 설정했어요.\n"
        "이제 `/파티생성`은 이 채널에서만 사용할 수 있습니다.",
        ephemeral=True,
    )


@configure_party_channel.error
async def configure_party_channel_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


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
        self.selected_date = None
        self.selected_hour = None
        self.selected_minute = None
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
                [
                    discord.SelectOption(label="정각 (00분)", value="00"),
                    discord.SelectOption(label="30분", value="30"),
                ],
                row=2,
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

        return (
            f"🎮 **{party_type_name(self.max_members)} · {self.name}**의 시작 시간을 선택해주세요.\n"
            f"• 날짜: **{date_text}**\n"
            f"• 시간: **{time_text}**\n\n"
            "날짜·시간을 모두 선택한 뒤 `파티 생성`을 눌러주세요. "
            "모집글은 파티 시작 10시간 후 자동으로 삭제됩니다."
        )

    @discord.ui.button(label="파티 생성", style=discord.ButtonStyle.success, row=3)
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
            interaction, self.channel, self.name, scheduled_at, self.max_members
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

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=3)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="파티 생성을 취소했어요.", view=None)


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
            content=(
                f"🎮 **{party_type_name(max_members)} · {self.name}**을 선택했어요.\n"
                "이제 시작 날짜와 시간을 선택해주세요."
            ),
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


async def publish_party(interaction, channel, name, scheduled_at, max_members):
    creator_id = str(interaction.user.id)

    try:
        async with party_action_lock:
            active = await find_active_party_for_member(str(interaction.guild.id), creator_id)
            if active is not None:
                await interaction.followup.send(
                    "❌ 이미 참가 중인 파티가 있어요. 기존 파티에서 나오거나 파티를 삭제한 뒤 다시 만들어주세요.",
                    ephemeral=True,
                )
                return None

            now = utc_now()
            data = {
                "name": name,
                "creator_id": creator_id,
                "member_ids": [creator_id],
                "guild_id": str(interaction.guild.id),
                "channel_id": str(channel.id),
                "max_members": max_members,
                "scheduled_at": scheduled_at,
                "delete_at": scheduled_at + PARTY_DELETE_DELAY,
                "created_at": now,
                "updated_at": now,
                "status": "open",
            }

            embed = await build_party_embed(data, interaction.guild)
            message = await channel.send(embed=embed)
            data["message_id"] = str(message.id)
            party_ref = db.collection(PARTY_COLLECTION).document(str(message.id))
            try:
                await asyncio.to_thread(party_ref.set, data)
                await message.edit(embed=embed, view=PartyView(data))
            except Exception:
                await asyncio.to_thread(party_ref.delete)
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


@bot.tree.command(name="파티생성", description="5인 또는 내전 파티 모집글을 생성합니다")
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
        f"🎮 **{name}** 파티의 모집 유형을 선택해주세요.",
        view=view,
        ephemeral=True,
    )


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    # 관리자 등이 모집글을 직접 삭제한 경우 남은 DB 데이터가 참가를 막지 않도록 함께 정리
    party_ref = db.collection(PARTY_COLLECTION).document(str(payload.message_id))
    try:
        snapshot = await asyncio.to_thread(party_ref.get)
        if snapshot.exists:
            await asyncio.to_thread(party_ref.delete)
    except Exception:
        log.exception("삭제된 파티 모집글 DB 정리 실패 — message=%s", payload.message_id)


async def get_party_message(data):
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
            message, _ = await get_party_message(data)
            if message is None:
                continue
            embed = await build_party_embed(data, message.guild)
            await message.edit(embed=embed, view=PartyView(data))
        except discord.Forbidden:
            log.error("기존 파티 모집글 갱신 권한 없음 — party=%s", party_id)
        except Exception:
            log.exception("기존 파티 모집글 갱신 실패 — party=%s", party_id)


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
                message, reason = await get_party_message(data)
                if message is not None:
                    await message.delete()
                if reason != "guild_missing":
                    await asyncio.to_thread(party_ref.delete)
                continue

            if now >= scheduled_at and data.get("status") != "closed":
                data["status"] = "closed"
                data["updated_at"] = now
                await asyncio.to_thread(
                    party_ref.update,
                    {"status": "closed", "updated_at": now},
                )
                message, reason = await get_party_message(data)
                if message is not None:
                    embed = await build_party_embed(data, message.guild)
                    await message.edit(embed=embed, view=PartyView(data))
                elif reason in {"channel_missing", "message_missing"}:
                    await asyncio.to_thread(party_ref.delete)
        except discord.Forbidden:
            log.error("파티 모집글 정리 권한 없음 — party=%s", party_id)
        except Exception:
            log.exception("파티 자동 정리 실패 — party=%s", party_id)


@cleanup_parties.before_loop
async def before_cleanup_parties():
    await bot.wait_until_ready()


bot.run(TOKEN)
