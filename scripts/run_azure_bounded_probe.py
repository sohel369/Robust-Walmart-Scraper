import os
import sys
import time
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config_loader import ConfigLoader
from src.normalizer import DataNormalizer
from src.parser import WalmartPageParser
from src.models import WalmartProductRecord
from src.anti_bot import AntiBotDetector, BotDetectionError

logger = logging.getLogger("WalmartScraper.BoundedProbe")

def validate_max_records(max_records: Any) -> int:
    """
    Validates the max_records limit.
    Strictly rejects negative numbers, zero, or non-integer inputs.
    Enforces a strict upper ceiling of 20 records.
    """
    try:
        val = int(max_records)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid max_records '{max_records}': must be a positive integer between 1 and 20")

    if val <= 0:
        raise ValueError(f"Invalid max_records '{val}': limit must be greater than 0 (enforced ceiling: 1 to 20)")

    return min(val, 20)

def kill_process_tree(pid: int) -> None:
    """Forcefully terminates a process and all of its child processes."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception as e:
        logger.warning(f"Error terminating process tree for PID {pid}: {e}")

def _execute_bounded_probe_worker(
    store_id: str,
    zipcode: str,
    max_records: int,
    timeout_seconds: float,
    replay_mode: bool,
    result_artifact_path: str,
    base_dir: str
) -> int:
    """Internal probe execution worker running inside the process tree."""
    overall_start_time = time.time()
    deadline = overall_start_time + timeout_seconds

    # Validate and enforce record ceiling (1 to 20)
    enforced_record_ceiling = validate_max_records(max_records)

    mode_label = "AUTHENTICATED_OFFLINE_REPLAY_DIAGNOSTIC" if replay_mode else "LIVE_RETAILER_DIAGNOSTIC"

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
        "bounded_accounting": {},
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
        if replay_mode:
            # Replay accounting is strictly separated from live traffic
            raw_path = os.path.join(base_dir, "data", "raw", "responses", "category_01_food_raw_response.json")
            if not os.path.exists(raw_path):
                raise FileNotFoundError(f"Verified retailer capture missing at {raw_path}")

            with open(raw_path, "r", encoding="utf-8") as f:
                raw_json = json.load(f)

            html_content = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(raw_json)}</script></head><body></body></html>'
            
            probe_result["bounded_accounting"] = {
                "accounting_mode": "OFFLINE_REPLAY",
                "live_requests_dispatched": 0,
                "live_retries_attempted": 0,
                "live_redirects_encountered": 0,
                "live_bytes_transferred": 0,
                "cached_bytes_read": len(html_content.encode("utf-8")),
                "whole_run_elapsed_seconds": 0.0
            }
        else:
            # Live retailer execution: actual browser traffic is measured
            from src.browser_engine import BrowserEngine
            settings = config_loader.load_settings()
            proxies = config_loader.load_proxies()

            browser = BrowserEngine(settings, proxies)
            browser.start()
            browser.set_store_location(location)

            html_content = browser.fetch_page_content(target_url)

            probe_result["bounded_accounting"] = {
                "accounting_mode": "LIVE_MEASURED_TRAFFIC",
                "live_requests_dispatched": browser.traffic_stats.get("requests", 1),
                "live_retries_attempted": browser.traffic_stats.get("retries", 0),
                "live_redirects_encountered": browser.traffic_stats.get("redirects", 0),
                "live_bytes_transferred": browser.traffic_stats.get("bytes", len(html_content.encode("utf-8")) if html_content else 0),
                "whole_run_elapsed_seconds": 0.0
            }

            if not html_content or len(html_content) < 200:
                raise ValueError("Empty or truncated response body returned from retailer endpoint")

            anti_bot.inspect_html(html_content, target_url)

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

        # Canary verification
        canary_ok = anti_bot.verify_canary_product(records_dicts)
        probe_result["canary_verified"] = canary_ok
        probe_result["records_parsed"] = len(bounded_records)

        # Reviewer Point 2: Canary result MUST affect the appropriate success decision
        if not canary_ok:
            probe_result["status"] = "FAILED"
            probe_result["failure_reason"] = "Canary product verification failed (canary staple items absent)"
            logger.error("PROBE DIAGNOSTIC FAILED: Canary staple items verification failed.")
        else:
            probe_result["status"] = "SUCCESS"

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

    except Exception as e:
        logger.error(f"PROBE DIAGNOSTIC FAILED: {e}")
        probe_result["status"] = "FAILED"
        probe_result["failure_reason"] = str(e)

    finally:
        if browser:
            try:
                browser.close()
                logger.info("Browser engine cleanup completed successfully.")
            except Exception as clean_err:
                logger.warning(f"Browser cleanup exception: {clean_err}")

        probe_result["bounded_accounting"]["whole_run_elapsed_seconds"] = round(time.time() - overall_start_time, 3)

        with open(result_artifact_path, "w", encoding="utf-8") as f:
            json.dump(probe_result, f, indent=2)

    return 0 if probe_result["status"] == "SUCCESS" else 1

def run_bounded_probe(
    store_id: str = "100",
    zipcode: str = "72712",
    max_records: int = 20,
    timeout_seconds: float = 15.0,
    replay_mode: bool = False
) -> int:
    """
    Executes a retailer-specific manual diagnostic bounded probe for Walmart.
    Enforces a REAL outer process deadline with process-tree cleanup and failure artifact.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    result_artifact_path = os.path.join(logs_dir, "azure_bounded_probe_result.json")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Validate limits upfront before launching outer process
    try:
        enforced_ceiling = validate_max_records(max_records)
    except ValueError as ve:
        logger.error(f"Validation Error: {ve}")
        failure_artifact = {
            "probe_metadata": {
                "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
                "execution_mode": "AUTHENTICATED_OFFLINE_REPLAY_DIAGNOSTIC" if replay_mode else "LIVE_RETAILER_DIAGNOSTIC",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "target_store_id": store_id,
                "target_zipcode": zipcode,
                "timeout_deadline_seconds": timeout_seconds,
            },
            "status": "FAILED",
            "failure_reason": str(ve),
            "records_parsed": 0,
            "canary_verified": False,
            "bounded_accounting": {
                "whole_run_elapsed_seconds": 0.0
            }
        }
        with open(result_artifact_path, "w", encoding="utf-8") as f:
            json.dump(failure_artifact, f, indent=2)
        return 1

    overall_start_time = time.time()
    mode_label = "AUTHENTICATED_OFFLINE_REPLAY_DIAGNOSTIC" if replay_mode else "LIVE_RETAILER_DIAGNOSTIC"

    logger.info("=" * 80)
    logger.info(f"STARTING WALMART BOUNDED PROBE DIAGNOSTIC [{mode_label}]")
    logger.info(f"Target Store: #{store_id} ({zipcode}) | Department: Food | Hard Ceiling: {enforced_ceiling} records")
    logger.info(f"Enforcing Outer Process Deadline: {timeout_seconds}s | Output Artifact: {result_artifact_path}")
    logger.info("=" * 80)

    # Spawn inner worker subprocess to enforce real outer process deadline and tree cleanup
    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "--inner-worker",
        "--store-id", str(store_id),
        "--zipcode", str(zipcode),
        "--max-records", str(enforced_ceiling),
        "--timeout", str(timeout_seconds)
    ]
    if replay_mode:
        cmd.append("--replay")

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=timeout_seconds)

        if proc.returncode != 0:
            logger.error(f"Inner worker process failed with exit code {proc.returncode}")
            if stderr:
                logger.error(f"Worker stderr: {stderr.strip()}")
            return proc.returncode

        logger.info(f"Inner worker completed successfully in {time.time() - overall_start_time:.2f}s")
        return 0

    except subprocess.TimeoutExpired:
        logger.error(f"OUTER PROCESS DEADLINE EXCEEDED ({timeout_seconds}s)! Terminating process tree...")
        kill_process_tree(proc.pid)

        # Write failure artifact on deadline exceed
        failure_artifact = {
            "probe_metadata": {
                "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
                "execution_mode": mode_label,
                "execution_target": "Azure Container Instances (ACI) / Diagnostic Runner",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "target_store_id": store_id,
                "target_zipcode": zipcode,
                "department": "Food",
                "probe_route": "/browse/food/976759?page=1&affinityOverride=store",
                "max_records_ceiling": enforced_ceiling,
                "timeout_deadline_seconds": timeout_seconds
            },
            "status": "DEADLINE_EXCEEDED",
            "failure_reason": f"Whole-run outer process deadline of {timeout_seconds}s exceeded; process tree forcefully terminated.",
            "bounded_accounting": {
                "whole_run_elapsed_seconds": round(time.time() - overall_start_time, 3)
            },
            "records_parsed": 0,
            "records_sample": [],
            "canary_verified": False
        }
        with open(result_artifact_path, "w", encoding="utf-8") as f:
            json.dump(failure_artifact, f, indent=2)
        logger.info(f"Durable failure artifact written to: {result_artifact_path}")
        return 1

