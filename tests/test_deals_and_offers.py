import pytest
from src.normalizer import DataNormalizer

@pytest.fixture
def normalizer():
    return DataNormalizer(["great value", "equate"])

def test_combined_volume_deal_and_digital_coupon(normalizer):
    """
    BLOCKER 5 VERIFICATION:
    A combined volume promotion plus digital coupon MUST populate BOTH
    volume deal fields AND digital deal fields without either being dropped.
    """
    vol_desc, vol_price, dig_desc, dig_price = normalizer.parse_deals(
        promo_badge="2 for $5",
        deal_description="Clip Coupon: Save $1.00 on 2 (Limit 1)",
        displayed_promo_price="$5.00"
    )

    # Both offers populated
    assert vol_desc == "2 for $5"
    assert vol_price == "5.00"
    assert "Clip Coupon" in dig_desc
    assert "Save $1.00" in dig_desc
    assert dig_price == ""  # $1.00 is savings on coupon, NOT payable price!

def test_savings_not_treated_as_payable_price(normalizer):
    """
    BLOCKER 5 VERIFICATION:
    If promo price or text is 'Save $2.00', that $2.00 is discount savings,
    NOT the final payable price. digital_deals_price must remain empty string.
    """
    vol_desc, vol_price, dig_desc, dig_price = normalizer.parse_deals(
        promo_badge="Clip Coupon: Save $2.00",
        deal_description="Valid through Nov 30",
        displayed_promo_price="$2.00"  # This is the savings amount!
    )

    assert vol_desc == ""
    assert vol_price == ""
    assert "Clip Coupon: Save $2.00" in dig_desc
    assert dig_price == ""  # Must NOT be '2.00'!

def test_explicit_payable_price_with_coupon_is_captured(normalizer):
    """
    If the offer explicitly specifies a final payable price with digital coupon
    (e.g., '$13.98 with Digital Coupon'), digital_deals_price must be captured.
    """
    vol_desc, vol_price, dig_desc, dig_price = normalizer.parse_deals(
        promo_badge="$13.98 with Digital Coupon",
        deal_description="",
        displayed_promo_price="$13.98"
    )

    assert vol_desc == ""
    assert vol_price == ""
    assert "$13.98 with Digital Coupon" in dig_desc
    assert dig_price == "13.98"

def test_cashback_excluded_from_digital_deals(normalizer):
    """Cashback or Walmart Cash must never be treated as a digital coupon or price reduction."""
    vol_desc, vol_price, dig_desc, dig_price = normalizer.parse_deals(
        promo_badge="Walmart Cash: Earn $2.50",
        deal_description="Cash back offer",
        displayed_promo_price=""
    )

    assert vol_desc == ""
    assert vol_price == ""
    assert dig_desc == ""
    assert dig_price == ""
