"""Per-agent usage counts from fake turn records must add up exactly."""
import time, unittest

import _isolate  # noqa: F401
import relay

NOW = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
H = 3600


def turn(agent, ago, minutes, status):
    return dict(agent=agent, started=NOW - ago, ended=NOW - ago + minutes * 60, status=status)


class Usage(unittest.TestCase):
    def test_counts_add_up(self):
        g1 = dict(turns=[turn('a', 1 * H, 10, 'ok'), turn('a', 2 * H, 5, 'failed'), turn('b', 3 * H, 30, 'ok'),
                         turn('a', 24 * H, 20, 'ok'), dict(agent='a', started=NOW - 60, status='running')])
        g2 = dict(turns=[turn('a', 3 * 86400, 15, 'timeout'), turn('a', 8 * 86400, 99, 'ok'), turn('b', 6 * 86400, 12, 'limited')])
        u = relay.usage_counts([g1, g2], NOW)
        self.assertEqual(u['a']['today'], dict(turns=2, ok=1, failed=1, minutes=15.0))
        self.assertEqual(u['a']['week'], dict(turns=4, ok=2, failed=2, minutes=50.0))
        self.assertEqual(u['b']['today'], dict(turns=1, ok=1, failed=0, minutes=30.0))
        self.assertEqual(u['b']['week'], dict(turns=2, ok=1, failed=1, minutes=42.0))
        for who in u.values():
            for k in who.values():
                self.assertEqual(k['turns'], k['ok'] + k['failed'])

    def test_every_agent_has_usage(self):
        for a in relay.agents_usage([], NOW):
            self.assertEqual(a['usage']['today']['turns'], 0)


if __name__ == '__main__':
    unittest.main()
