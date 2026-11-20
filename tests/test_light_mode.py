"""Light mode for the 3D core: string checks on web/index.html (never opens a browser or port 8940)."""
import re, unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')


class LightMode(unittest.TestCase):
    def test_storage_key_and_url_parameter(self):
        self.assertIn("'vanguard-quality'", HTML)
        self.assertRegex(HTML, r"URLSearchParams\(location\.search\)\.get\('quality'\)")

    def test_auto_light_full_control(self):
        for q in ('auto', 'light', 'full'):
            self.assertIn(f'data-quality="{q}"', HTML)
        self.assertIn('id="quality-state"', HTML)

    def test_pixel_ratio_capped_in_light(self):
        self.assertIn('const dpr = lightOn ? 1 :', HTML)

    def test_auto_rule(self):
        self.assertIn('AUTO_SLOW_COST = 0.032', HTML)
        self.assertIn('AUTO_SLOW_SECS = 3', HTML)
        self.assertIn('frame.samples >= 90', HTML)
        self.assertIn('Light mode on: your device was struggling.', HTML)

    def test_frame_cap(self):
        self.assertIn('LIGHT_FRAME_MS = 30', HTML)
        self.assertRegex(HTML, r'lightOn && !still && .*now - frame\.last < LIGHT_FRAME_MS')

    def test_effects_toggled(self):
        self.assertIn('afterPass.enabled = !lightOn', HTML)
        self.assertIn('bloomPass.enabled = !lightOn', HTML)

    def test_reduce_motion_line_unchanged(self):
        self.assertIn("const still = matchMedia('(prefers-reduced-motion: reduce)').matches;", HTML)


if __name__ == '__main__':
    unittest.main()
