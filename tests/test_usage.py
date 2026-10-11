#!/usr/bin/env python3
"""Synthetic account/HTTP tests. Never query or change a real account."""

import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("usage", ROOT / "claude-account-usage.py")
USAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USAGE)
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
ORG = "33333333-3333-3333-3333-333333333333"
OTHER = "44444444-4444-4444-4444-444444444444"
NOW = 1791690000


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def window(used, reset):
    return {"utilization": used, "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None}


class UsageTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.base = Path(self.temp.name)
        self.active = self.base / "Claude"
        self.store = self.base / "Claude-accounts"
        self.work = self.store / "work"
        for root, aid in ((self.active, A), (self.work, B)):
            write_json(root / "config.json", {"lastKnownAccountUuid": aid,
                       "oauth:tokenCacheV2": "SYNTHETIC-ENCRYPTED-CACHE"})
            (root / "claude-code-sessions" / aid / ORG).mkdir(parents=True)
        (self.store / "main").symlink_to(self.active, target_is_directory=True)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def token(self, config, aid, now):
        return "SYNTHETIC-" + aid, ORG

    def fetch(self, endpoint, token, now):
        aid = token.removeprefix("SYNTHETIC-")
        self.calls.append((endpoint, aid))
        if endpoint == "profile":
            return {"account": {"uuid": aid}, "organization": {"uuid": ORG}}
        return {"five_hour": window(100 if aid == A else 0, NOW + 3600 if aid == A else None),
                "seven_day": window(14 if aid == A else 96, NOW + 4 * 86400)}

    def refresh(self, **kwargs):
        return USAGE.refresh(self.active, self.store, now=NOW, fetch=self.fetch,
                             token_reader=self.token, **kwargs)

    def test_both_accounts_have_four_cells_and_credentials_are_unchanged(self):
        before = [(root / "config.json").read_bytes() for root in (self.active, self.work)]
        self.refresh()
        rows = USAGE.rows(self.active, self.store, now=NOW)
        self.assertEqual([row[0] for row in rows], ["active", "switch:work"])
        self.assertEqual([row[2] for row in rows], ["86% 剩余", "4% 剩余"])
        self.assertEqual([row[4] for row in rows], ["0% 剩余", "100% 剩余"])
        self.assertEqual(rows[1][5], "未开始")
        self.assertNotEqual(rows[0][3], "未知")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(before, [(root / "config.json").read_bytes() for root in (self.active, self.work)])
        raw = (self.store / USAGE.CACHE).read_text()
        self.assertNotIn("SYNTHETIC", raw)
        self.assertNotIn("token", raw.lower())
        self.assertEqual((self.store / USAGE.CACHE).stat().st_mode & 0o777, 0o600)

    def test_refresh_interval_does_not_issue_another_request(self):
        self.refresh()
        self.refresh()
        self.assertEqual(len(self.calls), 4)

    def test_identity_mismatch_never_fetches_or_adopts_wrong_usage(self):
        def wrong(endpoint, token, now):
            self.calls.append(endpoint)
            return {"account": {"uuid": OTHER}, "organization": {"uuid": ORG}}
        USAGE.refresh(self.active, self.store, now=NOW, fetch=wrong, token_reader=self.token)
        self.assertEqual(self.calls, ["profile", "profile"])
        self.assertEqual(USAGE.rows(self.active, self.store, now=NOW)[0][2], "不可用")

    def test_organization_mismatch_never_fetches_wrong_usage(self):
        def wrong(endpoint, token, now):
            return {"account": {"uuid": token.removeprefix("SYNTHETIC-")}, "organization": {"uuid": OTHER}}
        summaries = USAGE.refresh(self.active, self.store, now=NOW, fetch=wrong, token_reader=self.token)
        self.assertTrue(all("身份不一致" in value for value in summaries))

    def test_multiple_desktop_organizations_refuse_before_auth(self):
        (self.active / "claude-code-sessions" / A / OTHER).mkdir()
        self.refresh()
        self.assertFalse(any(aid == A for _, aid in self.calls))

    def test_server_failure_keeps_last_valid_values_with_error_status(self):
        self.refresh()
        def fail(*args):
            raise USAGE.UsageError("网络或服务器不可用")
        USAGE.refresh(self.active, self.store, now=NOW + 61, fetch=fail, token_reader=self.token)
        row = USAGE.rows(self.active, self.store, now=NOW + 61)[0]
        self.assertEqual(row[2], "86% 剩余")
        self.assertIn("网络", row[6])

    def test_rate_limit_wait_is_respected_and_does_not_erase_data(self):
        self.refresh()
        def limited(*args):
            raise USAGE.UsageError("请求限流，稍后再刷新", 1200)
        USAGE.refresh(self.active, self.store, now=NOW + 61, fetch=limited, token_reader=self.token)
        self.calls.clear()
        USAGE.refresh(self.active, self.store, now=NOW + 121, fetch=self.fetch, token_reader=self.token)
        self.assertEqual(self.calls, [])
        self.assertEqual(USAGE.rows(self.active, self.store, now=NOW + 121)[0][2], "86% 剩余")

    def test_expired_reset_never_becomes_zero_or_an_invented_next_reset(self):
        self.refresh()
        row = USAGE.rows(self.active, self.store, now=NOW + 3601)[0]
        self.assertEqual(row[4:6], ["待刷新", "已到重置时间"])

    def test_missing_window_is_unavailable_not_full(self):
        self.assertEqual(USAGE.window_cells(None, NOW), ("不可用", "未知"))
        self.assertIsNone(USAGE.parse_window(None))

    def test_invalid_percentages_and_unzoned_dates_are_rejected(self):
        for value in (True, -1, 101, float("nan"), "20"):
            with self.subTest(value=value), self.assertRaises(USAGE.UsageError):
                USAGE.parse_window({"utilization": value, "resets_at": None})
        with self.assertRaises(USAGE.UsageError):
            USAGE.parse_window({"utilization": 20, "resets_at": "2026-10-15T12:00:00"})

    def test_local_sample_has_unknown_resets_and_never_fabricates_zero(self):
        write_json(self.active / "plan-usage-history.json", {"samples": [
            {"t": (NOW - 6 * 3600) * 1000, "org": ORG, "u": {"fh": 100, "sd": 96}}]})
        row = USAGE.rows(self.active, self.store, now=NOW)[0]
        self.assertEqual(row[2:6], ["4% 剩余", "未知", "待刷新", "未知"])
        self.assertIn("本机快照", row[6])

    def test_different_organization_sample_is_not_assigned_to_this_account(self):
        write_json(self.active / "plan-usage-history.json", {"samples": [
            {"t": NOW * 1000, "org": OTHER, "u": {"fh": 0, "sd": 0}}]})
        self.assertEqual(USAGE.rows(self.active, self.store, now=NOW)[0][2], "不可用")

    def test_changed_organization_does_not_reuse_cached_quota(self):
        self.refresh()
        bucket = self.active / "claude-code-sessions" / A
        (bucket / ORG).rename(bucket / OTHER)
        row = USAGE.rows(self.active, self.store, now=NOW)[0]
        self.assertEqual(row[2:6], ["不可用", "未知", "不可用", "未知"])
        self.assertIn("组织信息已变化", row[6])

    def test_signed_out_account_does_not_inherit_old_sample(self):
        write_json(self.active / "config.json", {})
        write_json(self.active / "plan-usage-history.json", {"samples": [
            {"t": NOW * 1000, "org": ORG, "u": {"fh": 0, "sd": 0}}]})
        row = USAGE.rows(self.active, self.store, now=NOW)[0]
        self.assertEqual(row[2], "不可用")
        self.assertEqual(row[6], "尚未登录")

    def test_cache_symlink_is_refused_without_requests_or_changes(self):
        foreign = self.base / "foreign"
        foreign.write_text("preserve")
        (self.store / USAGE.CACHE).symlink_to(foreign)
        with self.assertRaises(USAGE.UsageError):
            self.refresh()
        self.assertEqual(self.calls, [])
        self.assertEqual(foreign.read_text(), "preserve")

    def test_account_rename_reuses_id_but_a_new_login_does_not(self):
        self.refresh()
        self.work.rename(self.store / "renamed")
        row = USAGE.rows(self.active, self.store, now=NOW)[1]
        self.assertEqual(row[0], "switch:renamed")
        self.assertEqual(row[2], "4% 剩余")
        write_json(self.store / "renamed" / "config.json", {"lastKnownAccountUuid": OTHER})
        self.assertEqual(USAGE.rows(self.active, self.store, now=NOW)[1][2], "不可用")

    def test_http_429_is_sanitized_and_retry_after_is_preserved(self):
        error = urllib.error.HTTPError(USAGE.API + "usage", 429, "SYNTHETIC-SECRET", {"Retry-After": "900"}, io.BytesIO(b"SYNTHETIC-SECRET"))
        with patch.object(USAGE.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(USAGE.UsageError) as result:
                USAGE.api_get("usage", "SYNTHETIC-SECRET", NOW)
            self.assertEqual(result.exception.retry_after, 900)
            self.assertNotIn("SYNTHETIC-SECRET", str(result.exception))

    def test_credentials_are_sent_only_to_the_fixed_api_and_redirects_are_refused(self):
        with self.assertRaises(USAGE.UsageError):
            USAGE.api_get("https://example.test", "SYNTHETIC-SECRET", NOW)
        self.assertIsNone(USAGE.NoRedirect().redirect_request(None, None, None, None, None, None))
        with patch.object(USAGE.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            USAGE.api_get("usage", "SYNTHETIC-SECRET", NOW)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, USAGE.API + "usage")
            self.assertEqual(request.get_method(), "GET")

    def test_token_selection_is_bound_to_account_organization_scope_and_expiry(self):
        def entry(aid, org, expiry, token, scope="user:profile"):
            return (f"acct:{aid}|client:{org}:api.anthropic.com:{scope}", {"token": token, "expiresAt": expiry})
        cache = dict([entry(A, ORG, (NOW + 1000) * 1000, "SYNTHETIC-GOOD"),
                      entry(B, ORG, (NOW + 2000) * 1000, "SYNTHETIC-OTHER"),
                      entry(A, OTHER, (NOW + 2000) * 1000, "SYNTHETIC-ORG"),
                      entry(A, ORG, (NOW - 10) * 1000, "SYNTHETIC-OLD", "user:inference")])
        config = {"oauth:tokenCacheV2": "encrypted", "_usageOrganization": ORG}
        self.assertEqual(USAGE.access_token(config, A, NOW, decoder=lambda _: cache), ("SYNTHETIC-GOOD", ORG))
        del config["_usageOrganization"]
        with self.assertRaises(USAGE.UsageError):
            USAGE.access_token(config, A, NOW, decoder=lambda _: cache)

    def test_desktop_v10_and_v11_decryption_uses_memory_only(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
        plain = {"fixture": {"token": "SYNTHETIC-TOKEN"}}
        for prefix, password in ((b"v10", b"peanuts"), (b"v11", b"SYNTHETIC-KEYRING")):
            key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
            padding = PKCS7(128).padder()
            message = padding.update(json.dumps(plain).encode()) + padding.finalize()
            cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
            blob = base64.b64encode(prefix + cipher.update(message) + cipher.finalize()).decode()
            self.assertEqual(USAGE.decrypt_cache(blob, key_reader=lambda: password), plain)


if __name__ == "__main__":
    unittest.main(verbosity=2)
