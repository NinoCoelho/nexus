---
name: market-data
description: Use this whenever you need stock quotes, company fundamentals, forex, crypto, or market data. Prefer over manual web scraping for any financial data need.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
requires_keys:
  - ALPHA_VANTAGE_API_KEY
  - FINNHUB_API_KEY
---

## When to use
- Fetching stock quotes, historical prices, or company fundamentals
- Getting forex rates or crypto prices
- Market comparisons, earnings data, or financial analysis
- Enriching daily news summaries with market context
- Any "what's the price of X" or "how is Y stock doing" query

## Available APIs

### Alpha Vantage (`$ALPHA_VANTAGE_API_KEY`)
- **Rate limit**: 25 requests/day (free tier) -- use sparingly
- **Best for**: Historical time series, technical indicators, fundamentals

### Finnhub (`$FINNHUB_API_KEY`)
- **Rate limit**: 60 calls/minute (free tier) -- use for real-time data
- **Best for**: Real-time quotes, company news, basic financials, forex, crypto

## Steps

### 1. Real-time quote -- use Finnhub (fast, generous rate limit)

```
GET https://finnhub.io/api/v1/quote?symbol={SYMBOL}&token=$FINNHUB_API_KEY
```

Response fields: `c` (current), `d` (change), `dp` (change %), `h` (high), `l` (low), `o` (open), `pc` (prev close).

### 2. Historical daily prices -- use Alpha Vantage

```
GET https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={SYMBOL}&outputsize=compact&apikey=$ALPHA_VANTAGE_API_KEY
```

- `outputsize=compact` returns last 100 data points (default)
- `outputsize=full` returns 20+ years -- use only when specifically asked

### 3. Company fundamentals -- use Finnhub

```
GET https://finnhub.io/api/v1/stock/metric?symbol={SYMBOL}&metric=all&token=$FINNHUB_API_KEY
```

Returns: market cap, P/E, EPS, dividend yield, 52-week high/low, etc.

### 4. Company profile -- use Finnhub

```
GET https://finnhub.io/api/v1/stock/profile2?symbol={SYMBOL}&token=$FINNHUB_API_KEY
```

Returns: name, industry, exchange, logo, market cap, outstanding shares.

### 5. Company news -- use Finnhub

```
GET https://finnhub.io/api/v1/company-news?symbol={SYMBOL}&from=YYYY-MM-DD&to=YYYY-MM-DD&token=$FINNHUB_API_KEY
```

Date format: `YYYY-MM-DD`. Returns recent news articles for the ticker.

### 6. Forex rates -- use Finnhub

```
GET https://finnhub.io/api/v1/forex/rates?base=USD&token=$FINNHUB_API_KEY
```

### 7. Crypto prices -- use Finnhub

```
GET https://finnhub.io/api/v1/crypto/candle?symbol=BINANCE:BTCUSDT&resolution=D&from={UNIX_FROM}&to={UNIX_TO}&token=$FINNHUB_API_KEY
```

Resolution: `1`, `5`, `15`, `30`, `60`, `D`, `W`, `M`.

### 8. Earnings calendar -- use Finnhub

```
GET https://finnhub.io/api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD&token=$FINNHUB_API_KEY
```

### 9. Multiple quotes at once

For comparing multiple stocks, call Finnhub quote for each symbol sequentially or in parallel:

```
GET https://finnhub.io/api/v1/quote?symbol=AAPL&token=$FINNHUB_API_KEY
GET https://finnhub.io/api/v1/quote?symbol=MSFT&token=$FINNHUB_API_KEY
GET https://finnhub.io/api/v1/quote?symbol=GOOGL&token=$FINNHUB_API_KEY
```

### 10. Presenting results

For single stock: show current price, change, day range, previous close.
For comparisons: use a markdown table with columns: Symbol | Price | Change | Change %.
For historical: summarize trend (up/down/sideways over period) with key stats.

## Gotchas
- **Alpha Vantage rate limit is strict** (25/day free). Default to Finnhub for real-time; use Alpha Vantage only for historical time series or technical indicators that Finnhub doesn't offer.
- **Symbol format**: Use standard ticker symbols (AAPL, MSFT). For crypto on Finnhub, use `EXCHANGE:PAIR` format (e.g., `BINANCE:BTCUSDT`).
- **Finnhub free tier** doesn't include US premium data (some alternative data, insider transactions). If you get an empty response, the data may require a paid plan.
- **Stale data**: Free tiers may have 15-minute delayed quotes. Note this when presenting to the user.
- **Market hours**: Quotes outside US market hours will show the last traded price (previous close or after-hours).
