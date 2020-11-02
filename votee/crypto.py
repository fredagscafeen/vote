import base64
import re
import struct
from typing import Optional

import Crypto.Random
from Crypto.Cipher import AES


def rand128() -> bytes:
    return Crypto.Random.get_random_bytes(16)


def urlencode(key: bytes) -> str:
    assert isinstance(key, bytes)
    assert len(key) == 16
    encoded = base64.urlsafe_b64encode(key).decode()
    assert encoded.endswith("==")
    stripped = encoded.rpartition("==")[0]
    assert re.match(r"^[a-zA-Z0-9_-]{22}$", stripped), stripped
    return stripped


def urldecode(key: str) -> Optional[bytes]:
    if len(key) != 22:
        return None
    if not re.match(r"^[a-zA-Z0-9_-]{22}$", key):
        return None
    return base64.urlsafe_b64decode(key.encode("ascii", errors="replace") + b"==")


def encrypt_int(secret: bytes, plaintext: int) -> bytes:
    assert len(secret) == 16
    cipher = AES.new(secret, AES.MODE_ECB)
    b = struct.pack("<IIII", plaintext, 0, 0, 0)
    assert len(b) == 16
    return cipher.encrypt(b)


def decrypt_int(secret: bytes, ct: bytes, valid_values: int) -> Optional[int]:
    if len(ct) != 16:
        return None
    assert len(secret) == 16, len(secret)
    cipher = AES.new(secret, AES.MODE_ECB)
    pt = cipher.decrypt(ct)
    assert len(pt) == 16
    a, b, c, d = struct.unpack("<IIII", pt)
    if a >= valid_values or not b == c == d == 0:
        return None
    return a
