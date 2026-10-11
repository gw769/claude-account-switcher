#!/usr/bin/env python3
"""Contract tests on synthetic profiles only; never touch a real Claude app."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("session_sync", ROOT / "claude-account-sessions.py")
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


class SessionSyncTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.base = Path(self.temp.name)
        self.active = self.base / "config" / "Claude"
        self.store = self.base / "config" / "Claude-accounts"
        self.target = self.store / "work"
        self.projects = self.base / "claude-home" / "projects"
        self.project = self.base / "project"
        self.project.mkdir()
        self.aid, self.bid, self.org = (str(uuid.uuid4()) for _ in range(3))
        self.a = self.account(self.active, self.aid)
        self.b = self.account(self.target, self.bid)
        self.config_before = (self.active / "config.json").read_bytes()
        self.sid, self.cli = str(uuid.uuid4()), str(uuid.uuid4())
        self.card = {"sessionId": "local_" + self.sid, "cliSessionId": self.cli,
                     "cwd": str(self.project), "originCwd": str(self.project),
                     "title": "Synthetic conversation", "lastActivityAt": 10,
                     "completedTurns": 1, "unknownField": {"nested": [1, 2]},
                     "permissionMode": "bypassPermissions",
                     "remoteMcpServersConfig": [{"accountOnly": "A"}]}
        write_json(self.a / ("local_" + self.sid + ".json"), self.card)
        self.transcript(self.cli)

    def tearDown(self):
        self.temp.cleanup()

    def account(self, root, account):
        bucket = root / "claude-code-sessions" / account / self.org
        bucket.mkdir(parents=True)
        write_json(root / "config.json", {"lastKnownAccountUuid": account,
                   "oauth:tokenCacheV2": "SYNTHETIC-DO-NOT-CHANGE"})
        return bucket

    def transcript(self, session):
        folder = self.projects / "fixture-project"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (session + ".jsonl")).write_bytes(b'{"type":"user","text":"fixture"}\n')

    def plan(self, target=None):
        return SYNC.make_plan(self.active, self.store, target or self.target, self.projects)

    def target_card(self, bucket=None):
        return json.loads(((bucket or self.b) / ("local_" + self.sid + ".json")).read_bytes())

    def rotate(self):
        old = self.store / "main"
        os.rename(self.active, old)
        os.rename(self.target, self.active)
        self.a = old / "claude-code-sessions" / self.aid / self.org
        self.b = self.active / "claude-code-sessions" / self.bid / self.org
        return old

    def test_a_to_b_to_a_keeps_complete_history_and_continuation(self):
        history = snapshot(self.projects)
        self.plan().apply()
        self.assertEqual(self.target_card()["cliSessionId"], self.cli)
        old = self.rotate()
        next_cli = str(uuid.uuid4())
        self.transcript(next_cli)  # Represents the real app saving a continuation.
        continued = self.target_card()
        continued.update(cliSessionId=next_cli, priorCliSessionIds=[self.cli],
                         completedTurns=2, lastActivityAt=20, title="Continued in B")
        write_json(self.b / ("local_" + self.sid + ".json"), continued)
        history_after_reply = snapshot(self.projects)
        plan = self.plan(old)
        self.assertEqual(plan.updated, 1)
        plan.apply()
        restored = self.target_card(self.a)
        self.assertEqual(restored["cliSessionId"], next_cli)
        self.assertEqual(restored["priorCliSessionIds"], [self.cli])
        self.assertEqual(restored["completedTurns"], 2)
        self.assertEqual(snapshot(self.projects), history_after_reply)
        for name, content in history.items():
            self.assertEqual(history_after_reply[name], content)
        self.assertEqual((old / "config.json").read_bytes(), self.config_before)

    def test_dry_run_is_read_only_and_reports_missing_card(self):
        before = snapshot(self.base)
        plan = self.plan()
        self.assertEqual(plan.added, 1)
        self.assertEqual(snapshot(self.base), before)
        self.assertFalse((self.target / ".code-sync-backups").exists())

    def test_account_credentials_and_unknown_fields_are_preserved(self):
        config = (self.target / "config.json").read_bytes()
        history = snapshot(self.projects)
        self.plan().apply()
        result = self.target_card()
        self.assertEqual(result["unknownField"], self.card["unknownField"])
        self.assertEqual(result["remoteMcpServersConfig"], [])
        self.assertEqual(result["permissionMode"], "default")
        self.assertEqual((self.target / "config.json").read_bytes(), config)
        self.assertEqual(snapshot(self.projects), history)

    def test_destination_authorizations_stay_with_destination(self):
        own = dict(self.card, lastActivityAt=5, remoteMcpServersConfig=[{"accountOnly": "B"}],
                   permissionMode="plan")
        write_json(self.b / ("local_" + self.sid + ".json"), own)
        self.plan().apply()
        self.assertEqual(self.target_card()["remoteMcpServersConfig"], own["remoteMcpServersConfig"])
        self.assertEqual(self.target_card()["permissionMode"], "plan")

    def test_newer_destination_is_not_replaced_by_stale_source(self):
        newer = dict(self.card, lastActivityAt=30, completedTurns=3)
        write_json(self.b / ("local_" + self.sid + ".json"), newer)
        plan = self.plan()
        self.assertEqual(plan.updated, 0)
        plan.apply()
        self.assertEqual(self.target_card(), newer)

    def test_archive_and_title_changes_at_same_activity_follow_active_account(self):
        write_json(self.b / ("local_" + self.sid + ".json"), self.card)
        changed = dict(self.card, isArchived=True, title="New title without a new turn")
        write_json(self.a / ("local_" + self.sid + ".json"), changed)
        self.plan().apply()
        self.assertTrue(self.target_card()["isArchived"])
        self.assertEqual(self.target_card()["title"], changed["title"])

    def test_missing_prior_transcript_is_skipped_without_index_or_history_changes(self):
        card = dict(self.card, priorCliSessionIds=[str(uuid.uuid4())])
        write_json(self.a / ("local_" + self.sid + ".json"), card)
        before = snapshot(self.base)
        plan = self.plan()
        self.assertEqual(plan.skipped, 1)
        self.assertEqual(plan.added, 0)
        plan.apply()
        self.assertEqual(snapshot(self.base), before)

    def test_multiple_organizations_refuse_without_writes(self):
        (self.b.parent / str(uuid.uuid4())).mkdir()
        before = snapshot(self.base)
        with self.assertRaises(SYNC.SyncError):
            self.plan()
        self.assertEqual(snapshot(self.base), before)

    def test_ambiguous_account_without_marker_refuses(self):
        (self.target / "config.json").unlink()
        extra = self.target / "claude-code-sessions" / str(uuid.uuid4()) / self.org
        extra.mkdir(parents=True)
        with self.assertRaises(SYNC.SyncError):
            self.plan()

    def test_blank_new_profile_is_not_created_by_preflight(self):
        missing = self.store / "new"
        plan = self.plan(missing)
        self.assertTrue(plan.not_ready)
        plan.apply()
        self.assertFalse(missing.exists())

    def test_deleted_chat_is_hidden_but_history_and_backup_survive(self):
        write_json(self.b / ("local_" + self.sid + ".json"), self.card)
        (self.a / ("deleted_" + self.cli)).touch()
        history = snapshot(self.projects)
        plan = self.plan()
        self.assertEqual(plan.hidden, 1)
        plan.apply()
        self.assertFalse((self.b / ("local_" + self.sid + ".json")).exists())
        self.assertTrue((self.b / ("deleted_" + self.cli)).exists())
        self.assertEqual(snapshot(self.projects), history)
        backups = list((self.target / ".code-sync-backups").glob("*/before/**/local_*.json"))
        self.assertEqual(len(backups), 1)

    def test_corrupt_or_mismatched_identity_refuses_all_changes(self):
        write_json(self.a / ("local_" + self.sid + ".json"), dict(self.card, sessionId="local_wrong"))
        before = snapshot(self.base)
        with self.assertRaises(SYNC.SyncError):
            self.plan()
        self.assertEqual(snapshot(self.base), before)

    def test_symlinked_destination_does_not_touch_external_data(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        self.b.rmdir()
        self.b.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(SYNC.SyncError):
            self.plan()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_failed_commit_restores_preexisting_files(self):
        write_json(self.b / ("local_" + self.sid + ".json"), dict(self.card, lastActivityAt=5))
        other = str(uuid.uuid4())
        write_json(self.a / ("local_" + other + ".json"), dict(self.card, sessionId="local_" + other))
        before = snapshot(self.b)
        plan = self.plan()
        real_replace = os.replace
        calls = 0

        def fail_once(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic write failure")
            return real_replace(source, destination)

        with patch.object(SYNC.os, "replace", side_effect=fail_once):
            with self.assertRaises(SYNC.SyncError):
                plan.apply()
        self.assertEqual(snapshot(self.b), before)

    def test_no_folder_workspace_survives_round_trip_without_root_symlinks(self):
        rel = Path("scratch-workspaces") / self.aid / self.org / self.sid
        workspace = self.active / rel
        (workspace / "empty").mkdir(parents=True)
        (workspace / "note.txt").write_text("A", encoding="utf-8")
        (workspace / "linked").symlink_to(self.project, target_is_directory=True)
        card = dict(self.card, cwd=str(workspace), originCwd=str(workspace))
        write_json(self.a / ("local_" + self.sid + ".json"), card)
        plan = self.plan()
        self.assertEqual(plan.workspaces, 1)
        plan.apply()
        self.assertTrue((self.target / rel / "empty").is_dir())
        self.assertTrue((self.target / rel / "linked").is_symlink())
        self.assertFalse((self.target / "scratch-workspaces").is_symlink())
        old = self.rotate()
        (self.active / rel / "note.txt").write_text("B continued", encoding="utf-8")
        updated = dict(self.target_card(), lastActivityAt=20, completedTurns=2)
        write_json(self.b / ("local_" + self.sid + ".json"), updated)
        self.plan(old).apply()
        self.assertEqual((old / rel / "note.txt").read_text(), "B continued")
        self.assertEqual((self.active / rel / "note.txt").read_text(), "B continued")
        backup = list((old / ".code-sync-backups").glob("*/before/scratch-workspaces/*/*/*/note.txt"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_text(), "A")

    def test_failed_workspace_replacement_restores_old_directory(self):
        rel = Path("scratch-workspaces") / self.aid / self.org / self.sid
        source, target = self.active / rel, self.target / rel
        source.mkdir(parents=True)
        target.mkdir(parents=True)
        (source / "note.txt").write_text("new source")
        (target / "note.txt").write_text("original destination")
        write_json(self.a / ("local_" + self.sid + ".json"),
                   dict(self.card, cwd=str(source), originCwd=str(source)))
        before = snapshot(target)
        plan = self.plan()
        real_replace = os.replace
        calls = 0

        def fail_new_directory(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic directory failure")
            return real_replace(source, destination)

        with patch.object(SYNC.os, "replace", side_effect=fail_new_directory):
            with self.assertRaises(SYNC.SyncError):
                plan.apply()
        self.assertEqual(snapshot(target), before)
        self.assertFalse((self.b / ("local_" + self.sid + ".json")).exists())
        self.assertEqual((source / "note.txt").read_text(), "new source")

    def test_recorded_worktree_lease_moves_with_session_without_git_mutations(self):
        card = dict(self.card, worktreeName="fixture-worktree", worktreePath=str(self.project))
        write_json(self.a / ("local_" + self.sid + ".json"), card)
        entry = {"name": "fixture-worktree", "path": str(self.project),
                 "leasedBy": "local_" + self.sid, "baseRepo": str(self.project)}
        write_json(self.active / "git-worktrees.json", {"schemaVersion": 2,
                   "worktrees": {"fixture-worktree": entry}})
        self.plan().apply()
        registry = json.loads((self.target / "git-worktrees.json").read_bytes())
        self.assertEqual(registry["worktrees"]["fixture-worktree"], entry)

    def test_missing_worktree_lease_refuses(self):
        write_json(self.a / ("local_" + self.sid + ".json"), dict(self.card, worktreeName="unknown"))
        with self.assertRaises(SYNC.SyncError):
            self.plan()


if __name__ == "__main__":
    unittest.main(verbosity=2)
