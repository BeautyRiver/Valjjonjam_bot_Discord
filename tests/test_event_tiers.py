"""Run: python -m unittest discover -s tests -v (discord.py required).

Only allowlisted definitions are loaded from bot.py: no bot startup, credentials,
Firebase client, or network connection. Discord UI objects use the real SDK;
interactions and Firestore reads/writes are local test doubles.
"""

import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord


CONSTANTS = {
    "TIERS", "MAJOR_TIERS", "NO_DIVISION", "ROLES", "VERIFIED_ROLE",
    "INTERNAL_TIER_LABELS", "INTERNAL_TIER_SCORES",
    "EVENT_TIER_MODES", "EVENT_PUBLIC_MEMBER_LIMIT", "EVENT_TEAM_SIZE",
    "EVENT_CONFIRM_UNIT", "EVENT_REFRESH_WINDOW", "PARTY_TIMEZONE",
    "EVENT_COLLECTION", "TOOLKIT_RECRUITMENT_COLLECTION",
    "ROLE_FAILED_NOTICE", "NICKNAME_FORBIDDEN_NOTICE", "NICKNAME_FAILED_NOTICE",
    "DB_SAVE_FAILED_NOTICE", "recruitment_channel_ids", "recruitment_channel_loaded_guilds",
    "PARTY_COLLECTION", "PARTY_MAX_MEMBERS", "INHOUSE_PARTY_MAX_MEMBERS", "SUPPORTED_PARTY_SIZES",
    "PARTY_DELETE_DELAY", "PARTY_PUBLIC_WAITLIST_LIMIT", "PARTY_PREFERRED_TIER_LABELS",
}
DEFINITIONS = {
    "remember_recruitment_channel", "is_recruitment_channel", "on_app_command_error",
    "block_if_bot_target", "update_internal_tier",
    "party_max_members", "party_type_name", "party_preferred_tier_name", "party_status",
    "join_party_transaction", "leave_party_transaction", "get_party_data", "build_party_embed",
    "PartyView", "get_event_data", "delete_recruitment", "DeleteConfirmView",
    "EventMemberRemoveView",
    "make_tier", "resolve_tier", "build_nickname", "get_or_create_verified_role",
    "apply_tier_roles", "apply_nickname", "save_user_profile",
    "MajorTierSelect", "DivisionSelect", "RoleSelect", "TierRoleView",
    "ConfirmButton", "RoleSelectView", "BasicSetupConfirmButton", "BasicSetupView",
    "NicknameModal", "list_open_events", "list_recent_events",
    "utc_now", "as_utc", "event_tier_choices", "event_tier_score",
    "validate_event_tier_restriction", "event_tier_restriction_text",
    "event_tier_join_error", "event_status", "escape_display_text",
    "build_toolkit_recruitment_data", "get_toolkit_recruitment_ref",
    "set_recruitment_pair", "delete_recruitment_pair", "join_event_transaction",
    "leave_event_transaction", "set_event_registration_transaction",
    "split_event_members", "event_member_state_text", "event_member_lines",
    "event_team_progress", "build_event_embed",
    "make_event_date_options", "make_party_hour_options", "make_party_minute_options",
    "PartyCreationSelect", "EventTierSelect", "EventFeeSelectionView",
    "EventDetailsModal", "EventCreationView", "EventView", "publish_event",
}
source_path = Path(__file__).resolve().parents[1] / "bot.py"
tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
nodes = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in CONSTANTS for target in node.targets
    ):
        nodes.append(node)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in DEFINITIONS:
        node.decorator_list = []  # Firestore transaction wrapper is not needed by the local fake.
        nodes.append(node)
ns = {
    "discord": discord, "asyncio": asyncio, "datetime": datetime,
    "timedelta": timedelta, "timezone": timezone, "log": logging.getLogger(__name__),
    "firestore": SimpleNamespace(SERVER_TIMESTAMP="SERVER_TIMESTAMP"),
    "app_commands": discord.app_commands,
}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source_path), "exec"), ns)


def restriction(mode="internal", minimum="5하", maximum="3상"):
    return {"mode": mode, "min": minimum, "max": maximum}


class FakeDocument:
    def __init__(self, database, collection, document_id):
        self.database = database
        self.path = f"{collection}/{document_id}"
        self.id = str(document_id)

    def get(self, transaction=None):
        if transaction:
            assert not transaction.writes, "Firestore requires all reads before writes"
            transaction.reads.append(self.path)
        data = deepcopy(self.database.documents.get(self.path))
        return SimpleNamespace(exists=data is not None, to_dict=lambda: deepcopy(data))


