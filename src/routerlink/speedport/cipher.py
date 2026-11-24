"""Payload encryption of the Speedport Smart web UI (`encryptccm` / `decryptccm`
in `js/jquery-addons.js`).

- Key: 32 bytes from a 64-char hex string. Before login a fixed default; after
  login the session `challenge`.
- Nonce: the first 8 key bytes.
- AES-256-CCM, 128-bit tag, no associated data. Wire format: hex of
  ciphertext followed by the tag (SJCL layout).
"""

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from routerlink.speedport.errors import MalformedResponse

DEFAULT_KEY_HEX = "cdc0cac1280b516e674f0057e4929bca84447cca8425007e33a88a5cf598a190"
_NONCE_LENGTH = 8
_TAG_LENGTH = 16


class AuthenticationFailed(Exception):
    """The ciphertext was not sealed with this key."""


class SpeedportCipher:
    def __init__(self, key_hex: str) -> None:
        try:
            key = bytes.fromhex(key_hex)
        except ValueError as error:
            raise MalformedResponse("invalid encryption key") from error
        self._ccm = AESCCM(key, tag_length=_TAG_LENGTH)
        self._nonce = key[:_NONCE_LENGTH]

    def encrypt(self, plaintext: str) -> str:
        return self._ccm.encrypt(self._nonce, plaintext.encode(), None).hex()

    def decrypt(self, hex_text: str) -> bytes:
        try:
            sealed = bytes.fromhex(hex_text.strip())
        except ValueError as error:
            raise MalformedResponse("response is neither JSON nor hex") from error
        try:
            return self._ccm.decrypt(self._nonce, sealed, None)
        except InvalidTag as error:
            raise AuthenticationFailed from error
