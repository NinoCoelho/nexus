# Context Management Analysis Report

**Generated:** 2026-05-20
**Token Analyzed:** `xUImkLTj26qDL_XrBPNkcP78HY4pKsrpbW-9KZR7up8` (Master Key)

---

## Overall Rating: 7/10 - Needs Improvement

---

## Summary (Last 24 Hours)

| Metric | Value |
|--------|-------|
| **Total Requests** | 97 |
| **Success Rate** | 100% |
| **Total Tokens** | 3.88M |
| **Average Tokens/Request** | 39,986 |
| **Models Used** | GLM-5.1 (openai), GLM-5.1 |

---

## Context Usage Analysis

### Token Distribution

| Category | Count | % |
|----------|-------|---|
| **Very High** (>100K tokens) | 4 | 4% |
| **High** (50K-100K tokens) | 16 | 16% |
| **Medium** (25K-50K tokens) | 36 | 37% |
| **Normal** (<25K tokens) | 41 | 42% |

### Very High Token Requests (>100K tokens)

| Request ID | Time | Prompt Tokens | Total Tokens | Latency |
|------------|------|---------------|--------------|---------|
| 20260520151045750439dd383a4434 | 07:10:43 | 172,461 | 175,115 | 124s |
| 20260520180816f21431ab930b4da1 | 10:08:15 | 154,825 | 156,230 | 55s |
| 20260520151009079ef34d1d1049c4 | 07:10:08 | 129,399 | 129,791 | 18s |
| 20260520150937bd15ed1cff764921 | 07:09:34 | 124,707 | 124,823 | 18s |

---

## Cache Performance

| Metric | Value |
|--------|-------|
| **Requests with Cache** | 91/97 (94%) |
| **Cached Tokens** | 2.43M / 3.8M |
| **Cache Hit Rate** | 64% |

**Assessment:** Good cache utilization (64%) but lower than the overall 75% rate.

---

## Latency Analysis

| Metric | Value |
|--------|-------|
| Min | 5.3s |
| Max | **184s** (3 min!) |
| Average | **30.6s** |
| P50 (median) | 13s |
| P95 | **123s** |

⚠️ **High latency concern:** Average 30 seconds is very high. P95 at 2+ minutes indicates requests timing out or very slow.

---

## Hourly Pattern

| Hour | Requests | Avg Tokens | Issue |
|------|----------|------------|-------|
| 07:00 | 36 | 47K | High volume, very high tokens |
| 10:00 | 25 | 41K | High tokens |
| 11:00 | 26 | 31K | Moderate |

---

## Issues Identified

| Severity | Issue |
|----------|-------|
| 🔴 **Critical** | 4 requests exceeded 100K prompt tokens - approaching model limits |
| 🔴 **Critical** | Max latency 184s - requests timing out or very slow |
| 🟡 **Medium** | 20% of requests in "HIGH" token category (>50K) |
| 🟡 **Medium** | 64% cache rate - could be optimized |
| 🟢 **Good** | 100% success rate |

---

## Prompt Optimization Analysis

### Current Problem: Large System Prompt + Full HTML Scrapes

The agent "Nexus" makes research requests resulting in extremely large prompts (170K+ tokens) because:

1. **Massive System Prompt** - ~2,000+ tokens of tool descriptions and instructions
2. **Full HTML Content** - Web scrapes return complete HTML pages with CSS, JS, navigation - not cleaned content
3. **Multiple Parallel Scrapes** - 5-8 web scrapes per request, each with full HTML
4. **No Content Filtering** - Including HTML boilerplate, ads, navigation elements

### Token Breakdown (Example: 172K prompt request)

| Component | Est. Tokens |
|-----------|-------------|
| System prompt | ~2,000 |
| User query | ~500 |
| Web search results (5x) | ~5,000 |
| Web scrape results (8x full HTML) | ~160,000+ |
| Tool call messages | ~5,000 |

---

## Optimization Recommendations

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🔴 **Critical** | Full HTML in context | Modify `web_scrape` tool to extract **text content only** - strip all HTML/CSS/JS. Use readability or similar library |
| 🔴 **Critical** | No content limit | Add `max_chars` parameter to web_scrape - limit to ~5,000 chars per page |
| 🟡 **High** | Large system prompt | Shorten tool descriptions, use links to docs instead of inline docs |
| 🟡 **High** | Redundant searches | Use `parallel-research` skill properly - results still pollute context |
| 🟢 **Medium** | Search vs scrape | Use web_search snippets instead of full scraping when possible |

### Specific Code Changes Needed

**1. Fix web_scrape tool** - Extract only meaningful content:

```python
# Instead of returning full HTML, return cleaned text
from bs4 import BeautifulSoup

def clean_html(html_content, max_chars=5000):
    soup = BeautifulSoup(html_content, 'html.parser')
    # Remove scripts, styles, nav, footer
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    text = soup.get_text(separator='\n', strip=True)
    return text[:max_chars]  # Truncate to limit
```

**2. Add content limits:**

```yaml
# In your agent config
tools:
  web_scrape:
    max_content_length: 5000  # chars per page
    extract_text_only: true
```

**3. Use search results instead of scraping:**

Instead of scraping 8 full articles, use the search snippets which already contain summaries. Only scrape 1-2 key articles.

### Expected Impact

| Optimization | Token Reduction |
|--------------|-----------------|
| Strip HTML/CSS/JS | ~70-80% |
| Limit to 5K chars/page | ~90% |
| Reduce system prompt | ~30% |
| Prefer search over scrape | ~50% |

**Potential new prompt size: 20K-40K tokens** (vs 170K current)

---

## This Token vs Overall

| Metric | This Token | All Users |
|--------|------------|-----------|
| Avg Tokens/Request | 39,986 | 27K |
| Very High Token % | 4% | ~1% |
| Cache Rate | 64% | 75% |
| Success Rate | 100% | 99.5% |

**Conclusion:** This master key user is using **higher context** than average, with **lower cache efficiency** and **higher latency**. Should monitor closely for context management issues.

---

## Action Items

| Priority | Action |
|----------|--------|
| 🔴 High | Implement HTML stripping in web_scrape tool |
| 🔴 High | Add max_content_length parameter to web tools |
| 🟡 Medium | Set `max_input_tokens: 128000` limit for GLM-5.1 model |
| 🟡 Medium | Alert on requests exceeding 100K prompt tokens |
| 🟢 Low | Investigate 3 failed requests from unauthenticated IPs |