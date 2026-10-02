"""Harness del conector v2 de Bunny Space (`ISOLATED_ASSISTANCE_V1`).

Prueba el adaptador contra un relé FALSO que reproduce las reglas reales del
servidor (auth por `x-bot-key`, capability obligatoria en el claim — si no,
426; renew con fencing; submit validado contra el binding de la tarea) y un
cerebro simulado, de modo que se verifique el ciclo completo claim → dispatch →
renew → submit sin depender de la app corriendo.

Ejecutar con el intérprete de Hermes (stdlib + PyYAML del venv):

    cd <hermes-agent>
    ./venv/Scripts/python.exe -m unittest discover -s \
      "$LOCALAPPDATA/hermes/plugins/bunny_space/tests" -v
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERMES_SOURCE = Path(__file__).resolve().parents[3] / "hermes-agent"
PLUGIN_DIR = Path(__file__).resolve().parents[1]
for entry in (str(HERMES_SOURCE), str(PLUGIN_DIR.parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from gateway.config import Platform, PlatformConfig  # noqa: E402
from gateway.platform_registry import PlatformEntry, platform_registry  # noqa: E402

import bunny_space.adapter as adapter_module  # noqa: E402
from bunny_space.adapter import (  # noqa: E402
    ASSISTANCE_CAPABILITY,
    BunnySpaceAdapter,
    evaluate_assistance_isolation,
    extract_json_object,
)

BOT_KEY = "bt_test_key"
SLUG = "Hermes"


def _register_platform() -> None:
    if not platform_registry.is_registered("bunny_space"):
        platform_registry.register(
            PlatformEntry(
                name="bunny_space",
                label="Bunny Space",
                adapter_factory=lambda cfg: BunnySpaceAdapter(cfg),
                check_fn=lambda: True,
                required_env=[],
                source="plugin",
                plugin_name="bunny_space-platform",
            )
        )


_register_platform()


class FakeRelay:
    """Relé mínimo que aplica las reglas reales del servidor."""

    def __init__(self):
        self.claim: dict | None = None
        self.claims = 0
        self.session_gets = 0
        self.renews: list[dict] = []
        self.submits: list[dict] = []
        self.renew_error: str | None = None
        self.submit_error: str | None = None
        self.fail_renew_after: int | None = None
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silencioso
                return

            def _read_body(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    return json.loads(raw) if raw else {}
                except Exception:
                    return {}

            def _json(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                if self.headers.get("x-bot-key") != BOT_KEY:
                    self._json(403, {"error": "No autorizado"})
                    return False
                return True

            def do_GET(self):
                if not self._authorized():
                    return
                if self.path.startswith("/api/bot/v1/rooms/messages"):
                    return self._json(200, {"messages": []})
                if self.path.startswith("/api/bot/v1/tasks"):
                    return self._json(200, {"tasks": []})
                if self.path.startswith("/api/bot/v1/session"):
                    relay.session_gets += 1
                    return self._json(404, {"error": "not found"})
                if self.path.startswith("/api/bot/v1/assistance/tasks"):
                    capabilities = (self.headers.get("x-bot-capabilities") or "").split(",")
                    if ASSISTANCE_CAPABILITY not in [c.strip() for c in capabilities]:
                        return self._json(426, {"error": "Actualiza el conector para usar asistencia privada"})
                    relay.claims += 1
                    if relay.claim is None:
                        return self._json(200, {"task": None})
                    if relay.claim.get("_taken"):
                        return self._json(200, {"task": None})
                    relay.claim["_taken"] = True
                    return self._json(200, relay.claim)
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                if not self._authorized():
                    return
                body = self._read_body()
                if self.path.startswith("/api/bot/v1/assistance/update"):
                    if body.get("action") == "renew":
                        relay.renews.append(body)
                        if relay.fail_renew_after is not None and len(relay.renews) > relay.fail_renew_after:
                            return self._json(409, {"error": "LEASE_LOST"})
                        if relay.renew_error:
                            return self._json(409, {"error": relay.renew_error})
                        task = relay.claim or {}
                        lease = task.get("lease") or {}
                        return self._json(
                            200,
                            {
                                "taskId": body.get("taskId"),
                                "lease": {
                                    "generation": lease.get("generation", 1),
                                    "leaseExpiresAt": lease.get("leaseExpiresAt"),
                                },
                            },
                        )
                    if body.get("action") == "submit":
                        relay.submits.append(body)
                        if relay.submit_error:
                            return self._json(422, {"error": relay.submit_error})
                        task = relay.claim or {}
                        expected = task.get("task") or {}
                        lease = task.get("lease") or {}
                        result = body.get("result") or {}
                        if body.get("leaseToken") != lease.get("token"):
                            return self._json(403, {"error": "FENCED"})
                        if body.get("taskId") != expected.get("id"):
                            return self._json(404, {"error": "TASK_NOT_FOUND"})
                        if result.get("taskId") != expected.get("id"):
                            return self._json(422, {"error": "INVALID_RESULT"})
                        if expected.get("purpose") == "POST_REVIEW":
                            if result.get("postId") != expected.get("sourceRef") or result.get(
                                "sourceVersion"
                            ) != expected.get("sourceVersion"):
                                return self._json(422, {"error": "INVALID_RESULT"})
                            if not isinstance(result.get("answer"), str) or not result["answer"].strip():
                                return self._json(422, {"error": "INVALID_RESULT"})
                        else:
                            if result.get("draftId") != expected.get("sourceRef") or result.get(
                                "snapshotDigest"
                            ) != expected.get("sourceVersion"):
                                return self._json(422, {"error": "INVALID_RESULT"})
                            if not isinstance(result.get("tokens"), dict) or not result["tokens"]:
                                return self._json(422, {"error": "INVALID_RESULT"})
                        return self._json(200, {"task": {"id": expected.get("id"), "status": "COMPLETED"}})
                return self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- fabricación de claims -------------------------------------------------

    def post_review_claim(self, task_id="task-1", post_id="c" + "a" * 24, source_version="2026-09-10T00:00:00.000Z"):
        self.claim = {
            "task": {
                "id": task_id,
                "purpose": "POST_REVIEW",
                "sourceRef": post_id,
                "sourceVersion": source_version,
                "expiresAt": "2026-09-10T00:15:00.000Z",
            },
            "context": {
                "version": 1,
                "purpose": "POST_REVIEW",
                "source": {"postId": post_id, "sourceVersion": source_version, "content": "Contenido de prueba", "isNsfw": False},
                "objective": "SUMMARIZE",
                "question": "",
            },
            "lease": {"token": "t" * 48, "generation": 1, "leaseExpiresAt": "2026-09-10T00:01:00.000Z"},
        }

    def theme_advice_claim(self, task_id="task-2", draft_id="draft-1", digest="d" * 64):
        self.claim = {
            "task": {
                "id": task_id,
                "purpose": "THEME_ADVICE",
                "sourceRef": draft_id,
                "sourceVersion": digest,
                "expiresAt": "2026-09-10T00:15:00.000Z",
            },
            "context": {
                "version": 1,
                "purpose": "THEME_ADVICE",
                "objective": "Más calma",
                "draft": {"draftId": draft_id, "revision": 0, "scenario": "dashboard", "tokens": {"accent-primary": "red"}},
                "preferences": {"mood": "calma"},
            },
            "lease": {"token": "u" * 48, "generation": 1, "leaseExpiresAt": "2026-09-10T00:01:00.000Z"},
        }


def make_adapter(relay: FakeRelay, *, assistance: bool = True, renew_ms: int = 25000, profile_config=None):
    os.environ["BUNNY_SPACE_BASE_URL"] = relay.base_url
    os.environ["BUNNY_SPACE_BOT_KEY"] = BOT_KEY
    os.environ["BUNNY_SPACE_SLUG"] = SLUG
    os.environ["BUNNY_SPACE_POLL_MS"] = "50"
    os.environ["BUNNY_SPACE_ASSISTANCE"] = "1" if assistance else "0"
    os.environ["BUNNY_SPACE_ASSISTANCE_RENEW_MS"] = str(renew_ms)
    if profile_config is not None:
        adapter_module.load_active_profile_config = lambda: profile_config
    config = PlatformConfig(enabled=True, extra={"bot_key": BOT_KEY, "slug": SLUG, "base_url": relay.base_url})
    adapter = BunnySpaceAdapter(config)
    adapter._mark_connected()
    return adapter


class IsolationGateTests(unittest.TestCase):
    def test_missing_platform_toolsets_denies(self):
        allowed, reason = evaluate_assistance_isolation({})
        self.assertFalse(allowed)
        self.assertIn("platform_toolsets", reason)

    def test_core_bundle_denies(self):
        allowed, reason = evaluate_assistance_isolation({"platform_toolsets": {"bunny_space": ["hermes-cli"]}})
        self.assertFalse(allowed, reason)

    def test_read_only_toolset_allows(self):
        allowed, reason = evaluate_assistance_isolation({"platform_toolsets": {"bunny_space": ["vision"]}})
        self.assertTrue(allowed, reason)

    def test_empty_list_allows_no_tools(self):
        allowed, reason = evaluate_assistance_isolation({"platform_toolsets": {"bunny_space": []}})
        self.assertTrue(allowed, reason)

    def test_unknown_toolset_denies(self):
        allowed, _ = evaluate_assistance_isolation({"platform_toolsets": {"bunny_space": ["no-such-toolset"]}})
        self.assertFalse(allowed)

    def test_enabled_mcp_server_denies_unless_no_mcp(self):
        # Hermes adds every enabled MCP server to the platform's real toolset.
        mcp = {"mcp_servers": {"fs": {"command": "npx", "args": ["fs-server"]}}}
        allowed, reason = evaluate_assistance_isolation({"platform_toolsets": {"bunny_space": ["vision"]}, **mcp})
        self.assertFalse(allowed, reason)
        self.assertIn("fs", reason)
        allowed, reason = evaluate_assistance_isolation(
            {"platform_toolsets": {"bunny_space": ["vision", "no_mcp"]}, **mcp}
        )
        self.assertTrue(allowed, reason)

    def test_extract_json_object_tolerates_fences(self):
        payload = extract_json_object('```json\n{"explanation": "x", "tokens": {"a-b": "red"}}\n```')
        self.assertEqual(payload, {"explanation": "x", "tokens": {"a-b": "red"}})
        self.assertIsNone(extract_json_object("no json here"))


class AssistanceWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.relay = FakeRelay()
        self.addCleanup(self.relay.stop)
        self.dispatched = []

    async def _wait_until(self, predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
        """La base despacha el evento a una tarea en background: hay que esperarla."""
        import time as _time

        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(interval)
        return predicate()

    def _adapter(self, **kwargs):
        adapter = make_adapter(self.relay, **kwargs)
        self.addCleanup(lambda: asyncio.run(adapter.disconnect()))
        return adapter

    async def _capture(self, adapter):
        async def handler(event):
            self.dispatched.append(event)

        adapter.set_message_handler(handler)

    async def test_worker_stays_disabled_without_isolated_config(self):
        adapter = self._adapter(profile_config={})
        adapter._evaluate_assistance()
        self.assertFalse(adapter.assistance_enabled)
        await adapter._poll_once()
        self.assertEqual(self.relay.claims, 0, "Sin aislamiento probado no se reclama ninguna tarea")

    async def test_worker_stays_disabled_when_flag_off(self):
        adapter = self._adapter(assistance=False, profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter._evaluate_assistance()
        self.assertFalse(adapter.assistance_enabled)
        await adapter._poll_once()
        self.assertEqual(self.relay.claims, 0)

    async def test_relay_capability_alone_does_not_enable_worker(self):
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter.assistance_override = None  # BUNNY_SPACE_ASSISTANCE unset
        adapter.session_assistance_capable = True  # relay declares the capability
        adapter._evaluate_assistance()
        self.assertFalse(adapter.assistance_enabled, "Assistance must stay opt-in for the profile owner")
        await adapter._poll_once()
        self.assertEqual(self.relay.claims, 0)

    async def test_post_review_round_trip_submits_bound_result(self):
        self.relay.post_review_claim()
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter._evaluate_assistance()
        self.assertTrue(adapter.assistance_enabled)
        await self._capture(adapter)
        await adapter._poll_once()
        self.assertEqual(self.relay.claims, 1)
        self.assertTrue(
            await self._wait_until(lambda: len(self.dispatched) == 1),
            "El evento de asistencia debe llegar al cerebro del bot",
        )
        self.assertTrue(self.dispatched[0].source.chat_id.startswith("assist:task-1"))
        self.assertIn("Contenido de prueba", self.dispatched[0].text)

        result = await adapter.send("assist:task-1", "Resumen del asistente")
        self.assertTrue(result.success, result.error)
        self.assertEqual(len(self.relay.submits), 1)
        submitted = self.relay.submits[0]
        self.assertEqual(submitted["action"], "submit")
        self.assertEqual(submitted["leaseToken"], "t" * 48)
        self.assertEqual(submitted["result"]["purpose"], "POST_REVIEW")
        self.assertEqual(submitted["result"]["postId"], self.relay.claim["task"]["sourceRef"])
        self.assertEqual(submitted["result"]["sourceVersion"], self.relay.claim["task"]["sourceVersion"])
        self.assertEqual(submitted["result"]["answer"], "Resumen del asistente")
        self.assertEqual(adapter._assistance, {}, "El lease se libera tras entregar")

    async def test_theme_advice_round_trip_uses_claim_binding(self):
        self.relay.theme_advice_claim()
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter._evaluate_assistance()
        await self._capture(adapter)
        await adapter._poll_once()
        self.assertTrue(await self._wait_until(lambda: len(self.dispatched) == 1))
        self.assertIn("THEME_ADVICE", self.dispatched[0].text)
        reply = '```json\n{"explanation": "Bajando la saturación", "tokens": {"accent-primary": "#3355ff"}}\n```'
        result = await adapter.send("assist:task-2", reply)
        self.assertTrue(result.success, result.error)
        submitted = self.relay.submits[0]["result"]
        self.assertEqual(submitted["draftId"], "draft-1")
        self.assertEqual(submitted["snapshotDigest"], "d" * 64)
        self.assertEqual(submitted["tokens"], {"accent-primary": "#3355ff"})
        self.assertEqual(submitted["explanation"], "Bajando la saturación")

    async def test_theme_advice_unknown_token_is_never_submitted(self):
        self.relay.theme_advice_claim()
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter._evaluate_assistance()
        await self._capture(adapter)
        await adapter._poll_once()
        reply = '{"explanation": "x", "tokens": {"invented-token": "red"}}'
        result = await adapter.send("assist:task-2", reply)
        self.assertFalse(result.success)
        self.assertEqual(self.relay.submits, [], "Nunca se envía un token inventado")

    async def test_renew_keeps_the_lease_alive_while_the_brain_works(self):
        self.relay.post_review_claim()
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}}, renew_ms=1000)
        adapter._evaluate_assistance()
        await self._capture(adapter)
        await adapter._poll_once()
        self.assertTrue(
            await self._wait_until(lambda: len(self.relay.renews) >= 2, timeout=6.0),
            "El lease debe renovarse mientras el cerebro trabaja",
        )
        self.assertEqual(self.relay.renews[0]["taskId"], "task-1")
        result = await adapter.send("assist:task-1", "Análisis final")
        self.assertTrue(result.success, result.error)
        self.assertEqual(len(adapter._assistance), 0)
        renews_after_submit = len(self.relay.renews)
        await asyncio.sleep(1.3)
        self.assertEqual(len(self.relay.renews), renews_after_submit, "Tras entregar no hay más renew")

    async def test_lost_lease_blocks_a_late_submit(self):
        self.relay.post_review_claim()
        self.relay.fail_renew_after = 1
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}}, renew_ms=1000)
        adapter._evaluate_assistance()
        await self._capture(adapter)
        await adapter._poll_once()
        self.assertTrue(
            await self._wait_until(lambda: adapter._assistance == {}, timeout=6.0),
            "Un lease perdido se descarta",
        )
        result = await adapter.send("assist:task-1", "Tarde")
        self.assertFalse(result.success)
        self.assertEqual(self.relay.submits, [], "No se envía nada con un lease perdido")

    async def test_submit_rejection_is_reported_honestly(self):
        self.relay.post_review_claim()
        self.relay.submit_error = "INVALID_RESULT"
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter._evaluate_assistance()
        await self._capture(adapter)
        await adapter._poll_once()
        result = await adapter.send("assist:task-1", "Análisis")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_RESULT")

    async def test_claim_without_binding_is_not_dispatched(self):
        self.relay.post_review_claim()
        del self.relay.claim["task"]["sourceVersion"]
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": []}})
        adapter._evaluate_assistance()
        await self._capture(adapter)
        await adapter._poll_once()
        self.assertEqual(self.dispatched, [], "Sin binding completo no se consume el lease")

    async def test_probe_reports_the_reason_when_no_task(self):
        self.relay.claim = None
        adapter = self._adapter(profile_config={"platform_toolsets": {"bunny_space": ["vision"]}})
        adapter._evaluate_assistance()
        self.assertTrue(adapter.assistance_enabled)
        self.assertIn("vision_analyze", adapter.assistance_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SessionReadOrderTests(unittest.IsolatedAsyncioTestCase):
    """Review 2026-10-01: no relay call without a bot key."""

    def setUp(self):
        self.relay = FakeRelay()
        self.addCleanup(self.relay.stop)
        self._env_backup = {
            key: os.environ.get(key)
            for key in ("BUNNY_SPACE_BOT_KEY", "BUNNY_SPACE_SLUG", "BUNNY_SPACE_BASE_URL")
        }
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def test_session_is_not_requested_without_bot_key(self):
        os.environ["BUNNY_SPACE_BASE_URL"] = self.relay.base_url
        os.environ.pop("BUNNY_SPACE_BOT_KEY", None)
        os.environ.pop("BUNNY_SPACE_SLUG", None)
        config = PlatformConfig(enabled=True, extra={"base_url": self.relay.base_url})
        adapter = BunnySpaceAdapter(config)
        adapter._mark_connected()
        await adapter._load_session()
        self.assertEqual(
            self.relay.session_gets,
            0,
            "Sin bot key no se debe llamar a /session (evita GET con x-bot-key vacio)",
        )
        os.environ["BUNNY_SPACE_BOT_KEY"] = BOT_KEY
        config2 = PlatformConfig(
            enabled=True,
            extra={"bot_key": BOT_KEY, "slug": SLUG, "base_url": self.relay.base_url},
        )
        adapter2 = BunnySpaceAdapter(config2)
        adapter2._mark_connected()
        await adapter2._load_session()
        self.assertEqual(self.relay.session_gets, 1, "Con key si se lee la sesion")
