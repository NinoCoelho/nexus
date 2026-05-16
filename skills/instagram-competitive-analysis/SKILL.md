---
name: instagram-competitive-analysis
description: Analyze an Instagram profile and its competitors using the EnsembleData API. Produces a structured competitive report with metrics comparison, positioning analysis, and actionable recommendations. Saves the report to the vault.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
requires_keys:
  - ENSEMBLEDATA_API_KEY
---

# Instagram Competitive Analysis

Analyze an Instagram profile and its competitors using the EnsembleData API, producing a structured competitive report saved to the vault.

## Inputs Needed

Ask the user for:
1. **Target username** (the profile to analyze)
2. **Niche description** (e.g., "fitness coaches, personal development") -- used to find relevant competitors
3. **Specific competitors** (optional -- user may provide usernames; if not, search for them)

## API Details

- **Base URL:** `https://api.ensembledata.com`
- **Auth parameter:** `token` (NOT `api_key`)
- **API key:** `ENSEMBLEDATA_API_KEY` (set in environment or secrets)

### Endpoints Used

#### 1. Get profile info
```
GET /instagram/user/info?username={username}&token={KEY}
```
Returns: pk, username, full_name, is_private, is_verified, has_highlight_reels, profile_pic_url

#### 2. Get follower count
```
GET /instagram/user/followers?user_id={pk}&token={KEY}
```
Returns: `{ "data": { "count": N } }`

#### 3. Get user posts
```
GET /instagram/user/posts?user_id={pk}&depth=1&token={KEY}
```
Returns: `{ "data": { "count": N, "posts": [...] } }`

Note: `depth` is required and must be >= 1. Response is VERY large (includes video manifests). Extract only what you need.

## Execution Steps

### Phase 1: Baseline (target profile)
1. Call `/instagram/user/info` for the target username
2. Extract `pk` from response
3. Call `/instagram/user/followers` with the pk -> get follower count
4. Call `/instagram/user/posts` with the pk -> get post count + recent post metrics

### Phase 2: Find competitors
1. If user provided competitor usernames -> verify each with `/instagram/user/info`
2. If not -> brainstorm 5-8 candidate usernames based on niche keywords
3. For each valid competitor found:
   - Get pk from `/instagram/user/info`
   - Get follower count from `/instagram/user/followers`
   - Get posts + engagement from `/instagram/user/posts`

### Phase 3: Build comparison table
For each profile (target + competitors), extract:
| Metric | Source |
|---|---|
| Username | user/info |
| Full name | user/info |
| Follower count | user/followers |
| Post count | user/posts -> count |
| Verified | user/info -> is_verified |
| Highlights | user/info -> has_highlight_reels |
| Content type mix | user/posts -> __typename tally |
| Avg likes (recent) | user/posts -> edge_media_preview_like.count |
| Avg comments (recent) | user/posts -> edge_media_to_comment.count |
| Engagement rate | avg_likes / followers x 100 |
| Top hashtags | user/posts -> captions |

### Phase 4: Analysis and Recommendations
Generate:
1. **Positioning map** -- where the target stands relative to competitors
2. **Strengths** -- what the target does better
3. **Weaknesses** -- gaps vs competitors
4. **Opportunities** -- underexplored formats/topics in the niche
5. **Actionable recommendations** (5-7 specific actions, prioritized)

### Phase 5: Save report
Write the full report to the vault at an appropriate path.

## Report Template

```markdown
# Competitive Report -- @{target}

## 1. Profile Analyzed (Baseline)
[Table with all metrics]

### Recent Content
[Bullet list of last 5 posts: type, likes, comments, caption summary, date]

---

## 2. Competitors

### @{competitor_1}
[Table + engagement analysis]

### @{competitor_2}
[Table + engagement analysis]

---

## 3. Overall Comparison
[Summary table with all profiles side by side]

---

## 4. Positioning Map
[Text analysis of where target stands]

---

## 5. Diagnosis

### Strengths
1. ...

### Critical Issues
1. ...

---

## 6. Priority Recommendations
1. **[Action title]** -- [specific description]
2. ...
```

## Tips and Gotchas

- **API responses are huge** -- video posts include full DASH manifests (XML). Don't log full responses. Extract only the fields listed above.
- **`token` not `api_key`** -- the auth query parameter is `token`, not `api_key`. This is the #1 mistake.
- **`user_id` is `pk`** -- from `/user/info` response, use `data.pk` as `user_id` for other endpoints.
- **`depth` >= 1 required** -- the `/user/posts` endpoint requires depth parameter.
- **Rate limiting** -- if you get 429s, add a small delay between calls.
- **Null profiles** -- `/user/info` returns `"data": null` for non-existent usernames. Handle gracefully.
- **Batch efficiently** -- call multiple `/user/info` in parallel (independent calls), then batch follower/post calls by profile.
