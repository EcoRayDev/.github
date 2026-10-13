# Instagram profile feed

The homepage shows EcoRay's six most recent public Instagram posts as cached thumbnails, each linking to the post. It is refreshed once a day without a Meta access token, an Instagram account, or login cookies.

## How it works

1. `scripts/fetch_instagram_posts.mjs` opens `https://www.instagram.com/ecoray.dk/` in a fresh, logged-out Playwright Chromium context and declines optional cookies. It reads the public post grid and accepts only links under `/ecoray.dk/p/` or `/ecoray.dk/reel/` whose thumbnails are on Instagram's CDN. Pinned posts are ordered by their encoded post ID, so the newest six are selected. It writes six records to a temporary JSON file, or exits non-zero with a generic message.
2. `scripts/update_instagram_feed.py --posts FILE` validates the records, downloads each thumbnail with a size limit, requires JPEG data, and only then replaces the block between `<!-- INSTAGRAM:START -->` and `<!-- INSTAGRAM:END -->` in `profile/README.md` and the images in `profile/assets/instagram/`. The README never references Instagram's expiring CDN URLs.

Any failure — Instagram blocking logged-out access, a login wall, fewer than six posts, or an invalid image — leaves the existing README and images untouched.

## Reliability

This is not an official API. Instagram rate-limits logged-out requests from datacenter IPs: GitHub-hosted runners get HTTP 429, so the refresh runs from Freyr, a server on a residential connection. Instagram can still change its page or block that IP; when it does, the run fails and the last good grid stays up. There is no retry, proxy, or login fallback. If the feed stops updating, check `journalctl --user -u ecoray-instagram-feed` on Freyr.

## Schedule

Freyr runs `scripts/refresh_feed.sh` daily from a systemd user timer (`ecoray-instagram-feed.timer`). The script pulls `main`, runs both scripts inside the pinned `mcr.microsoft.com/playwright` image, and commits and pushes the README and images only when they changed. A failed fetch or validation commits nothing.

`.github/workflows/instagram-feed.yml` is manual-dispatch only and kept for reference; it fails on GitHub-hosted runners with HTTP 429.

## Local testing

From the repository root:

```sh
npm ci
npx playwright install chromium
node --test tests/fetch_instagram_posts.test.mjs
python3 -m unittest discover -s tests -v
node scripts/fetch_instagram_posts.mjs --output /tmp/instagram-posts.json
python3 scripts/update_instagram_feed.py --posts /tmp/instagram-posts.json
```
