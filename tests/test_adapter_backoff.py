# Retroceso del bucle de poll: cuando el rele esta caido (p. ej. el servidor
# local apagado) NO se martilla en cada ciclo; el retraso dobla por fallo
# consecutivo con techo de 5 min y un exito lo resetea.
import sys
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))

from bunny_space.adapter import _next_poll_backoff_ms  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
