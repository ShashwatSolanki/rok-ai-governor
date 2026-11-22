import unittest

from rok_governor.action_policy import action_priority, order_task_commands


class ActionPolicyTests(unittest.TestCase):
    def test_commands_are_sorted_by_the_seven_task_priorities(self):
        commands = [
            {"action_type": "claim_reward", "title": "Complete daily quest"},
            {"action_type": "upgrade_building", "title": "Upgrade Farm"},
            {"action_type": "claim_reward", "title": "Collect city food bubble"},
            {"action_type": "start_research", "title": "Research Technology"},
            {"action_type": "train_troops", "title": "Train troops"},
            {"action_type": "start_gathering", "title": "Send march to gather"},
            {"action_type": "other", "title": "Hunt barbarians", "preconditions": ["Screenshot shows AP: 25/100"]},
        ]
        self.assertEqual(
            [command["action_type"] for command in order_task_commands(commands)],
            ["start_gathering", "train_troops", "upgrade_building", "start_research", "claim_reward", "claim_reward", "other"],
        )

    def test_rejects_unlisted_activities_and_unrelated_rewards(self):
        self.assertIsNone(action_priority({"action_type": "manage_commander", "title": "Manage commander"}))
        self.assertIsNone(action_priority({"action_type": "claim_reward", "title": "Claim VIP reward"}))
        self.assertIsNone(action_priority({"action_type": "other", "title": "Start Path of Vengeance"}))

    def test_barbarian_hunts_require_visible_positive_ap(self):
        self.assertEqual(
            action_priority({
                "action_type": "other",
                "title": "Hunt barbarians",
                "preconditions": ["AP remaining: 30"],
            }),
            6,
        )
        self.assertIsNone(action_priority({
            "action_type": "other", "title": "Hunt barbarians",
            "preconditions": ["AP: 0/100"],
        }))
        self.assertIsNone(action_priority({
            "action_type": "other", "title": "Hunt barbarians in event",
            "preconditions": ["AP: 30/100"],
        }))

    def test_navigation_only_passes_when_it_supports_a_listed_task(self):
        self.assertEqual(action_priority({"action_type": "open_menu", "title": "Open map to gather"}), 0)
        self.assertEqual(action_priority({"action_type": "select_tab", "title": "Open quests tab"}), 5)
        self.assertIsNone(action_priority({"action_type": "open_menu", "title": "Open VIP menu"}))

    def test_city_center_preparation_sorts_before_task_actions(self):
        advice = {"current_screen": "city"}
        center = {
            "action_type": "open_menu",
            "title": "Inspect central building",
            "target": "Central building",
        }
        gather = {"action_type": "start_gathering", "title": "Send march to gather"}
        self.assertEqual(action_priority(center, advice), -1)
        self.assertEqual(order_task_commands([gather, center], advice), [center, gather])

    def test_city_hall_enter_popup_is_allowed_as_navigation(self):
        advice = {
            "current_screen": (
                "City exterior with City Hall selected and its white details popup open"
            )
        }
        command = {
            "action_type": "claim_reward",
            "title": "Enter Internal City Governance",
            "target": "Blue ENTER button on City Hall popup",
        }
        self.assertEqual(action_priority(command, advice), -1)

    def test_city_escape_menu_only_allows_dismissal_on_empty_ground(self):
        advice = {"current_screen": "City Escape menu"}
        safe_dismiss = {
            "action_type": "other",
            "title": "Dismiss City Escape menu",
            "target": "Empty ground area outside the building list",
        }
        listed_building = {
            "action_type": "open_menu",
            "title": "Open listed building",
            "target": "Building list",
        }
        self.assertEqual(action_priority(safe_dismiss, advice), -2)
        self.assertIsNone(action_priority(listed_building, advice))

        layout_advice = {"current_screen": "Cityscape layout selection cards"}
        self.assertIsNone(action_priority(
            {"action_type": "start_gathering", "title": "Send march to gather"},
            layout_advice,
        ))


if __name__ == "__main__":
    unittest.main()
