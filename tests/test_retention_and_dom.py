import os
import json
import pytest
from bs4 import BeautifulSoup
from src.normalizer import DataNormalizer
from src.parser import WalmartPageParser
from src.storage_manager import StorageManager

@pytest.fixture
def parser():
    normalizer = DataNormalizer(["great value", "equate"])
    return WalmartPageParser(normalizer)

def test_retention_preserves_historical_records_with_blank_upc(tmp_path):
    """
    BLOCKER 3 VERIFICATION:
    Verifies that historical records with BLANK UPC and stable product URL
    are preserved indefinitely during assortment retention, rather than being dropped.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    store_id = "100"
    target_output = os.path.join(storage.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")

    previous_catalog = [
        {
            "upc": "078742351988",
            "url": "https://www.walmart.com/ip/great-value-corn/10450901",
            "product_title": "Great Value Sweet Corn",
            "crawl_date": "2026-10-20"
        },
        {
            "upc": "",
            "url": "https://www.walmart.com/ip/fresh-bakery-french-bread/98765432",
            "product_title": "Fresh Bakery French Bread",
            "crawl_date": "2026-10-20"
        }
    ]
    with open(target_output, "w", encoding="utf-8") as f:
        json.dump(previous_catalog, f)

    fresh_records = [
        {
            "upc": "078742351988",
            "url": "https://www.walmart.com/ip/great-value-corn/10450901",
            "product_title": "Great Value Sweet Corn",
            "crawl_date": "2026-10-31"
        }
    ]

    merged, retained_count = storage.merge_indefinite_store_assortment(fresh_records, target_output)
    assert len(merged) == 2
    assert retained_count == 1

    retained_item = next(r for r in merged if r["url"] == "https://www.walmart.com/ip/fresh-bakery-french-bread/98765432")
    assert retained_item["upc"] == ""
    assert retained_item["product_title"] == "Fresh Bakery French Bread"
    assert retained_item["crawl_date"] == "2026-10-20"

def test_retention_reconciles_transition_from_blank_upc_to_observed_upc(tmp_path):
    """
    Reviewer check: The same URL with a previously blank UPC and a newly observed UPC
    MUST produce exactly ONE record (not duplicate records) because the identity reconciles.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    store_id = "100"
    target_output = os.path.join(storage.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")

    # Historical record had blank UPC
    previous_catalog = [
        {
            "upc": "",
            "url": "https://www.walmart.com/ip/great-value-milk/10450114",
            "product_title": "Great Value Whole Milk",
            "crawl_date": "2026-10-20"
        }
    ]
    with open(target_output, "w", encoding="utf-8") as f:
        json.dump(previous_catalog, f)

    # Fresh run observed the genuine UPC for the same URL
    fresh_records = [
        {
            "upc": "078742051111",
            "url": "https://www.walmart.com/ip/great-value-milk/10450114",
            "product_title": "Great Value Whole Milk",
            "crawl_date": "2026-10-31"
        }
    ]

    merged, retained_count = storage.merge_indefinite_store_assortment(fresh_records, target_output)
    # Must NOT duplicate into 2 records!
    assert len(merged) == 1
    assert retained_count == 0
    assert merged[0]["upc"] == "078742051111"
    assert merged[0]["crawl_date"] == "2026-10-31"

def test_successful_publication_retires_all_checkpoint_state(tmp_path):
    """
    Reviewer check: Successful publication must remove both checkpoint JSON AND
    checkpoint rows. A subsequent fresh run must NOT restore stale old rows.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    store_id = "100"

    staging_file = os.path.join(storage.staging_dir, "staging_1.jsonl")
    storage.write_staging_record(staging_file, {
        "upc": "078742351988",
        "url": "https://www.walmart.com/ip/item/1",
        "product_title": "Item 1"
    })

    # Save checkpoint
    storage.save_checkpoint(store_id, ["Food"], ["078742351988"], staging_file)
    assert os.path.exists(storage.get_checkpoint_path(store_id))
    assert os.path.exists(storage.get_checkpoint_rows_path(store_id))

    # Commit successfully
    committed = storage.commit_dataset_atomic(
        store_id=store_id,
        staging_filepath=staging_file,
        total_categories_expected=1,
        categories_processed=1,
        canary_verified=True
    )
    assert committed is True

    # Checkpoint state must be completely retired
    assert not os.path.exists(storage.get_checkpoint_path(store_id))
    assert not os.path.exists(storage.get_checkpoint_rows_path(store_id))

    # Subsequent fresh run should restore 0 rows
    fresh_staging = os.path.join(storage.staging_dir, "staging_fresh.jsonl")
    restored = storage.restore_checkpoint_rows(store_id, fresh_staging)
    assert restored == 0

def test_publication_aborts_if_previous_catalog_corrupt(tmp_path):
    """
    BLOCKER 3 VERIFICATION:
    If existing catalog file is corrupt / cannot be read, commit_dataset_atomic MUST abort
    to prevent accidental erasure or data loss.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    store_id = "100"
    target_output = os.path.join(storage.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")

    with open(target_output, "w", encoding="utf-8") as f:
        f.write("{corrupted_json_not_valid...")

    staging_file = os.path.join(storage.staging_dir, "staging.jsonl")
    storage.write_staging_record(staging_file, {
        "upc": "078742351988",
        "url": "https://www.walmart.com/ip/item/1",
        "product_title": "Item 1"
    })

    success = storage.commit_dataset_atomic(
        store_id=store_id,
        staging_filepath=staging_file,
        total_categories_expected=1,
        categories_processed=1,
        canary_verified=True
    )
    assert success is False

def test_dom_fallback_retains_unpriced_eligible_products(parser):
    """
    BLOCKER 3 VERIFICATION:
    In DOM fallback, products without a price element must be retained
    with empty source-missing values rather than omitted.
    """
    dom_html = """
    <html>
      <body>
        <div data-item-id="card_unpriced_1">
          <span data-automation-id="product-title">Walmart Bakery Fresh Sourdough Bread</span>
          <a href="/ip/walmart-bakery-sourdough/55443322">View</a>
        </div>
      </body>
    </html>
    """
    seen_keys = set()
    records = parser.parse_page(
        html_content=dom_html,
        category="Food",
        sub_category="Bakery",
        location_info={"store_id": "100"},
        seen_keys=seen_keys,
        enforce_store_validation=False
    )

    assert len(records) == 1
    rec = records[0]
    assert rec.product_title == "Walmart Bakery Fresh Sourdough Bread"
    assert rec.regular_price == ""  # Retained with empty source-missing value!
    assert rec.sale_price == ""
    assert "55443322" in rec.url
