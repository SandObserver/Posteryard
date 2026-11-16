import io

from PIL import Image

JPEG_QUALITY = 90
JPEG_COMMENT = 0xFE
JPEG_SCAN = 0xDA
MADE_BY = b"Posteryard"


def made_here(data: bytes) -> bool:
    if not data.startswith(b"\xff\xd8"):
        return False
    at = 2
    while at + 4 <= len(data) and data[at] == 0xFF:
        marker, size = data[at + 1], int.from_bytes(data[at + 2 : at + 4])
        if marker == JPEG_SCAN or size < 2:
            return False
        if marker == JPEG_COMMENT and data[at + 4 : at + 2 + size] == MADE_BY:
            return True
        at += 2 + size
    return False


def jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, comment=MADE_BY)
    return buffer.getvalue()
