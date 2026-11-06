import os
import json
import time
import random
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

logger = logging.getLogger("WalmartScraper.BrowserEngine")

class BrowserEngine:
    """
    Manages Playwright browser instances, anti-detect stealth capabilities,
    store location context selection, fulfillment state verification,
    and real-time request admission and byte budget enforcement.
    """

    def __init__(
        self,
        settings: Dict[str, Any],
        proxy_config: Optional[Dict[str, Any]] = None,
        max_requests: Optional[int] = None,
        max_bytes: Optional[int] = None,
        traffic_ledger_path: Optional[str] = None
    ):
        self.settings = settings.get("scraping", {})
        self.proxy_config = proxy_config or {}
        self.headless = self.settings.get("headless", True)
        self.page_timeout = self.settings.get("page_timeout_ms", 45000)
        self.min_delay = self.settings.get("request_delay_min_seconds", 2.0)
        self.max_delay = self.settings.get("request_delay_max_seconds", 4.5)

        self.max_requests = max_requests
        self.max_bytes = max_bytes
        self.traffic_ledger_path = traffic_ledger_path

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.traffic_stats = {"requests": 0, "redirects": 0, "bytes": 0, "retries": 0}

    def persist_ledger(self) -> None:
        """Atomically persists current traffic stats to ledger file as events occur."""
        if self.traffic_ledger_path:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.traffic_ledger_path)), exist_ok=True)
                temp_file = f"{self.traffic_ledger_path}.tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "requests_dispatched": self.traffic_stats.get("requests", 0),
                        "retries_attempted": self.traffic_stats.get("retries", 0),
                        "redirects_encountered": self.traffic_stats.get("redirects", 0),
                        "bytes_transferred": self.traffic_stats.get("bytes", 0),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }, f)
                import shutil
                shutil.move(temp_file, self.traffic_ledger_path)
            except Exception as e:
                logger.debug(f"Failed to persist traffic ledger: {e}")

    def record_traffic(
        self,
        requests: int = 0,
        bytes_count: int = 0,
        redirects: int = 0,
        retries: int = 0
    ) -> None:
        """
        Records traffic events, enforces admission budget limits in real time,
        and durably updates the persisted ledger on each event.
        """
        self.traffic_stats["requests"] += requests
        self.traffic_stats["bytes"] += bytes_count
        self.traffic_stats["redirects"] += redirects
        self.traffic_stats["retries"] += retries

        # Persist ledger immediately on event
        self.persist_ledger()

        # Enforce request admission limit
        if self.max_requests is not None and self.traffic_stats["requests"] > self.max_requests:
            raise RuntimeError(
                f"Live probe request admission budget exceeded: "
                f"{self.traffic_stats['requests']} requests dispatched (limit: {self.max_requests})"
            )

        # Enforce byte admission limit
        if self.max_bytes is not None and self.traffic_stats["bytes"] > self.max_bytes:
            raise RuntimeError(
                f"Live probe byte admission budget exceeded: "
                f"{self.traffic_stats['bytes']} bytes transferred (limit: {self.max_bytes})"
            )

    def start(self) -> None:
        """Starts Playwright and initializes browser context with stealth."""
        if self._playwright is None:
            self._playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certifcate-errors",
            "--ignore-certifcate-errors-spki-list",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]

        proxy_args = None
        if self.proxy_config.get("enabled"):
            server = self.proxy_config.get("http_proxy") or self.proxy_config.get("https_proxy")
            if server:
                proxy_args = {"server": server}
                logger.info(f"Using proxy: {server.split('@')[-1] if '@' in server else server}")

        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
            proxy=proxy_args
        )

        self._context = self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/Chicago"
        )

        # Context-wide traffic measurement attached BEFORE any navigation or store setup
        # Enforces admission budget limits in real time and durably persists ledger on each event
        def on_req(req):
            is_redirect = 1 if (req.is_navigation_request() and req.redirected_from) else 0
            self.record_traffic(requests=1, redirects=is_redirect)

        def on_res(res):
            b_len = 0
            try:
                b = res.body()
                b_len = len(b) if b else 0
            except Exception:
                pass
            if b_len > 0:
                self.record_traffic(bytes_count=b_len)

        self._context.on("request", on_req)
        self._context.on("response", on_res)

    def set_store_location(self, location: Dict[str, Any]) -> None:
        """
        Configures store and fulfillment context (Store ID, ZIP code, Pickup intent)
        in browser cookies and local storage so that in-store pricing is loaded.
        """
        if not self._context:
            return

        zipcode = str(location.get("zipcode", "72712"))
        store_id = str(location.get("store_id", "5260"))
        
        # Inject standard Walmart store context cookies
        cookies = [
            {"name": "postalCode", "value": zipcode, "domain": ".walmart.com", "path": "/"},
            {"name": "storeIntent", "value": "PICKUP", "domain": ".walmart.com", "path": "/"},
            {"name": "assrtLocation", "value": f'{{"intent":"PICKUP","postalCode":"{zipcode}","storeId":"{store_id}"}}', "domain": ".walmart.com", "path": "/"}
        ]
        try:
            self._context.add_cookies(cookies)
            logger.info(f"Store context configured in session: ZIP={zipcode}, StoreID={store_id}, Intent=PICKUP (verification deferred to source response)")
        except Exception as e:
            logger.warning(f"Failed to set store context cookies: {e}")

    def fetch_page_content(self, url: str) -> str:
        """
        Navigates to URL using Playwright stealth, waits for dynamic content,
        and returns rendered HTML.
        """
        if not self._context:
            self.start()

        # Controlled pacing delay
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

        page: Page = self._context.new_page()
        Stealth().apply_stealth_sync(page)

        try:
            logger.info(f"Navigating to {url} (pacing delay: {delay:.2f}s)")
            page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout)

            # Scroll slightly to trigger lazy-loading
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(1.0)

            content = page.content()
            return content
        finally:
            page.close()

    def close(self) -> None:
        """Closes browser and Playwright process cleanly."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.warning(f"Error closing browser engine: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
