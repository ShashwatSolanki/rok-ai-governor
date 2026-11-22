import unittest

from rok_governor.action_policy import action_priority, order_task_commands


class ActionPolicyTests(unittest.TestCase):
    def test_commands_are_sorted_by_the_six_task_priorities(self):
        commands = [
            {"action_type": "claim_reward", "title": "Complete daily quest"},
            {"action_type": "upgrade_building", "title": "Upgrade Farm"},
            {"action_type": "claim_reward", "title": "Collect city food bubble"},
            {"action_type": "start_research", "title": "Research Technology"},
            {"action_type": "train_troops", "title": "Train troops"},
            {"action_type": "start_gathering", "title": "Send march to gather"},
        ]
        self.assertEqual(
            [command["action_type"] for command in order_task_commands(commands)],
            ["start_gathering", "train_troops", "upgrade_building", "start_research", "claim_reward", "claim_reward"],
        )

    def test_rejects_unlisted_activities_and_unrelated_rewards(self):
        self.assertIsNone(action_priority({"action_type": "manage_commander", "title": "Manage commander"}))
        self.assertIsNone(action_priority({"action_type": "claim_reward", "title": "Claim VIP reward"}))
        self.assertIsNone(action_priority({"action_type": "other", "title": "Start Path of Vengeance"}))

    def test_navigation_only_passes_when_it_supports_a_listed_task(self):
        self.assertEqual(action_priority({"action_type": "open_menu", "title": "Open map to gather"}), 0)
        self.assertEqual(action_priority({"action_type": "select_tab", "title": "Open quests tab"}), 5)
        self.assertIsNone(action_priority({"action_type": "open_menu", "title": "Open VIP menu"}))


if __name__ == "__main__":
    unittest.main()
