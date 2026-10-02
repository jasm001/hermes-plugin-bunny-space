"""Review 2026-10-01: relay media downloads are fail-closed (https + host + cap)."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERMES_SOURCE = Path(__file__).resolve().parents[3] / "hermes-agent"
PLUGIN_DIR = Path(__file__).resolve().parents[1]
for entry in (str(HERMES_SOURCE), str(PLUGIN_DIR.parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import bunny_space.adapter as adapter_module  # noqa: E402
from bunny_space.adapter import (  # noqa: E402
    MEDIA_MAX_BYTES,
    BunnySpaceAdapter,
    _media_host_allowed,
)


class FakeResp:
    def __init__(self, chunks, content_type="image/png"):
        self._chunks = list(chunks)
        self.headers = {"content-type": content_type}

    def read(self, size=-1):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MediaDownloadTests(unittest.TestCase):
    def _stub(self, base_url="https://bunny-space.com"):
        return types.SimpleNamespace(base_url=base_url, _temp_media_paths=[])

    def test_host_allowlist(self):
        self.assertTrue(_media_host_allowed("bunny-space.com", "https://bunny-space.com"))
        self.assertTrue(_media_host_allowed("media.bunny-space.com", "https://bunny-space.com"))
        self.assertTrue(_media_host_allowed("pub-abc.r2.dev", "https://bunny-space.com"))
        self.assertTrue(_media_host_allowed("relay.example.net", "https://relay.example.net"))
        self.assertFalse(_media_host_allowed("evil.example.com", "https://bunny-space.com"))
        self.assertFalse(_media_host_allowed("evilbunny-space.com", "https://bunny-space.com"))
        self.assertFalse(_media_host_allowed("", "https://bunny-space.com"))

    def test_non_https_is_refused_without_fetch(self):
        stub = self._stub()
        with mock.patch.object(adapter_module.urllib.request, "urlopen") as fake:
            result = BunnySpaceAdapter._download_media_sync(stub, "http://bunny-space.com/x.png")
        self.assertIsNone(result)
        fake.assert_not_called()

    def test_unknown_host_is_refused_without_fetch(self):
        stub = self._stub()
        with mock.patch.object(adapter_module.urllib.request, "urlopen") as fake:
            result = BunnySpaceAdapter._download_media_sync(stub, "https://evil.example.com/x.png")
        self.assertIsNone(result)
        fake.assert_not_called()

    def test_oversized_download_is_refused(self):
        stub = self._stub()
        big = b"x" * (MEDIA_MAX_BYTES + 1)
        with mock.patch.object(
            adapter_module.urllib.request, "urlopen", return_value=FakeResp([big])
        ):
            result = BunnySpaceAdapter._download_media_sync(stub, "https://bunny-space.com/x.png")
        self.assertIsNone(result)

    def test_small_download_writes_and_cleans_up(self):
        stub = self._stub()
        with mock.patch.object(
            adapter_module.urllib.request,
            "urlopen",
            return_value=FakeResp([b"tiny-image"]),
        ):
            path = BunnySpaceAdapter._download_media_sync(stub, "https://bunny-space.com/x.png")
        self.assertIsNotNone(path)
        try:
            self.assertTrue(os.path.exists(path))
        finally:
            os.unlink(path)

    def test_cleanup_deletes_expired_files(self):
        stub = self._stub()
        fd, path = tempfile.mkstemp(prefix="bunny_test_")
        os.close(fd)
        stub._temp_media_paths.append(path)
        BunnySpaceAdapter._cleanup_temp_media(stub, max_age_seconds=0)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(stub._temp_media_paths, [])


if __name__ == "__main__":
    unittest.main()
