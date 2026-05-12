---
name: linkedin-news-extractor
description: Extract LinkedIn News headlines from authenticated feed using cookies
---

# LinkedIn News Extractor

**Use this whenever you need to fetch LinkedIn News headlines from a user's authenticated feed.** Prefer this over manual browsing when automating LinkedIn news monitoring or archiving.

---

## When to use

- User wants LinkedIn News headlines/trending stories
- User has LinkedIn cookies available (Netscape format)
- Need to monitor LinkedIn News programmatically
- Archive LinkedIn News URLs for later reference

**Reach for this INSTEAD of:**
- Manual browsing to LinkedIn
- Unauthenticated scraping (won't work - LinkedIn requires login)
- Generic news scraping tools (LinkedIn News is behind authentication)

## Prerequisites

- LinkedIn account cookies in Netscape format (exported from browser)
- Cookie file typically at `~/Downloads/www.linkedin.com_cookies.txt`
- Cookies must be fresh (LinkedIn sessions expire)

## Steps

### 1. Verify cookie file exists
```bash
ls -la ~/Downloads/*cookies.txt
# Should see: www.linkedin.com_cookies.txt
```

### 2. Fetch LinkedIn feed with cookies
```bash
curl -sL -b ~/Downloads/www.linkedin.com_cookies.txt \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "https://www.linkedin.com/feed/" > /tmp/linkedin-feed.html
```

### 3. Extract LinkedIn News story URLs
```bash
grep -oE 'https://www.linkedin.com/news/story/[^"]*' /tmp/linkedin-feed.html | sort -u
```

### 4. Parse headlines from URLs
```bash
for url in $(grep -oE 'https://www.linkedin.com/news/story/[^"]*' /tmp/linkedin-feed.html | sort -u); do
    # Extract slug and clean up
    echo "$url" | sed 's|.*story/||' | sed 's/-[0-9]*/$//' | sed 's/-/ /g' | sed 's/\b\(.\)/\u\1/g'
done
```

### 5. Save to vault
```bash
# Create summary with URLs and headlines
# Save to vault/notes/linkedin-news-YYYY-MM-DD.md
```

## Example workflow

```bash
# Fetch feed
curl -sL -b ~/Downloads/www.linkedin.com_cookies.txt \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "https://www.linkedin.com/feed/" > /tmp/linkedin-feed.html

# Extract unique news URLs
NEWS_URLS=$(grep -oE 'https://www.linkedin.com/news/story/[^"]*' /tmp/linkedin-feed.html | sort -u)

# Print count
echo "Found $(echo "$NEWS_URLS" | wc -l) LinkedIn News stories"

# Print formatted headlines
for url in $NEWS_URLS; do
    headline=$(echo "$url" | sed 's|.*story/||' | sed 's/-[0-9]*/$//' | sed 's/-/ /g')
    echo "- $headline"
    echo "  $url"
done
```

## Gotchas

### Cookie authentication
- **LinkedIn sessions expire** - cookies may stop working after hours/days
- **Cookie format matters** - must be Netscape format (export from browser extension or DevTools)
- **Single sign-on issues** - corporate LinkedIn accounts may have different cookie behavior

### Content extraction limits
- **Full article text is JS-rendered** - only URLs and basic headlines are extractable
- **No public RSS/API** - LinkedIn doesn't provide an authenticated news feed API
- **Rate limiting** - don't scrape too frequently or account may be flagged

### URL parsing quirks
- **URLs have numeric IDs** - pattern: `/news/story/{slug}-{id}/`
- **Duplicate URLs** - feed may contain same story multiple times - use `sort -u`
- **Truncated URLs** - sometimes URLs appear with trailing backslashes or garbage characters

### Fallback when cookies fail
- If you get 302 redirects to login page, cookies are expired
- Ask user to re-export cookies from browser
- Alternative: manually check `https://www.linkedin.com/today` in browser

## Tips

- Store cookie path in memory for reuse: `memory_write key=linkedin-cookies path="~/Downloads/www.linkedin.com_cookies.txt"`
- Archive daily news summaries with dates: `vault/notes/linkedin-news-2025-04-20.md`
- Use `grep -oE 'https://www.linkedin.com/news/story/[^"]*"' | tr -d '"'` to clean up quoted URLs
- Test cookies with a simple fetch first before running full extraction

## Related skills

- `web-scrape` - Use for general web scraping when authentication isn't needed