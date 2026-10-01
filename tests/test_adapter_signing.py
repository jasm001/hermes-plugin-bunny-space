# v1.0.3 — Firma del relé: espejo Python del canónico Node
# (`src/lib/bot-agents/bot-request-signature.ts` del producto). Congela el
# vector cruzado (Node/Python) para que ninguna mitad derive en silencio.
#
# Run (desde hermes-agent, como el resto de las pruebas del conector):
#   ./venv/Scripts/python.exe -m unittest discover -s \
#     "$LOCALAPPDATA/hermes/plugins/bunny_space/tests" -v
import io
import json as _json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERMES_SOURCE = Path(__file__).resolve().parents[3] / "hermes-agent"
PLUGIN_DIR = Path(__file__).resolve().parents[1]
for entry in (str(HERMES_SOURCE), str(PLUGIN_DIR.parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import bunny_space.adapter as adapter_module  # noqa: E402
from bunny_space.adapter import (  # noqa: E402
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
    _sign_relay_request,
)

#: Vector cruzado CONGELADO — espejo exacto de tests/bot-request-signature.test.ts.
CROSS_SECRET = "bs_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
CROSS_TS = "1759262400"
CROSS_SIGNATURE_POST = "7024ce33cb02151d21dcf55d01ddf3fc502febf794a42a2f673d65ba4ca286ed"
CROSS_SIGNATURE_GET = "5e9404fcc36a3f597239fc38c7fc3d86448e626f4abf459c252cf6ca42d6257c"
CROSS_BODY_POST = '{"taskId":"t1","output":"hola"}'


class RelaySignatureVectorTests(unittest.TestCase):
    def test_vector_cruzado_post(self):
        ts, sig = _sign_relay_request(
            CROSS_SECRET,
            "POST",
            "/api/bot/v1/update",
            CROSS_BODY_POST.encode("utf-8"),
            timestamp=CROSS_TS,
        )
        self.assertEqual(ts, CROSS_TS)
        self.assertEqual(sig, CROSS_SIGNATURE_POST)

    def test_vector_cruzado_get_sin_cuerpo_y_con_query(self):
        # El relé firma el PATHNAME: la query no entra; el método se normaliza.
        _, sig = _sign_relay_request(
            CROSS_SECRET, "get", "/api/bot/v1/tasks?bot=luna", b"", timestamp=CROSS_TS
        )
        self.assertEqual(sig, CROSS_SIGNATURE_GET)

    def test_cabeceras_canonicas(self):
        self.assertEqual(SIGNATURE_TIMESTAMP_HEADER, "x-bot-timestamp")
        self.assertEqual(SIGNATURE_HEADER, "x-bot-signature")


def _build_adapter():
    adapter = adapter_module.BunnySpaceAdapter.__new__(adapter_module.BunnySpaceAdapter)
    adapter.base_url = "https://relay.example"
    adapter.bot_key = "bt_test_key"
    adapter.slug = "luna"
    adapter.signature_secret = CROSS_SECRET
    adapter.signature_required = False
    adapter.signature_timestamp_header = SIGNATURE_TIMESTAMP_HEADER
    adapter.signature_header = SIGNATURE_HEADER
    adapter.session_assistance_capable = False
    adapter.session_reason = ""
    adapter.provision_digest = ""
    return adapter


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._payload


class RelaySignatureWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_relay_json_firma_cuando_hay_secreto(self):
        adapter = _build_adapter()
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _FakeResponse(_json.dumps({"ok": True}).encode("utf-8"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = await adapter._relay_json("GET", "/api/bot/v1/tasks?bot=luna")

        self.assertEqual(result, {"ok": True})
        headers = captured["headers"]
        self.assertIn(SIGNATURE_TIMESTAMP_HEADER, headers)
        self.assertIn(SIGNATURE_HEADER, headers)
        ts = headers[SIGNATURE_TIMESTAMP_HEADER]
        _, expected = _sign_relay_request(CROSS_SECRET, "GET", "/api/bot/v1/tasks", b"", timestamp=ts)
        self.assertEqual(headers[SIGNATURE_HEADER], expected)

    async def test_la_sesion_nunca_se_firma(self):
        adapter = _build_adapter()
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _FakeResponse(_json.dumps({"contractVersion": "BOT_PROVISION_V1"}).encode("utf-8"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            await adapter._relay_json("GET", "/api/bot/v1/session")

        self.assertNotIn(SIGNATURE_HEADER, captured["headers"])
        self.assertNotIn(SIGNATURE_TIMESTAMP_HEADER, captured["headers"])

    async def test_firma_rechazada_relee_sesion_y_reintenta(self):
        adapter = _build_adapter()
        calls = []
        state = {"n": 0}

        def fake_urlopen(req, timeout=None):
            state["n"] += 1
            url = req.full_url
            calls.append(url)
            if url.endswith("/api/bot/v1/session"):
                body = {
                    "contractVersion": "BOT_PROVISION_V1",
                    "bot": {"slug": "luna"},
                    "capabilities": [],
                    "signature": {
                        "algorithm": "hmac-sha256",
                        "required": True,
                        "secret": CROSS_SECRET,
                        "timestampHeader": "x-bot-timestamp",
                        "signatureHeader": "x-bot-signature",
                    },
                }
                return _FakeResponse(_json.dumps(body).encode("utf-8"))
            if state["n"] == 1:
                payload = _json.dumps(
                    {
                        "error": "Firma del relé inválida o ausente",
                        "code": "BOT_SIGNATURE_INVALID",
                        "reason": "mismatch",
                    }
                ).encode("utf-8")
                raise urllib.error.HTTPError(url, 401, "Unauthorized", None, io.BytesIO(payload))
            return _FakeResponse(_json.dumps({"tasks": []}).encode("utf-8"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = await adapter._relay_json("GET", "/api/bot/v1/tasks?bot=luna")

        self.assertEqual(result, {"tasks": []})
        self.assertTrue(any(u.endswith("/api/bot/v1/session") for u in calls))
        self.assertEqual(len([u for u in calls if "/tasks" in u]), 2)
        self.assertTrue(adapter.signature_required)
