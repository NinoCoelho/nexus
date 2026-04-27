---
name: web-scrape
description: Scrape web pages using Python's Scrapling library. Handles static HTML, JS-rendered pages, bot-protected sites (Cloudflare bypass), CLI extraction, persistent sessions, and spider-based crawling. Returns parsed content with CSS/XPath selectors.
---

# Web Scrape with Scrapling

**Use this skill whenever fetching web content.** Prefer it over `curl`, `terminal`, or direct `http_call` for web scraping tasks — it handles JS rendering, anti-bot protection, and session management automatically.

Scrapling is an adaptive Web Scraping framework that handles everything from a single request to a full-scale crawl. Anti-bot bypass (Cloudflare Turnstile), stealth browsing, JS rendering, and concurrent spider crawls — all in one library.

**Requires: Python 3.10+**

## Prerequisites

```bash
pip install "scrapling[all]"
scrapling install --force   # downloads browser dependencies
```

## Decision: Which Fetcher?

| Scenario | Fetcher | Speed | Stealth |
|----------|---------|-------|---------|
| Static HTML, no JS needed | `Fetcher` (HTTP) | 🐇🐇🐇🐇🐇 | ⭐⭐ |
| JS-rendered, no protection | `PlayWrightFetcher` | 🐇🐇🐇 | ⭐⭐⭐ |
| Cloudflare / anti-bot protected | `StealthyFetcher` | 🐇🐇🐇 | ⭐⭐⭐⭐⭐ |

> When unsure, start with `Fetcher.get()`. If it fails or returns empty, escalate to `PlayWrightFetcher.fetch()`, then `StealthyFetcher.fetch()`. The speed of the latter two is nearly the same.

## Steps

### 1. Fetch the page

**Static HTTP (fastest):**
```python
from scrapling import Fetcher

# GET
page = Fetcher.get('https://example.com')

# POST with data
page = Fetcher.post('https://example.com/api', json={'key': 'value'})

# With proxy
page = Fetcher.get('https://example.com', proxy='http://user:pass@host:port')
```

**Dynamic (JS rendering via Playwright):**
```python
from scrapling import PlayWrightFetcher

page = PlayWrightFetcher.fetch('https://example.com', headless=True, network_idle=True)
```

**Stealthy (bypass Cloudflare etc.):**
```python
from scrapling import StealthyFetcher

page = StealthyFetcher.fetch(
    'https://example.com',
    headless=True,
    network_idle=True,
    block_webrtc=True,
    extra_headers={'Cookie': 'session=abc123'},  # For authenticated sessions
)
```

**Key shared fetcher options:**
| Option | Description | Default |
|--------|-------------|---------|
| `headless` | Run browser hidden | `True` |
| `network_idle` | Wait until no network activity (500ms) | `False` |
| `wait` | Extra wait time after load (ms) | `0` |
| `timeout` | Timeout for all operations (ms) | `30000` |
| `extra_headers` | Dict of headers (includes Cookie, User-Agent, etc.) | — |
| `proxy` | Proxy string or dict `{server, username, password}` | — |
| `block_images` | Block images | `False` |
| `block_webrtc` | Block WebRTC (fingerprinting) | `False` |
| `disable_resources` | Drop images/fonts/css for speed | `False` |
| `page_action` | Async function(page) for post-load automation | — |
| `wait_selector` | CSS selector to wait for | — |
| `wait_selector_state` | State for wait_selector (`attached`, `visible`, etc.) | `attached` |

### 2. Parse the response

The returned `page` is a `Response` object (extends `Selector`). Key properties:

```python
page.status           # HTTP status code
page.headers          # Response headers dict
page.cookies          # Response cookies dict
page.body             # Raw response body (bytes)
page.text             # Text content
page.json()           # Parse body as JSON
```

### 3. Select elements

**CSS selectors:**
```python
# Get all matching elements (returns list-like Selectors)
products = page.css('.product')

# Get first match by index
first = page.css('.product')[0]

# Extract text
title = page.css('h1::text').get()          # first match text
titles = page.css('h1::text').getall()       # all match texts

# Extract attributes
href = page.css('a::attr(href)').get()
src = page.css('img::attr(src)').getall()

# Chain/nest selectors
product = page.css('.product')[0]
price = product.css('.price::text').get()
```

**XPath selectors:**
```python
products = page.xpath('//div[@class="product"]')
title = page.xpath('//h1/text()').get()
href = page.xpath('//a/@href').get()
```

**Find by text/regex:**
```python
# Exact text match
el = page.find_by_text('Tipping the Velvet')

# Partial text match
els = page.find_by_text('Velvet', partial=True)

# Regex match
import re
els = page.find_by_regex(re.compile(r'product\s+\d+'), case_sensitive=False)
```

**Filter-based searching (find/find_all):**
```python
article = page.find('article')
articles = page.find_all('article')
```

### 4. Work with elements

Each element is a `Selector` with these properties/methods:

