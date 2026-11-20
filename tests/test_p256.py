"""RFC 6979 appendix A.2.5 vectors for NIST P-256 with SHA-256."""
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolate  # noqa: F401
import p256

# RFC 6979 A.2.5. Private key x, public point U.
PRIV = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
UX = 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
UY = 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299

# message, k, r, s. Hashes are SHA-256 of the message.
VECTORS = (
    (
        b'sample',
        0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60,
        0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716,
        0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8,
    ),
    (
        b'test',
        0xD16B6AE827F17175E040871A1C7EC3500192C4C92677336EC2537ACAEE0008E0,
        0xF1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367,
        0x019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083,
    ),
)


def _sig(r, s):
    return r.to_bytes(32, 'big') + s.to_bytes(32, 'big')


class P256(unittest.TestCase):
    def test_public_key_matches_vector(self):
        self.assertEqual(p256.public_key(PRIV), (UX, UY))

    def test_public_bytes_uncompressed(self):
        raw = p256.public_bytes(PRIV)
        self.assertEqual(len(raw), 65)
        self.assertEqual(raw[0], 0x04)
        self.assertEqual(raw, b'\x04' + UX.to_bytes(32, 'big') + UY.to_bytes(32, 'big'))

    def test_rfc6979_k_matches_vectors(self):
        for msg, k, _r, _s in VECTORS:
            digest = hashlib.sha256(msg).digest()
            self.assertEqual(p256.rfc6979_k(PRIV, digest), k, msg)

    def test_sign_matches_vectors(self):
        for msg, _k, r, s in VECTORS:
            self.assertEqual(p256.sign(PRIV, msg), _sig(r, s), msg)

    def test_verify_accepts_vectors(self):
        pub = (UX, UY)
        for msg, _k, r, s in VECTORS:
            self.assertTrue(p256.verify(pub, msg, _sig(r, s)), msg)

    def test_verify_rejects_bad_inputs(self):
        pub = (UX, UY)
        msg, _k, r, s = VECTORS[0]
        good = _sig(r, s)
        self.assertFalse(p256.verify(pub, msg[::-1], good))
        flipped = bytearray(good)
        flipped[10] ^= 0x01
        self.assertFalse(p256.verify(pub, msg, bytes(flipped)))
        self.assertFalse(p256.verify(pub, msg, _sig(0, s)))

    def test_fresh_key_signs_and_verifies(self):
        d = p256.generate_private_key()
        self.assertGreaterEqual(d, 1)
        self.assertLessEqual(d, p256.n - 1)
        pub = p256.public_key(d)
        sig = p256.sign(d, b'vanguard')
        self.assertEqual(len(sig), 64)
        self.assertTrue(p256.verify(pub, b'vanguard', sig))
        self.assertFalse(p256.verify(pub, b'vanguard-no', sig))

    def test_point_mul_order_is_infinity(self):
        self.assertIsNone(p256.point_mul(p256.n, p256.G))


if __name__ == '__main__':
    unittest.main()
