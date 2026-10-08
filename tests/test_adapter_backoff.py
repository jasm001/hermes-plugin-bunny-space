# Retroceso del bucle de poll: cuando el rele esta caido (p. ej. el servidor
# local apagado) NO se martilla en cada ciclo; el retraso dobla por fallo
# consecutivo con techo de 5 min y un exito lo resetea.
import sys
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))

import bunny_space.adapter as adapter_module  # noqa: E402
from bunny_space.adapter import _effective_poll_ms, _next_poll_backoff_ms  # noqa: E402


class PollBackoffTests(unittest.TestCase):
    def test_dobla_por_fallo_y_respeta_el_techo(self):
        self.assertEqual(_next_poll_backoff_ms(3000, 3000), 6000)
        self.assertEqual(_next_poll_backoff_ms(6000, 3000), 12000)
        self.assertEqual(_next_poll_backoff_ms(240_000, 3000), 300_000)
        self.assertEqual(_next_poll_backoff_ms(300_000, 3000), 300_000)

    def test_nunca_baja_del_poll_ms_y_no_crece_sin_techo(self):
        # con poll_ms=30 s el primer retroceso es 60 s
        self.assertEqual(_next_poll_backoff_ms(100, 30_000), 60_000)
        # si el poll_ms ya supera el techo, se queda en el poll_ms
        self.assertEqual(_next_poll_backoff_ms(600_000, 600_000), 600_000)




class PollIntervalTests(unittest.TestCase):
    def test_el_servidor_no_acelera_por_debajo_de_lo_configurado(self):
        # hint mas lento que lo configurado: manda el hint
        self.assertEqual(_effective_poll_ms(3000, 45000), 45000)
        # hint mas rapido: no acelera; se queda en lo configurado
        self.assertEqual(_effective_poll_ms(30000, 1000), 30000)
        # sin hint o basura: configurado
        self.assertEqual(_effective_poll_ms(15000, None), 15000)
        self.assertEqual(_effective_poll_ms(15000, "abc"), 15000)

class PollIntervalHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_session_adopta_el_hint_del_servidor(self):
        adapter = adapter_module.BunnySpaceAdapter.__new__(adapter_module.BunnySpaceAdapter)
        adapter.bot_key = "bt_x"
        adapter.base_url = "http://127.0.0.1:9"
        adapter.slug = "Luna"
        adapter.poll_ms = 30000
        adapter._poll_ms_configured = 30000
        adapter.session_reason = ""
        adapter.session_assistance_capable = False
        adapter.provision_digest = None
        adapter.signature_secret = None
        adapter.signature_required = False
        adapter.signature_timestamp_header = "x-bot-timestamp"
        adapter.signature_header = "x-bot-signature"

        async def relay_slow(method, path, body=None):
            return {"contractVersion": "BOT_PROVISION_V1", "pollIntervalMs": 45000, "bot": {"slug": "Luna"}}

        adapter._relay_json = relay_slow
        await adapter._load_session()
        self.assertEqual(adapter.poll_ms, 45000)

        async def relay_fast(method, path, body=None):
            return {"contractVersion": "BOT_PROVISION_V1", "pollIntervalMs": 1000}

        adapter._relay_json = relay_fast
        await adapter._load_session()
        self.assertEqual(adapter.poll_ms, 30000)

if __name__ == "__main__":
    unittest.main()
