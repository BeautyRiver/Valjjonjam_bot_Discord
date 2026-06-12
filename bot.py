import discord
from discord.ext import commands
from discord import app_commands
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import os
import asyncio

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

# 기본설정 안내 문구 (입장 안내 / 전체 안내 공용)
SETUP_GUIDE = (
    "👋 **발쫀잼 서버에 오신 걸 환영해요!**\n"
    "서버에서 `/기본설정` 을 입력해 **학번 · 이름 · 발로란트 닉네임 · 티어 · 역할군**을 등록해주세요.\n"
    "등록하면 역할과 닉네임이 자동으로 세팅됩니다! 🎮"
)

# 필독-규칙 채널 ID (채널 우클릭 → ID 복사. 0이면 링크 생략)
RULES_CHANNEL_ID = 1478315682503327824

# 규칙 채널 링크를 붙인 안내 문구 생성
def build_setup_guide(guild=None):
    if RULES_CHANNEL_ID:
        return SETUP_GUIDE + f"\n\n📜 먼저 <#{RULES_CHANNEL_ID}> 을(를) 꼭 확인해주세요!"
    return SETUP_GUIDE

@bot.event
async def on_ready():
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
class TierRoleView(discord.ui.View):
    def __init__(self, confirm_item, timeout=120):
        super().__init__(timeout=timeout)
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
        member = interaction.user

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
    def __init__(self):
        super().__init__(ConfirmButton(), timeout=60)

# 슬래시 명령어
@bot.tree.command(name="역할설정", description="역할군과 티어를 설정합니다")
async def set_role(interaction: discord.Interaction):
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

    if created:
        await interaction.followup.send(f"✅ {len(created)}개 역할 생성 완료!\n`{'`, `'.join(created)}`", ephemeral=True)
    else:
        await interaction.followup.send("이미 모든 역할이 존재합니다!", ephemeral=True)
        
class DeleteConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    @discord.ui.button(label="✅ 예", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        deleted = []

        all_roles = TIERS + ROLES
        for role in guild.roles:
            if role.name in all_roles:
                await role.delete()
                deleted.append(role.name)

        if deleted:
            await interaction.followup.send(f"🗑️ {len(deleted)}개 역할 삭제 완료!\n`{'`, `'.join(deleted)}`", ephemeral=True)
        else:
            await interaction.followup.send("삭제할 역할이 없습니다!", ephemeral=True)

    @discord.ui.button(label="❌ 아니요", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("취소했습니다.", ephemeral=True)

@bot.tree.command(name="역할삭제", description="역할군&티어 역할을 전부 삭제합니다")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def delete_roles(interaction: discord.Interaction):
    await interaction.response.send_message(
        "⚠️ 정말로 모든 역할군/티어를 삭제할까요?",
        view=DeleteConfirmView(),
        ephemeral=True
    )

@delete_roles.error
async def delete_roles_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 어드민만 사용 가능한 명령어입니다!", ephemeral=True)


@bot.tree.command(name="전체안내", description="아직 등록 안 한 멤버 전원에게 기본설정 안내 DM을 보냅니다")
@app_commands.describe(테스트="나에게만 미리 보내보기 (기본: 전체 발송)")
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def announce_setup(interaction: discord.Interaction, 테스트: bool = False):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    guide = build_setup_guide(guild)

    # 디버그: 나에게만 보내보기
    if 테스트:
        try:
            await interaction.user.send(guide)
            await interaction.followup.send("🧪 테스트: 본인에게만 안내 DM을 보냈어요!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 본인 DM이 막혀있어 못 보냈어요. DM 허용 후 다시 시도해주세요.", ephemeral=True)
        return

    # 이미 /기본설정 한 멤버는 제외 (DB에 등록된 ID 한 번에 조회)
    registered = await asyncio.to_thread(
        lambda: {doc.id for doc in db.collection("users").stream()}
    )

    sent, failed, skipped = 0, 0, 0
    for member in guild.members:
        if member.bot:
            continue
        if str(member.id) in registered:
            skipped += 1
            continue
        try:
            await member.send(guide)
            sent += 1
        except discord.Forbidden:
            failed += 1  # DM 차단된 멤버

    await interaction.followup.send(
        f"📣 안내 DM 전송 완료!\n"
        f"✅ 보냄: {sent}명 | ⏭️ 이미 등록: {skipped}명 | ❌ DM 차단: {failed}명",
        ephemeral=True
    )

@announce_setup.error
async def announce_setup_error(interaction: discord.Interaction, error):
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

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "마지막으로 티어와 역할군을 선택한 뒤 확인을 눌러주세요!",
            view=BasicSetupView(str(self.student_id), str(self.name), str(self.riot_id)),
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
        member = interaction.user

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

        # 닉네임 + 역할을 한 번의 호출로 적용 (닉네임 실패해도 역할은 적용되게 분리)
        new_nick = build_nickname(view.student_id, view.name, view.riot_id)
        keep = [r for r in member.roles if r.name not in managed and not r.is_default()]
        nick_failed = False
        try:
            await member.edit(roles=keep + to_add, nick=new_nick)
        except discord.Forbidden:
            # 닉네임 권한 문제일 수 있으니 역할만 따로 적용
            nick_failed = True
            await member.edit(roles=keep + to_add)

        msg = (
            f"✅ **{view.name}** 님 설정 완료!\n"
            f"학번: `{view.student_id}` | 발로ID: `{view.riot_id}`\n"
            f"티어: `{tier}` | 역할군: `{', '.join(view.selected_roles)}`"
        )
        if nick_failed:
            msg += "\n⚠️ 닉네임 변경 권한이 없어 닉네임은 바꾸지 못했어요. (봇 역할을 멤버보다 위로 올리고 '닉네임 관리' 권한을 주세요)"
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
            }
        )

# 2단계 View (모달 입력값을 들고 다님)
class BasicSetupView(TierRoleView):
    def __init__(self, student_id, name, riot_id):
        super().__init__(BasicSetupConfirmButton(), timeout=120)
        self.student_id = student_id
        self.name = name
        self.riot_id = riot_id

@bot.tree.command(name="기본설정", description="학번/이름/발로란트 닉네임과 티어·역할군을 설정합니다")
async def basic_setup(interaction: discord.Interaction):
    await interaction.response.send_modal(BasicSetupModal())


# ===== /닉네임변경 =====

# 학번/이름/발로닉만 받아 디스코드 닉네임을 "학번 이름 / 발로닉" 으로 변경
class NicknameModal(discord.ui.Modal):
    def __init__(self, student_id="", name="", riot_id=""):
        super().__init__(title="닉네임 변경")
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
        member = interaction.user
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
    # 기존에 저장된 정보가 있으면 모달에 미리 채워줌
    doc = db.collection("users").document(str(interaction.user.id)).get()
    data = doc.to_dict() if doc.exists else {}
    await interaction.response.send_modal(NicknameModal(
        data.get("student_id", ""),
        data.get("name", ""),
        data.get("riot_id", ""),
    ))


bot.run(TOKEN)