---
name: github-trends-analyzer
description: Use this whenever you need to analyze GitHub trending repositories, identify new entries, detect changes, and generate trend reports. Fetches trending data from GitHub API and compares with existing vault files.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- Tracking daily GitHub trending repositories
- Identifying new repositories that appeared in trending
- Detecting significant changes (stars, forks, growth rates)
- Generating trend analysis reports
- Comparing trending snapshots over time
- Monitoring AI/agent ecosystem trends on GitHub

## Steps

1. **Fetch current trending data**
   ```bash
   # Use GitHub API to get trending repos
   # Option 1: API search with date filter
   https://api.github.com/search/repositories?q=created:>2025-01-01&sort=stars&order=desc&per_page=20
   
   # Option 2: Scrape trending page
   https://github.com/trending
   ```

2. **List existing tracked repos**
   ```bash
   vault_list path=git-trending
   ```

3. **Identify new repositories**
   - Extract repo names from API results (format: `owner/repo`)
   - Compare with existing `.md` files in `git-trending/`
   - New = exists in API but NOT in existing files

4. **Identify changed repositories**
   - For repos that exist in both:
     - Extract current stars/forks from API
     - Read existing stats from `.md` file
     - Calculate growth rate
   - Flag significant changes (e.g., >10% growth, rank changes)

5. **Identify dropped repositories**
   - Check if existing files no longer appear in trending API
   - Mark as "dropped" with last known stats

6. **Generate analysis report**
   - Create structured markdown report with:
     - New repositories (name, stars, description)
     - Significant growth (rank changes, star increases)
     - Dropped repos (if any)
     - Category breakdown (AI agents, tools, frameworks, etc.)
     - Growth velocity analysis

7. **Save report to vault**
   ```bash
   vault_write path=git-trending/analysis-YYYY-MM-DD.md content="[report content]"
   ```

8. **Update README summary**
   - Update `git-trending/README.md` with latest trending table
   - Maintain status indicators (trending, tracked, dropped)

## Gotchas

- **Rate limiting:** GitHub API has 60 requests/hour limit for unauthenticated requests. Use caching and spread requests.
- **API vs trending page:** API search results (sorted by stars) do not equal trending page (algorithm-based). For true trending, scrape the page.
- **Data format:** API returns JSON; trending page requires parsing HTML. Use web-scrape skill for trending page if needed.
- **File naming:** Use `owner-repo.md` format consistently (lowercase, hyphen separator).
- **Growth rate calculation:** Calculate as: `(current_stars - previous_stars) / previous_stars * 100`
- **Timezone consistency:** Timestamps from API are UTC; vault files should note timezone used.
- **Duplicate detection:** Case-insensitive comparison for repo names (e.g., `OpenAI/Codex` == `openai/codex`).
