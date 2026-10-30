# Stream keepalive (v1.0.7): the ping ACK must never wake the poll cycle.
#
# Why: the wake channel is instant so the bot does not burn CPU polling. If the
# keepalive `pong` counted as an event, every bot would poll once more every 25 s.
import sys
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))

from bunny_space.adapter import (  # noqa: E402
    STREAM_KEEPALIVE_SECONDS,
    _is_stream_wake,
)


class StreamKeepaliveTests(unittest.TestCase):
    def test_the_ack_does_not_wake(self) -> None:
        self.assertFalse(_is_stream_wake("pong"))
        self.assertFalse(_is_stream_wake("  PONG \n"))

    def test_a_hub_event_does_wake(self) -> None:
        self.assertTrue(_is_stream_wake('{"type":"wake","at":1}'))
        self.assertTrue(_is_stream_wake("wake"))
        self.assertTrue(_is_stream_wake(b"\x00"))

    def test_cadence_stays_below_the_protocol_ping(self) -> None:
        # The application keepalive replaces the protocol one (ping_interval=None):
        # 25 s < the library's former 30 s and well below the Durable Object idle
        # window.
        self.assertLess(STREAM_KEEPALIVE_SECONDS, 30.0)
        self.assertGreaterEqual(STREAM_KEEPALIVE_SECONDS, 10.0)


if __name__ == "__main__":
    unittest.main()
