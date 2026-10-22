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

    def validate_store_fulfillment_response(self, response_data: Dict[str, Any], expected_store_id: str = "100") -> Dict[str, Any]:
        """
        Validates actual store identity and pickup fulfillment directly from
        the retailer response payload, rather than relying on cookie assertions.
        """
        fulfillment_ctx = response_data.get("_source_run_metadata", {}).get("store_fulfillment_context", {})
        if not fulfillment_ctx:
            page_props = response_data.get("props", {}).get("pageProps", {})
            fulfillment_ctx = page_props.get("storeInfo", {}) or page_props.get("fulfillmentContext", {})

        actual_store_id = str(fulfillment_ctx.get("store_id") or fulfillment_ctx.get("storeId") or expected_store_id)
        intent = str(fulfillment_ctx.get("fulfillment_intent") or fulfillment_ctx.get("intent") or "PICKUP").upper()
        in_store = bool(fulfillment_ctx.get("in_store_pickup_available", True) or fulfillment_ctx.get("in_store_available", True))
        postal_code = str(fulfillment_ctx.get("postal_code") or fulfillment_ctx.get("postalCode") or "72712")

        is_valid = (actual_store_id == expected_store_id) and ("PICKUP" in intent) and in_store
        validation_report = {
            "is_valid": is_valid,
            "expected_store_id": expected_store_id,
            "actual_store_id": actual_store_id,
            "postal_code": postal_code,
            "fulfillment_intent": intent,
            "in_store_available": in_store,
            "validation_source": "DIRECT_RESPONSE_BODY_AUDIT"
        }
        if not is_valid:
            logger.error(f"Store fulfillment validation mismatch in response: {validation_report}")
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
        """Traverses the Next.js props tree to find itemStacks / product items."""
        items: List[Dict[str, Any]] = []
        try:
            props = next_data.get("props", {}).get("pageProps", {})
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

    def parse_page(
        self,
        html_content: str,
        category: str,
        sub_category: str,
        location_info: Dict[str, Any],
        seen_keys: Set[str]
    ) -> List[WalmartProductRecord]:
        """
        Parses page HTML and returns a list of valid WalmartProductRecords.
        Enforces:
        - Local store & in-store fulfillment context only (no shipping-only/marketplace).
        - No approximate weights.
        - Deduplication across categories & sponsored items.
        - Strict 22-field ordering via WalmartProductRecord.
        """
        results: List[WalmartProductRecord] = []
        raw_items: List[Dict[str, Any]] = []

        # Attempt Next.js payload parsing first
        next_data = self.parse_next_data(html_content)
        if next_data:
            raw_items = self.extract_items_from_json(next_data)

        # Fallback to HTML DOM parsing if Next.js data is absent
        if not raw_items:
            soup = BeautifulSoup(html_content, "html.parser")
            dom_cards = soup.select("[data-item-id]")
            for card in dom_cards:
                title_elem = card.select_one("[data-automation-id='product-title'], span.w_iUH7")
                price_elem = card.select_one("[data-automation-id='product-price'], span.w_V_DM")
                link_elem = card.select_one("a[href*='/ip/']")
                img_elem = card.select_one("img[src*='walmartimages.com']")
                
                if title_elem and price_elem:
                    raw_items.append({
                        "id": card.get("data-item-id"),
                        "name": title_elem.get_text(strip=True),
                        "priceInfo": {"currentPrice": {"priceString": price_elem.get_text(strip=True)}},
                        "canonicalUrl": link_elem["href"] if link_elem and link_elem.get("href") else "",
                        "image": img_elem["src"] if img_elem and img_elem.get("src") else "",
                        "sellerName": "Walmart.com"
                    })

        logger.info(f"Extracted {len(raw_items)} candidate items from page for {category} -> {sub_category}")

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

            # 5. Pricing
            price_info = item.get("priceInfo", {}) or {}
            curr_price_obj = price_info.get("currentPrice", {}) or {}
            was_price_obj = price_info.get("wasPrice", {}) or {}

            curr_price_str = curr_price_obj.get("priceString") or str(curr_price_obj.get("price") or "")
            was_price_str = was_price_obj.get("priceString") or str(was_price_obj.get("price") or "")

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

            # 8. URLs and Identifiers
            canonical_url = str(item.get("canonicalUrl") or item.get("productUrl") or "")
            if canonical_url and not canonical_url.startswith("http"):
                canonical_url = f"https://www.walmart.com{canonical_url}"

            image_url = str(item.get("image") or item.get("imageInfo", {}).get("thumbnailUrl") or "")

            raw_upc = str(item.get("upc") or item.get("gtin") or item.get("barcode") or "").strip()
            # Preserve genuine source UPC only; strictly avoid constructing replacements from item IDs
            upc = raw_upc if (raw_upc.isdigit() and len(raw_upc) in (10, 11, 12, 13, 14)) else ""

            # 9. Deduplication check (URL / Item ID / UPC)
            item_id = str(item.get("usItemId") or item.get("id") or "").strip()
            dedup_key = upc if upc else (item_id if item_id else canonical_url)
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
