"""Amounts as people read them: the casino's 8 decimals, trailing zeros dropped down to 2."""

DECIMALS = 8


def _trim(fixed: str) -> str:
    whole, _, fraction = fixed.partition(".")
    return f"{whole}.{fraction.rstrip('0').ljust(2, '0')}"


def amount(value: float) -> str:
    """1.01 as `1.01`, 1.000001 as `1.000001`, 1 as `1.00`, 0.00000001 as is."""
    return _trim(f"{value:.{DECIMALS}f}")


def signed(value: float) -> str:
    """A profit with its sign, e.g. `+1.01` or `-0.0003`."""
    return _trim(f"{value:+.{DECIMALS}f}")
