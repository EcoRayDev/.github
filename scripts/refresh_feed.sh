#!/usr/bin/env bash
# Refreshes the homepage Instagram grid and pushes it when it changed.
# Runs on a host with a residential connection; Instagram rate-limits
# logged-out requests from GitHub-hosted runners.
set -euo pipefail

cd "$(dirname "$0")/.."
git pull --ff-only --quiet

docker run --rm --init --ipc=host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD":/work -w /work \
  mcr.microsoft.com/playwright:v1.55.0-noble \
  sh -c 'npm ci --no-audit --no-fund --silent \
    && node scripts/fetch_instagram_posts.mjs --output /tmp/instagram-posts.json \
    && python3 scripts/update_instagram_feed.py --posts /tmp/instagram-posts.json'

git add profile/README.md profile/assets/instagram
if git diff --cached --quiet; then
  echo "Instagram feed is unchanged."
  exit 0
fi
# This unattended bot must not inherit a workstation personal GPG signing key.
git -c commit.gpgsign=false \
  -c user.name="EcoRay Instagram feed" -c user.email="noreply@ecoray.dk" \
  commit --quiet -m "chore(profile): refresh Instagram feed"
git push --quiet origin HEAD:main
echo "Instagram feed updated and pushed."
