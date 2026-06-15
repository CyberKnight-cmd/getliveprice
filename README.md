# GetLivePrice

Live spot prices for Platinum (PT), Palladium (PD), and Rhodium (RH) sourced from Kitco, plus the live USD to INR exchange rate.

Prices are fetched automatically 3x a day via GitHub Actions and committed as `prices.json` to this repo. No server required.

## Latest prices

```
https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main/prices.json
```

Response shape:

```json
{
  "pt_bid": 1716.0,
  "pd_bid": 1268.0,
  "rh_bid": 7625.0,
  "usd_to_inr": 95.12,
  "fetched_at": "2026-06-14T10:00:00+00:00"
}
```

## Schedule

Prices are auto-fetched every hour. You can also trigger a manual fetch anytime from the **Actions** tab → **Fetch Metal Prices** → **Run workflow**.

## Run the API locally

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Then hit `http://localhost:8001/prices`.  
Swagger docs at `http://localhost:8001/docs`.

## How it works (the Kitco exploit)

Kitco's website is a Next.js app. When their server responds to a page request, it embeds the full React Query data cache into the HTML inside a `<script id="__NEXT_DATA__">` tag — this is how Next.js hydrates the frontend without making a second API call in the browser.

That tag contains a `dehydratedState.queries` array — essentially Kitco's internal API response, serialised directly into the page HTML. One of those queries, keyed `allMetalsQuote`, holds live bid/ask prices for gold, silver, platinum, palladium, and rhodium.

We never call any official API. We just request the same HTML page a browser would, parse the `__NEXT_DATA__` JSON blob, walk the React Query cache, and pull `results[0].bid` for PT, PD, and RH.

Kitco has no idea we're doing this — to them it looks like a regular browser page load.

## Data sources

- **Metal prices** — [kitco.com](https://www.kitco.com) (bid prices, USD per troy oz)
- **Exchange rate** — [frankfurter.app](https://www.frankfurter.app)
