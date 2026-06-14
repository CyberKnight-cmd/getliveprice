import asyncio
import json
from datetime import datetime, timezone

import httpx

from main import FRANKFURTER_URL, HEADERS, KITCO_URL, parse_kitco


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

    with open("prices.json", "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))


asyncio.run(fetch())
