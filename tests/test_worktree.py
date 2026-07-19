import tempfile
import unittest
from pathlib import Path

from multicoders.dispatcher import Dispatcher, write_candidate_worktree
from multicoders.storage import Storage


class WorktreeWriteTests(unittest.TestCase):
    def test_strips_code_fences(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = write_candidate_worktree(
                Path(tmp), "task-1", "claude", "```python\nprint('ok')\n```"
            )
            content = (target / "candidate.py").read_text(encoding="utf-8")
            self.assertEqual(content, "print('ok')\n")

    def test_dispatcher_persists_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tracker.db"
            wt = Path(tmp) / "runs"
            storage = Storage(db_path=str(db))
            storage.create_task("t1", {"prompt": "x"})
            dispatcher = Dispatcher(
                storage,
                {"claude": lambda p: "def fn():\n    return 1\n"},
                worktree_root=wt,
            )
            candidates = dispatcher.dispatch("t1", "build a thing")
            self.assertEqual(len(candidates), 1)
            self.assertIsNotNone(candidates[0].workdir)
            self.assertTrue(Path(candidates[0].workdir, "candidate.py").exists())
            artifact = storage.get_artifact(candidates[0].artifact_id)
            self.assertEqual(artifact["workdir"], candidates[0].workdir)

    def test_dispatcher_without_root_keeps_workdir_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tracker.db"
            storage = Storage(db_path=str(db))
            storage.create_task("t2", {"prompt": "x"})
            dispatcher = Dispatcher(storage, {"claude": lambda p: "ok"})
            candidates = dispatcher.dispatch("t2", "y")
            self.assertIsNone(candidates[0].workdir)
            artifact = storage.get_artifact(candidates[0].artifact_id)
            self.assertIsNone(artifact["workdir"])


if __name__ == "__main__":
    unittest.main()
