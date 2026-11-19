"""Generate the application's multi-resolution Windows icon without extra packages."""
from pathlib import Path
import struct
import zlib


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(size):
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            u, v = (x + .5) / size, (y + .5) / size
            dx, dy = max(.15 - u, 0, u - .85), max(.15 - v, 0, v - .85)
            color = (58, 103, 230, 255) if dx * dx + dy * dy < .15 ** 2 else (0, 0, 0, 0)
            if .25 < u < .75 and .19 < v < .81:
                color = (250, 252, 255, 255)
            if .34 < u < .66 and any(a < v < a + .045 for a in [.35, .47, .59]):
                color = (58, 103, 230, 255)
            pixels.extend(color)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(pixels), 9)) + chunk(b"IEND", b""))


def main():
    sizes = [16, 32, 48, 64, 128, 256]
    images = [png(size) for size in sizes]
    offset = 6 + 16 * len(sizes)
    entries = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    for size, image in zip(sizes, images):
        entries.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    path = Path(__file__).with_name("app.ico")
    path.write_bytes(entries + b"".join(images))
    print(path)


if __name__ == "__main__":
    main()
