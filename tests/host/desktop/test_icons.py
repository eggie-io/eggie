import struct
from pathlib import Path

import pytest

RESOURCES = Path(__file__).resolve().parents[3] / "host" / "desktop" / "resources"
PNG = b"\x89PNG\r\n\x1a\n"


def frames(path: Path) -> list[int]:
    """Sizes of an ICO's PNG frames, checking each directory entry against its image."""
    data = path.read_bytes()
    _reserved, kind, count = struct.unpack_from("<HHH", data)
    assert kind == 1, f"{path.name} is not an icon"
    sizes = []
    for i in range(count):
        w, h, _c, _r, _planes, _bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        image = data[offset:offset + length]
        assert len(image) == length and image.startswith(PNG), f"{path.name} frame {i} is not a PNG"
        png_w, png_h = struct.unpack_from(">II", image, 16)
        assert (w or 256, h or 256) == (png_w, png_h), f"{path.name} frame {i} says {w}x{h}"
        sizes.append(png_w)
    return sizes


@pytest.mark.parametrize("name", ["tray-on-light.ico", "tray-on-dark.ico"])
def test_tray_icons_go_up_to_64px(name):
    # Pillow hands pystray the largest frame only, and Windows asks for 48px at
    # 150% scale; anything smaller is upscaled and blurs.
    assert max(frames(RESOURCES / name)) >= 64


def test_app_icon_has_a_256px_frame():
    # PyInstaller builds the macOS .icns from the largest frame.
    assert 256 in frames(RESOURCES / "icon.ico")
