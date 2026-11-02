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
    "TIERS", "MAJOR_TIERS", "INTERNAL_TIER_LABELS", "INTERNAL_TIER_SCORES",
    "EVENT_TIER_MODES", "EVENT_PUBLIC_MEMBER_LIMIT", "PARTY_TIMEZONE",
    "EVENT_COLLECTION", "TOOLKIT_RECRUITMENT_COLLECTION",
}
DEFINITIONS = {
    "utc_now", "as_utc", "event_tier_choices", "event_tier_score",
    "validate_event_tier_restriction", "event_tier_restriction_text",
    "event_tier_join_error", "event_status", "escape_event_text",
    "build_toolkit_recruitment_data", "get_toolkit_recruitment_ref",
    "set_recruitment_pair", "delete_recruitment_pair", "join_event_transaction",
    "leave_event_transaction", "set_event_registration_transaction",
    "event_team_progress", "event_format_recommendation", "build_event_embed",
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
        self.assertEqual(mirror["member_ids"], ["42"])
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


if __name__ == "__main__":
    unittest.main()
