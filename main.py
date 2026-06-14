from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Metal Spot Prices API", version="1.0.0")

KITCO_URL = "https://www.kitco.com/price/precious-metals"
FRANKFURTER_URL = "https://api.frankfurter.app/latest?from=USD&to=INR"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
}

# Map Kitco JSON metal name (lowercase) → our canonical key
METAL_KEYS = {"platinum": "platinum", "palladium": "palladium", "rhodium": "rhodium"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PricesResponse(BaseModel):
    pt_bid: Optional[float] = None
    pd_bid: Optional[float] = None
    rh_bid: Optional[float] = None
    usd_to_inr: Optional[float] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Kitco parsing — Next.js / React Query dehydrated state
# ---------------------------------------------------------------------------


def _extract_from_results_array(results_list: list) -> dict:
    """Pull bid/ask/change/change_pct from a Kitco 'results' array entry."""
    if not results_list or not isinstance(results_list[0], dict):
        return {}
    r = results_list[0]
    return {
        "bid": _to_float(r.get("bid") or r.get("price") or r.get("last")),
        "ask": _to_float(r.get("ask") or r.get("offer")),
        "change": _to_float(r.get("change") or r.get("netChange")),
        "change_pct": _to_float(
            r.get("changePercentage")
            or r.get("changePct")
            or r.get("pctChange")
        ),
    }


def _walk_query_data(state_data: object, found: dict) -> None:
    """
    Kitco uses React Query (TanStack Query).  The dehydrated state has:
      state.data = {
          "Platinum": {"results": [{"bid": 1716, "change": -3, ...}]},
          "Palladium": {"results": [{"bid": 1268, ...}]},
          "Rhodium":   {"results": [{"bid": 5200, ...}]},
          ...
      }
    Walk any dict/list and fill 'found' when a metal key is seen.
    """
    if isinstance(state_data, dict):
        for raw_key, val in state_data.items():
            key = raw_key.lower()
            if key in METAL_KEYS and not found[METAL_KEYS[key]].get("bid"):
                if isinstance(val, dict):
                    # Results-array pattern
                    if "results" in val:
                        price_data = _extract_from_results_array(val["results"])
                    else:
                        # Flat pattern: {"bid": ..., "ask": ..., ...}
                        price_data = {
                            "bid": _to_float(val.get("bid") or val.get("price")),
                            "ask": _to_float(val.get("ask") or val.get("offer")),
                            "change": _to_float(val.get("change") or val.get("netChange")),
                            "change_pct": _to_float(val.get("changePercentage") or val.get("changePct")),
                        }
                    if price_data.get("bid"):
                        found[METAL_KEYS[key]].update(price_data)
                elif isinstance(val, (int, float)):
                    found[METAL_KEYS[key]]["bid"] = float(val)
            # Always recurse
            _walk_query_data(val, found)
    elif isinstance(state_data, list):
        for item in state_data:
            _walk_query_data(item, found)


def parse_kitco(html: str) -> dict:
    found: dict = {m: {} for m in METAL_KEYS}
    soup = BeautifulSoup(html, "lxml")

    # --- Strategy 1: __NEXT_DATA__ (Next.js SSR + React Query dehydrated state) ---
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            data = json.loads(tag.string)
            _walk_query_data(data, found)
        except json.JSONDecodeError:
            pass

    if all(found[m].get("bid") for m in found):
        return found

    # --- Strategy 2: Any inline script containing metal names + JSON blobs ---
    for script in soup.find_all("script"):
        src = script.string or ""
        if not any(m in src.lower() for m in METAL_KEYS):
            continue
        for match in re.finditer(r"\{[^<>]{10,20000}\}", src, re.DOTALL):
            try:
                obj = json.loads(match.group())
                _walk_query_data(obj, found)
            except json.JSONDecodeError:
                pass
        if all(found[m].get("bid") for m in found):
            break

    # --- Strategy 3: HTML table rows ---
    if not all(found[m].get("bid") for m in found):
        PRICE_RE = re.compile(r"\b\d{3,6}\.\d{2,4}\b")
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                texts = [c.get_text(strip=True) for c in cells]
                row_lower = " ".join(texts).lower()
                for metal in METAL_KEYS:
                    if metal in row_lower and not found[metal].get("bid"):
                        nums = [float(n) for t in texts for n in PRICE_RE.findall(t) if float(n) > 50]
                        if nums:
                            found[metal]["bid"] = nums[0]
                            if len(nums) > 1:
                                found[metal]["ask"] = nums[1]

    return found


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get(
    "/prices",
    response_model=PricesResponse,
    summary="Live PT, PD, RH spot prices (USD) + INR/USD exchange rate",
)
async def get_prices():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            kitco_resp, fx_resp = await asyncio.gather(
                client.get(KITCO_URL, headers=HEADERS),
                client.get(FRANKFURTER_URL, follow_redirects=True),
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail=f"Network error: {exc}")

    if kitco_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Kitco returned HTTP {kitco_resp.status_code}",
        )

    raw = parse_kitco(kitco_resp.text)

    usd_to_inr: Optional[float] = None
    if fx_resp.status_code == 200:
        try:
            usd_to_inr = fx_resp.json()["rates"]["INR"]
        except (KeyError, ValueError):
            pass

    return PricesResponse(
        pt_bid=raw.get("platinum", {}).get("bid"),
        pd_bid=raw.get("palladium", {}).get("bid"),
        rh_bid=raw.get("rhodium", {}).get("bid"),
        usd_to_inr=usd_to_inr,
    )


@app.get("/debug/kitco", summary="Raw Kitco probe — inspect HTML + what the parser found")
async def debug_kitco():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(KITCO_URL, headers=HEADERS)

    parsed = parse_kitco(resp.text)

    # Also surface every query's key from dehydratedState for inspection
    queries_summary = []
    soup = BeautifulSoup(resp.text, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            nd = json.loads(tag.string)
            queries = (
                nd.get("props", {})
                .get("pageProps", {})
                .get("dehydratedState", {})
                .get("queries", [])
            )
            for q in queries:
                qk = q.get("queryKey", "?")
                data_keys = list(q.get("state", {}).get("data", {}).keys()) if isinstance(q.get("state", {}).get("data"), dict) else "non-dict"
                queries_summary.append({"queryKey": qk, "data_keys": data_keys})
        except json.JSONDecodeError:
            pass

    return {
        "status_code": resp.status_code,
        "final_url": str(resp.url),
        "content_length": len(resp.text),
        "parsed_prices": parsed,
        "react_query_cache": queries_summary,
        "html_head": resp.text[:3000],
        "html_tail": resp.text[-3000:],
    }


@app.get("/debug/fx", summary="Raw exchange rate response from frankfurter.app")
async def debug_fx():
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(FRANKFURTER_URL)
    return {"status_code": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text}
