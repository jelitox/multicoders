"""Checkpoint persistence: save and read latest snapshot per node."""
from __future__ import annotations

import os
import tempfile

from multicoders.storage import Storage


def _fresh_storage() -> tuple[Storage, str]:
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "mc.db")
    return Storage(db_path=db_path), db_path


def test_save_and_get_latest_checkpoint_per_node() -> None:
    storage, _ = _fresh_storage()
    storage.create_task("t1", {"prompt": "x"})

    storage.save_checkpoint("t1", "research", {"enriched_prompt": "ctx"})
    storage.save_checkpoint("t1", "dispatcher", {"artifact_ids": [1, 2]}, attempt=0)
    storage.save_checkpoint("t1", "arena", {"verdicts": []}, attempt=0)
    storage.save_checkpoint("t1", "dispatcher", {"artifact_ids": [3, 4]}, attempt=1)

    research = storage.get_latest_checkpoint("t1", "research")
    assert research is not None
    assert research["state"]["enriched_prompt"] == "ctx"

    latest_dispatcher = storage.get_latest_checkpoint("t1", "dispatcher")
    assert latest_dispatcher["attempt"] == 1
    assert latest_dispatcher["state"]["artifact_ids"] == [3, 4]

    latest_any = storage.get_latest_checkpoint("t1")
    assert latest_any["node"] == "dispatcher"


def test_get_task_returns_payload_dict() -> None:
    storage, _ = _fresh_storage()
    storage.create_task("t2", {"prompt": "hello", "k": 1})
    task = storage.get_task("t2")
    assert task["status"] == "pending"
    assert task["payload"] == {"prompt": "hello", "k": 1}


def test_get_task_unknown_returns_none() -> None:
    storage, _ = _fresh_storage()
    assert storage.get_task("nope") is None
    assert storage.get_latest_checkpoint("nope") is None
