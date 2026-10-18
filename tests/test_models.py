import pytest
from src.models import WalmartProductRecord

def test_field_ordering_and_length():
    record = WalmartProductRecord(
        zipcode="72712",
        store_name="Bentonville Supercenter",
        product_title="Test Item",
        regular_price="2.50"
    )
    ordered = record.to_ordered_dict()
    keys = list(ordered.keys())
    assert len(keys) == 22
    assert keys[0] == "zipcode"
    assert keys[-1] == "crawl_date"
    assert keys[17] == "upc"
    assert keys[18] == "gupc"
    assert keys[19] == "ppm"
    assert keys[20] == "unit_price"

def test_null_conversion_to_empty_string():
    record = WalmartProductRecord(
        zipcode=None,
        sale_price=None,
        volume_deals_description=None
    )
    assert record.zipcode == ""
    assert record.sale_price == ""
    assert record.volume_deals_description == ""

def test_enforced_empty_gupc_and_unit_price():
    record = WalmartProductRecord(
        gupc="SOME_INVENTED_GUPC",
        unit_price="0.25"
    )
    assert record.gupc == ""
    assert record.unit_price == ""

def test_store_brand_values():
    r1 = WalmartProductRecord(store_brand="yes")
    assert r1.store_brand == "yes"

    r2 = WalmartProductRecord(store_brand="no")
    assert r2.store_brand == "no"

    r3 = WalmartProductRecord(store_brand="TRUE")
    assert r3.store_brand == "yes"

    r4 = WalmartProductRecord(store_brand="")
    assert r4.store_brand == "no"
