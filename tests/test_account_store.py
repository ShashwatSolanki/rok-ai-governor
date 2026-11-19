import tempfile
import unittest
from pathlib import Path

from rok_governor.account_store import AccountStore


class AccountStoreTests(unittest.TestCase):
    def test_observation_round_trip_releases_sqlite_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "account.sqlite3"
            store = AccountStore(database)
            observation_id = store.record({
                "screen": {"type": "city"},
                "account_state": {"resources.food": 123},
            })

            self.assertEqual(observation_id, 1)
            self.assertEqual(store.observation_count(), 1)
            self.assertEqual(store.latest_state()[0]["value"], 123)
            database.unlink()
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
