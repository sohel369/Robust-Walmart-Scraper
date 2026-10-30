import pytest
from src.anti_bot import AntiBotDetector
from src.storage_manager import StorageManager

def test_canary_failure_strictly_blocks_publication(tmp_path):
    """
    BLOCKER 2 VERIFICATION:
    Canary check failure MUST block publication atomically.
    """
    storage = StorageManager(base_dir=str(tmp_path), min_records_threshold=1)
    staging_file = str(tmp_path / "staging.jsonl")
    storage.write_staging_record(staging_file, {"product_title": "Random Item", "upc": "123"})

    # canary_verified=False MUST abort publication
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

    # 10 out of 11 categories completed -> MUST fail
    success = storage.commit_dataset_atomic(
        store_id="100",
        staging_filepath=staging_file,
        total_categories_expected=11,
        categories_processed=10,
        canary_verified=True
    )
    assert success is False
