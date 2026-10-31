import json
import logging
from typing import List, Dict, Any, Optional, Set
from bs4 import BeautifulSoup
from .models import WalmartProductRecord
from .normalizer import DataNormalizer

logger = logging.getLogger("WalmartScraper.Parser")

class WalmartPageParser:
    """
    Parses Walmart category and browse pages.
    Extracts raw products from Next.js state or DOM, enforces seller and fulfillment
    rules, filters out marketplace and approximate weights, and produces compliant records.
    """

    def __init__(self, normalizer: DataNormalizer):
        self.normalizer = normalizer
        self.last_raw_items_count: int = 0
        self.last_pagination_info: Dict[str, Any] = {}
        self.last_store_validation: Dict[str, Any] = {}
        self.last_page_status: str = "INITIALIZED"

    def validate_store_fulfillment_response(self, response_data: Dict[str, Any], expected_store_id: str = "100") -> Dict[str, Any]:
        """
        Validates actual store identity and pickup fulfillment directly from
        the retailer response payload, rather than relying on cookie assertions.
        Strictly fails closed: returns is_valid=False if context is missing, invalid, or mismatched.
        Native source fields take strict precedence over _source_run_metadata wrapper.
        """
        if not isinstance(response_data, dict):
            return {
                "is_valid": False,
                "expected_store_id": str(expected_store_id),
                "actual_store_id": "",
                "postal_code": "",
                "fulfillment_intent": "",
                "in_store_available": False,
                "validation_source": "DIRECT_RESPONSE_BODY_AUDIT"
            }

        # Check native pageProps store context first (native source fields must not be overridden by wrapper)
        page_props = response_data.get("props", {}).get("pageProps", {}) if isinstance(response_data.get("props"), dict) else {}
        native_ctx = page_props.get("storeInfo") or page_props.get("fulfillmentContext") or {}
        metadata_ctx = response_data.get("_source_run_metadata", {}).get("store_fulfillment_context", {})

        fulfillment_ctx = native_ctx if native_ctx else metadata_ctx

        # Strictly extract without falling back to expected store or defaults
        raw_store_id = fulfillment_ctx.get("store_id") or fulfillment_ctx.get("storeId")
        raw_intent = fulfillment_ctx.get("fulfillment_intent") or fulfillment_ctx.get("intent")
        raw_in_store = fulfillment_ctx.get("in_store_pickup_available")
        if raw_in_store is None:
            raw_in_store = fulfillment_ctx.get("in_store_available")
        raw_postal_code = fulfillment_ctx.get("postal_code") or fulfillment_ctx.get("postalCode")

        actual_store_id = str(raw_store_id).strip() if raw_store_id is not None else ""
        intent = str(raw_intent).upper().strip() if raw_intent is not None else ""
        postal_code = str(raw_postal_code).strip() if raw_postal_code is not None else ""

        # Validate types and exact modality
        # in_store: boolean check (string 'false' is strictly False, string 'true' is True)
        in_store = False
        if raw_in_store is not None:
            if isinstance(raw_in_store, str):
                in_store = raw_in_store.strip().lower() in ("true", "1", "yes")
            elif isinstance(raw_in_store, bool):
                in_store = raw_in_store
            elif isinstance(raw_in_store, (int, float)):
                in_store = bool(raw_in_store)

        # Modality: MUST be strictly PICKUP (reject NOT_PICKUP, NO_PICKUP, SHIPPING, DELIVERY)
        is_exact_pickup = False
        if intent:
            if "NOT" in intent or "NO_" in intent or "NON" in intent:
                is_exact_pickup = False
            elif intent in ("PICKUP", "STORE_PICKUP", "IN_STORE_PICKUP", "CURBSIDE_PICKUP"):
                is_exact_pickup = True
            elif intent.startswith("PICKUP"):
                is_exact_pickup = True

        # FAIL CLOSED: All essential store fulfillment fields must exist and match expected context
        has_context = bool(actual_store_id and intent and (raw_in_store is not None))
        is_valid = (
            has_context and
            (actual_store_id == str(expected_store_id)) and
            is_exact_pickup and
            (in_store is True)
        )
        validation_report = {
            "is_valid": is_valid,
            "expected_store_id": str(expected_store_id),
            "actual_store_id": actual_store_id,
            "postal_code": postal_code,
            "fulfillment_intent": intent,
            "in_store_available": in_store,
            "validation_source": "DIRECT_RESPONSE_BODY_AUDIT"
        }
        if not is_valid:
            logger.warning(f"Store fulfillment validation failed or context missing in response: {validation_report}")
        else:
            logger.info(f"Store fulfillment verified in response: Store #{actual_store_id} ({postal_code}), Intent={intent}")
        return validation_report

    def parse_next_data(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extracts the __NEXT_DATA__ JSON script tag from the page HTML."""
        soup = BeautifulSoup(html_content, "html.parser")
        script_tag = soup.find("script", id="__NEXT_DATA__")
        if script_tag and script_tag.string:
            try:
                return json.loads(script_tag.string)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to decode __NEXT_DATA__ JSON: {e}")
        return None

    def extract_items_from_json(self, next_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Traverses the Next.js props tree or API response payload to find itemStacks / product items."""
        items: List[Dict[str, Any]] = []
        try:
            # Location 0: Direct item_stack at root or props level
            direct_items = next_data.get("item_stack", [])
            if isinstance(direct_items, list) and direct_items:
                for item in direct_items:
                    if isinstance(item, dict) and (item.get("id") or item.get("usItemId") or item.get("productId")):
                        items.append(item)
                if items:
                    return items

            props = next_data.get("props", {}).get("pageProps", {})
            if props.get("item_stack"):
                for item in props.get("item_stack", []):
                    if isinstance(item, dict) and (item.get("id") or item.get("usItemId") or item.get("productId")):
                        items.append(item)
                if items:
                    return items

            initial_data = props.get("initialData", {}) or props.get("searchResult", {})
            
            # Location 1: itemStacks
            item_stacks = initial_data.get("itemStacks", [])
            for stack in item_stacks:
                for item in stack.get("items", []):
                    if isinstance(item, dict) and item.get("id"):
                        items.append(item)

            # Location 2: raw searchResults or aggs
            if not items:
                search_content = initial_data.get("searchContent", {})
                item_stacks = search_content.get("itemStacks", [])
                for stack in item_stacks:
                    for item in stack.get("items", []):
                        if isinstance(item, dict) and item.get("id"):
                            items.append(item)
        except Exception as e:
            logger.warning(f"Error traversing JSON for items: {e}")
        return items

    def extract_pagination_info(self, html_content: str, next_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Extracts source pagination markers (total pages, total records, has next page)
        from Next.js state or DOM to prevent premature pagination termination.
        """
        info = {
            "total_pages": None,
            "total_records": None,
            "has_next_page": None,
            "current_page": None
        }
        if next_data:
            try:
                props = next_data.get("props", {}).get("pageProps", {})
                initial_data = props.get("initialData", {}) or props.get("searchResult", {})
                pagination = initial_data.get("pagination", {})
                if pagination:
                    info["total_pages"] = pagination.get("maxPage") or pagination.get("totalPages")
                    info["total_records"] = pagination.get("totalResults")
                    info["current_page"] = pagination.get("page")
                    if info["total_pages"] and info["current_page"]:
                        info["has_next_page"] = info["current_page"] < info["total_pages"]
            except Exception as e:
                logger.debug(f"Error parsing Next.js pagination: {e}")

        if info["total_pages"] is None and html_content:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                next_btn = soup.select_one("a[aria-label='Next Page'], a[data-automation-id='pagination-next']")
                info["has_next_page"] = (next_btn is not None and "disabled" not in next_btn.get("class", []))
            except Exception as e:
                logger.debug(f"Error parsing DOM pagination: {e}")

        return info

    def parse_page(
        self,
        html_content: str,
        category: str,
        sub_category: str,
        location_info: Dict[str, Any],
        seen_keys: Set[str],
        enforce_store_validation: bool = True
    ) -> List[WalmartProductRecord]:
        """
        Parses page HTML and returns a list of valid WalmartProductRecords.
        Enforces:
        - Local store & in-store fulfillment context only (no shipping-only/marketplace).
        - Direct tie-in of returned product prices to verified store response context.
        - Retention of unpriced items in DOM fallback with empty source-missing strings.
        - No approximate weights.
        - Deduplication across categories & sponsored items.
        - Strict 22-field ordering via WalmartProductRecord.
        """
        results: List[WalmartProductRecord] = []
        raw_items: List[Dict[str, Any]] = []
        expected_store = str(location_info.get("store_id", "100"))
        page_store_verified = False

        # Attempt Next.js payload parsing first
        next_data = self.parse_next_data(html_content)
        if next_data:
            val_report = self.validate_store_fulfillment_response(next_data, expected_store_id=expected_store)
            self.last_store_validation = val_report

            # If response explicitly contains a conflicting/mismatched store context:
            if val_report.get("actual_store_id") and val_report["actual_store_id"] != expected_store:
                logger.error(
                    f"Rejecting page products: store response mismatch. "
                    f"Expected Store #{expected_store}, got #{val_report.get('actual_store_id')} ({val_report.get('fulfillment_intent')})"
                )
                self.last_page_status = "REJECTED_STORE_CONTEXT"
                self.last_raw_items_count = 0
                self.last_pagination_info = {}
                return []

            if val_report["is_valid"]:
                page_store_verified = True
                self.last_page_status = "VALID"
            else:
                # Missing or incomplete store context
                self.last_page_status = "UNVERIFIED_STORE_CONTEXT"

            raw_items = self.extract_items_from_json(next_data)

        # Fallback to HTML DOM parsing if Next.js data is absent
        if not raw_items:
            soup = BeautifulSoup(html_content, "html.parser")
            dom_store_id = ""
            
            # Check DOM for store indicators
            store_elem = soup.select_one("[data-store-id], meta[name='store'], [data-automation-id='store-info']")
            if store_elem:
                dom_store_id = str(store_elem.get("data-store-id") or store_elem.get("content") or "").strip()

            if dom_store_id and dom_store_id != expected_store:
                logger.error(f"Rejecting DOM page: store mismatch (expected #{expected_store}, got #{dom_store_id})")
                self.last_page_status = "REJECTED_STORE_CONTEXT"
                self.last_raw_items_count = 0
                return []

            dom_verified = (dom_store_id == expected_store)
            val_report = {
                "is_valid": dom_verified,
                "expected_store_id": expected_store,
                "actual_store_id": dom_store_id,
                "fulfillment_intent": "PICKUP" if dom_verified else "",
                "in_store_available": dom_verified,
                "validation_source": "DOM_INSPECTION"
            }
            self.last_store_validation = val_report
            page_store_verified = dom_verified
            self.last_page_status = "VALID" if page_store_verified else "UNVERIFIED_STORE_CONTEXT"

            dom_cards = soup.select("[data-item-id]")
            for card in dom_cards:
                title_elem = card.select_one("[data-automation-id='product-title'], span.w_iUH7")
                price_elem = card.select_one("[data-automation-id='product-price'], span.w_V_DM")
                link_elem = card.select_one("a[href*='/ip/']")
                img_elem = card.select_one("img[src*='walmartimages.com']")
                
                # Retain eligible products even if price element is missing (source-missing value)
                if title_elem:
                    price_str = price_elem.get_text(strip=True) if price_elem else ""
                    raw_items.append({
                        "id": card.get("data-item-id"),
                        "name": title_elem.get_text(strip=True),
                        "priceInfo": {"currentPrice": {"priceString": price_str}},
                        "canonicalUrl": link_elem["href"] if link_elem and link_elem.get("href") else "",
                        "image": img_elem["src"] if img_elem and img_elem.get("src") else "",
                        "sellerName": "Walmart.com"
                    })

        self.last_raw_items_count = len(raw_items)
        self.last_pagination_info = self.extract_pagination_info(html_content, next_data)
        logger.info(
            f"Extracted {len(raw_items)} raw candidate items from page for {category} -> {sub_category} "
            f"(Store verified: {page_store_verified}, Status: {self.last_page_status})"
        )

        for item in raw_items:
            # 1. Filter out Marketplace / 3rd-Party items (Local Walmart only)
            seller_name = str(item.get("sellerName") or item.get("seller", {}).get("name") or "Walmart.com")
            if "walmart" not in seller_name.lower():
                continue # Skip 3rd party marketplace sellers

            # 2. Filter out Shipping-only items (Must be available for Local Pickup / In-Store)
            fulfillment = item.get("fulfillment", {}) or item.get("fulfillmentBadge", "")
            fulfillment_type = item.get("fulfillmentType") or ""
            if "shipping_only" in str(fulfillment_type).lower():
                continue

            # 3. Product Title & Brand
            title = str(item.get("name") or item.get("title") or "").strip()
            if not title:
                continue

            brand = str(item.get("brand") or "").strip()
            store_brand = self.normalizer.is_walmart_private_brand(title, brand)

            # 4. Weight & Packaging
            weight = str(item.get("weight") or item.get("packageSize") or item.get("size") or "").strip()

            # RULE: Exclude products whose weight contains "approx", "approximately" or equivalent
            if self.normalizer.is_approximate_weight(weight) or self.normalizer.is_approximate_weight(title):
                logger.debug(f"Excluding product with approximate weight: {title} ({weight})")
                continue

            # 5. Pricing & Item-level context validation
            price_info = item.get("priceInfo", {}) or {}
            item_store = str(price_info.get("storeId") or price_info.get("store_id") or item.get("storeId") or "").strip()
            item_fulfillment = str(price_info.get("fulfillmentType") or price_info.get("fulfillment") or item.get("fulfillmentType") or "").upper()

            has_conflicting_price_store = bool(item_store and item_store != expected_store)
            is_shipping_only_price = ("SHIPPING" in item_fulfillment and "PICKUP" not in item_fulfillment) or ("SHIPPING_ONLY" in item_fulfillment)

            # CRITICAL RULE (Reviewer Point 1):
            # Only export local store prices if the page-level store context is strictly verified
            # AND the item's own priceInfo does not conflict with expected store or specify shipping-only.
            # Otherwise preserve eligible unavailable identity with empty price values ("").
            can_export_local_price = page_store_verified and not has_conflicting_price_store and not is_shipping_only_price

            curr_price_obj = price_info.get("currentPrice", {}) or {}
            was_price_obj = price_info.get("wasPrice", {}) or {}

            curr_price_str = curr_price_obj.get("priceString") or str(curr_price_obj.get("price") or "")
            was_price_str = was_price_obj.get("priceString") or str(was_price_obj.get("price") or "")

            if can_export_local_price:
                cleaned_curr_price = self.normalizer.clean_price(curr_price_str)
                cleaned_was_price = self.normalizer.clean_price(was_price_str)

                if cleaned_was_price and cleaned_was_price != cleaned_curr_price:
                    regular_price = cleaned_was_price
                    sale_price = cleaned_curr_price
                else:
                    regular_price = cleaned_curr_price
                    sale_price = ""

                # 6. PPM (Price Per Measure) - Scraped directly, never computed
                raw_ppm = (
                    curr_price_obj.get("unitPrice") or
                    price_info.get("unitPrice", {}).get("priceString") or
                    item.get("unitPriceString") or
                    ""
                )
                ppm = self.normalizer.extract_ppm(str(raw_ppm))

                # 7. Deals Classification (Volume vs Digital)
                badge_text = str(item.get("badge", {}).get("text") or item.get("offerBadge") or "")
                deal_desc = str(item.get("dealDescription") or "")
                promo_price_disp = str(item.get("promoPrice") or "")

                vol_desc, vol_price, dig_desc, dig_price = self.normalizer.parse_deals(
                    badge_text, deal_desc, promo_price_disp
                )
            else:
                # Unverified store or conflicting priceInfo: preserve identity with empty price values
                regular_price = ""
                sale_price = ""
                ppm = ""
                badge_text = str(item.get("badge", {}).get("text") or item.get("offerBadge") or "")
                deal_desc = str(item.get("dealDescription") or "")
                vol_desc, _, dig_desc, _ = self.normalizer.parse_deals(badge_text, deal_desc, "")
                vol_price = ""
                dig_price = ""

            # 8. URLs and Identifiers
            canonical_url = str(item.get("canonicalUrl") or item.get("productUrl") or "")
            if canonical_url and not canonical_url.startswith("http"):
                canonical_url = f"https://www.walmart.com{canonical_url}"

            image_url = str(item.get("image") or item.get("imageInfo", {}).get("thumbnailUrl") or "")

            raw_upc = str(item.get("upc") or item.get("gtin") or item.get("barcode") or "").strip()
            # Preserve genuine source UPC only; strictly avoid constructing replacements from item IDs
            upc = raw_upc if (raw_upc.isdigit() and len(raw_upc) in (10, 11, 12, 13, 14)) else ""

            # 9. Deduplication check (Consistent fallback order: UPC -> URL -> ID -> Title)
            item_id = str(item.get("usItemId") or item.get("id") or item.get("productId") or "").strip()
            dedup_key = upc if upc else (canonical_url if canonical_url else (item_id if item_id else title))
            if not dedup_key or dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            # 10. Construct standardized record
            record = WalmartProductRecord(
                zipcode=str(location_info.get("zipcode", "")),
                store_name=str(location_info.get("store_name", "")),
                store_location=str(location_info.get("store_location", "")),
                store_logo=str(location_info.get("store_logo", "")),
                store_brand=store_brand,
                category=category,
                sub_category=sub_category,
                product_title=title,
                weight=weight,
                regular_price=regular_price,
                sale_price=sale_price,
                volume_deals_description=vol_desc,
                volume_deals_price=vol_price,
                digital_deals_description=dig_desc,
                digital_deals_price=dig_price,
                image_url=image_url,
                url=canonical_url,
                upc=upc,
                gupc="",           # Always empty string per requirements
                ppm=ppm,           # Scraped verbatim or empty
                unit_price=""      # Always empty string per requirements
            )
            results.append(record)

        return results
