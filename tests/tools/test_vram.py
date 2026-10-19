"""vram.py's pixel conversion on synthetic pixels. Every value is made up."""
import pathlib, struct, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from oracle import vram  # noqa: E402


def u16(*pixels):
    return struct.pack(f"<{len(pixels)}H", *pixels)


class VramToRgba(unittest.TestCase):
    def test_565(self):
        # R 21 (bits 0-4), G 51 (bits 5-10), B 7 (bits 11-15).
        px = 21 | 51 << 5 | 7 << 11
        rows = vram.vram_to_rgba(u16(px, 0xFFFF, 0), 3, 3, 1, 0)
        self.assertEqual(rows, [bytes([173, 207, 57, 255, 255, 255, 255, 255, 0, 0, 0, 255])])

    def test_5551_ignores_alpha(self):
        # R 21 (bits 0-4), G 9 (bits 5-9), B 30 (bits 10-14), with and without A (bit 15).
        px = 21 | 9 << 5 | 30 << 10
        rows = vram.vram_to_rgba(u16(px, px | 0x8000), 2, 2, 1, 1)
        self.assertEqual(rows, [bytes([173, 74, 247, 255] * 2)])

    def test_5_and_6_bit_expansion(self):
        for v in range(32):
            with self.subTest(v=v):
                want = (v << 3) | (v >> 2)
                self.assertEqual(vram.vram_to_rgba(u16(v), 1, 1, 1, 1)[0][0], want)
                self.assertEqual(vram.vram_to_rgba(u16(v << 11), 1, 1, 1, 0)[0][2], want)
        for v in range(64):
            with self.subTest(v=v):
                self.assertEqual(vram.vram_to_rgba(u16(v << 5), 1, 1, 1, 0)[0][1], (v << 2) | (v >> 4))

    def test_4444(self):
        # R 0xC (low nibble), G 0x5, B 0xA, A 0x3 (ignored).
        rows = vram.vram_to_rgba(u16(0x3A5C, 0xF00F), 2, 2, 1, 2)
        self.assertEqual(rows, [bytes([204, 85, 170, 255, 255, 0, 0, 255])])

    def test_8888(self):
        rows = vram.vram_to_rgba(bytes([1, 2, 3, 0, 250, 251, 252, 128]), 2, 2, 1, 3)
        self.assertEqual(rows, [bytes([1, 2, 3, 255, 250, 251, 252, 255])])

    def test_crop_to_width(self):
        # Stride 4, width 2, height 2: pixels 2 and 3 of each line are padding.
        data = u16(0x001F, 0x03E0, 0xFFFF, 0xFFFF,
                   0x7C00, 0x0000, 0xFFFF, 0xFFFF)
        rows = vram.vram_to_rgba(data, 4, 2, 2, 1)
        self.assertEqual(rows, [bytes([255, 0, 0, 255, 0, 255, 0, 255]),
                                bytes([0, 0, 255, 255, 0, 0, 0, 255])])
        data = bytes(range(32))                                  # 8888, stride 4, width 1
        self.assertEqual(vram.vram_to_rgba(data, 4, 1, 2, 3), [bytes([0, 1, 2, 255]),
                                                               bytes([16, 17, 18, 255])])

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "pixel format 4"):
            vram.vram_to_rgba(bytes(8), 2, 2, 1, 4)
        with self.assertRaisesRegex(ValueError, "stride 1"):
            vram.vram_to_rgba(bytes(8), 1, 2, 1, 1)
        with self.assertRaisesRegex(ValueError, "expected 16"):
            vram.vram_to_rgba(bytes(15), 4, 4, 2, 1)

    def test_size(self):
        self.assertEqual(vram.size(512, 272, 1), 512 * 272 * 2)
        self.assertEqual(vram.size(512, 272, 3), 512 * 272 * 4)


if __name__ == "__main__":
    unittest.main()
