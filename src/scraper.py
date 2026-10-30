import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Set, List

from .config_loader import ConfigLoader
from .normalizer import DataNormalizer
from .models import WalmartProductRecord
from .anti_bot import AntiBotDetector, BotDetectionError
from .storage_manager import StorageManager
from .parser import WalmartPageParser
from .browser_engine import BrowserEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("WalmartScraper.Pipeline")

class WalmartProductionScraper:
    """
    Production Scraper orchestrating multi-location runs, stealth browser automation,
    checkpoint resumption, anti-bot safeguards, and atomic dataset swapping.
    """

    def __init__(self, base_dir: Optional[str] = None):
        self.config_loader = ConfigLoader(base_dir)
        self.settings = self.config_loader.load_settings()
        self.private_brands = self.config_loader.load_private_brands()
        self.proxies = self.config_loader.load_proxies()

        self.normalizer = DataNormalizer(self.private_brands)
        self.parser = WalmartPageParser(self.normalizer)
        self.anti_bot = AntiBotDetector(
            canary_product_id=self.settings.get("scraping", {}).get("canary_product_id"),
            min_yield_threshold=self.settings.get("scraping", {}).get("min_items_threshold_warning", 10)
        )
        self.storage = StorageManager(
            base_dir=base_dir,
            min_records_threshold=self.settings.get("safety_and_storage", {}).get("min_successful_records_for_atomic_swap", 500)
        )

        self.browser_engine: Optional[BrowserEngine] = None

    def execute_run(self, zipcode: Optional[str] = None, max_pages_override: Optional[int] = None) -> Dict[str, Any]:
        """
        Executes a complete scraping run for the selected store location.
        """
        start_time = time.time()
        
        # 1. Select Store Location
        if zipcode:
            location = self.config_loader.get_location_by_zip(zipcode)
            if not location:
                logger.warning(f"Zipcode {zipcode} not found in locations.json, falling back to default.")
                location = self.config_loader.get_default_location()
        else:
            location = self.config_loader.get_default_location()

        store_id = str(location.get("store_id", "5260"))
        logger.info(
            f"INITIATING WALMART RUN: Store={location.get('store_name')}, "
            f"ZIP={location.get('zipcode')}, ID={store_id}, Mode=In-Store/Pickup"
        )

        # 2. Setup Staging and Checkpoints (with Row Restoration on Resume)
        checkpoint = self.storage.load_checkpoint(store_id)
        completed_subcategories: List[str] = checkpoint.get("completed_subcategories", [])
        seen_keys: Set[str] = set(checkpoint.get("scraped_ids", []))
        checkpoint_staging_file: Optional[str] = checkpoint.get("staging_filepath")

        if checkpoint_staging_file and os.path.exists(checkpoint_staging_file):
            staging_filepath = checkpoint_staging_file
            restored_records = self.storage.load_staging_records(staging_filepath)
            total_records_collected = len(restored_records)
            logger.info(
                f"CHECKPOINT DETECTED: Resuming session for Store #{store_id}. "
                f"Found {total_records_collected} existing rows in active staging ({staging_filepath})."
            )
        else:
            run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            staging_filename = f"staging_run_store_{store_id}_{run_timestamp}.jsonl"
            staging_filepath = os.path.join(self.storage.staging_dir, staging_filename)
            restored_count = self.storage.restore_checkpoint_rows(store_id, staging_filepath)
            total_records_collected = restored_count
            if restored_count > 0:
                logger.info(f"Restored {restored_count} verified rows from checkpoint snapshot into fresh staging session.")

        # 3. Load Categories CSV
        category_entries = self.config_loader.load_category_urls()
        total_subcategories = len(category_entries)
        logger.info(f"Loaded {total_subcategories} subcategories from Walmart_Category_URLs.csv")

        # 4. Initialize Browser Engine with Location Context
        self.browser_engine = BrowserEngine(self.settings, self.proxies)
        self.browser_engine.start()
        self.browser_engine.set_store_location(location)

        scraping_cfg = self.settings.get("scraping", {})
        max_pages = max_pages_override if max_pages_override is not None else scraping_cfg.get("max_pages_per_subcategory")
        debug_truncated = (max_pages_override is not None)
        max_retries = scraping_cfg.get("max_retries_per_page", 3)
        backoff_factor = scraping_cfg.get("retry_backoff_factor", 2.0)

        processed_categories_count = len(completed_subcategories)

        try:
            for entry in category_entries:
                cat = entry["category"]
                sub_cat = entry["sub_category"]
                base_url = entry["url"]
                subcat_key = f"{cat}::{sub_cat}"

                if subcat_key in completed_subcategories:
                    logger.info(f"Resuming: Skipping already completed subcategory [{subcat_key}]")
                    continue

                logger.info(f"Scraping category: {cat} -> {sub_cat} ({base_url})")
                subcat_records_count = 0
                subcat_had_failure = False
                page_num = 1

                # Unbounded dynamic pagination (no demonstration limits in production)
                while True:
                    if max_pages is not None and max_pages > 0 and page_num > max_pages:
                        logger.info(f"Reached page limit {max_pages} for {subcat_key}.")
                        break

                    page_url = f"{base_url}?page={page_num}&affinityOverride=store" if page_num > 1 else f"{base_url}?affinityOverride=store"
                    
                    html_content = ""
                    success = False

                    for attempt in range(1, max_retries + 1):
                        try:
                            html_content = self.browser_engine.fetch_page_content(page_url)
                            # Verify response for silent blocking / CAPTCHA
                            self.anti_bot.inspect_html(html_content, page_url)
                            success = True
                            break
                        except BotDetectionError as bde:
                            logger.error(f"Anti-bot triggered on {page_url} (attempt {attempt}/{max_retries}): {bde}")
                            time.sleep(backoff_factor * attempt)
                        except Exception as e:
                            logger.warning(f"Error fetching {page_url} (attempt {attempt}/{max_retries}): {e}")
                            time.sleep(backoff_factor * attempt)

                    if not success:
                        logger.error(
                            f"Permanent page failure on {page_url} after {max_retries} attempts. "
                            f"Subcategory [{subcat_key}] marked incomplete."
                        )
                        subcat_had_failure = True
                        break

                    # Parse page records with source store verification enforced
                    page_records: List[WalmartProductRecord] = self.parser.parse_page(
                        html_content=html_content,
                        category=cat,
                        sub_category=sub_cat,
                        location_info=location,
                        seen_keys=seen_keys,
                        enforce_store_validation=True
                    )

                    raw_count = self.parser.last_raw_items_count
                    pagination_info = self.parser.last_pagination_info
                    total_pages = pagination_info.get("total_pages")

                    # Check yield and thin response warning on source raw count
                    self.anti_bot.check_yield(cat, sub_cat, raw_count)

                    # Separate raw page yield from newly retained rows:
                    # End pagination ONLY when source payload itself has 0 items or source markers indicate completion
                    if raw_count == 0:
                        logger.info(f"Source returned 0 raw items on page {page_num} for {subcat_key}. Ending pagination.")
                        break

                    # Write new verified records to staging
                    new_retained_count = 0
                    for r in page_records:
                        self.storage.write_staging_record(staging_filepath, r.to_ordered_dict())
                        total_records_collected += 1
                        subcat_records_count += 1
                        new_retained_count += 1

                    if new_retained_count == 0:
                        logger.info(
                            f"Page {page_num} for {subcat_key} yielded {raw_count} raw items, but 0 new unique rows "
                            f"(all already seen or filtered). Continuing traversal to next page."
                        )

                    # Check source end-of-catalog markers
                    if total_pages is not None and page_num >= total_pages:
                        logger.info(f"Source pagination complete: reached page {page_num}/{total_pages} for {subcat_key}.")
                        break

                    if pagination_info.get("has_next_page") is False:
                        logger.info(f"Source pagination has_next_page is False for {subcat_key}. Ending traversal.")
                        break

                    page_num += 1

                if subcat_had_failure:
                    logger.warning(
                        f"Skipping checkpoint completion for [{subcat_key}] due to page fetch failures. "
                        f"It will be automatically re-attempted on subsequent runs."
                    )
                elif debug_truncated:
                    logger.warning(
                        f"DEBUG RUN OVERRIDE: Subcategory [{subcat_key}] was truncated by max_pages_override={max_pages_override}. "
                        f"Skipping checkpoint recording to prevent partial state corruption."
                    )
                else:
                    completed_subcategories.append(subcat_key)
                    processed_categories_count += 1
                    
                    # Checkpoint progress after each completed subcategory (persists staging path for full row restoration)
                    self.storage.save_checkpoint(store_id, completed_subcategories, list(seen_keys), staging_filepath)
                    logger.info(f"Completed {subcat_key}: {subcat_records_count} items. Total so far: {total_records_collected}")

        finally:
            if self.browser_engine:
                self.browser_engine.close()

        # 5. Finalize and Validate Dataset
        runtime_seconds = round(time.time() - start_time, 2)
        staging_records = self.storage.load_staging_records(staging_filepath)

        # Check Canary Product - return value MUST be enforced before publication
        canary_ok = self.anti_bot.verify_canary_product(staging_records)
        if not canary_ok:
            logger.error(
                "CANARY VALIDATION FAILED: Required staple canary product missing from staging dataset. "
                "Publication blocked to ensure live shelf integrity."
            )

        # Atomic commit to production - requires successful canary and exact 100% route completion
        is_committed = self.storage.commit_dataset_atomic(
            store_id=store_id,
            staging_filepath=staging_filepath,
            total_categories_expected=total_subcategories,
            categories_processed=processed_categories_count,
            canary_verified=canary_ok
        )

        # Compute audit statistics
        stats = self.storage.calculate_statistics(staging_records)
        stats["runtime_seconds"] = runtime_seconds
        stats["store_id"] = store_id
        stats["zipcode"] = location.get("zipcode")
        stats["store_name"] = location.get("store_name")
        stats["atomic_commit_successful"] = is_committed
        stats["categories_processed"] = f"{processed_categories_count}/{total_subcategories}"

        logger.info(
            f"WALMART RUN COMPLETE: {len(staging_records)} total records collected in {runtime_seconds}s. "
            f"Commit status: {is_committed}"
        )
        return stats
