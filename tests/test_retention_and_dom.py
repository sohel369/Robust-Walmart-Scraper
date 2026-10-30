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

    # Previous catalog contains:
    # 1. Item with UPC
    # 2. Historical bakery item with BLANK UPC and stable canonical URL
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

    # Fresh run collects only Item 1 (Item 2 is temporarily absent)
    fresh_records = [
        {
            "upc": "078742351988",
            "url": "https://www.walmart.com/ip/great-value-corn/10450901",
            "product_title": "Great Value Sweet Corn",
            "crawl_date": "2026-10-30"
        }
    ]

    merged, retained_count = storage.merge_indefinite_store_assortment(fresh_records, target_output)

    # Must retain BOTH items
    assert len(merged) == 2
    assert retained_count == 1

    # Find the retained blank-UPC item
    retained_item = next(r for r in merged if r["url"] == "https://www.walmart.com/ip/fresh-bakery-french-bread/98765432")
    assert retained_item["upc"] == ""
    assert retained_item["product_title"] == "Fresh Bakery French Bread"
    assert retained_item["crawl_date"] == "2026-10-20" # Historical observation date preserved!

def test_publication_aborts_if_previous_catalog_corrupt(tmp_path):
    """
    BLOCKER 3 VERIFICATION:
    If existing catalog file is corrupt / cannot be read, commit_dataset_atomic MUST abort
    to prevent accidental erasure or data loss.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    store_id = "100"
    target_output = os.path.join(storage.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")

    # Create a corrupted previous catalog file
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