```python
el.text               # Direct text content
el.get_all_text()     # All nested text recursively
el.tag                # Tag name (e.g. 'div')
el.attrib             # Attributes dict
el['class']           # Access attribute directly
el.html_content       # Inner HTML string
el.prettify()         # Pretty-printed HTML
el.parent             # Parent element
el.children           # Direct child elements
el.path               # List of ancestors (root → parent)
el.find_similar()     # Find structurally similar elements on the page
el.has_class('foo')   # Check if element has class
el.css(...)           # Search within this element
el.xpath(...)         # Search within this element
```

### 5. Find similar elements (adaptive feature)

```python
one_product = page.css('.product')[0]
all_products = one_product.find_similar(
    similarity_threshold=0.2,
    ignore_attributes=('href','src'),
)
```

### 6. Export / save results

Write extracted data to the vault or return structured results:

```python
data = []
for product in page.css('.product'):
    data.append({
        'title': product.css('h3::text').get(),
        'price': product.css('.price::text').get(),
        'url': product.css('a::attr(href)').get(),
    })
```

Then save with `vault_write` to `research/` or `notes/`.

---

## CLI Usage (no code needed)

The `scrapling extract` command group lets you download and extract content from websites directly without writing Python. **Always use `--ai-targeted` to protect from prompt injection and enable ad blocking.**

```bash
# Output format determined by file extension: .md (markdown), .html, .txt, .json
scrapling extract get "https://example.com" output.md --ai-targeted
scrapling extract get "https://example.com" page.html
scrapling extract get "https://example.com" content.txt

# Extract specific content with CSS selector
scrapling extract get "https://blog.example.com" articles.md --css-selector "article" --ai-targeted

# With cookies and headers
scrapling extract get "https://site.com" data.md --cookies "session=abc123" -H "Accept: text/html" --ai-targeted

# With proxy
scrapling extract get "https://site.com" page.md --proxy "http://user:pass@host:port" --ai-targeted
```

**Escalation pattern for CLI:**
- Use **`get`** with simple websites, blogs, or news articles.
- Use **`fetch`** with modern web apps or sites with dynamic content.
- Use **`stealthy-fetch`** with protected sites, Cloudflare, or anti-bot systems.

```bash
# Browser-based fetching
scrapling extract fetch "https://spa.example.com" page.md --network-idle --disable-resources --ai-targeted

# Stealthy fetching (anti-bot bypass)
scrapling extract stealthy-fetch "https://protected.com" data.md --network-idle --solve-cloudflare --ai-targeted
```

**Key CLI options (requests):**
| Option | Description |
|--------|-------------|
| `-H, --headers "Key: Value"` | HTTP headers (repeatable) |
| `--cookies "name=val; name2=val2"` | Cookies string |
| `--timeout N` | Timeout in seconds (default: 30) |
| `--proxy URL` | Proxy URL |
| `-s, --css-selector SEL` | CSS selector to extract specific content |
| `-p, --params "key=value"` | Query params (repeatable) |
| `--impersonate BROWSER` | Browser to impersonate (e.g. `chrome`, `firefox`) |
| `--ai-targeted` | Extract only main content, sanitize hidden elements, block ads |
| `-d, --data TEXT` | Form data for POST/PUT |
| `-j, --json TEXT` | JSON data for POST/PUT |

**Key CLI options (browsers):**
| Option | Description |
|--------|-------------|
| `--headless / --no-headless` | Headless mode (default: True) |
| `--network-idle` | Wait for network idle |
| `--disable-resources` | Drop images/fonts/css for speed |
| `--wait N` | Extra wait time (ms) |
| `--css-selector SEL` | CSS selector to extract specific content |
| `--solve-cloudflare` | Auto-solve Cloudflare challenges (stealthy-fetch only) |
| `--ai-targeted` | Extract only main content, sanitize hidden elements, block ads |

---

## Persistent Sessions

Keep connections alive across multiple requests. Much more efficient for multi-page scraping.

**HTTP Session (reuses connection + TLS fingerprint):**
```python
from scrapling.fetchers import FetcherSession

all_quotes = []
with FetcherSession(impersonate="chrome") as session:
    for i in range(1, 11):
        page = session.get(f"https://quotes.toscrape.com/page/{i}/", stealthy_headers=True)
        quotes = page.css(".quote .text::text").getall()
        all_quotes.extend(quotes)
        print(f"Page {i}: {len(quotes)} quotes (status {page.status})")
```

**Dynamic Session (browser stays open across requests):**
```python
from scrapling import PlayWrightFetcher

with PlayWrightFetcher.Session(headless=True, disable_resources=True) as session:
    for i in range(1, 11):
        page = session.fetch(f"https://quotes.toscrape.com/page/{i}/")
        # page is a full Response object — use .css(), .xpath(), etc.
```

**Stealthy Session (persistent stealth browser):**
```python
from scrapling import StealthyFetcher

with StealthyFetcher.Session(headless=True) as session:
    for i in range(1, 11):
        page = session.fetch(f"https://protected-site.com/page/{i}/")
        # bypasses anti-bot on every request
```

