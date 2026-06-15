import asyncio
import json
import sys
from datetime import datetime, timezone

import httpx

from main import FRANKFURTER_URL, HEADERS, KITCO_URL, parse_kitco

PRICE_KEYS = ["pt_bid", "pd_bid", "rh_bid", "usd_to_inr"]


async def fetch():
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        kitco, fx = await asyncio.gather(
            client.get(KITCO_URL, headers=HEADERS),
            client.get(FRANKFURTER_URL),
        )

    raw = parse_kitco(kitco.text)
    result = {
        "pt_bid": raw["platinum"].get("bid"),
        "pd_bid": raw["palladium"].get("bid"),
        "rh_bid": raw["rhodium"].get("bid"),
        "usd_to_inr": fx.json()["rates"]["INR"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    missing = [k for k in PRICE_KEYS if result[k] is None]
    if missing:
        print(f"ERROR: missing values for {missing} — prices.json NOT updated", file=sys.stderr)
        sys.exit(1)

    # Only write if prices actually changed — avoids a commit every hour for no reason
    try:
        with open("prices.json") as f:
            existing = json.load(f)
        if all(result[k] == existing.get(k) for k in PRICE_KEYS):
            print("Prices unchanged — skipping update")
            sys.exit(0)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    with open("prices.json", "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))


asyncio.run(fetch())