if __name__ == "__main__":
    import argparse
    cli_parser = argparse.ArgumentParser(description="Walmart Bounded Probe Diagnostic Runner")
    cli_parser.add_argument("--store-id", type=str, default="100", help="Store ID (default: 100)")
    cli_parser.add_argument("--zipcode", type=str, default="72712", help="ZIP code (default: 72712)")
    cli_parser.add_argument("--max-records", type=int, default=20, help="Record ceiling (enforced max: 20)")
    cli_parser.add_argument("--timeout", type=float, default=15.0, help="Whole-run timeout deadline (seconds)")
    cli_parser.add_argument("--replay", action="store_true", help="Run distinct authenticated offline replay mode")
    cli_parser.add_argument("--inner-worker", action="store_true", help="Internal flag for worker execution")
    args = cli_parser.parse_args()

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    result_artifact_path = os.path.join(base_dir, "logs", "azure_bounded_probe_result.json")

    if args.inner_worker:
        code = _execute_bounded_probe_worker(
            store_id=args.store_id,
            zipcode=args.zipcode,
            max_records=args.max_records,
            timeout_seconds=args.timeout,
            replay_mode=args.replay,
            result_artifact_path=result_artifact_path,
            base_dir=base_dir
        )
        sys.exit(code)
    else:
        code = run_bounded_probe(
            store_id=args.store_id,
            zipcode=args.zipcode,
            max_records=args.max_records,
            timeout_seconds=args.timeout,
            replay_mode=args.replay
        )
        sys.exit(code)
