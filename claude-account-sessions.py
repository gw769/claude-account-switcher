#!/usr/bin/env python3
"""Local Desktop Code-session sync. No network, tokens, or transcript writes.

Called by claude-account while Desktop is closed. --check is read-only.
Backups live inside the target profile and follow it through folder swaps.
"""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
ACCOUNT_FIELDS = {
    "remoteMcpServersConfig": [], "enabledMcpTools": {}, "bridgeSessionIds": [],
    "sessionPermissionUpdates": [], "alwaysAllowedReasons": [],
}


class SyncError(Exception):
    pass


def read_object(path):
    if path.is_symlink():
        raise SyncError("資料檔案是符號連結；為保護原資料，沒有同步。")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        raise SyncError("會話資料無法讀取或格式損壞；沒有同步。") from None
    if not isinstance(value, dict):
        raise SyncError("會話資料格式不受支援；沒有同步。")
    return value


def safe_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise SyncError("會話資料含不安全路徑；沒有同步。")
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise SyncError("目標資料路徑是符號連結；沒有同步。")
    return path


def buckets(root):
    base = safe_path(root, "claude-code-sessions")
    if not base.exists():
        return []
    if not base.is_dir():
        raise SyncError("Code 對話資料夾格式不受支援；沒有同步。")
    result = []
    for account in sorted(base.iterdir()):
        if not UUID.fullmatch(account.name):
            continue
        safe_path(root, account.relative_to(root))
        if not account.is_dir():
            raise SyncError("Code 帳號資料夾格式不受支援；沒有同步。")
        for org in sorted(account.iterdir()):
            if UUID.fullmatch(org.name):
                safe_path(root, org.relative_to(root))
                if not org.is_dir():
                    raise SyncError("Code 組織資料夾格式不受支援；沒有同步。")
                result.append(org)
    return result


def account_marker(root):
    config = root / "config.json"
    if not config.exists():
        return None
    # Only the account UUID is used; config.json is never copied or rewritten.
    value = read_object(config).get("lastKnownAccountUuid")
    return value if isinstance(value, str) and UUID.fullmatch(value) else None


def destination(root):
    found = buckets(root)
    if not found:
        return None
    marker = account_marker(root)
    if marker:
        found = [p for p in found if p.parent.name == marker]
    elif len({p.parent.name for p in found}) > 1:
        raise SyncError("無法確認目前登入的帳號；請先在 Desktop 登入並開啟 Code 分頁。")
    if not found:
        return None
    if len(found) != 1:
        raise SyncError("帳號有多個 Code 組織，不能自動判定；沒有同步或切號。")
    return found[0]


def profile_roots(active, store, target):
    roots = [active.resolve(), target.resolve()]
    if store.is_dir():
        for path in sorted(store.iterdir()):
            if PROFILE.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
                roots.append(path.resolve())
    return list(dict.fromkeys(p for p in roots if p.is_dir()))


