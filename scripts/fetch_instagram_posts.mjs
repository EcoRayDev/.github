import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const PROFILE_URL = 'https://www.instagram.com/ecoray.dk/';
const PROFILE_HANDLE = 'ecoray.dk';
const OUTPUT_TIMEOUT_MS = 60_000;
const EXTRACTION_TIMEOUT_MS = 3_000;
const SHORTCODE_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

function shortcodeMediaId(shortcode) {
  let id = 0n;
  for (const character of shortcode) {
    const digit = SHORTCODE_ALPHABET.indexOf(character);
    if (digit < 0) throw new Error('Invalid Instagram shortcode');
    id = id * 64n + BigInt(digit);
  }
  return id;
}

function canonicalPostLink(href) {
  if (typeof href !== 'string') return null;
  let url;
  try {
    url = new URL(href, PROFILE_URL);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || url.hostname !== 'www.instagram.com' || url.username || url.password) return null;
  const match = url.pathname.match(/^\/ecoray\.dk\/(p|reel)\/([A-Za-z0-9_-]+)\/?$/i);
  if (!match) return null;
  return { type: match[1], shortcode: match[2] };
}

function validImageUrl(value) {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password &&
      (url.hostname === 'cdninstagram.com' || url.hostname.endsWith('.cdninstagram.com') ||
       url.hostname === 'fbcdn.net' || url.hostname.endsWith('.fbcdn.net'));
  } catch {
    return false;
  }
}

export function normalizePosts(rows) {
  const unique = new Map();
  for (const row of rows) {
    const link = canonicalPostLink(row?.href);
    if (!link || !validImageUrl(row.imageUrl) || unique.has(link.shortcode)) continue;
    const caption = typeof row.caption === 'string' ? row.caption.trim().slice(0, 500) : '';
    const altText = typeof row.altText === 'string' ? row.altText.trim().slice(0, 300) : '';
    unique.set(link.shortcode, {
      id: link.shortcode,
      permalink: `https://www.instagram.com/${link.type}/${link.shortcode}/`,
      image_url: row.imageUrl,
      alt_text: altText,
      caption,
      mediaId: shortcodeMediaId(link.shortcode),
    });
  }
  const selected = [...unique.values()]
    .sort((left, right) => left.mediaId === right.mediaId ? 0 : left.mediaId > right.mediaId ? -1 : 1)
    .slice(0, 6)
    .map(({ mediaId: _mediaId, ...post }) => post);
  if (selected.length !== 6) throw new Error('Fewer than six valid Instagram posts');
  return selected;
}

async function extractPosts(page) {
  return page.locator('a[href]').evaluateAll((anchors) => {
    const posts = [];
    const seen = new Set();
    for (const anchor of anchors) {
      const href = anchor.getAttribute('href');
      if (!href) continue;
      let postUrl;
      try {
        postUrl = new URL(href, location.href);
      } catch {
        continue;
      }
      if (postUrl.protocol !== 'https:' || postUrl.hostname !== 'www.instagram.com' ||
          !/^\/ecoray\.dk\/(?:p|reel)\/[A-Za-z0-9_-]+\/?$/i.test(postUrl.pathname)) continue;
      let row = anchor;
      for (let depth = 0; depth < 5 && row; depth += 1, row = row.parentElement) {
        if (row.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]').length > 1) break;
        const image = row.querySelector('img');
        if (!image) continue;
        const source = image.currentSrc || image.src;
        if (!source) continue;
        const key = `${postUrl.pathname}\n${source}`;
        if (seen.has(key)) break;
        seen.add(key);
        posts.push({ href: postUrl.href, imageUrl: source, altText: image.alt || '', caption: '' });
        break;
      }
    }
    return posts;
  }, undefined, { timeout: EXTRACTION_TIMEOUT_MS });
}

async function declineOptionalCookies(page) {
  const buttons = page.getByRole('button', { name: /decline optional cookies|only allow essential cookies/i });
  if (await buttons.count().catch(() => 0)) await buttons.first().click({ timeout: 2_000 }).catch(() => {});
}

async function fetchPosts() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      acceptDownloads: false,
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    page.setDefaultTimeout(OUTPUT_TIMEOUT_MS);
    const timer = setTimeout(() => void browser.close().catch(() => {}), OUTPUT_TIMEOUT_MS);
    timer.unref();
    try {
      const response = await page.goto(PROFILE_URL, { waitUntil: 'domcontentloaded', timeout: OUTPUT_TIMEOUT_MS });
      if (!response || response.status() >= 400) throw new Error(`Instagram profile could not be loaded (HTTP ${response ? response.status() : 'none'})`);
      const login = await page.locator('input[name="username"], form[action*="/accounts/login"]').count().catch(() => 0);
      if (login || /accounts\/login|challenge|checkpoint/.test(page.url())) throw new Error('Instagram requires login or account verification');
      await declineOptionalCookies(page);
      const deadline = Date.now() + OUTPUT_TIMEOUT_MS;
      let rows = [];
      while (Date.now() < deadline) {
        rows = await extractPosts(page).catch(() => []);
        try {
          return normalizePosts(rows);
        } catch {
          // Continue only while public profile content has not yet reached six usable posts.
        }
        await page.waitForTimeout(1_000);
      }
      throw new Error('Timed out waiting for six public Instagram posts');
    } finally {
      clearTimeout(timer);
      await context.close().catch(() => {});
    }
  } finally {
    await browser.close();
  }
}

function parseOutputPath(argv) {
  if (argv.length === 0) return null;
  if (argv.length !== 2 || argv[0] !== '--output' || !argv[1]) throw new Error('Usage: node scripts/fetch_instagram_posts.mjs [--output FILE]');
  return argv[1];
}

async function main() {
  const outputPath = parseOutputPath(process.argv.slice(2));
  const posts = await fetchPosts();
  const json = `${JSON.stringify(posts, null, 2)}\n`;
  if (outputPath) await writeFile(outputPath, json, { encoding: 'utf8', mode: 0o600 });
  else process.stdout.write(json);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`Instagram public feed fetch failed: ${error instanceof Error ? error.message.split('\n')[0].slice(0, 200) : 'unknown error'}\n`);
    process.exitCode = 1;
  });
}
