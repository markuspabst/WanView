import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from routerlink.speedport.cipher import DEFAULT_KEY_HEX, AuthenticationFailed, SpeedportCipher
from routerlink.speedport.client import _form_encode
from routerlink.speedport.engineer_page import EngineerPage, number
from routerlink.speedport.errors import MalformedResponse
from routerlink.speedport.varlist import VarList

# NIST SP 800-38C, Appendix C: the library must match the CCM the web UI's SJCL uses.
NIST_KEY = "404142434445464748494a4b4c4d4e4f"
NIST_VECTORS = [
    ("10111213141516", "0001020304050607", "20212223", 4, "7162015b4dac255d"),
    ("1011121314151617", "000102030405060708090a0b0c0d0e0f", "202122232425262728292a2b2c2d2e2f", 6,
     "d2a1f0e051ea5f62081a7792073d593d1fc64fbfaccd"),
    ("101112131415161718191a1b", "000102030405060708090a0b0c0d0e0f10111213",
     "202122232425262728292a2b2c2d2e2f3031323334353637", 8,
     "e3b201a9f5b71a7a9b1ceaeccd97e70b6176aad9a4428aa5484392fbc1b09951"),
]


@pytest.mark.parametrize(("nonce", "adata", "plaintext", "tag_length", "sealed"), NIST_VECTORS)
def test_ccm_matches_nist(nonce: str, adata: str, plaintext: str, tag_length: int, sealed: str) -> None:
    ccm = AESCCM(bytes.fromhex(NIST_KEY), tag_length=tag_length)
    assert ccm.encrypt(bytes.fromhex(nonce), bytes.fromhex(plaintext), bytes.fromhex(adata)).hex() == sealed


def test_cipher_round_trip_accepts_uppercase_hex() -> None:
    cipher = SpeedportCipher(DEFAULT_KEY_HEX)
    sealed = cipher.encrypt("getChallenge=1&httoken=123")
    assert cipher.decrypt(sealed.upper()) == b"getChallenge=1&httoken=123"


def test_cipher_rejects_other_key() -> None:
    sealed = SpeedportCipher(DEFAULT_KEY_HEX).encrypt("x")
    with pytest.raises(AuthenticationFailed):
        SpeedportCipher("00" * 32).decrypt(sealed)


def test_varlist_reads_values_and_templates() -> None:
    varlist = VarList.from_json("""
    [{"vartype":"value","varid":"a","varvalue":"1&#044;5"},
     {"vartype":"template","varid":"row","varvalue":[{"vartype":"value","varid":"id","varvalue":"7"}]},
     {"vartype":"template","varid":"row","varvalue":[{"vartype":"value","varid":"id","varvalue":"8"}]}]
    """)
    assert varlist.string("a") == "1,5"
    assert varlist.string("missing") is None
    assert [row.string("id") for row in varlist.lists("row")] == ["7", "8"]


def test_varlist_rejects_non_list() -> None:
    with pytest.raises(MalformedResponse):
        VarList.from_json('{"a":1}')


def test_engineer_page_later_assignment_wins() -> None:
    page = EngineerPage("""
        var Receiver_RSRP_5G = "-";
    Receiver_RSRP_5G = "-103.0";
    upstream_status[2] = " 7.0 dB";
    var navClass = "internet";
    document.write(' ' + dsl_opmode + ' ');
    """)
    assert page.string("Receiver_RSRP_5G") == "-103.0"
    assert page.element("upstream_status", 2) == "7.0 dB"
    assert page.element("upstream_status", 3) is None
    assert number("90000 Kbps") == 90000
    assert number("-") is None


def test_form_encoding_matches_swift_client() -> None:
    assert _form_encode({"httoken": "12", "a": "x-y.z~ ä"}) == "a=x%2Dy%2Ez%7E%20%C3%A4&httoken=12"
