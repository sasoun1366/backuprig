import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from backuprig.crypto import MasterKey, WrongMasterPassword, derive_key, generate_salt


def test_new_master_key_roundtrip():
    key, salt, verifier = MasterKey.new("correct horse battery staple")
    token = key.encrypt_str("super secret password")
    assert key.decrypt_str(token) == "super secret password"

    unlocked = MasterKey.unlock("correct horse battery staple", salt, verifier)
    assert unlocked.decrypt_str(token) == "super secret password"


def test_wrong_password_raises():
    _key, salt, verifier = MasterKey.new("right-password")
    with pytest.raises(WrongMasterPassword):
        MasterKey.unlock("wrong-password", salt, verifier)


def test_different_salts_give_different_keys():
    salt1 = generate_salt()
    salt2 = generate_salt()
    assert salt1 != salt2
    k1 = derive_key("same-password", salt1)
    k2 = derive_key("same-password", salt2)
    assert k1 != k2


def test_encrypt_produces_different_ciphertext_each_time():
    key, _salt, _verifier = MasterKey.new("pw")
    a = key.encrypt_str("data")
    b = key.encrypt_str("data")
    assert a != b  # Fernet includes random IV/timestamp
    assert key.decrypt_str(a) == key.decrypt_str(b) == "data"


def test_decrypt_garbage_raises():
    key, _salt, _verifier = MasterKey.new("pw")
    with pytest.raises(WrongMasterPassword):
        key.decrypt(b"not-a-valid-fernet-token")
