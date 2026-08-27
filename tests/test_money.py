import unittest
from decimal import Decimal

from backend.money import (
    has_excess_precision,
    major_to_minor,
    minor_to_major,
    multiply_minor,
    parse_display_amount,
)


class MoneyConversionTests(unittest.TestCase):
    def test_rounds_half_up_at_two_decimal_boundary(self):
        self.assertEqual(major_to_minor("1.004", "MYR"), 100)
        self.assertEqual(major_to_minor("1.005", "MYR"), 101)
        self.assertEqual(major_to_minor("1.006", "MYR"), 101)

    def test_rounds_negative_half_up_away_from_zero(self):
        self.assertEqual(major_to_minor("-1.004", "MYR"), -100)
        self.assertEqual(major_to_minor("-1.005", "MYR"), -101)
        self.assertEqual(major_to_minor("-1.006", "MYR"), -101)

    def test_respects_currency_minor_unit_exponent(self):
        self.assertEqual(major_to_minor("12.5", "JPY"), 13)
        self.assertEqual(major_to_minor("12.3455", "KWD"), 12346)
        self.assertEqual(minor_to_major(12346, "KWD"), Decimal("12.346"))

    def test_detects_excess_precision(self):
        self.assertFalse(has_excess_precision("10.01", "MYR"))
        self.assertTrue(has_excess_precision("10.001", "MYR"))

    def test_parses_existing_display_values(self):
        self.assertEqual(parse_display_amount("RM 20,000.005", "MYR"), 2_000_001)
        self.assertIsNone(parse_display_amount("", "MYR"))

    def test_multiplies_minor_units_with_half_up_rounding(self):
        self.assertEqual(multiply_minor(333, Decimal("1.5")), 500)
        self.assertEqual(multiply_minor(-333, Decimal("1.5")), -500)


if __name__ == "__main__":
    unittest.main()
