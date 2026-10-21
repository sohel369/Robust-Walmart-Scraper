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
