#!/usr/bin/env python3
"""Refresh the EcoRay profile's cached public Instagram thumbnails."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

POST_COUNT = 6
MAX_RESPONSE_BYTES = 1_000_000
MAX_IMAGE_BYTES = 5_000_000
TIMEOUT_SECONDS = 20
START_MARKER = "<!-- INSTAGRAM:START -->"
END_MARKER = "<!-- INSTAGRAM:END -->"
USER_AGENT = "EcoRayDev-Profile-Feed/1.0"
RECORD_FIELDS = {"id", "permalink", "image_url", "alt_text", "caption"}
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
PERMALINK_PATTERN = re.compile(r"/(?:p|reel)/[A-Za-z0-9_-]{1,128}/")


class FeedError(Exception):
    """A safe-to-display operational error without provider response details."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def _https_url(url: str) -> urllib.parse.SplitResult | None:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            return None
        return parsed
    except (ValueError, TypeError):
        return None


def valid_media_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = _https_url(url)
    if not parsed:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return (host.endswith(".cdninstagram.com") or host == "cdninstagram.com"
            or host.endswith(".fbcdn.net") or host == "fbcdn.net")


def valid_permalink(url: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = _https_url(url)
    return bool(parsed and parsed.hostname == "www.instagram.com" and not parsed.query
                and PERMALINK_PATTERN.fullmatch(parsed.path))


def validate_posts(posts: object) -> list[dict[str, str]]:
    if not isinstance(posts, list) or len(posts) != POST_COUNT:
        raise FeedError("Instagram feed must contain exactly six posts.")
    validated: list[dict[str, str]] = []
    ids: set[str] = set()
    permalinks: set[str] = set()
    for post in posts:
        if not isinstance(post, dict) or set(post) != RECORD_FIELDS:
            raise FeedError("Instagram feed data was invalid.")
        if any(not isinstance(post[key], str) for key in RECORD_FIELDS):
            raise FeedError("Instagram feed data was invalid.")
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for key in RECORD_FIELDS for char in post[key]):
            raise FeedError("Instagram feed contains invalid text.")
        post_id = post["id"]
        permalink = post["permalink"]
        if not ID_PATTERN.fullmatch(post_id) or post_id in ids:
            raise FeedError("Instagram feed contains an invalid or duplicate post ID.")
        if not valid_permalink(permalink):
            raise FeedError("Instagram feed contains an invalid post link.")
        permalink_match = PERMALINK_PATTERN.fullmatch(urllib.parse.urlsplit(permalink).path)
        if permalink in permalinks or permalink_match.group().strip("/").split("/", 1)[1] != post_id:
            raise FeedError("Instagram feed contains an invalid or duplicate post link.")
        if not valid_media_url(post["image_url"]):
            raise FeedError("Instagram feed contains an invalid image link.")
        for key in ("alt_text", "caption"):
            if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in post[key]):
                raise FeedError("Instagram feed contains invalid text.")
        ids.add(post_id)
        permalinks.add(permalink)
        validated.append(post)
    return validated


def _read_bounded(response, limit: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > limit:
                raise FeedError("Instagram feed response exceeded its size limit.")
        except ValueError:
            raise FeedError("Instagram feed response was invalid.") from None
    body = response.read(limit + 1)
    if len(body) > limit:
        raise FeedError("Instagram feed response exceeded its size limit.")
    return body


def _request(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg"})
    try:
        with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise FeedError("Instagram feed request failed.")
            return _read_bounded(response, limit)
    except FeedError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise FeedError("Instagram feed request failed.") from None


def fetch_image(url: str) -> bytes:
    if not valid_media_url(url):
        raise FeedError("Instagram feed contains an invalid image link.")
    image = _request(url, MAX_IMAGE_BYTES)
    if not image.startswith(b"\xff\xd8\xff"):
        raise FeedError("Instagram image was invalid.")
    return image


def _safe_text(value: str, limit: int = 500) -> str:
    return html.escape(value[:limit], quote=True)


def render_posts(posts: list[dict[str, str]]) -> str:
    posts = validate_posts(posts)
    lines = ["### Latest from Instagram", "", '<table><tbody>']
    for row_start in range(0, POST_COUNT, 3):
        lines.append("<tr>")
        for post in posts[row_start:row_start + 3]:
            image = f"assets/instagram/{post['id']}.jpg"
            permalink = html.escape(post["permalink"], quote=True)
            alt = _safe_text(post["alt_text"])
            lines.extend([
                '<td align="center">'
                f'<a href="{permalink}"><img src="{image}" alt="{alt}" width="200"></a>'
                "</td>"
            ])
        lines.append("</tr>")
    lines.extend(["</tbody></table>"])
    return "\n".join(lines) + "\n"


def _replace_block(readme: str, rendered: str) -> str:
    starts = readme.count(START_MARKER)
    ends = readme.count(END_MARKER)
    if starts != ends or starts > 1:
        raise FeedError("README Instagram feed markers are invalid.")
    if starts == 1:
        start_marker = readme.index(START_MARKER)
        end_marker = readme.index(END_MARKER)
        start = start_marker + len(START_MARKER)
        end = end_marker
        if end_marker < start_marker or end < start:
            raise FeedError("README Instagram feed markers are invalid.")
        return readme[:start] + "\n\n" + rendered + "\n" + readme[end:]
    return readme.rstrip() + "\n\n" + START_MARKER + "\n\n" + rendered + "\n" + END_MARKER + "\n"


def update_profile(root: Path, posts: list[dict[str, str]]) -> bool:
    root = Path(root)
    profile = root / "profile"
    if not profile.is_dir():
        profile = root
    readme_path = profile / "README.md"
    assets_path = profile / "assets" / "instagram"
    posts = validate_posts(posts)
    readme = readme_path.read_text(encoding="utf-8")
    rendered = _replace_block(readme, render_posts(posts))

    with tempfile.TemporaryDirectory(prefix="instagram-feed-", dir=root) as temp_name:
        temp = Path(temp_name)
        staged = temp / "instagram"
        staged.mkdir()
        for post in posts:
            (staged / f"{post['id']}.jpg").write_bytes(fetch_image(post["image_url"]))
        if rendered == readme and all(
            (assets_path / f"{post['id']}.jpg").is_file()
            and (assets_path / f"{post['id']}.jpg").read_bytes() == (staged / f"{post['id']}.jpg").read_bytes()
            for post in posts
        ):
            return False

        staged_readme = temp / "README.md"
        staged_readme.write_text(rendered, encoding="utf-8")
        previous_assets = temp / "previous-instagram"
        assets_path.parent.mkdir(parents=True, exist_ok=True)
        if assets_path.exists():
            assets_path.rename(previous_assets)
        try:
            staged.rename(assets_path)
            staged_readme.replace(readme_path)
        except OSError:
            if assets_path.exists():
                shutil.rmtree(assets_path, ignore_errors=True)
            if previous_assets.exists():
                previous_assets.rename(assets_path)
            raise
    return True


def load_posts(path: Path) -> list[dict[str, str]]:
    try:
        posts = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FeedError("Instagram posts file could not be read.") from None
    return validate_posts(posts)


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", required=True, type=Path, help="JSON file containing six browser-fetched posts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        changed = update_profile(default_root(), load_posts(args.posts))
        print("Instagram feed updated." if changed else "Instagram feed is already current.")
        return 0
    except FeedError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Instagram feed update failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