class FakeTransaction:
    def __init__(self, database):
        self.database = database
        self.reads = []
        self.writes = []

    def update(self, ref, data):
        self.writes.append(("update", ref.path, deepcopy(data)))
        self.database.documents[ref.path].update(deepcopy(data))

    def set(self, ref, data):
        self.writes.append(("set", ref.path, deepcopy(data)))
        self.database.documents[ref.path] = deepcopy(data)

    def delete(self, ref):
        self.writes.append(("delete", ref.path, None))
        self.database.documents.pop(ref.path, None)

    def commit(self):
        pass


class FakeDatabase:
    def __init__(self):
        self.documents = {}
        self.last_transaction = None

    def collection(self, name):
        return SimpleNamespace(document=lambda doc_id: FakeDocument(self, name, doc_id))

    def transaction(self):
        self.last_transaction = FakeTransaction(self)
        return self.last_transaction

    batch = transaction


def interaction(user_id=42):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, guild_permissions=SimpleNamespace(administrator=True)),
        guild=SimpleNamespace(id=100),
        response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock(),
            send_modal=AsyncMock(), defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=SimpleNamespace(id=99),
    )


def event_data(condition=None):
    return {
        "name": "테스트 발쫀전", "creator_id": "1", "member_ids": [],
        "guild_id": "100", "channel_id": "200", "status": "open",
        "scheduled_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "prize": "테스트 상품", "has_entry_fee": False,
        "entry_fee": None, "bank_holder": None, "account_number": None,
        "rules": "테스트 안내", "tier_restriction": condition,
    }


class TierRuleTests(unittest.TestCase):
    def test_every_valorant_range_includes_all_divisions_at_both_ends(self):
        majors = ["아이언", "브론즈", "실버", "골드", "플래티넘", "다이아", "초월자", "불멸", "레디언트"]
        for low, minimum in enumerate(majors):
            for high in range(low, len(majors)):
                condition = restriction("valorant", minimum, majors[high])
                self.assertIsNone(ns["validate_event_tier_restriction"](condition))
                for rank, major in enumerate(majors):
                    tiers = [major] if major == "레디언트" else [f"{major} {d}" for d in (1, 2, 3)]
                    for tier in tiers:
                        with self.subTest(condition=condition, tier=tier):
                            error = ns["event_tier_join_error"](condition, {"tier": tier})
                            self.assertEqual(error is None, low <= rank <= high)

    def test_every_internal_range_and_question_mark_equivalence(self):
        expected = {"1": 17}
        for tier, lower in ((2, 13), (3, 9), (4, 5), (5, 1)):
            expected.update({f"{tier}하": lower, f"{tier}?": lower, f"{tier}중": lower + 1, f"{tier}상": lower + 2})
        bounds = ns["event_tier_choices"]("internal")
        self.assertEqual(len(bounds), 13)
        for minimum in bounds:
            for maximum in bounds:
                condition = restriction("internal", minimum, maximum)
                if expected[minimum] > expected[maximum]:
                    self.assertIsNotNone(ns["validate_event_tier_restriction"](condition))
                    continue
                for tier, score in expected.items():
                    with self.subTest(condition=condition, tier=tier):
                        error = ns["event_tier_join_error"](condition, {"internal_tier": tier})
                        self.assertEqual(error is None, expected[minimum] <= score <= expected[maximum])

    def test_missing_unclassified_unranked_and_unknown_tiers_are_blocked(self):
        for mode, field, command in (("internal", "internal_tier", "/내부티어설정"), ("valorant", "tier", "/역할설정")):
            condition = restriction() if mode == "internal" else restriction(mode, "아이언", "레디언트")
            for tier in (None, "", "unclassified", "언랭", "잘못된 티어", 1, [], "다이아4"):
                with self.subTest(mode=mode, tier=tier):
                    self.assertIn(command, ns["event_tier_join_error"](condition, {field: tier}))
            self.assertIn(command, ns["event_tier_join_error"](condition, None))

    def test_invalid_and_reversed_conditions_are_rejected(self):
        invalid = [
            {}, "internal", {"mode": "unknown"}, restriction("internal", None, "3상"),
            restriction("internal", "1", "5하"), restriction("valorant", "다이아", "아이언"),
            restriction("valorant", "언랭", "다이아"), restriction("internal", "5하", []),
        ]
        for condition in invalid:
            with self.subTest(condition=condition):
                self.assertIsNotNone(ns["validate_event_tier_restriction"](condition))
                self.assertIn("운영진", ns["event_tier_join_error"](condition, {}))

    def test_free_events_and_only_selected_tier_axis(self):
        for condition in (None, {"mode": "none"}):
            self.assertIsNone(ns["event_tier_join_error"](condition, None))
        self.assertIsNone(ns["event_tier_join_error"](restriction(), {"tier": "레디언트", "internal_tier": "5?"}))
        self.assertIsNone(ns["event_tier_join_error"](restriction("valorant", "아이언", "다이아"), {"tier": "다이아 3", "internal_tier": "1"}))
        self.assertEqual(ns["event_tier_score"]("valorant", "초월자1"), ns["event_tier_score"]("valorant", "초월자 1"))


