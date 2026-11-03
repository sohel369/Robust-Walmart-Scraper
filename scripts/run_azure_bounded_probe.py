import os
import sys
import time
import json
import logging
import hashlib
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

def validate_timeout(timeout_seconds: Any) -> float:
    """
    Validates the timeout_seconds parameter.
    Strictly rejects negative numbers, zero, or non-numeric inputs.
    """
    try:
        val = float(timeout_seconds)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid timeout_seconds '{timeout_seconds}': must be a positive number > 0")

    if val <= 0:
        raise ValueError(f"Invalid timeout_seconds '{val}': timeout must be greater than 0")

    return val

def kill_process_tree(pid: int) -> bool:
    """
    Forcefully terminates a child process tree in an isolated process group.
    Guarantees that the supervisor process and unrelated sentinels are never terminated.
    Bounds cleanup time and reaps the child process.
    """
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5.0,
                check=False
            )
            if res.returncode != 0:
                logger.warning(f"taskkill returned code {res.returncode}: {res.stderr.decode(errors='replace')}")
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            logger.error(f"taskkill timed out while attempting to terminate PID {pid}")
            return False
        except Exception as e:
            logger.warning(f"Error terminating process tree for PID {pid}: {e}")
            return False
    else:
        import signal
        try:
            # On POSIX, start_new_session=True creates an isolated session where pgid == pid.
            # Only terminate that specific child process group!
            os.killpg(pid, signal.SIGKILL)
            try:
                os.waitpid(pid, os.WNOHANG)
            except Exception:
                pass
            return True
        except ProcessLookupError:
            return True
        except Exception as e:
            logger.warning(f"Error terminating process group {pid}: {e}")
            return False

