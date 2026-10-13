import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "update_instagram_feed.py"
spec = importlib.util.spec_from_file_location("update_instagram_feed", SCRIPT)
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)


def posts_fixture():
    return [
        {
            "id": f"shortcode{index}",
            "permalink": f"https://www.instagram.com/p/shortcode{index}/",
            "image_url": f"https://scontent.cdninstagram.com/image{index}.jpg",
            "alt_text": f'Alt <{index}> & "text"',
            "caption": f'Caption <{index}> & "quoted"',
        }
        for index in range(6)
    ]


class InstagramFeedTests(unittest.TestCase):
    def test_six_posts_render_in_input_order_as_linked_three_by_two_grid(self):
        posts = posts_fixture()
        rendered = feed.render_posts(posts)
        self.assertIn("### Latest from Instagram", rendered)
        self.assertEqual(rendered.count("<tr>"), 2)
        self.assertEqual(rendered.count("<td"), 6)
        self.assertEqual(rendered.count('<img src="assets/instagram/'), 6)
        self.assertIn('width="200"', rendered)
        self.assertIn('alt="Alt &lt;0&gt; &amp; &quot;text&quot;"', rendered)
        self.assertIn('<a href="https://www.instagram.com/p/shortcode1/">', rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("Caption", rendered)
        self.assertLess(rendered.index("shortcode0"), rendered.index("shortcode5"))

    def test_accepts_exact_contract_and_preserves_browser_selected_order(self):
        posts = posts_fixture()
        posts[0]["caption"] = "newest"
        posts[5]["caption"] = "oldest"
        self.assertEqual(feed.validate_posts(posts), posts)

    def test_rejects_any_post_count_other_than_six(self):
        # A partially blocked page must never shrink or blank the published grid.
        for count in (0, 5, 7):
            with self.subTest(count=count):
                posts = (posts_fixture() * 2)[:count]
                for index, post in enumerate(posts):
                    post["id"] = f"code{index}"
                    post["permalink"] = f"https://www.instagram.com/p/code{index}/"
                with self.assertRaises(feed.FeedError):
                    feed.validate_posts(posts)

    def test_rejects_duplicate_ids_or_permalink_and_wrong_permalink_path(self):
        posts = posts_fixture()
        posts[1]["id"] = posts[0]["id"]
        with self.assertRaises(feed.FeedError):
            feed.validate_posts(posts)
        posts = posts_fixture()
        posts[1]["permalink"] = posts[0]["permalink"]
        with self.assertRaises(feed.FeedError):
            feed.validate_posts(posts)
        posts = posts_fixture()
        posts[0]["permalink"] = "https://www.instagram.com/accounts/login/"
        with self.assertRaises(feed.FeedError):
            feed.validate_posts(posts)

    def test_rejects_non_cdn_or_non_https_image_urls(self):
        for url in (
            "http://scontent.cdninstagram.com/image.jpg",
            "https://cdninstagram.com.evil.test/image.jpg",
            "https://user@scontent.cdninstagram.com/image.jpg",
            "https://127.0.0.1/image.jpg",
        ):
            with self.subTest(url=url):
                posts = posts_fixture()
                posts[0]["image_url"] = url
                with self.assertRaises(feed.FeedError):
                    feed.validate_posts(posts)

    def test_rejects_control_characters_and_non_exact_record_shape(self):
        posts = posts_fixture()
        posts[0]["alt_text"] = "bad\x00alt"
        with self.assertRaises(feed.FeedError):
            feed.validate_posts(posts)
        posts = posts_fixture()
        posts[0]["extra"] = "unexpected"
        with self.assertRaises(feed.FeedError):
            feed.validate_posts(posts)

    def test_response_size_is_bounded(self):
        class Response:
            headers = {}

            def read(self, limit):
                return b"x" * limit

        with self.assertRaises(feed.FeedError):
            feed._read_bounded(Response(), 10)
        class OversizedHeader(Response):
            headers = {"Content-Length": "11"}

        with self.assertRaises(feed.FeedError):
            feed._read_bounded(OversizedHeader(), 10)

    def test_partial_download_failure_preserves_readme_and_existing_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "README.md"
            assets = root / "assets" / "instagram"
            assets.mkdir(parents=True)
            readme.write_text("before\n", encoding="utf-8")
            original = assets / "keep.jpg"
            original.write_bytes(b"existing")
            with patch.object(feed, "fetch_image", side_effect=[b"\xff\xd8\xffvalid", feed.FeedError("generic")]):
                with self.assertRaises(feed.FeedError):
                    feed.update_profile(root, posts_fixture())
            self.assertEqual(readme.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(list(assets.iterdir()), [original])
            self.assertEqual(original.read_bytes(), b"existing")

    def test_malformed_marker_pair_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("before\n<!-- INSTAGRAM:START -->\n", encoding="utf-8")
            with self.assertRaises(feed.FeedError):
                feed.update_profile(root, posts_fixture())
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "before\n<!-- INSTAGRAM:START -->\n")
            self.assertFalse((root / "assets").exists())

    def test_cli_requires_posts_file_and_default_root_is_script_parent(self):
        self.assertEqual(feed.default_root(), SCRIPT.resolve().parents[1])
        self.assertIsNotNone(feed.parse_args(["--posts", "feed.json"]))
        with self.assertRaises(SystemExit):
            feed.parse_args([])

    def test_update_writes_six_jpegs_and_linked_thumbnail_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("profile\n", encoding="utf-8")
            posts_path = root / "posts.json"
            posts_path.write_text(json.dumps(posts_fixture()), encoding="utf-8")
            with patch.object(feed, "fetch_image", return_value=b"\xff\xd8\xffimage"):
                self.assertTrue(feed.update_profile(root, feed.load_posts(posts_path)))
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertTrue(readme.startswith("profile\n"))
            self.assertEqual(readme.count("<td"), 6)
            self.assertEqual(len(list((root / "assets" / "instagram").iterdir())), 6)
            self.assertIn("https://www.instagram.com/p/shortcode0/", readme)
            self.assertNotIn("scontent.cdninstagram.com", readme)

    def test_images_must_be_jpeg(self):
        for image in (b"RIFFxxxxWEBP", b"\x89PNG\r\n\x1a\nrest"):
            with patch.object(feed, "_request", return_value=image):
                with self.assertRaises(feed.FeedError):
                    feed.fetch_image("https://scontent.cdninstagram.com/image")


if __name__ == "__main__":
    unittest.main()
