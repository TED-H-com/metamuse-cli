"""Shared key classification retains short names, never long credential names."""

import inspect

import pytest

from metamuse import safety


@pytest.mark.parametrize("name", ["_credential_name", "redaction_sensitive_key"])
@pytest.mark.parametrize("length", [255, 256, 257])
def test_shared_key_cache_length_boundary(name, length):
    normalizer = safety._credential_name
    predicate = safety.redaction_sensitive_key
    function = getattr(safety, name)
    key = "A" * (length - len("_PASSWORD")) + "_PASSWORD"
    expected = key.lower().replace("_", "") if function is normalizer else True
    normalizer.cache_clear()
    predicate.cache_clear()
    try:
        assert function(key) == expected
        assert function(key) == expected
        info = function.cache_info()
        assert info.maxsize == 2048
        if length <= 256:
            assert (info.hits, info.misses, info.currsize) == (1, 1, 1)
        else:
            assert (info.hits, info.misses, info.currsize) == (0, 0, 0)
            assert normalizer.cache_info().currsize == 0
            assert predicate.cache_info().currsize == 0
    finally:
        normalizer.cache_clear()
        predicate.cache_clear()


def test_long_names_keep_complete_classification_and_normalization():
    long = "Q" * 65536
    rows = [
        (long + "_PASSWORD", long.lower() + "password", True),
        (long + ".API_TOKEN", long.lower() + ".apitoken", True),
        ("X-Amz-" + long, "xamz" + long.lower(), True),
        ("x-goog-" + long, "xgoog" + long.lower(), True),
        ("__Secure-" + long, "secure" + long.lower(), True),
        ("__Host-" + long, "host" + long.lower(), True),
        (long + "%25255Fpassword", long.lower() + "password", True),
        ("next" + long + "token", "next" + long.lower() + "token", False),
        (long + "page_token", long.lower() + "pagetoken", False),
        (long + ".eos_token", long.lower() + ".eostoken", False),
        (long + "_token_id", long.lower() + "tokenid", False),
        ("ordinary" + long, "ordinary" + long.lower(), False),
    ]
    normalizer = safety._credential_name
    predicate = safety.redaction_sensitive_key
    normalizer.cache_clear()
    predicate.cache_clear()
    try:
        for key, normalized, sensitive in rows:
            assert normalizer(key) == normalized
            assert predicate(key) is sensitive
        assert normalizer.cache_info().currsize == 0
        assert predicate.cache_info().currsize == 0
    finally:
        normalizer.cache_clear()
        predicate.cache_clear()


def test_shared_key_cache_keeps_introspection_and_cache_controls():
    for function in (safety._credential_name, safety.redaction_sensitive_key):
        original = function.__wrapped__
        assert inspect.signature(function) == inspect.signature(original)
        assert inspect.unwrap(function) is original
        assert function.__name__ == original.__name__
        assert function.__qualname__ == original.__qualname__
        assert function.__doc__ == original.__doc__
        assert function.__module__ == original.__module__
        assert function.__annotations__ == original.__annotations__
        assert function.cache_parameters() == {"maxsize": 2048, "typed": False}
        function.cache_clear()
        try:
            assert function(key="api_token") == original(key="api_token")
            function(key="api_token")
            assert function.cache_info().hits >= 1
            assert function.cache_info().currsize == 1
            function.cache_clear()
            assert tuple(function.cache_info()) == (0, 0, 2048, 0)
        finally:
            function.cache_clear()