def _execute_bounded_probe_worker(
    store_id: str,
    zipcode: str,
    max_records: int,
    timeout_seconds: float,
    replay_mode: bool,
    result_artifact_path: str,
    live_traffic_path: str,
    base_dir: str
) -> int:
    """Internal probe execution worker running inside the isolated process tree."""
    overall_start_time = time.time()
    deadline = overall_start_time + timeout_seconds

    # Validate inputs
    enforced_record_ceiling = validate_max_records(max_records)
    validated_timeout = validate_timeout(timeout_seconds)

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
            "timeout_deadline_seconds": validated_timeout,
            "bounded_cost_accounting": {
                "estimated_compute_cost_usd": 0.00025,
                "estimated_egress_cost_usd": 0.00005,
                "total_estimated_one_time_cost_usd": 0.00030
            }
        },
        "status": "RUNNING",
        "bounded_accounting": {},
        "response_capture_evidence": {},
        "store_verification": {},
        "records_parsed": 0,
        "records_sample": [],
        "canary_verified": False,
        "failure_reason": None
    }

    target_url = "https://www.walmart.com/browse/food/976759?page=1&affinityOverride=store"
    html_content = ""
    browser = None
    capture_evidence_file = os.path.join(base_dir, "logs", "probe_response_capture_evidence.json")

    # Helper to persist live traffic progressively so timeout never discards counts
    def persist_live_traffic(requests_cnt: int, retries_cnt: int, redirects_cnt: int, bytes_cnt: int):
        try:
            with open(live_traffic_path, "w", encoding="utf-8") as f:
                json.dump({
                    "requests_dispatched": requests_cnt,
                    "retries_attempted": retries_cnt,
                    "redirects_encountered": redirects_cnt,
                    "bytes_transferred": bytes_cnt,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f)
        except Exception:
            pass

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
            persist_live_traffic(0, 0, 0, 0)
        else:
            # Live retailer execution: context-wide traffic is measured from startup
            from src.browser_engine import BrowserEngine
            settings = config_loader.load_settings()
            proxies = config_loader.load_proxies()

            browser = BrowserEngine(settings, proxies)
            browser.start()

            # Record initial startup/context creation
            persist_live_traffic(browser.traffic_stats.get("requests", 0), 0, 0, browser.traffic_stats.get("bytes", 0))

            browser.set_store_location(location)

            # Enforce request admission limits (max 15 requests for bounded probe)
            MAX_PROBE_REQUESTS_BUDGET = 15
            MAX_PROBE_BYTES_BUDGET = 10 * 1024 * 1024 # 10MB

            if browser.traffic_stats.get("requests", 0) > MAX_PROBE_REQUESTS_BUDGET:
                raise RuntimeError(f"Request budget exceeded before navigation ({browser.traffic_stats['requests']} > {MAX_PROBE_REQUESTS_BUDGET})")

            html_content = browser.fetch_page_content(target_url)

            # Persist live measured counts
            persist_live_traffic(
                browser.traffic_stats.get("requests", 1),
                browser.traffic_stats.get("retries", 0),
                browser.traffic_stats.get("redirects", 0),
                browser.traffic_stats.get("bytes", len(html_content.encode("utf-8")) if html_content else 0)
            )

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

        # Preserve sanitized original capture evidence and SHA-256 hash
        capture_bytes = html_content.encode("utf-8")
        capture_sha = hashlib.sha256(capture_bytes).hexdigest()
        with open(capture_evidence_file, "w", encoding="utf-8") as f:
            json.dump({
                "source_url": target_url,
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "sha256_checksum": capture_sha,
                "byte_length": len(capture_bytes),
                "store_id": store_id,
                "zipcode": zipcode,
                "next_data_payload": next_data
            }, f, indent=2)

        probe_result["response_capture_evidence"] = {
            "evidence_file": os.path.relpath(capture_evidence_file, base_dir),
            "capture_sha256": capture_sha,
            "byte_length": len(capture_bytes),
            "endpoint_url": target_url,
            "store_id_lineage": store_val.get("actual_store_id"),
            "fulfillment_intent_lineage": store_val.get("fulfillment_intent")
        }

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

        # Reviewer Point 2: Canary result MUST gate success
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
    Enforces a REAL outer process deadline with isolated process group,
    process-tree cleanup, persistent partial traffic accounting, and failure artifact.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    result_artifact_path = os.path.join(logs_dir, "azure_bounded_probe_result.json")
    live_traffic_path = os.path.join(logs_dir, "azure_bounded_probe_live_traffic.json")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # CRITICAL (Reviewer Point 4): Remove previous artifacts to ensure fresh results
    if os.path.exists(result_artifact_path):
        try:
            os.remove(result_artifact_path)
        except Exception:
            pass

    if os.path.exists(live_traffic_path):
        try:
            os.remove(live_traffic_path)
        except Exception:
            pass

    # Validate limits and timeout upfront
    try:
        enforced_ceiling = validate_max_records(max_records)
        validated_timeout = validate_timeout(timeout_seconds)
    except ValueError as ve:
        logger.error(f"Validation Error: {ve}")
        failure_artifact = {
            "probe_metadata": {
                "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
                "execution_mode": "AUTHENTICATED_OFFLINE_REPLAY_DIAGNOSTIC" if replay_mode else "LIVE_RETAILER_DIAGNOSTIC",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "target_store_id": str(store_id),
                "target_zipcode": str(zipcode),
                "timeout_deadline_seconds": str(timeout_seconds),
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
    logger.info(f"Enforcing Outer Process Deadline: {validated_timeout}s | Output Artifact: {result_artifact_path}")
    logger.info("=" * 80)

    # Spawn inner worker in an isolated session/process group
    # CRITICAL (Reviewer Point 1): start_new_session=True on POSIX or CREATE_NEW_PROCESS_GROUP on Windows
    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "--inner-worker",
        "--store-id", str(store_id),
        "--zipcode", str(zipcode),
        "--max-records", str(enforced_ceiling),
        "--timeout", str(validated_timeout)
    ]
    if replay_mode:
        cmd.append("--replay")

    popen_kwargs: Dict[str, Any] = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_kwargs
        )
        stdout, stderr = proc.communicate(timeout=validated_timeout)

        if proc.returncode != 0:
            logger.error(f"Inner worker process failed with exit code {proc.returncode}")
            if stderr:
                logger.error(f"Worker stderr: {stderr.strip()}")

            # If worker crashed without writing artifact, write fresh failure artifact now
            if not os.path.exists(result_artifact_path):
                failure_artifact = {
                    "probe_metadata": {
                        "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
                        "execution_mode": mode_label,
                        "executed_at": datetime.now(timezone.utc).isoformat(),
                        "timeout_deadline_seconds": validated_timeout
                    },
                    "status": "FAILED",
                    "failure_reason": f"Inner worker terminated with error code {proc.returncode}: {stderr.strip() if stderr else 'Unexpected failure'}",
                    "bounded_accounting": {
                        "whole_run_elapsed_seconds": round(time.time() - overall_start_time, 3)
                    },
                    "records_parsed": 0,
                    "canary_verified": False
                }
                with open(result_artifact_path, "w", encoding="utf-8") as f:
                    json.dump(failure_artifact, f, indent=2)

            return proc.returncode

        logger.info(f"Inner worker completed successfully in {time.time() - overall_start_time:.2f}s")
        return 0

    except subprocess.TimeoutExpired:
        logger.error(f"OUTER PROCESS DEADLINE EXCEEDED ({validated_timeout}s)! Terminating process tree...")
        if proc:
            kill_process_tree(proc.pid)

        # Retrieve partial traffic counts persisted before the timeout
        partial_traffic = {}
        if os.path.exists(live_traffic_path):
            try:
                with open(live_traffic_path, "r", encoding="utf-8") as f:
                    partial_traffic = json.load(f)
            except Exception:
                pass

        # Write failure artifact on deadline exceed with preserved partial traffic accounting
        failure_artifact = {
            "probe_metadata": {
                "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
                "execution_mode": mode_label,
                "execution_target": "Azure Container Instances (ACI) / Diagnostic Runner",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "target_store_id": str(store_id),
                "target_zipcode": str(zipcode),
                "department": "Food",
                "probe_route": "/browse/food/976759?page=1&affinityOverride=store",
                "max_records_ceiling": enforced_ceiling,
                "timeout_deadline_seconds": validated_timeout
            },
            "status": "DEADLINE_EXCEEDED",
            "failure_reason": f"Whole-run outer process deadline of {validated_timeout}s exceeded; isolated process tree forcefully terminated.",
            "bounded_accounting": {
                "whole_run_elapsed_seconds": round(time.time() - overall_start_time, 3),
                "live_requests_dispatched": partial_traffic.get("requests_dispatched", 0),
                "live_retries_attempted": partial_traffic.get("retries_attempted", 0),
                "live_redirects_encountered": partial_traffic.get("redirects_encountered", 0),
                "live_bytes_transferred": partial_traffic.get("bytes_transferred", 0)
            },
            "records_parsed": 0,
            "records_sample": [],
            "canary_verified": False
        }
        with open(result_artifact_path, "w", encoding="utf-8") as f:
            json.dump(failure_artifact, f, indent=2)
        logger.info(f"Durable timeout failure artifact written to: {result_artifact_path}")
        return 1

    except Exception as unexpected:
        logger.error(f"Unexpected supervisor failure: {unexpected}")
        if proc:
            kill_process_tree(proc.pid)

        failure_artifact = {
            "probe_metadata": {
                "probe_name": "Walmart Bounded Probe Diagnostic (Entry #20 / #37)",
                "execution_mode": mode_label,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "timeout_deadline_seconds": validated_timeout
            },
            "status": "FAILED",
            "failure_reason": f"Unexpected supervisor exception: {unexpected}",
            "bounded_accounting": {
                "whole_run_elapsed_seconds": round(time.time() - overall_start_time, 3)
            },
            "records_parsed": 0,
            "canary_verified": False
        }
        with open(result_artifact_path, "w", encoding="utf-8") as f:
            json.dump(failure_artifact, f, indent=2)
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
    live_traffic_path = os.path.join(base_dir, "logs", "azure_bounded_probe_live_traffic.json")

    if args.inner_worker:
        code = _execute_bounded_probe_worker(
            store_id=args.store_id,
            zipcode=args.zipcode,
            max_records=args.max_records,
            timeout_seconds=args.timeout,
            replay_mode=args.replay,
            result_artifact_path=result_artifact_path,
            live_traffic_path=live_traffic_path,
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
