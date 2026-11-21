import pytest

from app.amounts import amount, signed


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1.01, "1.01"),
        (1.000001, "1.000001"),
        (1, "1.00"),
        (0, "0.00"),
        (0.00000001, "0.00000001"),
        (12.12345678, "12.12345678"),
        (0.123456789, "0.12345679"),
        (-1.5, "-1.50"),
    ],
)
def test_amount_keeps_eight_decimals_and_trims_to_two(value: float, text: str) -> None:
    assert amount(value) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [(1.01, "+1.01"), (0, "+0.00"), (-0.0003, "-0.0003"), (-0.00000001, "-0.00000001")],
)
def test_signed_shows_the_sign(value: float, text: str) -> None:
    assert signed(value) == text
