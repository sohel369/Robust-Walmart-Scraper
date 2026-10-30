import os
import json
import shutil
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("WalmartScraper.Storage")

class StorageManager:
    """
    Manages atomic file swaps, checkpointing, resume capability,
    and guarantees that an incomplete or failed run never overwrites a good dataset.
    """

    def __init__(self, base_dir: Optional[str] = None, min_records_threshold: int = 500):
        if base_dir is None:
            self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        else:
            self.base_dir = os.path.abspath(base_dir)

        self.staging_dir = os.path.join(self.base_dir, "data", "staging")
        self.output_dir = os.path.join(self.base_dir, "data", "output")
        self.checkpoint_dir = os.path.join(self.base_dir, "data", "checkpoints")
        self.min_records_threshold = min_records_threshold

        for d in [self.staging_dir, self.output_dir, self.checkpoint_dir]:
            os.makedirs(d, exist_ok=True)

    def get_checkpoint_path(self, store_id: str) -> str:
        return os.path.join(self.checkpoint_dir, f"checkpoint_store_{store_id}.json")

    def get_checkpoint_rows_path(self, store_id: str) -> str:
        return os.path.join(self.checkpoint_dir, f"checkpoint_{store_id}_rows.jsonl")

    def load_checkpoint(self, store_id: str) -> Dict[str, Any]:
        """Loads completed categories, seen keys, and staging filepath for resumption."""
        cp_path = self.get_checkpoint_path(store_id)
        if os.path.exists(cp_path):
            try:
                with open(cp_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not read checkpoint file {cp_path}: {e}")
        return {"completed_subcategories": [], "scraped_ids": [], "staging_filepath": None}

    def save_checkpoint(
        self,
        store_id: str,
        completed_subcategories: List[str],
        scraped_ids: List[str],
        staging_filepath: Optional[str] = None
    ) -> None:
        """Saves current state and active staging file to checkpoint."""
        cp_path = self.get_checkpoint_path(store_id)
        temp_path = f"{cp_path}.tmp"
        data = {
            "store_id": store_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "staging_filepath": staging_filepath,
            "completed_subcategories": completed_subcategories,
            "scraped_ids": list(set(scraped_ids))
        }
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        shutil.move(temp_path, cp_path)

        # Synchronize staged rows into dedicated persistent checkpoint rows snapshot
        if staging_filepath and os.path.exists(staging_filepath):
            rows_path = self.get_checkpoint_rows_path(store_id)
            rows_temp = f"{rows_path}.tmp"
            shutil.copyfile(staging_filepath, rows_temp)
            shutil.move(rows_temp, rows_path)

    def get_record_identity(self, record: Dict[str, Any]) -> str:
        """
        Derives a consistent genuine identity across collection, checkpoint restoration,
        and indefinite retention. Prioritizes: genuine UPC -> product URL / canonical URL -> product ID -> product title.
        Ensures products with missing UPCs are never lost during retention or resume.
        """
        upc = str(record.get("upc") or "").strip()
        if upc:
            return upc
        url = str(record.get("url") or record.get("canonicalUrl") or "").strip()
        if url:
            return url
        item_id = str(record.get("id") or record.get("product_id") or record.get("usItemId") or "").strip()
        if item_id:
            return item_id
        return str(record.get("product_title") or record.get("title") or "").strip()

    def restore_checkpoint_rows(self, store_id: str, current_staging_filepath: str) -> int:
        """
        Restores previously collected rows from an interrupted run into current staging.
        Performs strict ID-level deduplication to prevent duplicate rows across repeat resumes,
        using consistent genuine identity across UPCs, URLs, and item IDs.
        """
        rows_path = self.get_checkpoint_rows_path(store_id)
        restored = 0
        if not os.path.exists(rows_path):
            return restored

        existing_keys = set()
        if os.path.exists(current_staging_filepath):
            for r in self.load_staging_records(current_staging_filepath):
                key = self.get_record_identity(r)
                if key:
                    existing_keys.add(key)

        with open(rows_path, "r", encoding="utf-8") as src, open(current_staging_filepath, "a", encoding="utf-8") as dst:
            for line in src:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    record = json.loads(line_str)
                    key = self.get_record_identity(record)
                    if key and key in existing_keys:
                        continue
                    if key:
                        existing_keys.add(key)
                    dst.write(line_str + "\n")
                    restored += 1
                except json.JSONDecodeError:
                    continue

        logger.info(
            f"Restored {restored} unique previously collected rows from checkpoint into {current_staging_filepath}."
        )
        return restored

    def clear_checkpoint(self, store_id: str) -> None:
        """Removes checkpoint file after a completely successful run."""
        cp_path = self.get_checkpoint_path(store_id)
        if os.path.exists(cp_path):
            os.remove(cp_path)

    def write_staging_record(self, staging_filepath: str, record_dict: Dict[str, Any]) -> None:
        """Appends a single record to the staging file (JSON lines format)."""
        with open(staging_filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record_dict, ensure_ascii=False) + "\n")

    def load_staging_records(self, staging_filepath: str) -> List[Dict[str, Any]]:
        """Reads all records accumulated in staging."""
        records = []
        if not os.path.exists(staging_filepath):
            return records
        with open(staging_filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def merge_indefinite_store_assortment(
        self,
        fresh_records: List[Dict[str, Any]],
        existing_target_filepath: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Per Brief Requirement:
        'retain all unavailable store-assortment products indefinitely, including those with 
        no current pickup offer or price; keep carried observation dates/values distinct from fresh evidence.'
        
        Preserves historical products missing from fresh crawl while maintaining their original crawl_date.
        Uses consistent genuine product identity (UPC or stable URL/item_id) so products with missing UPC
        are never dropped.
        CRITICAL: Stops publication if the previous catalog exists but cannot be read.
        """
        if not os.path.exists(existing_target_filepath):
            return fresh_records, 0

        try:
            with open(existing_target_filepath, "r", encoding="utf-8") as f:
                previous_records = json.load(f)
        except Exception as e:
            logger.critical(
                f"ABORTING PUBLICATION: Failed to read existing production catalog at {existing_target_filepath}: {e}. "
                f"Publication stopped to protect historical assortment data from being lost or overwritten."
            )
            raise RuntimeError(f"Cannot read previous catalog for assortment retention: {e}")

        fresh_identities = {self.get_record_identity(r) for r in fresh_records if self.get_record_identity(r)}
        carried_forward = []

        for prev in previous_records:
            prev_id = self.get_record_identity(prev)
            if prev_id and prev_id not in fresh_identities:
                # Retain unavailable store-assortment product indefinitely with original historical crawl_date
                carried_forward.append(prev)

        if carried_forward:
            logger.info(
                f"[LOG-OBS: ASSORTMENT_RETENTION] Retained {len(carried_forward)} unavailable store-assortment "
                f"products indefinitely from previous catalog (including items with missing UPC; original crawl_dates preserved)."
            )

        merged = fresh_records + carried_forward
        return merged, len(carried_forward)

    def commit_dataset_atomic(
        self,
        store_id: str,
        staging_filepath: str,
        total_categories_expected: int,
        categories_processed: int,
        canary_verified: bool = True
    ) -> bool:
        """
        Validates the run and performs an atomic swap to data/output/walmart_scraped_data_{store_id}_latest.json.
        CRITICAL RULES:
        - Never overwrites last successful dataset with an empty or incomplete run.
        - Requires successful canary product verification.
        - Requires exact 100% required-route completion.
        - Stops publication if previous catalog cannot be read.
        """
        records = self.load_staging_records(staging_filepath)
        total_count = len(records)
        target_output = os.path.join(self.output_dir, f"walmart_scraped_data_store_{store_id}_latest.json")
        backup_output = os.path.join(self.output_dir, f"walmart_scraped_data_store_{store_id}_backup.json")

        logger.info(f"Validating dataset: {total_count} records collected. Categories processed: {categories_processed}/{total_categories_expected}, Canary: {canary_verified}")

        # Quality check 1: Canary product verification gate
        if not canary_verified:
            logger.error(
                "ABORTING DATASET COMMIT: Canary product verification check failed. "
                "Publication blocked to prevent dummy or invalid shelf data publication."
            )
            return False

        # Quality check 2: Ensure minimum records threshold is met
        if total_count < self.min_records_threshold:
            logger.error(
                f"ABORTING DATASET COMMIT: Record count ({total_count}) is below required "
                f"minimum threshold ({self.min_records_threshold}). Existing dataset will NOT be overwritten!"
            )
            return False

        # Category coverage check 3: Strict 100% category coverage required in production
        if total_categories_expected > 0 and categories_processed < total_categories_expected:
            logger.error(
                f"ABORTING DATASET COMMIT: Incomplete category coverage ({categories_processed}/{total_categories_expected}). "
                f"Production requires 100% category completion. Existing dataset will NOT be overwritten!"
            )
            return False

        # Indefinite Store Assortment Retention:
        # Merge unavailable/unobserved products from previous catalog, retaining original crawl_date
        # Stops publication if previous catalog cannot be read
        try:
            records, retained_count = self.merge_indefinite_store_assortment(records, target_output)
            total_count = len(records)
        except Exception as e:
            logger.critical(f"ABORTING DATASET COMMIT: Store assortment retention failed: {e}")
            return False

        # Safe Atomic Swap:
        # 1. Write the new dataset to a temporary file first
        temp_final = f"{target_output}.tmp"
        with open(temp_final, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)

        # 2. Back up previous dataset if it exists
        if os.path.exists(target_output):
            try:
                shutil.copy2(target_output, backup_output)
                logger.info(f"Previous successful dataset backed up to {backup_output}")
            except Exception as e:
                logger.warning(f"Could not create backup of previous dataset: {e}")

        # 3. Replace target with new validated dataset atomically
        shutil.move(temp_final, target_output)
        logger.info(f"SUCCESS: New production dataset committed atomically to {target_output} ({total_count} records)")

        # Clear checkpoint on clean completion
        self.clear_checkpoint(store_id)
        return True

    def calculate_statistics(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculates field-completeness, unique count, and category distributions
        for contest submission requirements.
        """
        total_records = len(records)
        if total_records == 0:
            return {"total_count": 0}

        category_counts: Dict[str, int] = {}
        unique_urls = set()
        unique_upcs = set()
        duplicates_count = 0
        field_fill_counts: Dict[str, int] = {}

        ppm_examples = []
        volume_deal_examples = []
        digital_deal_examples = []
        no_deal_examples = []

        # List of required fields
        sample_keys = list(records[0].keys())
        for k in sample_keys:
            field_fill_counts[k] = 0

        for r in records:
            cat = r.get("category", "Unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1

            url = r.get("url", "")
            upc = r.get("upc", "")
            key = upc if upc else url
            if key in unique_urls:
                duplicates_count += 1
            else:
                unique_urls.add(key)

            for k in sample_keys:
                val = r.get(k, "")
                if val is not None and str(val).strip() != "":
                    field_fill_counts[k] += 1

            # Categorize deal examples for contest entry
            if r.get("ppm") and len(ppm_examples) < 3:
                ppm_examples.append(r)
            if r.get("volume_deals_description") and len(volume_deal_examples) < 3:
                volume_deal_examples.append(r)
            if r.get("digital_deals_description") and len(digital_deal_examples) < 3:
                digital_deal_examples.append(r)
            if not r.get("volume_deals_description") and not r.get("digital_deals_description") and len(no_deal_examples) < 3:
                no_deal_examples.append(r)

        completeness_pct = {
            k: round((count / total_records) * 100, 2)
            for k, count in field_fill_counts.items()
        }

        return {
            "total_records": total_records,
            "unique_records": len(unique_urls),
            "duplicate_records": duplicates_count,
            "category_distribution": category_counts,
            "field_completeness_percentages": completeness_pct,
            "examples": {
                "ppm": ppm_examples,
                "volume_deals": volume_deal_examples,
                "digital_deals": digital_deal_examples,
                "no_deals": no_deal_examples
            }
        }