def tree_entries(root):
    entries = {}
    pending = [root]
    while pending:
        folder = pending.pop()
        for path in folder.iterdir():
            mode = path.lstat().st_mode
            relative = path.relative_to(root)
            if stat.S_ISLNK(mode):
                entries[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(mode):
                entries[relative] = ("dir", None)
                pending.append(path)
            elif stat.S_ISREG(mode):
                entries[relative] = ("file", None)
            else:
                raise SyncError("臨時工作目錄包含不受支援的檔案；沒有同步。")
    return entries


def trees_equal(first, second):
    if not second.exists():
        return False
    if not second.is_dir():
        raise SyncError("臨時工作目錄格式不受支援；沒有同步。")
    entries = tree_entries(first)
    if entries != tree_entries(second):
        return False
    for relative, (kind, _) in entries.items():
        if kind == "file":
            with (first / relative).open("rb") as a, (second / relative).open("rb") as b:
                while True:
                    left, right = a.read(1024 * 1024), b.read(1024 * 1024)
                    if left != right:
                        return False
                    if not left:
                        break
    return True


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class Plan:
    def __init__(self, target):
        self.target = target.resolve()
        self.changes = {}
        self.added = self.updated = self.skipped = self.hidden = self.workspaces = 0
        self.not_ready = False

    def put(self, relative, value):
        path = safe_path(self.target, relative)
        if value is None:
            if path.exists():
                self.changes[Path(relative)] = None
        elif isinstance(value, Path):
            tree_entries(value)
            if not trees_equal(value, path):
                self.changes[Path(relative)] = value
                self.workspaces += 1
        elif not path.exists() or path.read_bytes() != value:
            self.changes[Path(relative)] = value

    def summary(self):
        if self.not_ready:
            return "尚未同步：請登入目標帳號、開啟 Code 分頁，再選「同步 Code 對話」。"
        return (f"Code 對話：新增 {self.added}，更新 {self.updated}，隱藏已刪除 {self.hidden}，"
                f"工作目錄 {self.workspaces}，跳過缺少完整歷史或工作目錄 {self.skipped}。")

    def apply(self):
        if not self.changes:
            return
        backup_base = safe_path(self.target, ".code-sync-backups")
        backup = backup_base / uuid.uuid4().hex
        backup.mkdir(parents=True, mode=0o700)
        before = backup / "before"
        staging = backup / "staging"
        entries = []
        completed = []
        try:
            # Prepare every replacement and backup before changing any session.
            for relative, value in self.changes.items():
                dest = safe_path(self.target, relative)
                old = before / relative
                existed = dest.exists()
                if existed:
                    old.parent.mkdir(parents=True, exist_ok=True)
                    if dest.is_dir():
                        shutil.copytree(dest, old, symlinks=True)
                    else:
                        shutil.copy2(dest, old)
                staged = staging / relative
                if value is not None:
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    if isinstance(value, Path):
                        shutil.copytree(value, staged, symlinks=True)
                    else:
                        staged.write_bytes(value)
                        staged.chmod(0o600)
                entries.append({"path": str(relative), "existed": existed,
                                "directory": isinstance(value, Path)})
            (backup / "manifest.json").write_bytes(encoded({"entries": entries, "state": "prepared"}))
            # Workspaces first, so a published card never points at an unprepared directory.
            ordered = sorted(self.changes, key=lambda rel: not isinstance(self.changes[rel], Path))
            for relative in ordered:
                dest = safe_path(self.target, relative)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_dir():
                    os.replace(dest, backup / ("replaced-" + str(len(completed) + 1)))
                    completed.append(relative)
                if self.changes[relative] is None:
                    if dest.exists():
                        dest.unlink()
                else:
                    os.replace(staging / relative, dest)
                if relative not in completed:
                    completed.append(relative)
            (backup / "manifest.json").write_bytes(encoded({"entries": entries, "state": "complete"}))
        except Exception:
            # Restore every changed UI/workspace file; original transcripts were never opened.
            failed_restore = False
            for relative in reversed(completed):
                try:
                    dest = safe_path(self.target, relative)
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    elif dest.exists():
                        dest.unlink()
                    old = before / relative
                    if old.is_dir():
                        shutil.copytree(old, dest, symlinks=True)
                    elif old.exists():
                        shutil.copy2(old, dest)
                except OSError:
                    failed_restore = True
            if failed_restore:
                raise SyncError("同步失敗，自動回復未完成；沒有切號，請從保留的同步備份回復。") from None
            raise SyncError("同步未完成，已回復本次改動；備份保留在帳號資料夾。") from None


def make_plan(active, store, target, projects):
    plan = Plan(target)
    dest = destination(plan.target)
    if dest is None:
        plan.not_ready = True
        return plan
    roots = profile_roots(active, store, target)
    active_real = active.resolve()
    active_account = account_marker(active_real) if buckets(active_real) else None
    copies = {}
    deleted = set()
    for root in roots:
        for bucket in buckets(root):
            for file in bucket.iterdir():
                if file.name.startswith("deleted_") and UUID.fullmatch(file.name[8:]):
                    safe_path(root, file.relative_to(root))
                    deleted.add(file.name[8:])
                elif file.name.startswith("local_") and file.suffix == ".json":
                    sid = file.stem
                    if not UUID.fullmatch(sid[6:]):
                        raise SyncError("會話 ID 格式不受支援；沒有同步。")
                    card = read_object(file)
                    if card.get("sessionId") != sid:
                        raise SyncError("會話 ID 與檔名不一致；沒有同步。")
                    activity = card.get("lastActivityAt", card.get("createdAt", 0))
                    if not isinstance(activity, (int, float)) or isinstance(activity, bool):
                        raise SyncError("會話時間格式不受支援；沒有同步。")
                    current = root == active_real and bucket.parent.name == active_account
                    rank = (activity, current, bucket == dest)
                    copies.setdefault(sid, []).append((rank, file, card, root))
    imported = {}
    for sid, versions in sorted(copies.items()):
        _, file, card, root = max(versions, key=lambda entry: entry[0])
        own = dest / (sid + ".json")
        cli = card.get("cliSessionId")
        if sid[6:] in deleted or cli in deleted:
            if own.exists():
                plan.put(own.relative_to(plan.target), None)
                plan.hidden += 1
            continue
        history_ids = [cli, *card.get("priorCliSessionIds", [])]
        if any(not isinstance(s, str) or not UUID.fullmatch(s)
               or not any(projects.glob("*/" + s + ".jsonl")) for s in history_ids):
            plan.skipped += 1
            continue
        cwd = card.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or ".." in Path(cwd).parts:
            plan.skipped += 1
            continue
        scratch = None
        scratch_required = False
        for prefix in (active / "scratch-workspaces", root / "scratch-workspaces"):
            try:
                parts = Path(cwd).relative_to(prefix).parts
            except ValueError:
                continue
            scratch_required = True
            if len(parts) < 3:
                raise SyncError("臨時工作目錄格式不受支援；沒有同步。")
            relative = Path("scratch-workspaces", *parts[:3])
            source = safe_path(root, relative)
            if not source.is_dir():
                plan.skipped += 1
                break
            # Keep the app's literal stable path, never symlink its scratch root.
            if prefix != active / "scratch-workspaces":
                raise SyncError("會話仍使用舊帳號的臨時路徑；請先完成舊版配置轉換。")
            plan.put(relative, source)
            scratch = source
            break
        else:
            if not Path(cwd).is_dir():
                plan.skipped += 1
                continue
        if scratch_required and scratch is None:
            continue
        result = copy.deepcopy(card)
        own_card = read_object(own) if own.exists() else {}
        if file != own:
            for key, default in ACCOUNT_FIELDS.items():
                result[key] = own_card.get(key, copy.deepcopy(default))
            result["permissionMode"] = own_card.get("permissionMode", "default")
        data = encoded(result)
        if not own.exists():
            plan.added += 1
        elif read_object(own) != result:
            plan.updated += 1
        if not own.exists() or own_card != result:
            plan.put(own.relative_to(plan.target), data)
        imported[sid] = result
    for identifier in sorted(deleted):
        marker = dest / ("deleted_" + identifier)
        if not marker.exists():
            plan.put(marker.relative_to(plan.target), b"")
    # Carry only recorded leases required by the selected sessions; never run git.
    registries = []
    for root in roots:
        path = root / "git-worktrees.json"
        if path.exists():
            registry = read_object(path)
            if registry.get("schemaVersion") != 2 or not isinstance(registry.get("worktrees", {}), dict):
                if any(card.get("worktreeName") for card in imported.values()):
                    raise SyncError("Git 工作樹登記格式不受支援；沒有同步或切號。")
                continue
            registries.append(registry)
    required = [card for card in imported.values() if card.get("worktreeName") and not card.get("isArchived")]
    if required:
        path = plan.target / "git-worktrees.json"
        registry = read_object(path) if path.exists() else {"schemaVersion": 2, "worktrees": {}}
        owners = {}
        for card in required:
            name, cwd, sid = card["worktreeName"], card["cwd"], card["sessionId"]
            if cwd in owners and owners[cwd] != sid:
                raise SyncError("兩段會話同時佔用一個 Git 工作樹；沒有同步或切號。")
            owners[cwd] = sid
            options = [r.get("worktrees", {}).get(name) for r in registries]
            entry = next((item for item in options if isinstance(item, dict)
                          and item.get("path") == cwd and item.get("leasedBy") in (None, sid)), None)
            if entry is None:
                raise SyncError("找不到匹配的 Git 工作樹登記；沒有同步或切號。")
            registry.setdefault("worktrees", {})[name] = copy.deepcopy(entry)
            registry["worktrees"][name]["leasedBy"] = sid
        plan.put(Path("git-worktrees.json"), encoded(registry))
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("active-root", "store", "target-root", "projects"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        plan = make_plan(args.active_root, args.store, args.target_root, args.projects)
        if not args.check:
            plan.apply()
        print(plan.summary())
    except (SyncError, OSError, ValueError, TypeError) as error:
        print(str(error) if isinstance(error, SyncError) else "同步資料無法讀寫；沒有切號。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
