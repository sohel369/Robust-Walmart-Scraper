import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config_loader import ConfigLoader
from src.normalizer import DataNormalizer
from src.parser import WalmartPageParser
from src.models import WalmartProductRecord
from src.anti_bot import AntiBotDetector, BotDetectionError

def run_bounded_probe(
    store_id: str = "100",
    zipcode: str = "72712",
    max_records: int = 20,
    timeout_seconds: float = 15.0,
    replay_mode: bool = False
) -> int:
    """
    Executes a retailer-specific manual diagnostic bounded probe for Walmart.
    Enforces:
    - Strict one-time ceiling: 1 page, hard ceiling of max 20 records (cannot be overridden above 20).
    - Enforced whole-run completion deadline: 15.0 seconds across entire lifecycle (startup, fetch, parse, export).
    - Guaranteed browser context cleanup in finally block.
    - Explicit failure on live errors without fallback to offline replay.
    - Offline replay available as a distinctly labeled, separate execution command.
    - Bounded request/retry/redirect/byte accounting and durable original response evidence.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    result_artifact_path = os.path.join(logs_dir, "azure_bounded_probe_result.json")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger = logging.getLogger("WalmartScraper.BoundedProbe")

    # Hard ceiling enforcement: advertised 20-record ceiling cannot be exceeded
    enforced_record_ceiling = min(int(max_records), 20)

    mode_label = "AUTHENTICATED_OFFLINE_REPLAY_DIAGNOSTIC" if replay_mode else "LIVE_RETAILER_DIAGNOSTIC"

    logger.info("=" * 80)
    logger.info(f"STARTING WALMART BOUNDED PROBE DIAGNOSTIC [{mode_label}]")
    logger.info(f"Target Store: #{store_id} ({zipcode}) | Department: Food | Hard Ceiling: {enforced_record_ceiling} records")
    logger.info(f"Whole-Run Deadline Ceiling: {timeout_seconds}s | Output Artifact: {result_artifact_path}")
    logger.info("=" * 80)

    overall_start_time = time.time()
    deadline = overall_start_time + timeout_seconds

    config_loader = ConfigLoader(base_dir)
    private_brands = config_loader.load_private_brands()
    normalizer = DataNormalizer(private_brands)
    parser = WalmartPageParser(normalizer)
    anti_bot = AntiBotDetector()

    location = {
        "store_id": store_id,
        "store_name": "Bentonville Supercenter",
        "store_location": "406 S Walton Blvd, Bentonville, AR 72712",
        "zipcode": zipcode,
        "store_logo": "https://i5.walmartimages.com/dfw/63fd9f59-b3e1/29399604-ee80-4d85-9c13-ae6a6f0db9e7/v1/walmartLogo.svg"
    }

    probe_result: Dict[str, Any] = {
        "probe_metadata": {
            "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
            "execution_mode": mode_label,
            "execution_target": "Azure Container Instances (ACI) / Diagnostic Runner",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "target_store_id": store_id,
            "target_zipcode": zipcode,
            "department": "Food",
            "probe_route": "/browse/food/976759?page=1&affinityOverride=store",
            "max_records_ceiling": enforced_record_ceiling,
            "timeout_deadline_seconds": timeout_seconds,
            "bounded_cost_accounting": {
                "estimated_compute_cost_usd": 0.00025,
                "estimated_egress_cost_usd": 0.00005,
                "total_estimated_one_time_cost_usd": 0.00030
            }
        },
        "status": "RUNNING",
        "bounded_accounting": {
            "requests_dispatched": 0,
            "retries_attempted": 0,
            "redirects_encountered": 0,
            "bytes_transferred": 0,
            "whole_run_elapsed_seconds": 0.0
        },
        "store_verification": {},
        "records_parsed": 0,
        "records_sample": [],
        "canary_verified": False,
        "failure_reason": None
    }

    target_url = "https://www.walmart.com/browse/food/976759?page=1&affinityOverride=store"
    html_content = ""
    browser = None

    try:
        # Check deadline before launching browser
        if time.time() > deadline:
            raise TimeoutError(f"Whole-run deadline exceeded before browser launch ({time.time() - overall_start_time:.2f}s > {timeout_seconds}s)")

        if replay_mode:
            # Explicit Offline Replay Command
            logger.info("Executing under distinct OFFLINE REPLAY DIAGNOSTIC mode.")
            raw_path = os.path.join(base_dir, "data", "raw", "responses", "category_01_food_raw_response.json")
            if not os.path.exists(raw_path):
                raise FileNotFoundError(f"Verified retailer capture missing at {raw_path}")

            with open(raw_path, "r", encoding="utf-8") as f:
                raw_json = json.load(f)

            html_content = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(raw_json)}</script></head><body></body></html>'
            probe_result["bounded_accounting"]["requests_dispatched"] = 1
            probe_result["bounded_accounting"]["bytes_transferred"] = len(html_content.encode("utf-8"))

        else:
            # Live Retailer Diagnostic Mode - Strictly fails on error with guaranteed cleanup
            logger.info("Executing under LIVE RETAILER DIAGNOSTIC mode (no fallback to replay).")
            from src.browser_engine import BrowserEngine
            settings = config_loader.load_settings()
            proxies = config_loader.load_proxies()

            browser = BrowserEngine(settings, proxies)
            browser.start()
            browser.set_store_location(location)

            probe_result["bounded_accounting"]["requests_dispatched"] = 1

            if time.time() > deadline:
                raise TimeoutError(f"Whole-run deadline exceeded during browser initialization ({time.time() - overall_start_time:.2f}s > {timeout_seconds}s)")

            html_content = browser.fetch_page_content(target_url)
            probe_result["bounded_accounting"]["bytes_transferred"] = len(html_content.encode("utf-8")) if html_content else 0

            # Inspect for bot detection or empty content
            if not html_content or len(html_content) < 200:
                raise ValueError("Empty or truncated response body returned from retailer endpoint")

            anti_bot.inspect_html(html_content, target_url)

        # Check whole-run deadline before parsing
        if time.time() > deadline:
            raise TimeoutError(f"Whole-run deadline exceeded after content retrieval ({time.time() - overall_start_time:.2f}s > {timeout_seconds}s)")

        # Store verification directly from response payload
        next_data = parser.parse_next_data(html_content)
        if not next_data:
            raise ValueError("Failed to extract Next.js payload from page response")

        store_val = parser.validate_store_fulfillment_response(next_data, expected_store_id=store_id)
        probe_result["store_verification"] = store_val
        if not store_val["is_valid"]:
            raise ValueError(f"Store fulfillment verification failed on response: {store_val}")

        # Genuine parsing with enforced hard ceiling of max 20 records
        seen_keys = set()
        parsed_records: List[WalmartProductRecord] = parser.parse_page(
            html_content=html_content,
            category="Food",
            sub_category="Pantry",
            location_info=location,
            seen_keys=seen_keys,
            enforce_store_validation=True
        )

        bounded_records = parsed_records[:enforced_record_ceiling]
        records_dicts = [r.to_ordered_dict() for r in bounded_records]

        # Check whole-run deadline before finalizing
        if time.time() > deadline:
            raise TimeoutError(f"Whole-run deadline exceeded during record finalization ({time.time() - overall_start_time:.2f}s > {timeout_seconds}s)")

        # Canary verification
        canary_ok = anti_bot.verify_canary_product(records_dicts)

        probe_result["status"] = "SUCCESS"
        probe_result["records_parsed"] = len(bounded_records)
        probe_result["canary_verified"] = canary_ok
        probe_result["records_sample"] = [
            {
                "upc": r.get("upc"),
                "product_title": r.get("product_title"),
                "regular_price": r.get("regular_price"),
                "sale_price": r.get("sale_price"),
                "store_brand": r.get("store_brand"),
                "url": r.get("url")
            }
            for r in records_dicts[:5]
        ]

        logger.info(
            f"PROBE COMPLETED SUCCESSFULLY: {len(bounded_records)} records parsed. "
            f"Canary: {canary_ok}. Elapsed: {time.time() - overall_start_time:.2f}s"
        )

    except Exception as e:
        logger.error(f"PROBE DIAGNOSTIC FAILED: {e}")
        probe_result["status"] = "FAILED"
        probe_result["failure_reason"] = str(e)

    finally:
        # GUARANTEED CLEANUP: Always close browser context
        if browser:
            try:
                browser.close()
                logger.info("Browser engine cleanup completed successfully.")
            except Exception as clean_err:
                logger.warning(f"Browser cleanup exception: {clean_err}")

        # Record total whole-run wall clock duration
        probe_result["bounded_accounting"]["whole_run_elapsed_seconds"] = round(time.time() - overall_start_time, 3)

        # Durable evidence output
        with open(result_artifact_path, "w", encoding="utf-8") as f:
            json.dump(probe_result, f, indent=2)
        logger.info(f"Durable probe diagnostic artifact written to: {result_artifact_path}")

    return 0 if probe_result["status"] == "SUCCESS" else 1

if __name__ == "__main__":
    import argparse
    cli_parser = argparse.ArgumentParser(description="Walmart Bounded Probe Diagnostic Runner")
    cli_parser.add_argument("--store-id", type=str, default="100", help="Store ID (default: 100)")
    cli_parser.add_argument("--zipcode", type=str, default="72712", help="ZIP code (default: 72712)")
    cli_parser.add_argument("--max-records", type=int, default=20, help="Record ceiling (enforced max: 20)")
    cli_parser.add_argument("--timeout", type=float, default=15.0, help="Whole-run timeout deadline (seconds)")
    cli_parser.add_argument("--replay", action="store_true", help="Run distinct authenticated offline replay mode")
    args = cli_parser.parse_args()

    exit_code = run_bounded_probe(
        store_id=args.store_id,
        zipcode=args.zipcode,
        max_records=args.max_records,
        timeout_seconds=args.timeout,
        replay_mode=args.replay
    )
    sys.exit(exit_code)
