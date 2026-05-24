---
name: instagram-scraper
description: Use this whenever you need to scrape Instagram data -- profiles, posts, reels, comments, follower counts. Prefer over manual web scraping for any Instagram data need.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
requires_keys:
  - ENSEMBLEDATA_API_KEY
---

## When to use

- Fetching any Instagram profile info, follower/following counts, posts, reels, or comments.
- Building engagement reports, content audits, or competitive analyses.
- Any task that needs structured Instagram data (not just competitor analysis).
- Prefer this over `web-scrape` or `http_call` for Instagram -- it knows the API surface and gotchas.

## API Details

- **Base URL:** `https://api.ensembledata.com`
- **Auth param:** `token` (NOT `api_key`)
- **API key:** `ENSEMBLEDATA_API_KEY` (set in environment or secrets)

## Endpoints (all verified working)

### 1. Profile Info (lookup by username -> get pk)
```
GET /instagram/user/info?username={username}&token={KEY}
```
Returns: `data.pk`, `data.username`, `data.full_name`, `data.is_private`, `data.is_verified`, `data.profile_pic_url`, `data.has_highlight_reels`

If `"data": null` -> profile doesn't exist.

### 2. Follower Count
```
GET /instagram/user/followers?user_id={pk}&depth=1&token={KEY}
```
Returns: `{ "data": { "count": N } }`

### 3. Following Count
```
GET /instagram/user/following?user_id={pk}&depth=1&token={KEY}
```
Returns: `{ "data": { "count": N } }`

### 4. User Posts (feed posts)
```
GET /instagram/user/posts?user_id={pk}&depth=1&token={KEY}
```
Returns: `{ "data": { "count": N, "posts": [...] } }`

Each post is `node` with these useful fields:
- `__typename` -> `GraphImage` | `GraphVideo` | `GraphSidecar` (carousel)
- `edge_media_preview_like.count` -> likes
- `edge_media_to_comment.count` -> comment count
- `edge_media_to_caption.edges[0].node.text` -> caption + hashtags
- `video_view_count` -> views (video posts only)
- `taken_at_timestamp` -> Unix timestamp of post date
- `shortcode` -> slug for URL (`instagram.com/p/{shortcode}`)
- `owner.username`
- `dimensions` -> `{ height, width }`

### 5. User Reels
```
GET /instagram/user/reels?user_id={pk}&depth=1&token={KEY}
```
Returns: `{ "data": { "reels": [...] } }`

Each reel has `media` object with:
- `media.pk` -> reel ID
- `media.caption.text` -> caption
- `media.like_count` -> likes
- `media.comment_count` -> comments
- `media.play_count` -> views
- `media.user.username`
- `media.taken_at` -> Unix timestamp
- `media.image_versions2.candidates[0].url` -> thumbnail
- `media.video_duration` -> duration in seconds (sometimes)
- `media.code` -> shortcode for URL

### 6. Post Comments
```
GET /instagram/post/comments?media_id={media_id}&cursor=&sorting=popular&token={KEY}
```
Sorting options: `popular` | `recent`

## Steps

### Scrape a single profile
1. Call `/instagram/user/info?username={target}` -> extract `pk`
2. Call `/instagram/user/followers?user_id={pk}` -> get follower count
3. Call `/instagram/user/following?user_id={pk}` -> get following count
4. Call `/instagram/user/posts?user_id={pk}&depth=1` -> get recent posts + post count
5. Optional: call `/instagram/user/reels?user_id={pk}&depth=1` -> get reels separately

### Scrape post comments
1. Get the `media_id` (numeric ID from posts response: `node.id` or from reels: `media.pk`)
2. Call `/instagram/post/comments?media_id={id}&cursor=&sorting=popular`
3. Paginate with `cursor` from last comment if needed

### Scrape multiple profiles in batch
1. Call `/instagram/user/info` for each username (these are independent -> parallel)
2. Collect all pks
3. Call followers/following/posts for each pk (these are independent -> parallel)
4. Extract and compare metrics

## Common Patterns

### Engagement rate
```
engagement_rate = (avg_likes + avg_comments) / followers * 100
```
- < 1% = low, 1-3% = average, 3-6% = good, > 6% = excellent (for 10k+ followers)
- For small accounts (< 1k), 5-10%+ is normal

### Content type breakdown
Tally `__typename` across posts: `GraphImage`, `GraphVideo`, `GraphSidecar`

### Top performing posts
Sort posts by `edge_media_preview_like.count` descending

## Gotchas

- **`token` NOT `api_key`** -- the query param is `token`. #1 source of errors.
- **`user_id` is `pk`** -- from `/user/info` response, use `data.pk` as `user_id` for all other endpoints.
- **`depth` is required** for posts and reels -- must be >= 1.
- **Responses are HUGE** -- video posts include full DASH manifests (XML). Never log full response bodies. Extract only the fields listed above.
- **Null profiles** -- `/user/info` returns `"data": null` for non-existent or private accounts. Handle gracefully.
- **Rate limiting** -- if you get 429s, add 1-2 second delays between calls.
- **Post count vs returned posts** -- `count` field gives total lifetime posts; `posts` array only returns recent ones (controlled by `depth`).
- **Comment sorting** -- must be exactly `popular` or `recent` (not `top`).
- **Stories endpoint doesn't exist** -- `/user/stories` returns 404. Use reels instead.
- **No search endpoint** -- there's no user search. Use `/user/info` with exact usernames.
- **Reels structure differs from posts** -- reels use `media.like_count` (flat), posts use `edge_media_preview_like.count` (nested). Adjust extraction accordingly.
- **Batch efficiently** -- call multiple `/user/info` requests in parallel, then batch follower/post calls per profile.