class EventEvenTeamTests(unittest.TestCase):
    def test_members_are_confirmed_only_in_units_of_two_teams(self):
        for count, confirmed in ((0, 0), (7, 0), (10, 10), (13, 10), (15, 10), (19, 10), (20, 20), (25, 20)):
            member_ids = [str(index) for index in range(count)]
            with self.subTest(count=count):
                confirmed_ids, waitlist_ids = ns["split_event_members"](member_ids)
                self.assertEqual(confirmed_ids, member_ids[:confirmed])
                self.assertEqual(waitlist_ids, member_ids[confirmed:])
                self.assertEqual(len(confirmed_ids) // 5 % 2, 0)  # 홀수 팀이 확정되지 않음

    def test_team_progress_and_member_state_text(self):
        self.assertEqual(ns["event_team_progress"](7), "확정된 팀 없음 · 2팀 확정까지 3명 남음")
        self.assertEqual(ns["event_team_progress"](10), "2팀 확정 (10명) · 4팀까지 10명 남음")
        self.assertEqual(ns["event_team_progress"](13), "2팀 확정 (10명) · 대기 3명 · 4팀까지 7명 남음")
        member_ids = [str(index) for index in range(13)]
        self.assertIn("참가 확정", ns["event_member_state_text"](member_ids, "9"))
        self.assertIn("대기 2번", ns["event_member_state_text"](member_ids, "11"))

    def test_cancellation_moves_next_waiting_member_into_confirmed(self):
        member_ids = [str(index) for index in range(13)]
        member_ids.remove("3")
        confirmed_ids, waitlist_ids = ns["split_event_members"](member_ids)
        self.assertEqual(confirmed_ids[-1], "10")
        self.assertEqual(waitlist_ids, ["11", "12"])

    def test_mirror_splits_events_but_keeps_party_waitlist(self):
        event = event_data()
        event["member_ids"] = [str(index) for index in range(13)]
        mirror = ns["build_toolkit_recruitment_data"]("event", "99", event)
        self.assertEqual(mirror["member_ids"], event["member_ids"][:10])
        self.assertEqual(mirror["waitlist_ids"], ["10", "11", "12"])
        self.assertIsNone(mirror["max_members"])
        for field in ("prize", "rules", "entry_fee", "bank_holder", "account_number"):
            self.assertNotIn(field, mirror)


class EventTransactionTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.db.documents["events/99"] = event_data(restriction())
        self.db.documents["users/42"] = {"internal_tier": "5?"}
        self.patch = patch.dict(ns, {"db": self.db})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def join(self):
        return ns["join_event_transaction"](
            self.db.transaction(), self.db.collection("events").document("99"),
            self.db.collection("toolkit_recruitments").document("event_99"),
            "42", datetime.now(timezone.utc),
        )

    def test_allowed_join_reads_tier_in_same_transaction_and_mirrors_condition(self):
        result, data, error = self.join()
        self.assertEqual(result, "joined")
        self.assertIsNone(error)
        self.assertEqual(self.db.last_transaction.reads, ["events/99", "users/42"])
        self.assertEqual(data["member_ids"], ["42"])
        mirror = self.db.documents["toolkit_recruitments/event_99"]
        # 10명 미만이면 원본에는 신청 순서로 남고, 미러에서는 대기로 전달됩니다.
        self.assertEqual(mirror["member_ids"], [])
        self.assertEqual(mirror["waitlist_ids"], ["42"])
        self.assertEqual(mirror["tier_restriction"], restriction())
        self.assertNotIn("account_number", mirror)

    def test_out_of_range_or_missing_member_never_writes(self):
        for profile in ({"internal_tier": "2하"}, {}, None):
            if profile is None:
                self.db.documents.pop("users/42", None)
            else:
                self.db.documents["users/42"] = profile
            result, data, error = self.join()
            self.assertEqual(result, "tier_restricted")
            self.assertTrue(error)
            self.assertEqual(data["member_ids"], [])
            self.assertEqual(self.db.last_transaction.writes, [])
        self.db.documents["users/42"] = {"internal_tier": "3상"}
        self.assertEqual(self.join()[0], "joined")  # Updated DB tier is used on retry.

    def test_legacy_and_free_events_do_not_require_a_tier_read(self):
        self.db.documents.pop("users/42")
        for condition in (None, {"mode": "none"}):
            data = event_data(condition)
            if condition is None:
                data.pop("tier_restriction")
            self.db.documents["events/99"] = data
            self.assertEqual(self.join()[0], "joined")
            self.assertEqual(self.db.last_transaction.reads, ["events/99"])

    def test_existing_member_closed_missing_and_corrupt_conditions(self):
        self.assertEqual(self.join()[0], "joined")
        self.db.documents["users/42"]["internal_tier"] = "1"
        self.assertEqual(self.join()[0], "already")
        self.assertEqual(self.db.last_transaction.writes, [])
        self.db.documents["events/99"]["status"] = "closed"
        self.assertEqual(self.join()[0], "closed")
        self.db.documents["events/99"] = event_data({"mode": "broken"})
        self.assertEqual(self.join()[0], "tier_restricted")
        self.assertEqual(self.db.last_transaction.writes, [])
        self.db.documents.pop("events/99")
        self.assertEqual(self.join(), ("missing", None, None))

    def test_member_can_leave_after_tier_changes(self):
        self.join()
        self.db.documents["users/42"]["internal_tier"] = "1"
        result, data = ns["leave_event_transaction"](
            self.db.transaction(), self.db.collection("events").document("99"),
            self.db.collection("toolkit_recruitments").document("event_99"),
            "42", datetime.now(timezone.utc),
        )
        self.assertEqual(result, "left")
        self.assertEqual(data["member_ids"], [])


class EventUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDatabase()
        self.message = SimpleNamespace(id=99, jump_url="https://example.test/event", edit=AsyncMock())
        self.channel = SimpleNamespace(id=200, send=AsyncMock(return_value=self.message))
        self.patch = patch.dict(ns, {
            "db": self.db, "event_action_lock": asyncio.Lock(),
            "get_party_member_names": AsyncMock(return_value={"1": "주최자", "42": "참가자"}),
            "block_if_not_in_guild": AsyncMock(return_value=False),
            "block_if_unverified": AsyncMock(return_value=False),
            "try_edit_recruitment_message": AsyncMock(return_value=True),
            "parse_event_time": Mock(return_value=(datetime.now(timezone.utc) + timedelta(hours=1), None)),
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def choose(self, view, field, value):
        item = next(child for child in view.children if getattr(child, "kind", None) == field)
        item._values = [value]  # Simulates the SDK-populated interaction selection.
        await item.callback(interaction())

    def check_components(self, view, rows):
        components = view.to_components()
        self.assertEqual(len(components), rows)
        for row in components:
            for item in row["components"]:
                if item["type"] == 3:
                    self.assertLessEqual(len(item["options"]), 25)
                    self.assertLessEqual(sum(option.get("default", False) for option in item["options"]), 1)

    async def test_default_mode_switching_and_selection_defaults(self):
        view = ns["EventFeeSelectionView"](42, "테스트", self.channel)
        self.assertEqual(view.tier_mode, "none")
        self.check_components(view, 2)
        await self.choose(view, "tier_mode", "internal")
        self.check_components(view, 4)
        await self.choose(view, "tier_min", "5하")
        await self.choose(view, "tier_max", "3상")
        self.assertEqual(view.tier_restriction(), restriction())
        for field, value in (("tier_min", "5하"), ("tier_max", "3상")):
            select = next(child for child in view.children if getattr(child, "kind", None) == field)
            self.assertEqual([option.value for option in select.options if option.default], [value])
        await self.choose(view, "tier_mode", "valorant")
        self.assertIsNone(view.tier_min)
        self.assertIsNone(view.tier_max)
        self.check_components(view, 4)
        await self.choose(view, "tier_mode", "none")
        self.check_components(view, 2)
        self.assertIn("제한 없음", view.render_content())

    async def test_incomplete_and_reversed_range_cannot_open_details(self):
        view = ns["EventFeeSelectionView"](42, "테스트", self.channel)
        await self.choose(view, "tier_mode", "internal")
        for minimum, maximum in ((None, None), ("1", "5하")):
            view.tier_min, view.tier_max = minimum, maximum
            call = interaction()
            await view.no_entry_fee.callback(call)
            call.response.send_message.assert_awaited_once()
            call.response.send_modal.assert_not_awaited()
            self.assertFalse(view.started)
        self.assertFalse(await view.interaction_check(interaction(user_id=43)))

    async def test_full_creation_flow_for_both_fee_paths_and_all_modes(self):
        for mode in ("none", "valorant", "internal"):
            for has_fee in (False, True):
                with self.subTest(mode=mode, fee=has_fee):
                    view = ns["EventFeeSelectionView"](42, "테스트", self.channel)
                    if mode != "none":
                        await self.choose(view, "tier_mode", mode)
                        await self.choose(view, "tier_min", "아이언" if mode == "valorant" else "5하")
                        await self.choose(view, "tier_max", "다이아" if mode == "valorant" else "3상")
                    call = interaction()
                    button = view.has_entry_fee if has_fee else view.no_entry_fee
                    await button.callback(call)
                    modal = call.response.send_modal.call_args.args[0]
                    self.assertEqual(modal.tier_restriction, view.tier_restriction())
                    self.assertEqual(len(modal.children), 5 if has_fee else 2)
                    self.assertFalse(await view.interaction_check(interaction()))
                    for item in modal.children:
                        item._value = "테스트 입력"
                    submitted = interaction()
                    await modal.on_submit(submitted)
                    creation = submitted.response.send_message.call_args.kwargs["view"]
                    self.check_components(creation, 4)
                    self.assertEqual(creation.details["tier_restriction"], view.tier_restriction())
                    creation.selected_date, creation.selected_hour, creation.selected_minute = "2026-11-03", "21", "00"
                    confirm = interaction()
                    await creation.confirm.callback(confirm)
                    confirm.edit_original_response.assert_awaited_once()
                    saved = self.db.documents["events/99"]
                    self.assertEqual(saved["tier_restriction"], view.tier_restriction())
                    self.assertEqual(saved["has_entry_fee"], has_fee)
                    mirror = self.db.documents["toolkit_recruitments/event_99"]
                    self.assertEqual(mirror["tier_restriction"], saved["tier_restriction"])
                    embed = self.channel.send.call_args.kwargs["embed"]
                    text = next(field.value for field in embed.fields if "참가 티어 조건" in field.name)
                    self.assertIn({"none": "제한 없음", "valorant": "최대 티어", "internal": "?는"}[mode], text)

    async def test_creation_is_revalidated_before_sending_or_saving(self):
        data = event_data(restriction("internal", "1", "5하"))
        view = ns["EventCreationView"](42, "테스트", self.channel, data)
        call = interaction()
        await view.confirm.callback(call)
        call.response.send_message.assert_awaited_once()
        self.assertIsNone(await ns["publish_event"](call, self.channel, "테스트", data["scheduled_at"], data))
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.db.documents, {})

    async def test_participation_button_sends_private_rejection_or_success(self):
        for tier, expected in ((None, "내부티어설정"), ("2상", "내 등록 티어"), ("5?", "완료")):
            self.db.documents["events/99"] = event_data(restriction())
            self.db.documents["users/42"] = {"internal_tier": tier}
            call = interaction()
            await ns["EventView"]().join_event.callback(call)
            call.followup.send.assert_awaited_once()
            self.assertIn(expected, call.followup.send.call_args.args[0])
            self.assertTrue(call.followup.send.call_args.kwargs["ephemeral"])
            self.assertEqual(self.db.documents["events/99"]["member_ids"], ["42"] if tier == "5?" else [])
        self.assertIn("대기 1번", call.followup.send.call_args.args[0])

    async def test_embed_splits_confirmed_and_waitlist_without_format_advice(self):
        data = event_data()
        data.update(member_ids=[str(index) for index in range(10, 23)], has_entry_fee=True)
        names = AsyncMock(side_effect=lambda guild, ids: {member_id: f"멤버{member_id}" for member_id in ids})
        with patch.dict(ns, {"get_party_member_names": names}):
            embed = await ns["build_event_embed"](data, None)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("1. 멤버10", fields["✅ 참가 확정 (10명)"])
        self.assertNotIn("멤버20", fields["✅ 참가 확정 (10명)"])
        self.assertEqual(fields["⏳ 대기 (3명)"], "1. 멤버20\n2. 멤버21\n3. 멤버22")
        self.assertIn("대기 3명", fields["팀 구성 현황"])
        self.assertIn("참가 확정 명단에 있는 분만", fields["💳 참가비"])
        self.assertFalse(any("권장" in name for name in fields))


def http_error(cls=discord.HTTPException, status=400):
    return cls(SimpleNamespace(status=status, reason="test"), "test error")


class FakeUsersDatabase:
    def __init__(self, fail=False):
        self.saved = {}
        self.fail = fail

    def collection(self, name):
        def document(doc_id):
            def set_document(data, merge=False):
                if self.fail:
                    raise RuntimeError("Firestore unavailable")
                self.saved[f"{name}/{doc_id}"] = data
            return SimpleNamespace(set=set_document)
        return SimpleNamespace(document=document)


class MemberProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.roles = {name: SimpleNamespace(name=name) for name in ("실버 1", "골드 2", "타격대", "인증됨")}
        self.member = SimpleNamespace(
            id=42, name="tester", display_name="테스터", roles=[self.roles["실버 1"]],
            add_roles=AsyncMock(), remove_roles=AsyncMock(), edit=AsyncMock(),
        )
        self.db = FakeUsersDatabase()
        self.log = Mock()
        self.patch = patch.dict(ns, {"db": self.db, "log": self.log})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def call(self):
        call = interaction()
        call.guild = SimpleNamespace(id=100, roles=list(self.roles.values()))
        return call

    def content(self, call):
        return call.edit_original_response.call_args.kwargs["content"]

    async def confirm(self, view):
        view.major_tier, view.division, view.selected_roles = "골드", "2", ["타격대"]
        button = next(child for child in view.children if isinstance(child, discord.ui.Button))
        call = self.call()
        await button.callback(call)
        return call

    async def submit_nickname(self):
        modal = ns["NicknameModal"](self.member, "21", "홍길동", "test#kr1")
        for item, value in ((modal.student_id, "21"), (modal.name, "홍길동"), (modal.riot_id, "test#kr1")):
            item._value = value
        call = self.call()
        await modal.on_submit(call)
        return call

    async def test_nickname_rejected_by_discord_still_saves_and_explains(self):
        self.member.edit.side_effect = http_error()
        content = self.content(await self.submit_nickname())
        self.assertIn("`discord`", content)
        self.assertIn("정보는 저장했어요", content)
        self.assertEqual(self.db.saved["users/42"]["name"], "홍길동")

    async def test_nickname_without_permission_and_success_messages(self):
        self.member.edit.side_effect = http_error(discord.Forbidden, 403)
        self.assertIn("권한이 없어요", self.content(await self.submit_nickname()))
        self.member.edit.side_effect = None
        self.assertIn("변경했어요", self.content(await self.submit_nickname()))

    async def test_role_setup_failure_is_reported_and_profile_is_saved(self):
        self.member.remove_roles.side_effect = http_error(discord.Forbidden, 403)
        call = await self.confirm(ns["RoleSelectView"](self.member))
        self.assertIn("일부 역할을 적용하지 못했어요", self.content(call))
        self.member.remove_roles.assert_awaited_once_with(self.roles["실버 1"])
        self.assertEqual(self.db.saved["users/42"]["tier"], "골드 2")
        self.assertEqual(self.db.saved["users/42"]["role"], ["타격대"])

    async def test_db_failure_is_shown_instead_of_silent_success(self):
        self.db.fail = True
        content = self.content(await self.confirm(ns["RoleSelectView"](self.member)))
        self.assertIn("설정 완료", content)
        self.assertIn("DB에 저장하지 못했어요", content)
        self.log.exception.assert_called_once()
        self.member.add_roles.assert_awaited_once_with(self.roles["골드 2"], self.roles["타격대"])

    async def test_basic_setup_grants_verified_role_even_if_nickname_fails(self):
        self.member.edit.side_effect = http_error()
        view = ns["BasicSetupView"]("21", "홍길동", "test#kr1", self.member)
        content = self.content(await self.confirm(view))
        self.member.add_roles.assert_awaited_once_with(
            self.roles["골드 2"], self.roles["타격대"], self.roles["인증됨"]
        )
        self.assertIn("닉네임은 바꾸지 못했어요", content)
        self.assertEqual(self.db.saved["users/42"]["riot_id"], "test#kr1")


class EventQueryTests(unittest.TestCase):
    def test_event_loops_read_only_open_or_recent_events(self):
        db = Mock()
        where = db.collection.return_value.where
        where.return_value.stream.return_value = [SimpleNamespace(id="1", to_dict=lambda: {"status": "open"})]
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        with patch.dict(ns, {"db": db, "FieldFilter": lambda *args: args}):
            self.assertEqual(ns["list_open_events"](), [("1", {"status": "open"})])
            where.assert_called_with(filter=("status", "==", "open"))
            ns["list_recent_events"](now)
            where.assert_called_with(filter=("scheduled_at", ">=", now - timedelta(days=1)))
        db.collection.assert_called_with("events")


def party_data(max_members=5, scheduled_in=timedelta(hours=1)):
    return {
        "name": "테스트 파티", "creator_id": "1", "member_ids": ["1"], "waitlist_ids": [],
        "guild_id": "100", "channel_id": "200", "message_id": "99", "status": "open",
        "max_members": max_members, "preferred_tier": "free",
        "scheduled_at": datetime.now(timezone.utc) + scheduled_in,
    }


class PartyTransactionTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.db.documents["parties/99"] = party_data()
        self.patch = patch.dict(ns, {"db": self.db})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def refs(self):
        return (
            self.db.transaction(), self.db.collection("parties").document("99"),
            self.db.collection("toolkit_recruitments").document("party_99"),
        )

    def join(self, user_id):
        return ns["join_party_transaction"](*self.refs(), user_id, datetime.now(timezone.utc))

    def leave(self, user_id):
        return ns["leave_party_transaction"](*self.refs(), user_id, datetime.now(timezone.utc))

    def test_full_party_waitlists_and_promotes_in_fifo_order(self):
        for user_id in ("2", "3", "4", "5"):
            self.assertEqual(self.join(user_id)[0], "joined")
        self.assertEqual(self.join("6")[::2], ("waitlisted", 1))
        self.assertEqual(self.join("7")[::2], ("waitlisted", 2))
        self.assertEqual(self.join("6")[::2], ("already_waitlist", 1))
        self.assertEqual(self.join("3")[0], "already_member")

        result, data, promoted_id = self.leave("3")
        self.assertEqual((result, promoted_id), ("left_promoted", "6"))
        self.assertEqual(data["member_ids"], ["1", "2", "4", "5", "6"])
        self.assertEqual(data["waitlist_ids"], ["7"])
        self.assertEqual(data["status"], "full")
        mirror = self.db.documents["toolkit_recruitments/party_99"]
        self.assertEqual((mirror["member_ids"], mirror["waitlist_ids"]), (data["member_ids"], ["7"]))

    def test_creator_waitlist_and_unknown_leave_results(self):
        for user_id in ("2", "3", "4", "5", "6"):
            self.join(user_id)
        self.assertEqual(self.leave("1")[0], "creator")
        self.assertEqual(self.leave("6")[0], "waitlist_left")
        self.assertEqual(self.leave("8")[0], "not_member")
        result, data, promoted_id = self.leave("2")
        self.assertEqual((result, promoted_id, data["status"]), ("left", None, "open"))

    def test_started_party_rejects_join_and_leave(self):
        self.db.documents["parties/99"] = party_data(scheduled_in=-timedelta(minutes=1))
        self.assertEqual(self.join("2")[0], "closed")
        self.assertEqual(self.leave("1")[0], "closed")
        self.assertEqual(self.db.last_transaction.writes, [])

    def test_inhouse_party_holds_ten_before_waitlisting(self):
        self.db.documents["parties/99"] = party_data(max_members=10)
        results = [self.join(str(user_id))[0] for user_id in range(2, 12)]
        self.assertEqual(results, ["joined"] * 9 + ["waitlisted"])


class RecruitmentManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDatabase()
        self.message = SimpleNamespace(id=99, guild=None, delete=AsyncMock(), edit=AsyncMock())
        self.log = Mock()
        self.patch = patch.dict(ns, {
            "db": self.db, "log": self.log, "party_action_lock": asyncio.Lock(),
            "event_action_lock": asyncio.Lock(),
            "block_if_not_in_guild": AsyncMock(return_value=False),
            "block_if_unverified": AsyncMock(return_value=False),
            "get_party_member_names": AsyncMock(side_effect=lambda guild, ids: {i: f"멤버{i}" for i in ids}),
            "try_edit_recruitment_message": AsyncMock(return_value=True),
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def store_party(self):
        self.db.documents["parties/99"] = party_data()
        self.db.documents["toolkit_recruitments/party_99"] = {"kind": "party"}

    async def test_party_delete_button_asks_before_deleting(self):
        self.store_party()
        call = interaction()
        call.message = self.message
        await ns["PartyView"]().delete_party.callback(call)
        view = call.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(view, ns["DeleteConfirmView"])
        self.assertIn("삭제할까요", call.followup.send.call_args.args[0])
        self.message.delete.assert_not_awaited()
        self.assertIn("parties/99", self.db.documents)

    async def test_delete_confirm_and_cancel(self):
        self.store_party()
        cancel_view = ns["DeleteConfirmView"](42, self.message, "party", "99", asyncio.Lock())
        await cancel_view.cancel.callback(interaction())
        self.message.delete.assert_not_awaited()
        self.assertFalse(await cancel_view.interaction_check(interaction(user_id=7)))

        view = ns["DeleteConfirmView"](42, self.message, "party", "99", asyncio.Lock())
        call = interaction()
        await view.confirm.callback(call)
        self.message.delete.assert_awaited_once()
        self.assertNotIn("parties/99", self.db.documents)
        self.assertNotIn("toolkit_recruitments/party_99", self.db.documents)
        self.assertIn("삭제했어요", call.edit_original_response.call_args.kwargs["content"])

    async def test_waitlisted_member_sees_position_when_joining_again(self):
        data = party_data()
        data.update(member_ids=["1", "2", "3", "4", "5"], waitlist_ids=["8", "42"])
        self.db.documents["parties/99"] = data
        call = interaction()
        await ns["PartyView"]().join_party.callback(call)
        self.assertIn("대기 명단 2번", call.followup.send.call_args.args[0])

    async def test_admin_can_remove_event_member_even_after_close(self):
        data = event_data()
        data.update(member_ids=["10", "42", "11"], status="closed")
        self.db.documents["events/99"] = data
        view = ns["EventMemberRemoveView"](1, self.message)
        self.assertTrue(view.remove.disabled)
        view.target = SimpleNamespace(id=42, display_name="참가자")
        call = interaction()
        await view.remove.callback(call)
        self.assertEqual(self.db.documents["events/99"]["member_ids"], ["10", "11"])
        self.assertEqual(self.db.documents["toolkit_recruitments/event_99"]["waitlist_ids"], ["10", "11"])
        self.assertIn("제외했어요", call.edit_original_response.call_args.kwargs["content"])

        view = ns["EventMemberRemoveView"](1, self.message)
        view.target = SimpleNamespace(id=42, display_name="참가자")
        call = interaction()
        await view.remove.callback(call)
        self.assertIn("참가하고 있지 않아요", call.edit_original_response.call_args.kwargs["content"])

    async def test_member_cannot_leave_closed_event_by_themselves(self):
        data = event_data()
        data.update(member_ids=["42"], status="closed")
        self.db.documents["events/99"] = data
        result, _ = ns["leave_event_transaction"](
            self.db.transaction(), self.db.collection("events").document("99"),
            self.db.collection("toolkit_recruitments").document("event_99"),
            "42", datetime.now(timezone.utc),
        )
        self.assertEqual(result, "closed")

    async def test_message_delete_checks_only_recruitment_channels(self):
        lookup = Mock(return_value="200")
        with patch.dict(ns, {
            "get_party_channel_id": lookup, "recruitment_channel_ids": set(),
            "recruitment_channel_loaded_guilds": set(),
        }):
            self.assertTrue(await ns["is_recruitment_channel"](100, 200))
            self.assertFalse(await ns["is_recruitment_channel"](100, 300))
            self.assertFalse(await ns["is_recruitment_channel"](None, 200))
            lookup.assert_called_once_with("100")
            ns["remember_recruitment_channel"](300)
            self.assertTrue(await ns["is_recruitment_channel"](100, 300))

    async def test_command_errors_are_answered_and_logged(self):
        call = interaction()
        call.response.is_done = Mock(return_value=False)
        await ns["on_app_command_error"](call, discord.app_commands.MissingPermissions(["administrator"]))
        self.assertIn("어드민만", call.response.send_message.call_args.args[0])
        self.log.error.assert_not_called()

        call = interaction()
        call.response.is_done = Mock(return_value=True)
        call.command = SimpleNamespace(name="역할생성")
        await ns["on_app_command_error"](call, discord.app_commands.AppCommandError("boom"))
        self.assertIn("오류가 발생했어요", call.followup.send.call_args.args[0])
        self.log.error.assert_called_once()

    async def test_internal_tier_set_and_reset_share_one_flow(self):
        db = Mock()
        ref = db.collection.return_value.document.return_value
        ref.get.return_value = SimpleNamespace(exists=True, to_dict=lambda: {"name": "홍길동"})
        target = SimpleNamespace(id=42, bot=False, display_name="홍길동")
        with patch.dict(ns, {"db": db}):
            call = interaction()
            await ns["update_internal_tier"](call, target, "3상")
            ref.set.assert_called_with({"internal_tier": "3상", "updated_at": "SERVER_TIMESTAMP"}, merge=True)
            self.assertIn("11점", call.followup.send.call_args.args[0])
            call = interaction()
            await ns["update_internal_tier"](call, target, None)
            ref.set.assert_called_with({"internal_tier": None, "updated_at": "SERVER_TIMESTAMP"}, merge=True)
            self.assertIn("미분류", call.followup.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
