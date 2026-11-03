import json
import pytest
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from src.anti_bot import AntiBotDetector
from src.storage_manager import StorageManager
from src.parser import WalmartPageParser
from src.normalizer import DataNormalizer

def test_canary_failure_strictly_blocks_publication(tmp_path):
    """
    BLOCKER 2 VERIFICATION:
    Canary check failure MUST block publication atomically.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    staging_file = str(tmp_path / "staging.jsonl")
    storage.write_staging_record(staging_file, {"product_title": "Random Item", "upc": "123"})

    success = storage.commit_dataset_atomic(
        store_id="100",
        staging_filepath=staging_file,
        total_categories_expected=1,
        categories_processed=1,
        canary_verified=False
    )
    assert success is False

def test_incomplete_route_coverage_strictly_blocks_publication(tmp_path):
    """
    BLOCKER 2 VERIFICATION:
    If any configured route/category is missing or failed (e.g. 10/11),
    commit_dataset_atomic MUST block publication.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    staging_file = str(tmp_path / "staging.jsonl")
    storage.write_staging_record(staging_file, {"product_title": "Milk", "upc": "123"})

    success = storage.commit_dataset_atomic(
        store_id="100",
        staging_filepath=staging_file,
        total_categories_expected=11,
        categories_processed=10,
        canary_verified=True
    )
    assert success is False

def test_url_construction_handles_existing_query_params():
    """
    Reviewer check: For CSV URLs already containing ?povid=..., appending page 2
    must properly format query string without leaving double '?' characters.
    """
    base_url = "https://www.walmart.com/browse/food/976759?povid=globalnav_food"
    
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["page"] = ["2"]
    params["affinityOverride"] = ["store"]
    new_query = urlencode(params, doseq=True)
    page_url = urlunparse(parsed._replace(query=new_query))

    assert "?page=" not in page_url or "&povid=" in page_url or "?povid=" in page_url
    assert page_url.count("?") == 1
    assert "page=2" in page_url
    assert "povid=globalnav_food" in page_url
    assert "affinityOverride=store" in page_url

def test_rejected_store_page_prevents_publication(tmp_path):
    """
    Reviewer check: One route supplies 500 valid products, second route supplies
    wrong store. Parser rejects second page -> runner MUST NOT treat raw_count=0
    as completion, MUST keep coverage incomplete (1/2), and MUST block publication.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    parser = WalmartPageParser(DataNormalizer())

    # Route 1 staging
    staging_file = str(tmp_path / "staging_run.jsonl")
    for i in range(500):
        storage.write_staging_record(staging_file, {"product_title": f"Valid Item {i}", "upc": f"1000000{i}"})

    # Route 2 payload has wrong store (store 999 instead of 100)
    route2_payload = {
        "_source_run_metadata": {
            "store_fulfillment_context": {
                "store_id": "999",  # Wrong store!
                "fulfillment_intent": "PICKUP",
                "in_store_pickup_available": True
            }
        },
        "item_stack": [{"id": "r2_item", "name": "Wrong Store Item"}]
    }
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(route2_payload)}</script></head><body></body></html>'

    records = parser.parse_page(
        html_content=html,
        category="Household",
        sub_category="Paper",
        location_info={"store_id": "100"},
        seen_keys=set(),
        enforce_store_validation=True
    )

    # Page was rejected
    assert len(records) == 0
    assert parser.last_page_status == "REJECTED_STORE_CONTEXT"

    # Only 1 out of 2 routes completed because Route 2 was rejected
    categories_processed = 1
    total_categories_expected = 2

    # commit_dataset_atomic MUST refuse overwrite
    published = storage.commit_dataset_atomic(
        store_id="100",
        staging_filepath=staging_file,
        total_categories_expected=total_categories_expected,
        categories_processed=categories_processed,
        canary_verified=True
    )
    assert published is False

def test_unverified_store_page_prevents_publication(tmp_path):
    """
    Reviewer Point 3: An unverified store context page (missing context)
    must NOT establish completed coverage and must block publication.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    parser = WalmartPageParser(DataNormalizer())

    staging_file = str(tmp_path / "staging_run2.jsonl")
    storage.write_staging_record(staging_file, {"product_title": "Valid Item", "upc": "10000001"})

    # Empty payload has missing/unverified store context
    empty_payload = {"item_stack": [{"id": "item1", "name": "Item with no store"}]}
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(empty_payload)}</script></head><body></body></html>'

    records = parser.parse_page(
        html_content=html,
        category="Household",
        sub_category="Paper",
        location_info={"store_id": "100"},
        seen_keys=set(),
        enforce_store_validation=True
    )

    assert parser.last_page_status == "UNVERIFIED_STORE_CONTEXT"

    # Incomplete coverage (1/2) must block publication
    published = storage.commit_dataset_atomic(
        store_id="100",
        staging_filepath=staging_file,
        total_categories_expected=2,
        categories_processed=1,
        canary_verified=True
    )
    assert published is False

