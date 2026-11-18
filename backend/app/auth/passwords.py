"""Password policy and argon2id hashing."""

from argon2 import PasswordHasher
from zxcvbn import zxcvbn

MIN_LENGTH = 12
MIN_SCORE = 3
# zxcvbn's run time grows with input length; anything past this is long enough anyway.
SCORED_PREFIX = 100

_hasher = PasswordHasher()


def password_problem(password: str, email: str) -> str | None:
    """A message the user can act on when `password` is too weak, otherwise None.

    zxcvbn's dictionaries reject common and leaked passwords, and the email is passed
    in so a password built from it scores low.
    """
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    result = zxcvbn(password[:SCORED_PREFIX], user_inputs=[email, *email.split("@")])
    if result["score"] < MIN_SCORE:
        return "Password is too easy to guess. Try a longer phrase of unrelated words."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)
