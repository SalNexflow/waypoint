"""Access code and token primitives.

Pure functions, no database and no HTTP, so these run inside the api
container with everything else:

    docker compose exec api python -m pytest tests/test_field_auth.py

The end-to-end flow -- issue, redeem, scope, revoke -- is tested over HTTP in
tests/test_api.py, because what matters there is the wiring.
"""

from __future__ import annotations

import pytest

from api.auth import (
    _ALPHABET,
    _CODE_LEN,
    hash_secret,
    new_access_code,
    new_token,
    normalise_code,
)


# --- Code shape -------------------------------------------------------------


def test_a_code_is_two_groups_of_four():
    code = new_access_code()
    assert len(code) == 9
    assert code[4] == "-"


def test_codes_never_contain_a_confusable_character():
    """I, L, O and U are excluded from the alphabet.

    The whole point of Crockford's Base32 here: a code is READ ALOUD, and
    "oh" versus "zero" is a support call. Generating 500 covers every
    position often enough that a mistake in the alphabet would show up.
    """
    for _ in range(500):
        raw = new_access_code().replace("-", "")
        assert set(raw) <= set(_ALPHABET)
        assert not (set(raw) & set("ILOU"))


def test_codes_differ():
    """Not a randomness test -- a guard against a constant slipping in."""
    assert len({new_access_code() for _ in range(200)}) > 190


# --- Normalisation ----------------------------------------------------------


def test_normalise_accepts_what_a_person_actually_types():
    code = new_access_code()
    canonical = normalise_code(code)
    assert len(canonical) == _CODE_LEN

    for typed in (
        code.lower(),
        code.replace("-", ""),
        code.replace("-", " "),
        f"  {code}  ",
        code.replace("-", "").lower(),
    ):
        assert normalise_code(typed) == canonical, typed


@pytest.mark.parametrize(
    ("typed", "means"),
    [("O", "0"), ("o", "0"), ("I", "1"), ("i", "1"), ("L", "1"), ("l", "1")],
)
def test_confusable_characters_are_mapped_not_rejected(typed, means):
    """A technician reading "0" off a screen may well type "O".

    Rejecting that would be technically correct and practically hostile, so
    the confusable characters are folded onto the ones the alphabet uses.
    """
    assert normalise_code(f"{typed}2345678") == normalise_code(f"{means}2345678")


@pytest.mark.parametrize(
    "junk",
    ["", "short", "waytoolongtobeacode", "ABCD-EFG", "ABCD-EFGHI", "!!!!!!!!"],
)
def test_normalise_rejects_anything_that_cannot_be_a_code(junk):
    """Returns "" rather than raising, so callers need one check, not two."""
    assert normalise_code(junk) == ""


# --- Hashing and tokens -----------------------------------------------------


def test_hash_is_stable_and_sha256_shaped():
    assert hash_secret("abc") == hash_secret("abc")
    assert hash_secret("abc") != hash_secret("abd")
    assert len(hash_secret("abc")) == 64


def test_a_token_carries_256_bits():
    """token_urlsafe(32) is 32 BYTES of entropy, rendered as 43 characters.

    Worth asserting because the argument is a byte count and reads like a
    character count -- a plausible "tidy-up" to 16 would quarter the entropy
    and change nothing visible.
    """
    token = new_token()
    assert len(token) == 43
    assert len({new_token() for _ in range(200)}) == 200


def test_a_token_is_not_a_code():
    """Different alphabets, different lengths. If these ever converged, a
    token would become guessable at code entropy."""
    assert normalise_code(new_token()) == ""
