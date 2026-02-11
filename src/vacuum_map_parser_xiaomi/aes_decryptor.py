"""Module that provides functions for decrypting a map."""

import base64
import hashlib
import zlib
from typing import Union

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

isEncryptKeyTypeHex = True


def aes_encrypt(data: bytes, key: bytes, iv: bytes) -> str:
    """
    Encrypts a string using AES encryption in CBC mode.
    """
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(data, AES.block_size))
    return encrypted.hex().upper()


def inflate(data: Union[bytes, bytearray, memoryview, str]) -> str:
    if isinstance(data, str):
        # If we were accidentally passed a hex string, interpret it as such.
        # Otherwise, preserve raw byte values via latin1.
        stripped = data.strip()
        is_hex = len(stripped) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in stripped)
        data = bytes.fromhex(stripped) if is_hex else stripped.encode("latin1")

    inflated_string = zlib.decompress(bytes(data)).decode("utf-8")
    return inflated_string


def aes_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """
    Decrypts a string using AES decryption in CBC mode.
    """
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(data)
        decrypted_unpadded = unpad(decrypted, AES.block_size, "pkcs7")
        return decrypted_unpadded
    except Exception as e:
        raise RuntimeError("AES decrypt failed (check modelKey/did/key derivation and input map data)") from e


def md5_hash(data: bytes) -> str:
    """
    Returns the MD5 hash of the given data.
    """
    return hashlib.md5(data).hexdigest()


def base64Encoding(data: bytes) -> str:
    dataBase64 = base64.b64encode(data)
    dataBase64P = dataBase64.decode("UTF-8")
    return dataBase64P


def base64_decode(data: bytes) -> str:
    """
    Decodes a Base64 string to hexadecimal.
    """
    decoded_bytes = base64.decodebytes(data)
    return decoded_bytes.hex()


def decrypt(encryptedMapContent: bytes, modelKey: str, did: str) -> str:

    originalWork = modelKey + did

    iv = b"ABCDEF1234123412"  # iv as a byte array

    encKey = aes_encrypt(originalWork.encode("latin1"), modelKey.encode("latin1"), iv)
    encKey2 = bytes.fromhex(encKey)
    md5Key = md5_hash(encKey2)
    decryptKey = bytes.fromhex(md5Key)

    encryptedBytes = bytes.fromhex(str(encryptedMapContent))
    decrypted_base64_bytes = aes_decrypt(encryptedBytes, decryptKey, iv)
    inflatedString = inflate(decrypted_base64_bytes)
    return inflatedString


def gen_md5_key(modelKey: str, did: str) -> str:
    originalWork = modelKey + did

    iv = b"ABCDEF1234123412"  # iv as a byte array

    encKey = aes_encrypt(originalWork.encode("latin1"), modelKey.encode("latin1"), iv)
    encKey2 = bytes.fromhex(encKey)
    md5Key = md5_hash(encKey2)
    return md5Key
