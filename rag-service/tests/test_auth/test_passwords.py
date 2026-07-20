from importlib import import_module


def _load_password_hasher():
    try:
        module = import_module("src.auth.passwords")
    except ModuleNotFoundError:
        return None
    return getattr(module, "PasswordHasher", None)


def test_password_hash_is_argon2id_and_verifies() -> None:
    password_hasher = _load_password_hasher()
    assert password_hasher is not None, "PasswordHasher has not been implemented"

    hasher = password_hasher()
    encoded = hasher.hash("correct horse battery staple")

    assert encoded.startswith("$argon2id$")
    assert hasher.verify(encoded, "correct horse battery staple")
    assert not hasher.verify(encoded, "wrong")

