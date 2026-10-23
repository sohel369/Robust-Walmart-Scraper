import os
import json
import pytest
from src.storage_manager import StorageManager

def test_atomic_swap_prevents_incomplete_overwrite(tmp_path):
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=10)
    store_id = "5260"
    
    # Setup initial good dataset with 15 records
    good_dataset_file = os.path.join(storage.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")
    good_records = [{"title": f"Good Item {i}"} for i in range(15)]
    with open(good_dataset_file, "w", encoding="utf-8") as f:
        json.dump(good_records, f)

    # Simulate an incomplete/thin run with only 3 records
    staging_file = os.path.join(storage.staging_dir, "staging_failed_run.jsonl")
    for i in range(3):
        storage.write_staging_record(staging_file, {"title": f"Incomplete Item {i}"})

    # Attempt atomic commit - MUST return False and refuse overwrite
    commit_success = storage.commit_dataset_atomic(
        store_id=store_id,
        staging_filepath=staging_file,
        total_categories_expected=5,
        categories_processed=2
    )

    assert commit_success is False

    # Verify original good dataset is completely intact and untouched!
    with open(good_dataset_file, "r", encoding="utf-8") as f:
        persisted = json.load(f)
    assert len(persisted) == 15
    assert persisted[0]["title"] == "Good Item 0"

def test_checkpoint_lifecycle(tmp_path):
    storage = StorageManager(base_dir=str(tmp_path))
    store_id = "9999"

    # Save checkpoint
    storage.save_checkpoint(store_id, ["Pantry::Canned Goods"], ["UPC123"])
    
    # Load checkpoint
    cp = storage.load_checkpoint(store_id)
    assert "Pantry::Canned Goods" in cp["completed_subcategories"]
    assert "UPC123" in cp["scraped_ids"]

    # Clear checkpoint
    storage.clear_checkpoint(store_id)
    cp_after = storage.load_checkpoint(store_id)
    assert cp_after["completed_subcategories"] == []

def test_row_restoration_on_resume(tmp_path):
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=10)
    store_id = "100"
    staging_file = os.path.join(storage.staging_dir, "staging_test_resume.jsonl")

    # Phase 1: write 7 rows before simulated interruption
    for i in range(7):
        storage.write_staging_record(staging_file, {"id": f"item_{i}", "title": f"Product {i}"})
    
    # Save checkpoint with active staging path
    storage.save_checkpoint(
        store_id=store_id,
        completed_subcategories=["Food::Pantry"],
        scraped_ids=[f"item_{i}" for i in range(7)],
        staging_filepath=staging_file
    )

    # Phase 2: Resume from checkpoint
    cp = storage.load_checkpoint(store_id)
    assert cp["staging_filepath"] == staging_file
    restored_rows = storage.load_staging_records(cp["staging_filepath"])
    assert len(restored_rows) == 7

    # Phase 3: Append 8 more rows after resume
    for i in range(7, 15):
        storage.write_staging_record(staging_file, {"id": f"item_{i}", "title": f"Product {i}"})

    all_rows = storage.load_staging_records(staging_file)
    assert len(all_rows) == 15
    assert all_rows[0]["id"] == "item_0"
    assert all_rows[14]["id"] == "item_14"

def test_strict_100_percent_category_gate(tmp_path):
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=10)
    store_id = "100"
    staging_file = os.path.join(storage.staging_dir, "staging_gate_test.jsonl")
    for i in range(15):
        storage.write_staging_record(staging_file, {"id": f"item_{i}"})

    # Under 80% rule, 4 out of 5 categories (80%) would pass.
    # Under strict 100% production gate, 4 out of 5 MUST FAIL.
    commit_4_of_5 = storage.commit_dataset_atomic(
        store_id=store_id,
        staging_filepath=staging_file,
        total_categories_expected=5,
        categories_processed=4
    )
    assert commit_4_of_5 is False

    # Only 5 out of 5 (100%) succeeds
    commit_5_of_5 = storage.commit_dataset_atomic(
        store_id=store_id,
        staging_filepath=staging_file,
        total_categories_expected=5,
        categories_processed=5
    )
    assert commit_5_of_5 is True

def test_indefinite_store_assortment_retention(tmp_path):
    """
    Verifies that unavailable/unobserved products from previous runs are retained indefinitely,
    preserving their original historical crawl_date while fresh evidence receives current crawl_date.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=2)
    store_id = "100"
    target_output = os.path.join(storage.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")

    # Initial Run 1 on 2026-10-20: 2 products collected
    initial_catalog = [
        {"upc": "078742351988", "product_title": "Available Item 1", "crawl_date": "2026-10-20", "category": "Food"},
        {"upc": "078742351999", "product_title": "Currently Unavailable Item 2", "crawl_date": "2026-10-20", "category": "Food"}
    ]
    with open(target_output, "w", encoding="utf-8") as f:
        json.dump(initial_catalog, f)

    # Fresh Run 2 on 2026-10-25: Item 2 is temporarily out-of-stock/unavailable, Item 3 is new
    staging_file = os.path.join(storage.staging_dir, "staging_run2.jsonl")
    fresh_items = [
        {"upc": "078742351988", "product_title": "Available Item 1", "crawl_date": "2026-10-25", "category": "Food"},
        {"upc": "078742351111", "product_title": "New Item 3", "crawl_date": "2026-10-25", "category": "Household"}
    ]
    for item in fresh_items:
        storage.write_staging_record(staging_file, item)

    # Commit Run 2
    success = storage.commit_dataset_atomic(
        store_id=store_id,
        staging_filepath=staging_file,
        total_categories_expected=2,
        categories_processed=2
    )
    assert success is True

    # Inspect committed catalog
    with open(target_output, "r", encoding="utf-8") as f:
        committed = json.load(f)

    # Must retain all 3 items (Item 1 fresh, Item 3 fresh, Item 2 carried forward)
    assert len(committed) == 3
    catalog_by_upc = {r["upc"]: r for r in committed}

    # Item 1 updated to fresh date
    assert catalog_by_upc["078742351988"]["crawl_date"] == "2026-10-25"
    # Item 3 added with fresh date
    assert catalog_by_upc["078742351111"]["crawl_date"] == "2026-10-25"
    # Item 2 retained indefinitely with original historical date!
    assert catalog_by_upc["078742351999"]["crawl_date"] == "2026-10-20"
    assert catalog_by_upc["078742351999"]["product_title"] == "Currently Unavailable Item 2"

