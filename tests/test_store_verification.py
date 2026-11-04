import json
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

def test_store_verification_rejects_string_false(parser):
    """
    Reviewer check: Validator must NOT accept string 'false' as available.
    """
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": "false"  # String 'false'!
            }
        }
    }
    report = parser.validate_store_fulfillment_response(payload, expected_store_id="100")
    assert report["is_valid"] is False
    assert report["in_store_available"] is False

def test_store_verification_rejects_not_pickup_intent(parser):
    """
    Reviewer check: Validator must NOT accept 'NOT_PICKUP' as a valid pickup intent.
    """
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "fulfillment_intent": "NOT_PICKUP",
                "in_store_pickup_available": True
            }
        }
    }
    report = parser.validate_store_fulfillment_response(payload, expected_store_id="100")
    assert report["is_valid"] is False

def test_native_source_fields_override_contradictory_metadata(parser):
    """
    Reviewer check: Native pageProps fields must NOT be overridden by contradictory
    metadata wrapper. If native fields say store 999, validation MUST fail.
    """
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        },
        "props": {
            "pageProps": {
                "storeInfo": {
                    "storeId": "999",  # Contradictory native field!
                    "intent": "PICKUP",
                    "in_store_available": True
                }
            }
        }
    }
    report = parser.validate_store_fulfillment_response(payload, expected_store_id="100")
    assert report["is_valid"] is False
    assert report["actual_store_id"] == "999"

def test_parse_page_with_no_store_context_does_not_invent_local_price(parser):
    """
    Reviewer check: A product page with no store context must NOT export its $4.00 price
    under the configured Bentonville store. It must preserve eligible identity with regular_price=''.
    """
    payload = {
        "item_stack": [
            {
                "id": "item_no_ctx_1",
                "name": "Eligible Staple Item",
                "sellerName": "Walmart.com",
                "priceInfo": {
                    "currentPrice": {"price": 4.00, "priceString": "$4.00"}
                },
                "canonicalUrl": "/ip/staple-item/112233"
            }
        ]
    }
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></head><body></body></html>'
    seen_keys = set()
    records = parser.parse_page(
        html_content=html,
        category="Food",
        sub_category="Pantry",
        location_info={"store_id": "100"},
        seen_keys=seen_keys,
        enforce_store_validation=True
    )
    assert len(records) == 1
    assert records[0].product_title == "Eligible Staple Item"
    # Identity preserved without inventing local price:
    assert records[0].regular_price == ""
    assert records[0].sale_price == ""

def test_item_level_shipping_or_conflicting_store_price_is_suppressed(parser):
    """
    Reviewer check: Correct page-level 100/PICKUP context must NOT accept a priceInfo
    explicitly marked storeId=999 or SHIPPING. Preserve identity without inventing local price.
    """
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        },
        "item_stack": [
            {
                "id": "item_shipping_price",
                "name": "Item With Shipping Price",
                "sellerName": "Walmart.com",
                "priceInfo": {
                    "storeId": "999",  # Conflicting store on item!
                    "fulfillmentType": "SHIPPING",
                    "currentPrice": {"price": 12.50, "priceString": "$12.50"}
                },
                "canonicalUrl": "/ip/shipping-item/998877"
            }
        ]
    }
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></head><body></body></html>'
    seen_keys = set()
    records = parser.parse_page(
        html_content=html,
        category="Food",
        sub_category="Pantry",
        location_info={"store_id": "100"},
        seen_keys=seen_keys,
        enforce_store_validation=True
    )
    assert len(records) == 1
    # Conflicting store price rejected:
    assert records[0].regular_price == ""
    assert records[0].sale_price == ""

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
    assert parser.last_page_status == "REJECTED_STORE_CONTEXT"

def test_nextjs_item_level_negative_and_contradictory_pickup_scoped_to_offer(parser):
    """
    Reviewer Nov 4 Check:
    Verifies that Next.js item-level negative wording or missing/contradictory pickup
    context on individual product offers suppresses local store price export
    while preserving product identity.
    """
    payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "100",
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        },
        "item_stack": [
            {
                "id": "item_valid",
                "name": "Great Value Butter",
                "sellerName": "Walmart.com",
                "inStorePickupAvailable": True,
                "priceInfo": {
                    "currentPrice": {"price": 3.98, "priceString": "$3.98"}
                },
                "canonicalUrl": "/ip/gv-butter/2001"
            },
            {
                "id": "item_neg_wording_badge",
                "name": "Great Value Milk",
                "sellerName": "Walmart.com",
                "fulfillmentBadge": "Pickup unavailable",
                "priceInfo": {
                    "currentPrice": {"price": 3.12, "priceString": "$3.12"}
                },
                "canonicalUrl": "/ip/gv-milk/2002"
            },
            {
                "id": "item_neg_wording_title",
                "name": "Great Value Cheese",
                "sellerName": "Walmart.com",
                "fulfillmentTitle": "Pickup not available at Store #100",
                "priceInfo": {
                    "currentPrice": {"price": 4.50, "priceString": "$4.50"}
                },
                "canonicalUrl": "/ip/gv-cheese/2003"
            },
            {
                "id": "item_contradictory_pickup_opt",
                "name": "Great Value Juice",
                "sellerName": "Walmart.com",
                "pickupOptions": [{"fulfillmentMethod": "PICKUP", "available": False}],
                "priceInfo": {
                    "currentPrice": {"price": 2.88, "priceString": "$2.88"}
                },
                "canonicalUrl": "/ip/gv-juice/2004"
            },
            {
                "id": "item_pickup_disabled",
                "name": "Specialty Bakery Bread",
                "sellerName": "Walmart.com",
                "inStorePickupAvailable": False,
                "priceInfo": {
                    "currentPrice": {"price": 5.00, "priceString": "$5.00"}
                },
                "canonicalUrl": "/ip/spec-bread/2005"
            }
        ]
    }
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></head><body></body></html>'
    seen_keys = set()
    records = parser.parse_page(
        html_content=html,
        category="Food",
        sub_category="Pantry",
        location_info={"store_id": "100"},
        seen_keys=seen_keys,
        enforce_store_validation=True
    )

    assert parser.last_page_status == "VALID"
    assert len(records) == 5

    rec_by_id = {r.url.split("/")[-1]: r for r in records}

    # Item 1: Valid item has price extracted
    r1 = rec_by_id["2001"]
    assert r1.product_title == "Great Value Butter"
    assert r1.regular_price == "3.98"

    # Item 2: Negative wording 'Pickup unavailable' badge -> price suppressed
    r2 = rec_by_id["2002"]
    assert r2.product_title == "Great Value Milk"
    assert r2.regular_price == ""

    # Item 3: Negative wording 'Pickup not available' title -> price suppressed
    r3 = rec_by_id["2003"]
    assert r3.product_title == "Great Value Cheese"
    assert r3.regular_price == ""

    # Item 4: Contradictory pickup options (available: False) -> price suppressed
    r4 = rec_by_id["2004"]
    assert r4.product_title == "Great Value Juice"
    assert r4.regular_price == ""

    # Item 5: Explicit inStorePickupAvailable: False -> price suppressed
    r5 = rec_by_id["2005"]
    assert r5.product_title == "Specialty Bakery Bread"
    assert r5.regular_price == ""

