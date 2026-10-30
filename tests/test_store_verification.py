import pytest
from src.normalizer import DataNormalizer
from src.parser import WalmartPageParser

@pytest.fixture
def parser():
    normalizer = DataNormalizer(["great value", "equate"])
    return WalmartPageParser(normalizer)

def test_store_verification_fails_closed_on_empty_payload(parser):
    """
    BLOCKER 1 VERIFICATION:
    validate_store_fulfillment_response({}) MUST fail closed and return is_valid=False.
    Must NOT default to Store #100, ZIP 72712 or PICKUP.
    """
    report = parser.validate_store_fulfillment_response({}, expected_store_id="100")
    
    assert report["is_valid"] is False
    assert report["actual_store_id"] == ""
    assert report["fulfillment_intent"] == ""
    assert report["in_store_available"] is False

def test_store_verification_rejects_mismatched_store_id(parser):
    """Rejects response if retailer payload points to store 200 when 100 is expected."""
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "200",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        }
    }
    report = parser.validate_store_fulfillment_response(payload, expected_store_id="100")
    assert report["is_valid"] is False
    assert report["actual_store_id"] == "200"

def test_store_verification_rejects_shipping_intent(parser):
    """Rejects response if fulfillment intent is DELIVERY/SHIPPING instead of PICKUP."""
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "fulfillment_intent": "DELIVERY",
                "in_store_pickup_available": False
            }
        }
    }
    report = parser.validate_store_fulfillment_response(payload, expected_store_id="100")
    assert report["is_valid"] is False

def test_store_verification_succeeds_on_valid_in_store_pickup(parser):
    """Succeeds only when genuine source payload confirms expected store and pickup."""
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "postal_code": "72712",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        }
    }
    report = parser.validate_store_fulfillment_response(payload, expected_store_id="100")
    assert report["is_valid"] is True
    assert report["actual_store_id"] == "100"
    assert report["fulfillment_intent"] == "PICKUP"
    assert report["in_store_available"] is True

def test_parse_page_rejects_mismatched_store_context(parser):
    """
    Ensures parse_page rejects products when source payload contains
    an explicit mismatched store context (e.g. Store #999 instead of Store #100).
    """
    mismatched_payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "999",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        },
        "item_stack": [
            {
                "id": "1111",
                "name": "Item from Wrong Store",
                "sellerName": "Walmart.com"
            }
        ]
    }
    import json
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(mismatched_payload)}</script></head><body></body></html>'
    
    seen_keys = set()
    records = parser.parse_page(
        html_content=html,
        category="Food",
        sub_category="Pantry",
        location_info={"store_id": "100"},
        seen_keys=seen_keys,
        enforce_store_validation=True
    )
    assert len(records) == 0
