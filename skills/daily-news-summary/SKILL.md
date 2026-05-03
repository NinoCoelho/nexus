---
name: daily-news-summary
description: Gather and summarize the latest news from major RSS feeds into a structured daily briefing saved to the vault.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
requires_keys:
  - NEWS_API_KEY
  - TAVILY_API_KEY
---

# Daily News Summary

**Use this whenever you need to gather and summarize the latest news from major RSS feeds.** Prefer this over manually checking individual news sites -- it aggregates multiple sources efficiently and produces a structured daily briefing.

## When to use
- Starting the day and want a quick news briefing
- Tracking major tech, business, and market developments
- Building a daily news archive in the vault
- Before meetings or decisions that require current market awareness

## Steps

### 1. Fetch news from API sources (primary)

Use NewsAPI and Tavily as primary structured sources, then supplement with RSS.

**NewsAPI -- top headlines:**
```
GET https://newsapi.org/v2/top-headlines?country=us&pageSize=20&apiKey=$NEWS_API_KEY
GET https://newsapi.org/v2/top-headlines?country=us&category=technology&pageSize=15&apiKey=$NEWS_API_KEY
GET https://newsapi.org/v2/top-headlines?country=us&category=business&pageSize=15&ApiKey=$NEWS_API_KEY
```

**NewsAPI -- everything (topic search):**
```
GET https://newsapi.org/v2/everything?q=AI+artificial+intelligence&sortBy=publishedAt&pageSize=10&apiKey=$NEWS_API_KEY
GET https://newsapi.org/v2/everything?q=stock+market+earnings&sortBy=publishedAt&pageSize=10&apiKey=$NEWS_API_KEY
```

**Tavily -- AI-optimized search (fallback / enrichment):**
```
POST https://api.tavily.com/search
Body: {"api_key": "$TAVILY_API_KEY", "query": "today's top tech and business news", "max_results": 10, "topic": "news"}
```

### 1b. Fetch RSS feeds (supplement)

```bash
# Business & Finance
curl -s "https://www.cnbc.com/id/10000664/device/rss/rss.html" > /tmp/cnbc.xml
curl -s "https://feeds.a.dj.com/rss/RSSMarketsMain.xml" > /tmp/wsj.xml
curl -s "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml" > /tmp/nyt.xml
curl -s "https://www.ft.com/world?format=rss" > /tmp/ft.xml
curl -s "https://feeds.bbci.co.uk/news/business/rss.xml" > /tmp/bbc-biz.xml

# Technology
curl -s "https://techcrunch.com/feed/" > /tmp/tc.xml
curl -s "https://www.theverge.com/rss/index.xml" > /tmp/verge.xml
curl -s "https://arstechnica.com/feed/" > /tmp/ars.xml
curl -s "https://feeds.bbci.co.uk/news/technology/rss.xml" > /tmp/bbc-tech.xml

# General
curl -s "https://www.theguardian.com/world/rss" > /tmp/guardian.xml
```

### 2. Parse RSS and extract top headlines (first 10-15 items per feed)

Use `grep` to extract titles and descriptions:

```bash
grep -E '<title>|<description>|<pubDate>' /tmp/cnbc.xml | head -40
grep -E '<title>|<description>|<pubDate>' /tmp/wsj.xml | head -40
grep -E '<title>|<description>|<pubDate>' /tmp/nyt.xml | head -40
```

### 3. Analyze and categorize the headlines

Group into these categories:
- **Major Tech Stories** (AI, big tech, startups, funding, product launches)
- **Markets & Finance** (stock moves, earnings, economic indicators, commodities)
- **Policy & Regulation** (government actions, trade policy, antitrust, international)
- **Cybersecurity** (hacks, data breaches, security incidents)
- **Business Mergers & Leadership** (acquisitions, CEO changes, executive moves)
- **Industry-Specific News** (space, automotive, energy, etc.)

### 4. Generate structured daily summary

Create a markdown document with:
- **Date header** (format: "April 21, 2026")
- **Source list** at the top
- **Categorized sections** with bullet points
- **Brief descriptions** for each story (1-2 sentences max)
- **Emphasis on major stories** with bold text

Save with filename: `notes/news-summary-YYYY-MM-DD.md`

### 5. Save to vault

Use `vault_write` to save the summary:
```python
vault_write(
  path="notes/news-summary-2026-04-21.md",
  content="<full markdown summary>"
)
```

### 6. Generate editorial analysis article

Using the **editorial-ghostwriter** skill, write an analysis piece based on the most important news from today's summary:

1. **Identify the main story** -- Find the 1-2 most significant developments
2. **Use only the most important content** -- Filter out minor stories
3. **Apply the editorial framework** -- Use the 7-part structure
4. **Generate in English** -- The editorial-ghostwriter always outputs English regardless of input language
5. **Aim for 600-900 words** -- Sufficient depth for substantive analysis

### 7. Save editorial article to vault

Use `vault_write` to save the editorial:
```python
vault_write(
  path="articles/editorial-YYYY-MM-DD.md",
  content="<full editorial markdown>",
  frontmatter={"tags": ["editorial", "analysis"], "date": "YYYY-MM-DD"}
)
```

### 8. Update memory with RSS feed status

Check for any feeds that failed and update the memory key `news/sources` accordingly.

## Gotchas

### RSS Feed Failures
- Some feeds may timeout or return errors -- skip them and continue with others
- Bloomberg and Reuters have anti-bot protection on their main sites, but their RSS feeds may still work
- If multiple feeds fail, check if there's a network connectivity issue

### Duplicate Stories
- Many outlets cover the same major stories
- Deduplicate by story topic, not by exact headline
- Attribute to the most authoritative source for each story

### Date Confusion
- Some feeds use UTC, others use local time
- The actual publication date is usually in the `<pubDate>` tag
- Group stories by "today's" date based on your local time

### Content Limits
- WSJ and NYT items may show as "PAID" in RSS -- still include the headline but note it's behind paywall
- Some descriptions are truncated -- that's fine, headline is most important

### Editorial Generation
- **News selection matters** -- Don't try to cover everything. Pick the 1-2 most significant stories and go deep.
- **Avoid news aggregation** -- The editorial should not be a summary. It should be an analysis.
- **If no major story** -- On slow news days, it's okay to skip editorial generation.

## Source List Reference

### Business & Finance
- CNBC: `https://www.cnbc.com/id/10000664/device/rss/rss.html`
- Wall Street Journal (Markets): `https://feeds.a.dj.com/rss/RSSMarketsMain.xml`
- New York Times (Business): `https://rss.nytimes.com/services/xml/rss/nyt/Business.xml`
- Financial Times (World): `https://www.ft.com/world?format=rss`
- BBC Business: `https://feeds.bbci.co.uk/news/business/rss.xml`

### Technology
- TechCrunch: `https://techcrunch.com/feed/`
- The Verge: `https://www.theverge.com/rss/index.xml`
- Ars Technica: `https://arstechnica.com/feed/`
- BBC Technology: `https://feeds.bbci.co.uk/news/technology/rss.xml`

### General
- The Guardian (World): `https://www.theguardian.com/world/rss`
