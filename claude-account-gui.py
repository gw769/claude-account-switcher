#!/usr/bin/env python3
"""Keep the Desktop account window open while owned subprocesses do the work."""

import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gdk
GLib.set_prgname("claude-account-switcher")
GLib.set_application_name("Claude 账号切换")
from gi.repository import Gio, Gtk, GdkPixbuf
Gdk.set_program_class("claude-account-switcher")

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
COLUMNS = ("账号 / 动作", "周额度", "周重置时间", "5 小时额度", "5 小时重置时间", "更新时间 / 状态")
BACKUPS_LABEL = "查看对话同步备份文件夹"
APPLICATION_ID = "io.github.gw769.ClaudeAccountSwitcher"


def layout(active, store):
    """Read folder markers only; never read or change login credentials here."""
    entries = sorted(store.iterdir()) if store.is_dir() else []
    name, legacy = "", False
    if active.is_symlink():
        target = Path(os.readlink(active))
        if target.parent == store and NAME.fullmatch(target.name):
            name, legacy = target.name, True
    else:
        name = next((p.name for p in entries if NAME.fullmatch(p.name) and p.is_symlink()
                     and os.readlink(p) == str(active)), "")
    others = [p.name for p in entries if NAME.fullmatch(p.name) and p.is_dir()
              and not p.is_symlink() and p.name != name]
    return name, legacy, others


