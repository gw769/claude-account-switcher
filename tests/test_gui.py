#!/usr/bin/env python3
"""Real GTK/child-process checks with synthetic profiles; never query real accounts."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("account_gui", ROOT / "claude-account-gui.py")
GUI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUI)
Gtk, GLib = GUI.Gtk, GUI.GLib
A, B, ORG = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
             "33333333-3333-3333-3333-333333333333")


class GuiTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.root = Path(self.temp.name)
        self.active, self.store = self.root / "Claude", self.root / "Claude-accounts"
        self.active.mkdir()
        (self.store / "work").mkdir(parents=True)
        (self.store / "main").symlink_to(self.active, target_is_directory=True)
        (self.active / ".code-sync-backups").mkdir()
        self.protected = {}
        for profile, aid in ((self.active, A), (self.store / "work", B)):
            path = profile / "config.json"
            path.write_text(json.dumps({"lastKnownAccountUuid": aid, "oauth:tokenCacheV2": "SYNTHETIC"}))
            self.protected[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.cache = self.store / ".usage-cache.json"
        self.cache.write_text(json.dumps({"accounts": {aid: {"organization": ORG, "checkedAt": time.time(),
            "windows": {key: {"used": 10, "reset": time.time() + 3600} for key in ("five_hour", "seven_day")}}
            for aid in (A, B)}}))
        worker = self.root / "fake_usage.py"
        worker.write_text(f'''import importlib.util,json,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("real_usage",{str(ROOT / "claude-account-usage.py")!r})
real=importlib.util.module_from_spec(spec);spec.loader.exec_module(real)
rows=real.rows
if __name__=="__main__":
    import os
    store=Path(sys.argv[sys.argv.index("--store")+1])
    (store/"worker.pid").write_text(str(os.getpid()))
    mode=(store/"mode").read_text() if (store/"mode").exists() else "ok"
    time.sleep(60 if mode=="hang" else .3)
    if mode=="fail": print("查询失败；保留有效数值。",file=sys.stderr);sys.exit(1)
    cache=store/".usage-cache.json";data=json.loads(cache.read_text())
    for entry in data["accounts"].values():
        entry["checkedAt"]=time.time()
        for window in entry["windows"].values(): window["used"]=25
    cache.write_text(json.dumps(data))
    print("已更新两个测试账号。")
''')
        backend = self.root / "fake_backend"
        backend.write_text(f'''#!{sys.executable}
import json,sys,time
from pathlib import Path
Path({str(self.root / "backend.args")!r}).write_text(json.dumps(sys.argv[1:]))
time.sleep(.2)
sys.exit(1 if Path({str(self.root / "backend.fail")!r}).exists() else 0)
''')
        backend.chmod(0o700)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        shutil.copy2(backend, self.fake_bin / "gio")
        self.app = GUI.AccountWindow(backend, worker, self.active, self.store)
        self.pump()
        self.native = self.app.window.get_window()
        self.visibility_changes = []
        self.app.window.connect("unmap", lambda *_: self.visibility_changes.append("unmap"))
        self.app.window.connect("map", lambda *_: self.visibility_changes.append("map"))

    def tearDown(self):
        if self.app.worker is not None:
            self.wait(lambda: self.app.worker is None, timeout=2)
        if not self.app.closed:
            self.app.close()
        self.pump()
        for path, digest in self.protected.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.temp.cleanup()

    def pump(self):
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump()
            time.sleep(.005)
        self.pump()
        self.assertTrue(predicate(), "Asynchronous GUI operation did not complete")

    def select(self, key):
        for row in self.app.model:
            if row[0] == key:
                self.app.table.get_selection().select_iter(row.iter)
                return
        self.fail("Missing action " + key)

    def assert_resident(self):
        self.assertTrue(self.app.window.get_visible())
        self.assertTrue(self.app.window.get_mapped())
        self.assertEqual(self.app.window.get_window(), self.native)
        self.assertEqual(self.visibility_changes, [], "Main window was hidden or reopened")

    def answer_dialog(self, answer, response=Gtk.ResponseType.OK):
        def respond():
            for dialog in Gtk.Window.list_toplevels():
                if not isinstance(dialog, Gtk.Dialog) or dialog.get_transient_for() != self.app.window:
                    continue
                self.assert_resident()
                if answer is not None:
                    for child in dialog.get_content_area().get_children():
                        if isinstance(child, Gtk.Entry): child.set_text(answer)
                dialog.response(response)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        return GLib.timeout_add(10, respond)

    def test_every_action_keeps_main_window_mapped_without_reopening(self):
        cases = [("switch:work", ["switch", "work"]), ("sync", ["sync"]),
                 ("add", ["add", "new_account"]), ("remove", ["remove", "work"]),
                 ("backups", ["open", str(self.active / ".code-sync-backups")])]
        with patch.dict(os.environ, {"PATH": str(self.fake_bin) + os.pathsep + os.environ["PATH"]}):
            for action, expected in cases:
                with self.subTest(action=action):
                    self.select(action)
                    if action in ("add", "remove"): self.answer_dialog("new_account" if action == "add" else None)
                    self.assertTrue(self.app.execute())
                    self.assert_resident()
                    self.wait(lambda: self.app.worker is None)
                    self.assertEqual(json.loads((self.root / "backend.args").read_text()), expected)
                    self.assert_resident()
        self.app.refresh()
        self.assert_resident()
        self.wait(lambda: self.app.worker is None)
        self.assert_resident()

    def test_cancelled_native_dialogs_do_not_hide_main_window(self):
        for action in ("add", "remove"):
            with self.subTest(action=action):
                self.select(action)
                self.answer_dialog(None, Gtk.ResponseType.CANCEL)
                self.assertFalse(self.app.execute())
                self.assertIsNone(self.app.worker)
                self.assert_resident()

    def test_invalid_name_error_keeps_main_window_mapped(self):
        self.select("add")
        self.answer_dialog("bad/name")
        observed = []
        def dismiss_error():
            for dialog in Gtk.Window.list_toplevels():
                if isinstance(dialog, Gtk.MessageDialog) and dialog.get_transient_for() == self.app.window:
                    self.assert_resident()
                    observed.append(True)
                    dialog.response(Gtk.ResponseType.OK)
                    return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        GLib.timeout_add(10, dismiss_error)
        self.assertFalse(self.app.execute())
        self.assertEqual(observed, [True])
        self.assertIsNone(self.app.worker)
        self.assert_resident()

    def test_refresh_child_keeps_window_visible_and_updates_same_table(self):
        self.select("switch:work")
        native, model = self.app.window.get_window(), self.app.model
        previous = [list(row) for row in model]
        self.assertTrue(self.app.refresh())
        pid = int(self.app.worker.get_identifier())
        self.assertNotEqual(pid, os.getpid())
        self.assertEqual([list(row) for row in model], previous)
        self.assertTrue(self.app.window.get_visible())
        self.assertTrue(self.app.table.get_sensitive())
        ticks = []
        timer = GLib.timeout_add(10, lambda: ticks.append(time.monotonic()) or GLib.SOURCE_CONTINUE)
        self.wait(lambda: self.app.worker is None)
        GLib.source_remove(timer)
        self.assertGreaterEqual(len(ticks), 3)
        self.assertEqual(self.app.window.get_window(), native)
        self.assertIs(self.app.model, model)
        self.assertEqual(self.app.selected(), "switch:work")
        self.assertEqual(self.app.model[0][2], "75% 剩余")
        self.assertTrue(self.app.refresh_button.get_sensitive())
        self.assertEqual(self.app.status.get_text(), "已更新两个测试账号。")

    def test_duplicate_refresh_and_switch_are_blocked_until_query_finishes(self):
        self.app.refresh()
        process = self.app.worker
        self.assertFalse(self.app.refresh())
        self.select("switch:work")
        self.assertFalse(self.app.execute())
        self.assertIs(self.app.worker, process)
        self.assertFalse(self.app.execute_button.get_sensitive())
        self.assertTrue(self.app.close_button.get_sensitive())
        self.wait(lambda: self.app.worker is None)

    def test_failed_refresh_keeps_previous_values_and_window(self):
        (self.store / "mode").write_text("fail")
        before, native = self.cache.read_bytes(), self.app.window.get_window()
        self.app.refresh()
        self.wait(lambda: self.app.worker is None)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(self.app.model[0][2], "90% 剩余")
        self.assertIn("查询失败", self.app.status.get_text())
        self.assertEqual(self.app.window.get_window(), native)

    def test_refresh_timeout_only_ends_our_child_and_preserves_values(self):
        (self.store / "mode").write_text("hang")
        before = self.cache.read_bytes()
        self.app.refresh()
        GLib.source_remove(self.app.timer)
        self.app.refresh_timeout()
        self.wait(lambda: self.app.worker is None)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertIn("超时", self.app.status.get_text())
        self.assertTrue(self.app.window.get_visible())

    def test_close_during_refresh_ends_only_the_owned_usage_child(self):
        (self.store / "mode").write_text("hang")
        self.app.refresh()
        pid = int(self.app.worker.get_identifier())
        self.app.close()
        self.wait(lambda: self.app.worker is None)
        self.assertTrue(self.app.closed)
        self.assertFalse(Path("/proc") .joinpath(str(pid)).exists())

    def test_current_account_selection_keeps_native_window(self):
        native = self.app.window.get_window()
        self.select("active")
        self.assertTrue(self.app.execute())
        self.assertIsNone(self.app.worker)
        self.assertEqual(self.app.window.get_window(), native)
        self.assertIn("默认已开启", self.app.status.get_text())

    def test_switch_worker_preserves_window_and_cannot_be_killed_mid_transition(self):
        native = self.app.window.get_window()
        self.select("switch:work")
        self.assertTrue(self.app.execute())
        self.assertFalse(self.app.close_button.get_sensitive())
        self.app.close()
        self.assertFalse(self.app.closed)
        self.wait(lambda: self.app.worker is None)
        self.assertEqual(json.loads((self.root / "backend.args").read_text()), ["switch", "work"])
        self.assertEqual(self.app.window.get_window(), native)
        self.assertTrue(self.app.close_button.get_sensitive())

    def test_backend_failure_and_cancelled_add_keep_window(self):
        (self.root / "backend.fail").touch()
        self.select("sync")
        self.app.execute()
        self.wait(lambda: self.app.worker is None)
        self.assertIn("未完成", self.app.status.get_text())
        self.select("add")
        with patch.object(self.app, "ask_name", return_value=""):
            self.assertFalse(self.app.execute())
        self.assertIsNone(self.app.worker)
        self.assertTrue(self.app.window.get_visible())

    def test_backups_label_describes_opening_a_folder(self):
        self.assertIn(["backups", "查看对话同步备份文件夹", "", "", "", "", ""],
                      [list(row) for row in self.app.model])

    def test_repeated_launches_activate_one_window_during_refresh(self):
        (self.store / "mode").write_text("hang")
        log = self.root / "singleton.jsonl"
        runner = self.root / "singleton.py"
        runner.write_text(f'''import importlib.util,json,os,sys
from pathlib import Path
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location("gui",{str(ROOT / "claude-account-gui.py")!r})
gui=importlib.util.module_from_spec(spec);spec.loader.exec_module(gui)
events=[]
def factory():
    window=gui.AccountWindow({str(self.root / "fake_backend")!r},
                            {str(self.root / "fake_usage.py")!r},
                            {str(self.active)!r},{str(self.store)!r})
    window.window.connect("map",lambda *_:events.append("map"))
    window.window.connect("unmap",lambda *_:events.append("unmap"))
    window.refresh()
    return window
app=gui.SingleInstanceApplication(factory,"io.github.gw769.ClaudeAccountSwitcher.Test{uuid.uuid4().hex}")
def activated(_app):
    window=app.account_window
    with Path({str(log)!r}).open("a") as output:
        output.write(json.dumps({{"pid":os.getpid(),"window":hash(window.window.get_window()),
                                "worker":window.worker.get_identifier(),"events":events}})+"\\n")
app.connect("activate",activated)
gui.GLib.timeout_add_seconds(8,lambda:app.account_window.close() or gui.GLib.SOURCE_REMOVE)
raise SystemExit(app.run(["claude-account-switcher-singleton-test"]))
''')
        with (self.root / "singleton-errors.log").open("w") as errors:
            primary = subprocess.Popen([sys.executable, str(runner)], stdout=errors, stderr=errors)
            try:
                self.wait(lambda: log.exists() and len(log.read_text().splitlines()) == 1, timeout=4)
                for _ in range(2):
                    secondary = subprocess.run([sys.executable, str(runner)], stdout=errors, stderr=errors, timeout=3)
                    self.assertEqual(secondary.returncode, 0)
                entries = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(len(entries), 3)
                self.assertEqual({entry["pid"] for entry in entries}, {primary.pid})
                self.assertEqual(len({entry["window"] for entry in entries}), 1)
                self.assertEqual(len({entry["worker"] for entry in entries}), 1)
                self.assertTrue(all(entry["events"] == [] for entry in entries))
                self.assertIsNone(primary.poll())
                primary.wait(timeout=10)
                self.assertEqual(primary.returncode, 0)
                worker = entries[0]["worker"]
                self.assertFalse((Path("/proc") / worker).exists())
            finally:
                if primary.poll() is None:
                    primary.terminate()
                    primary.wait(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