---

## Spider Framework (concurrent crawling)

For full-site crawls with automatic pagination, concurrent requests, and export.

```python
from scrapling.spiders import Spider, Response

class QuotesSpider(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    concurrent_requests = 5  # fetch up to 5 pages at once

    async def parse(self, response: Response):
        # Extract all quotes on the current page
        for quote in response.css(".quote"):
            yield {
                "text": quote.css(".text::text").get(),
                "author": quote.css(".author::text").get(),
                "tags": quote.css(".tags .tag::text").getall(),
            }

        # Follow pagination links automatically
        next_page = response.css(".next a")
        if next_page:
            yield response.follow(next_page[0].attrib["href"])

if __name__ == "__main__":
    result = QuotesSpider().start()
    print(f"Scraped: {result.stats.items_scraped} items")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time: {result.stats.elapsed_seconds:.2f}s")
    print(f"Speed: {result.stats.requests_per_second:.2f} req/s")

    # Export to JSON
    result.items.to_json("quotes.json", indent=True)
```

**Spider features:**
- `concurrent_requests` — parallel page fetching
- `response.follow(url)` — queue another URL for crawling
- `result.stats` — live crawl statistics (items_scraped, requests_count, elapsed_seconds, requests_per_second)
- `result.items` — all yielded items, with `.to_json()` export
- Pause/resume checkpoints for long-running crawls
- Automatic proxy rotation support

---

## Common Patterns

### Scrape a list of items
```python
from scrapling.fetchers import Fetcher

page = Fetcher.get('https://books.toscrape.com/')
books = []
for book in page.css('article.product_pod'):
    books.append({
        'title': book.css('h3 a::attr(title)').get(),
        'price': book.css('.price_color::text').get(),
        'rating': book.css('p.star-rating').attrib.get('class', '').replace('star-rating ', ''),
    })
```

### Handle pagination
```python
from scrapling.fetchers import Fetcher

url = 'https://example.com/page/1'
all_items = []
while url:
    page = Fetcher.get(url)
    for item in page.css('.item'):
        all_items.append(item.css('h2::text').get())
    next_link = page.css('a.next::attr(href)').get()
    url = page.urljoin(next_link) if next_link else None
```

### Bypass Cloudflare-protected page
```python
from scrapling.fetchers import StealthyFetcher

page = StealthyFetcher.fetch(
    'https://protected-site.com',
    headless=True,
    network_idle=True,
    solve_cloudflare=True,
    wait=2000,
)
data = page.css('.content::text').get()
```

### Automate interactions (click, scroll, etc.)
```python
from scrapling import PlayWrightFetcher

async def scroll_and_accept(page):
    await page.click('#accept-cookies')
    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    await page.wait_for_timeout(2000)

result = PlayWrightFetcher.fetch(
    'https://example.com',
    headless=True,
    page_action=scroll_and_accept,
    wait=1000,
)
```

### Scrape with authenticated session (cookies)
```python
from scrapling import StealthyFetcher

# Parse Netscape cookie file format (common browser export format)
cookie_path = '~/Downloads/www.example.com_cookies.txt'
cookie_pairs = []
with open(cookie_path) as f:
    for line in f:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.strip().split('\t')
        if len(parts) >= 7:
            name = parts[5].strip()
            value = parts[6].strip().strip('"')
            cookie_pairs.append(f"{name}={value}")

cookie_header = "; ".join(cookie_pairs)

page = StealthyFetcher.fetch(
    'https://www.example.com/feed/',
    headless=True,
    network_idle=True,
    extra_headers={'Cookie': cookie_header},
    timeout=30000,
)
```

## Tips

- **Always start with this skill** for web scraping — don't use `curl` or `terminal` directly.
- Start with `Fetcher.get()` — it's fastest and sufficient for most static sites.
- **Escalate**: `Fetcher.get()` → `PlayWrightFetcher.fetch()` → `StealthyFetcher.fetch()`. Start simple, go stealthy only when needed.
- Use `network_idle=True` on dynamic fetchers to wait for JS to finish loading.
- `StealthyFetcher` handles Cloudflare automatically; enable `block_webrtc=True` for extra stealth.
- **Use sessions** (`FetcherSession`, `PlayWrightFetcher.Session`, `StealthyFetcher.Session`) when making multiple requests — they reuse connections and browsers.
- **Use the CLI** with `--ai-targeted` for quick one-off extractions — it blocks ads and sanitizes hidden elements.
- **Use spiders** for full-site crawls with automatic pagination and concurrent requests.
- Chain `.css()` and `.xpath()` on elements for scoped queries.
- Use `::text` and `::attr(name)` pseudo-selectors to extract values cleanly.
- `.get()` returns first match as string; `.getall()` returns all as list.
- `page.urljoin(relative_url)` resolves relative URLs to absolute.
- For JSON APIs, just use `Fetcher.get(url).json()`.