class AccountWindow:
    def __init__(self, switcher, usage_helper, active, store, icon=None):
        self.switcher, self.usage_helper = str(switcher), str(usage_helper)
        self.active, self.store = Path(active), Path(store)
        spec = importlib.util.spec_from_file_location("desktop_usage", usage_helper)
        self.usage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.usage)
        self.worker, self.job, self.timer, self.job_error = None, "", 0, ""
        self.closed = False
        icon = Path(icon) if icon else Path(__file__).parent / "icons/claude-account-switcher.svg"
        Gtk.Window.set_default_icon_name("claude-account-switcher")
        self.window = Gtk.Window(title="Claude 帳號切換")
        self.window.set_icon_name("claude-account-switcher")
        if icon.is_file():
            self.window.set_icon_from_file(str(icon))
        self.window.set_default_size(1160, 490)
        self.window.set_border_width(16)
        self.window.connect("delete-event", self.close)
        self.window.connect("key-press-event", self.key_press)
        self.window.connect("destroy", self.destroyed)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window.add(box)
        header_box = Gtk.Box(spacing=12)
        if icon.is_file():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon), 32, 32, True)
            header_box.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
        self.header = Gtk.Label(xalign=0)
        header_box.pack_start(self.header, True, True, 0)
        self.refresh_button = Gtk.Button(label="刷新额度")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header_box.pack_end(self.refresh_button, False, False, 0)
        box.pack_start(header_box, False, False, 0)
        box.pack_start(Gtk.Label(label="Code 对话同步已开启；每次切号自动同步本机 Code 对话。", xalign=0), False, False, 0)
        box.pack_start(Gtk.Label(label="额度为剩余百分比；刷新后在本窗口更新。重置时间使用本机时区："
                                + time.strftime("%Z %z"), xalign=0), False, False, 0)
        self.model = Gtk.ListStore(*([str] * 7))
        self.table = Gtk.TreeView(model=self.model)
        self.table.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        for index, title in enumerate(COLUMNS, 1):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=index)
            column.set_resizable(True)
            column.set_expand(index in (1, 6))
            self.table.append_column(column)
        self.table.connect("row-activated", lambda *_: self.execute())
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.table)
        box.pack_start(scroll, True, True, 0)
        status_box = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        status_box.pack_start(self.spinner, False, False, 0)
        self.status = Gtk.Label(label="窗口常驻；关闭窗口才退出。", xalign=0)
        self.status.set_line_wrap(True)
        status_box.pack_start(self.status, True, True, 0)
        box.pack_start(status_box, False, False, 0)
        buttons = Gtk.Box(spacing=12, homogeneous=True)
        self.close_button = Gtk.Button(label="关闭")
        self.close_button.connect("clicked", self.close)
        self.execute_button = Gtk.Button(label="执行所选项")
        self.execute_button.connect("clicked", lambda *_: self.execute())
        for button in (self.close_button, self.execute_button):
            buttons.pack_start(button, True, True, 0)
        box.pack_start(buttons, False, False, 0)
        self.load_rows()
        self.window.show_all()

    def selected(self):
        model, iterator = self.table.get_selection().get_selected()
        return model[iterator][0] if iterator is not None else ""

    def load_rows(self):
        previous = self.selected()
        try:
            active_name, legacy, others = layout(self.active, self.store)
            rows = self.usage.rows(self.active, self.store, active_name)
        except (OSError, ValueError, RuntimeError):
            self.status.set_text("账号数据暂时不可用；保留当前列表。")
            return
        if legacy:
            rows.append(["upgrade", "转换文件夹配置（修正旧记录）", "", "", "", "", ""])
        rows.append(["add", "新增账号…", "", "", "", "", ""])
        if self.active.is_dir():
            rows.append(["sync", "同步 Code 对话（目前账号）", "", "", "", "", ""])
        if (self.active / ".code-sync-backups").is_dir():
            rows.append(["backups", BACKUPS_LABEL, "", "", "", "", ""])
        if others:
            rows.append(["remove", "移除账号…", "", "", "", "", ""])
        self.model.clear()
        chosen = None
        for row in rows:
            iterator = self.model.append(row)
            if row[0] == previous:
                chosen = iterator
        if chosen is None:
            chosen = self.model.get_iter_first()
        if chosen is not None:
            self.table.get_selection().select_iter(chosen)
        self.header.set_text("目前账号：" + (active_name or "尚未命名"))

    def busy(self, enabled):
        self.refresh_button.set_sensitive(not enabled)
        self.execute_button.set_sensitive(not enabled)
        self.refresh_button.set_label("刷新中…" if enabled and self.job == "refresh" else "刷新额度")
        self.close_button.set_sensitive(not enabled or self.job == "refresh")
        if enabled:
            self.spinner.start()
        else:
            self.spinner.stop()

    def start_job(self, argv, kind, message):
        if self.closed or self.worker is not None:
            return False
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        launcher.setenv("CLAUDE_ACCOUNT_UI", "gui", True)
        try:
            process = launcher.spawnv([str(arg) for arg in argv])
        except GLib.Error:
            self.status.set_text("无法启动后台操作；账号状态没有修改。")
            return False
        self.worker, self.job, self.job_error = process, kind, ""
        self.busy(True)
        self.status.set_text(message)
        if kind == "refresh":
            # Each profile has two HTTP requests, each bounded to ten seconds.
            profiles = sum(row[0] == "active" or row[0].startswith("switch:") for row in self.model)
            self.timer = GLib.timeout_add_seconds(max(30, profiles * 22 + 10), self.refresh_timeout)
        process.communicate_utf8_async(None, None, self.finished)
        return True

    def refresh(self):
        return self.start_job([sys.executable, self.usage_helper, "--active-root", self.active,
                               "--store", self.store, "--refresh"], "refresh",
                              "正在后台查询额度；旧数值继续显示，完成后原地更新。")

    def refresh_timeout(self):
        self.timer = 0
        if self.worker is not None and self.job == "refresh":
            self.job_error = "额度查询超时；保留上次有效数值。"
            self.worker.force_exit()
        return GLib.SOURCE_REMOVE

    def finished(self, process, result):
        try:
            _, output, error = process.communicate_utf8_finish(result)
            success = process.get_successful()
        except GLib.Error:
            output, error, success = "", "后台操作未完成；保留当前账号状态。", False
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        kind = self.job
        self.worker, self.job = None, ""
        if self.closed:
            return
        self.busy(False)
        self.load_rows()
        if self.job_error:
            message = self.job_error
        elif kind == "refresh":
            message = (output if success else error).strip() or ("额度刷新完成。" if success else "额度刷新失败；保留上次有效数值。")
        elif kind == "backups":
            message = "已打开对话同步备份文件夹。" if success else "无法打开对话同步备份文件夹。"
        else:
            message = "操作结束；Code 对话仍在切号时自动同步。" if success else "操作未完成；请查看提示，窗口继续保留。"
        self.status.set_text(message)

    def ask_name(self, prompt, default=""):
        dialog = Gtk.Dialog(title="账号名称", transient_for=self.window, modal=True)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "确定", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(10)
        content.add(Gtk.Label(label=prompt, xalign=0))
        entry = Gtk.Entry(text=default, activates_default=True)
        content.add(entry)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        name = "".join(entry.get_text().split()) if response == Gtk.ResponseType.OK else ""
        dialog.destroy()
        if name and not NAME.fullmatch(name):
            message = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.ERROR,
                                        buttons=Gtk.ButtonsType.OK, text="账号名称只能用英文字母、数字、- 或 _，最多 32 个字元。")
            message.run()
            message.destroy()
            return ""
        return name

    def pick_account(self):
        _, _, others = layout(self.active, self.store)
        if not others:
            return ""
        dialog = Gtk.Dialog(title="要移除哪个账号？", transient_for=self.window, modal=True)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "继续", Gtk.ResponseType.OK)
        picker = Gtk.ComboBoxText()
        for name in others:
            picker.append_text(name)
        picker.set_active(0)
        dialog.get_content_area().set_border_width(12)
        dialog.get_content_area().add(picker)
        dialog.show_all()
        response = dialog.run()
        name = picker.get_active_text() if response == Gtk.ResponseType.OK else ""
        dialog.destroy()
        return name

    def execute(self):
        if self.worker is not None or self.closed:
            return False
        action = self.selected()
        args = None
        if action == "active":
            self.status.set_text("当前账号无需切换；Code 对话同步默认已开启。")
            return True
        if action.startswith("switch:"):
            args = ["switch", action[7:]]
        elif action == "upgrade":
            args = ["switch", layout(self.active, self.store)[0]]
        elif action == "sync":
            args = ["sync"]
        elif action == "backups":
            return self.start_job(["gio", "open", self.active / ".code-sync-backups"], "backups", "正在打开备份文件夹…")
        elif action == "add":
            active_name = layout(self.active, self.store)[0]
            adopt_as = self.ask_name("先替目前登录的账号取名：", "main") if self.active.is_dir() and not active_name else ""
            if self.active.is_dir() and not active_name and not adopt_as:
                return False
            name = self.ask_name("新账号名称：")
            if not name:
                return False
            args = ["add", name] + (["--as", adopt_as] if adopt_as else [])
        elif action == "remove":
            name = self.pick_account()
            if name:
                args = ["remove", name]
        if args:
            return self.start_job([self.switcher, *args], "backend", "正在执行账号操作；窗口继续显示。")
        return False

    def key_press(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            return self.close()
        return False

    def close(self, *_):
        if self.worker is not None and self.job != "refresh":
            self.status.set_text("账号操作正在完成；结束后即可关闭窗口。")
            return True
        self.closed = True
        if self.worker is not None:
            self.worker.force_exit()  # Only our read-only usage child, never Desktop.
        self.window.destroy()
        return True

    def destroyed(self, *_):
        self.closed = True
        if Gtk.main_level():
            Gtk.main_quit()


class SingleInstanceApplication(Gio.Application):
    """Activate the existing window through the desktop session bus."""

    def __init__(self, window_factory, application_id=APPLICATION_ID):
        super().__init__(application_id=application_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window_factory = window_factory
        self.account_window = None
        self.connect("activate", self.activate_window)

    def activate_window(self, _application):
        if self.account_window is None:
            self.account_window = self.window_factory()
            self.hold()
            self.account_window.window.connect("destroy", self.window_closed)
        self.account_window.window.present()

    def window_closed(self, *_):
        self.account_window = None
        self.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--switcher", type=Path, required=True)
    parser.add_argument("--usage-helper", type=Path, required=True)
    parser.add_argument("--active-root", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--icon", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    application = SingleInstanceApplication(lambda: AccountWindow(
        args.switcher, args.usage_helper, args.active_root, args.store, args.icon))
    return application.run(["claude-account-switcher"])


if __name__ == "__main__":
    raise SystemExit(main())
