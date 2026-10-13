import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizePosts } from '../scripts/fetch_instagram_posts.mjs';

function post(shortcode, overrides = {}) {
  return {
    href: `https://www.instagram.com/ecoray.dk/p/${shortcode}/`,
    imageUrl: `https://scontent.cdninstagram.com/v/t51.29350-15/${shortcode}.jpg`,
    altText: `alt ${shortcode}`,
    caption: `caption ${shortcode}`,
    ...overrides,
  };
}

test('normalizes canonical links and selects six distinct highest media IDs', () => {
  const selected = normalizePosts([
    post('C'),
    post('H'),
    post('F'),
    post('H', { altText: 'duplicate' }),
    post('B'),
    post('G'),
    post('E'),
    post('D'),
  ]);

  assert.deepEqual(
    selected.map(({ id }) => id),
    ['H', 'G', 'F', 'E', 'D', 'C'],
  );
  assert.equal(selected[0].permalink, 'https://www.instagram.com/p/H/');
  assert.equal(selected[0].alt_text, 'alt H');
  assert.equal(selected[0].caption, 'caption H');
});

test('sorts decoded shortcode IDs as BigInt instead of Number', () => {
  const lower = 'zzzzzzzzzzzz';
  const higher = '____________';
  const selected = normalizePosts([
    post('A'),
    post('B'),
    post('C'),
    post('D'),
    post('E'),
    post(lower),
    post(higher),
  ]);

  assert.equal(selected[0].id, higher);
  assert.equal(selected[1].id, lower);
});
test('accepts only case-insensitive EcoRay profile links and rejects other accounts', () => {
  const selected = normalizePosts([
    post('B', { href: 'https://www.instagram.com/EcoRay.dk/p/B/' }),
    post('C'),
    post('D'),
    post('E'),
    post('F'),
    post('G'),
    post('H', { href: 'https://www.instagram.com/another.account/p/H/' }),
  ]);
  assert.deepEqual(selected.map(({ id }) => id), ['G', 'F', 'E', 'D', 'C', 'B']);
});

test('canonicalizes reel permalinks', () => {
  const selected = normalizePosts([
    post('B', { href: 'https://www.instagram.com/ecoray.dk/reel/B/' }),
    post('C'), post('D'), post('E'), post('F'), post('G'),
  ]);
  assert.equal(selected.find(({ id }) => id === 'B').permalink, 'https://www.instagram.com/reel/B/');
});

test('rejects unrelated image hosts and fewer than six valid posts', () => {
  assert.throws(
    () => normalizePosts([post('A'), post('B'), post('C'), post('D'), post('E'), post('F', { imageUrl: 'https://example.com/post.jpg' })]),
    /Fewer than six valid Instagram posts/,
  );
});
