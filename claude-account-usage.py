#!/usr/bin/env python3
"""Read Desktop account usage; credentials are decrypted in memory and never changed."""

import argparse
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ACCOUNT = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
API = "https://api.anthropic.com/api/oauth/"
MIN_REFRESH = 60
CACHE = ".usage-cache.json"


class UsageError(Exception):
    def __init__(self, message, retry_after=MIN_REFRESH):
        super().__init__(message)
        self.retry_after = retry_after


def read_json(path, default=None):
    try:
        if path.is_symlink() or path.stat().st_size > 4_000_000:
            return default
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError, UnicodeError):
        return default


def profiles(active, store, active_name=""):
    entries = sorted(store.iterdir()) if store.is_dir() else []
    if not active_name:
        active_name = next((p.name for p in entries if NAME.fullmatch(p.name)
                            and p.is_symlink() and p.resolve() == active.resolve()), "")
    result = []
    if active.is_dir():
        result.append({"name": active_name, "path": active, "active": True})
    for path in entries:
        if NAME.fullmatch(path.name) and path.is_dir() and not path.is_symlink() and path.resolve() != active.resolve():
            result.append({"name": path.name, "path": path, "active": False})
    for profile in result:
        config = read_json(profile["path"] / "config.json", {})
        aid = config.get("lastKnownAccountUuid", "")
        profile["account"] = aid if isinstance(aid, str) and ACCOUNT.fullmatch(aid) else ""
        buckets = profile["path"] / "claude-code-sessions" / profile["account"]
        profile["organizations"] = sorted(p.name for p in buckets.iterdir()
                                            if ACCOUNT.fullmatch(p.name) and p.is_dir()) if profile["account"] and buckets.is_dir() else []
    return result


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_window(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not number(value.get("utilization")):
        raise UsageError("服务器额度数据不可用")
    used = value["utilization"]
    if not 0 <= used <= 100:
        raise UsageError("服务器额度数据不可用")
    reset = value.get("resets_at")
    if reset is not None:
        if not isinstance(reset, str):
            raise UsageError("服务器重置时间不可用")
        try:
            date = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError
            reset = date.timestamp()
        except (ValueError, OverflowError):
            raise UsageError("服务器重置时间不可用") from None
    return {"used": used, "reset": reset}


def desktop_key():
    """Read the existing, unlocked Claude SecretService item; never create/unlock it."""
    try:
        bus = Path(f"/run/user/{os.getuid()}/bus")
        if "DBUS_SESSION_BUS_ADDRESS" not in os.environ and bus.exists():
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        service = Secret.Service.get_sync(Secret.ServiceFlags.NONE, None)
        items = service.search_sync(None, {"application": "Claude",
                                     "xdg:schema": "chrome_libsecret_os_crypt_password_v2"},
                                    Secret.SearchFlags.ALL, None)
        items = [item for item in items if item.get_label() == "Claude Safe Storage"]
        if len(items) != 1 or items[0].get_locked():
            raise UsageError("系统密钥环不可用或已锁定")
        items[0].load_secret_sync(None)
        password = items[0].get_secret().get_text()
        if not password:
            raise UsageError("系统密钥环不可用或已锁定")
        return password.encode()
    except UsageError:
        raise
    except Exception:
        raise UsageError("无法读取系统密钥环；需要 Python SecretService 支持") from None


def decrypt_cache(encoded, key_reader=desktop_key):
    try:
        blob = base64.b64decode(encoded, validate=True)
        if blob[:3] not in (b"v10", b"v11"):
            raise UsageError("Desktop 登录存储格式不支持")
        password = b"peanuts" if blob[:3] == b"v10" else key_reader()
        key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
        decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(decryptor.update(blob[3:]) + decryptor.finalize()) + unpadder.finalize()
        result = json.loads(plain)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except UsageError:
        raise
    except ImportError:
        raise UsageError("需要 Python cryptography 才能读取 Desktop 登录状态") from None
    except Exception:
        raise UsageError("无法读取 Desktop 登录状态；请在 Desktop 重新登录") from None


def access_token(config, aid, now, decoder=decrypt_cache):
    candidates, error = [], None
    for field in ("oauth:tokenCacheV2", "oauth:tokenCache"):
        encoded = config.get(field)
        if not isinstance(encoded, str):
            continue
        try:
            cache = decoder(encoded)
        except UsageError as problem:
            error = problem
            continue
        for key, value in cache.items():
            if not (isinstance(key, str) and key.startswith("acct:" + aid + "|")
                    and "user:profile" in key and "api.anthropic.com" in key
                    and isinstance(value, dict)):
                continue
            expires = value.get("expiresAt")
            token = value.get("token")
            parts = key.partition("|")[2].split(":", 2)
            org = parts[1] if len(parts) == 3 else ""
            if not ACCOUNT.fullmatch(org) or (config.get("_usageOrganization") and org != config["_usageOrganization"]):
                continue
            if number(expires) and expires > (now + 5) * 1000 and isinstance(token, str) and token:
                candidates.append((expires, token, org))
    if not candidates:
        raise error or UsageError("登录已过期；请在该账号的 Desktop 中重新登录")
    if len({candidate[2] for candidate in candidates}) != 1:
        raise UsageError("多组织账号：无法确定要显示哪个组织的额度")
    selected = max(candidates, key=lambda candidate: candidate[0])
    return selected[1], selected[2]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api_get(endpoint, token, now):
    if endpoint not in ("profile", "usage"):
        raise UsageError("不支持的用量请求")
    request = urllib.request.Request(API + endpoint, headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-account-switcher/0.4.3", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise UsageError("服务器返回的数据过大")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise UsageError("服务器额度数据不可用")
            return value
    except urllib.error.HTTPError as error:
        if error.code == 429:
            delay = 300
            value = error.headers.get("Retry-After", "")
            try:
                delay = max(MIN_REFRESH, int(value))
            except ValueError:
                try:
                    delay = max(MIN_REFRESH, parsedate_to_datetime(value).timestamp() - now)
                except (ValueError, TypeError, OverflowError):
                    pass
            raise UsageError("请求限流，稍后再刷新", delay) from None
        if error.code in (401, 403):
            raise UsageError("该账号登录失效或无权读取额度") from None
        raise UsageError(f"读取失败（HTTP {error.code}）") from None
    except UsageError:
        raise
    except Exception:
        raise UsageError("网络或服务器不可用") from None


def save_cache(store, cache):
    path = store / CACHE
    if store.is_symlink() or path.is_symlink():
        raise UsageError("用量缓存位置不安全；未保存")
    descriptor, temporary = tempfile.mkstemp(prefix=".usage-", dir=store)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(cache, output, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def refresh(active, store, active_name="", now=None, fetch=api_get, token_reader=access_token):
    now = time.time() if now is None else now
    if store.is_symlink() or (store / CACHE).is_symlink():
        raise UsageError("账号存储位置不支持")
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = store / ".lock"
    if lock_path.is_symlink():
        raise UsageError("账号锁文件不安全")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UsageError("账号正在切换或刷新，请稍后重试") from None
        cache = read_json(store / CACHE, {})
        accounts = cache.get("accounts", {})
        if not isinstance(accounts, dict):
            accounts = {}
        summaries = []
        for profile in profiles(active, store, active_name):
            aid = profile["account"]
            name = profile["name"] or "当前账号"
            if not aid:
                summaries.append(name + "：尚未登录")
                continue
            old = accounts.get(aid, {})
            old = old if isinstance(old, dict) else {}
            if number(old.get("retryAt")) and old["retryAt"] > now:
                summaries.append(name + "：请稍后刷新（保留上次结果）")
                continue
            try:
                config = read_json(profile["path"] / "config.json", {})
                organizations = profile["organizations"]
                if len(organizations) > 1:
                    raise UsageError("多组织账号：无法确定要显示哪个组织的额度")
                if organizations:
                    config["_usageOrganization"] = organizations[0]
                token, org = token_reader(config, aid, now)
                identity = fetch("profile", token, now)
                if (not isinstance(identity.get("account"), dict) or identity["account"].get("uuid") != aid
                        or not isinstance(identity.get("organization"), dict) or identity["organization"].get("uuid") != org):
                    raise UsageError("返回的账号身份不一致；没有采用额度数据")
                usage = fetch("usage", token, now)
                windows = {key: parse_window(usage.get(key)) for key in ("five_hour", "seven_day")}
                if not any(windows.values()):
                    raise UsageError("该账号未提供套餐额度")
                accounts[aid] = {"organization": org, "checkedAt": now, "retryAt": now + MIN_REFRESH,
                                 "windows": windows, "error": ""}
                summaries.append(name + "：更新成功")
            except UsageError as error:
                accounts[aid] = {**old, "retryAt": now + error.retry_after, "error": str(error)}
                summaries.append(name + "：" + str(error))
        save_cache(store, {"version": 1, "accounts": accounts})
        return summaries


def date_label(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime("%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return "未知"


def window_cells(window, now):
    if not isinstance(window, dict) or not number(window.get("used")) or not 0 <= window["used"] <= 100:
        return "不可用", "未知"
    reset = window.get("reset")
    if number(reset) and reset <= now:
        return "待刷新", "已到重置时间"
    remaining = f"{100 - window['used']:.1f}".rstrip("0").rstrip(".")
    label = date_label(reset) if number(reset) else "未开始" if window["used"] == 0 else "未知"
    return remaining + "% 剩余", label


def rows(active, store, active_name="", now=None):
    now = time.time() if now is None else now
    cache = read_json(store / CACHE, {}).get("accounts", {})
    cache = cache if isinstance(cache, dict) else {}
    result = []
    for profile in profiles(active, store, active_name):
        stored = cache.get(profile["account"], {})
        stored = stored if isinstance(stored, dict) else {}
        organizations = profile["organizations"]
        if stored.get("windows") and (len(organizations) > 1 or
                       (organizations and stored.get("organization") != organizations[0])):
            stored = {"error": "组织信息已变化；请刷新额度"}
        windows = stored.get("windows", {})
        windows = windows if isinstance(windows, dict) else {}
        seen = stored.get("checkedAt")
        status = "更新 " + date_label(seen) if number(seen) else "点击刷新额度"
        weekly = window_cells(windows.get("seven_day"), now)
        five = window_cells(windows.get("five_hour"), now)
        if not windows and profile["account"]:
            samples = read_json(profile["path"] / "plan-usage-history.json", {}).get("samples", [])
            samples = [sample for sample in samples if isinstance(sample, dict)
                       and number(sample.get("t")) and isinstance(sample.get("u"), dict)] if isinstance(samples, list) else []
            if len(profile["organizations"]) == 1:
                samples = [sample for sample in samples if sample.get("org") == profile["organizations"][0]]
            elif len(profile["organizations"]) > 1:
                samples = []
            sample = max(samples, key=lambda value: value["t"], default=None)
            if sample:
                sample_time = sample["t"] / 1000
                if sample_time <= now + 60:
                    weekly = window_cells({"used": sample["u"].get("sd")}, now)
                    five = window_cells({"used": sample["u"].get("fh")}, now)
                    # A local percentage sample never establishes a reset time, even at zero.
                    weekly, five = (weekly[0], "未知"), (five[0], "未知")
                    status = "本机快照 " + date_label(sample_time)
                    if now - sample_time >= 5 * 3600:
                        five = ("待刷新", "未知")
                    if now - sample_time >= 7 * 86400:
                        weekly = ("待刷新", "未知")
        if isinstance(stored.get("error"), str) and stored["error"]:
            status = stored["error"] + "；" + status
        if not profile["account"]:
            status = "尚未登录"
        name = profile["name"] or "尚未命名"
        action = "active" if profile["active"] else "switch:" + profile["name"]
        display = name + "（当前）" if profile["active"] else name
        result.append([action, display, *weekly, *five, status])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-root", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--active-name", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--rows", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    progress = None
    try:
        if args.refresh:
            if args.progress:
                progress = subprocess.Popen(["zenity", "--progress", "--pulsate", "--auto-close", "--no-cancel",
                                             "--title=刷新账号额度", "--text=正在读取各账号的周额度和 5 小时额度…"],
                                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("\n".join(refresh(args.active_root, args.store, args.active_name)))
        elif args.rows:
            for row in rows(args.active_root, args.store, args.active_name):
                for cell in row:
                    sys.stdout.write(str(cell).replace("\x00", "") + "\x00")
        else:
            print(json.dumps(rows(args.active_root, args.store, args.active_name), ensure_ascii=False))
    except UsageError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("额度数据暂时不可用；账号登录状态没有修改。", file=sys.stderr)
        return 1
    finally:
        if progress:
            progress.stdin.close()
            try:
                progress.wait(timeout=2)
            except subprocess.TimeoutExpired:
                progress.terminate()
                progress.wait(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
