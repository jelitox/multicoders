import unittest
import tempfile
import shutil
from pathlib import Path
from multicoders.flow import MulticodersFlow
from multicoders.storage import Storage
from multicoders.dispatcher import Candidate
from multicoders.arena import ArenaVerdict

class FlowCleanupTests(unittest.TestCase):
    def test_cleanup_losers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = tmp_path / "test.db"
            storage = Storage(db_path=str(db))

            winner_dir = tmp_path / "winner"
            loser_dir = tmp_path / "loser"
            winner_dir.mkdir()
            loser_dir.mkdir()

            (winner_dir / "code.py").write_text("ok")
            (loser_dir / "code.py").write_text("bad")

            candidates = [
                Candidate(artifact_id=1, author="c1", content="ok", workdir=str(winner_dir)),
                Candidate(artifact_id=2, author="c2", content="bad", workdir=str(loser_dir)),
            ]

            winner = candidates[0]
            MulticodersFlow._cleanup_losers(candidates, winner)

            self.assertTrue(winner_dir.exists())
            self.assertFalse(loser_dir.exists())

if __name__ == "__main__":
    unittest.main()
