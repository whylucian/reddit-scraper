#!/usr/bin/env python3
"""
Reddit Scraper - Downloads posts, comments, and images from subreddits.
Uses Reddit's public JSON endpoints (no API key needed).

Usage:
  ./scrape.py <subreddit> [--min-age DAYS] [--delay SECONDS]
  ./scrape.py <subreddit> [--min-score N] [--min-comments N]
  ./scrape.py <subreddit> --pullpush [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  ./scrape.py <subreddit> --arcticshift [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Archive APIs (--pullpush, --arcticshift) enable time-windowed queries,
bypassing Reddit's 1000 post limit for historical scraping.
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
HEADERS = {"User-Agent": USER_AGENT}
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
IMAGE_URL_PATTERN = re.compile(r'https?://[^\s\)\]\"\']+?(?:' + '|'.join(re.escape(e) for e in IMAGE_EXTENSIONS) + ')', re.IGNORECASE)
# Pattern to extract ALL URLs from text
ALL_URL_PATTERN = re.compile(r'https?://[^\s\)\]\"\'<>]+')
# Hosts that are image hosting pages (not direct images)
IMAGE_PAGE_HOSTS = ('ibb.co', 'imgbb.com', 'imgur.com', 'i.imgur.com')


class RedditScraper:
    def __init__(self, subreddit: str, output_dir: str, min_age_days: int = 7, delay: float = 1.0,
                 archive_api: str = None, start_date: str = None, end_date: str = None,
                 min_score: int = None, min_comments: int = None):
        self.subreddit = subreddit
        self.output_dir = Path(output_dir)
        self.min_age_days = min_age_days
        self.delay = delay
        self.archive_api = archive_api  # None, "pullpush", or "arcticshift"
        self.start_date = start_date
        self.end_date = end_date
        self.min_score = min_score
        self.min_comments = min_comments
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.seen_file = self.output_dir / ".seen_posts"
        self.seen_posts = self._load_seen_posts()
        self.rate_limit_remaining = 60
        self.rate_limit_reset = 0

    def _load_seen_posts(self) -> set:
        """Load set of already-scraped post IDs."""
        if self.seen_file.exists():
            return set(self.seen_file.read_text().strip().split('\n'))
        return set()

    def _save_seen_post(self, post_id: str):
        """Append post ID to seen file."""
        self.seen_posts.add(post_id)
        with open(self.seen_file, 'a') as f:
            f.write(f"{post_id}\n")

    def _check_rate_limit(self, resp: requests.Response):
        """Update rate limit tracking from response headers."""
        remaining = resp.headers.get('X-Ratelimit-Remaining')
        reset = resp.headers.get('X-Ratelimit-Reset')

        if remaining is not None:
            self.rate_limit_remaining = float(remaining)
        if reset is not None:
            self.rate_limit_reset = float(reset)

        # If we're running low, wait for reset
        if self.rate_limit_remaining < 5:
            wait_time = self.rate_limit_reset + 1
            print(f"  [!] Rate limit low ({self.rate_limit_remaining:.0f} left), waiting {wait_time:.0f}s...")
            time.sleep(wait_time)

    def _fetch_json(self, url: str) -> dict | None:
        """Fetch JSON from Reddit, respecting rate limits."""
        try:
            resp = self.session.get(url, timeout=30)

            # Check rate limit headers
            self._check_rate_limit(resp)

            if resp.status_code == 429:
                reset = float(resp.headers.get('X-Ratelimit-Reset', 60))
                print(f"  [!] Rate limited (429), waiting {reset:.0f}s...")
                time.sleep(reset + 1)
                return self._fetch_json(url)

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"  [!] Failed to fetch {url}: {e}")
            return None

    def _download_image(self, url: str, save_path: Path) -> bool:
        """Download an image file."""
        try:
            url = html.unescape(url)
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
            return True
        except Exception as e:
            return False

    def _is_direct_image_url(self, url: str) -> bool:
        """Check if URL points directly to an image file."""
        parsed = urlparse(url.lower())
        return any(parsed.path.endswith(ext) for ext in IMAGE_EXTENSIONS)

    def _resolve_ibb_url(self, url: str) -> str | None:
        """Resolve ibb.co/imgbb.com page URL to direct image URL."""
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            # Look for the direct image URL in the page
            # ibb.co stores it in og:image or image-url meta tag
            match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', resp.text)
            if not match:
                match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', resp.text)
            if match:
                return html.unescape(match.group(1))
            # Also try to find direct image link in page
            match = re.search(r'(https://i\.ibb\.co/[^"\'<>\s]+)', resp.text)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def _resolve_reddit_share_url(self, url: str) -> str | None:
        """Resolve Reddit share URL (/s/) to actual image URL."""
        try:
            # Fetch the page - share URLs have the real URL embedded in HTML
            resp = self.session.get(url, timeout=15, allow_redirects=True)

            # Try to extract post URL from HTML (for /s/ share links)
            # Matches both /r/subreddit/comments/... and /u/user/comments/... or /user/username/comments/...
            post_url_match = re.search(r'https://www\.reddit\.com/(?:r|u|user)/[^/]+/comments/[a-z0-9]+/[^"<>\s]*', resp.text)
            if post_url_match:
                post_url = post_url_match.group(0).rstrip('/')
            elif '/comments/' in resp.url:
                post_url = resp.url.rstrip('/')
            else:
                return None

            # Fetch the post JSON
            json_url = post_url + '.json'
            json_resp = self.session.get(json_url, timeout=15)
            if json_resp.status_code != 200:
                return None

            data = json_resp.json()
            post_data = data[0]['data']['children'][0]['data']

            # Check for direct image URL
            img_url = post_data.get('url', '')
            if self._is_direct_image_url(img_url):
                return img_url

            # Check for gallery/media_metadata
            if post_data.get('media_metadata'):
                for mid, meta in post_data['media_metadata'].items():
                    if 's' in meta and 'u' in meta['s']:
                        return html.unescape(meta['s']['u'])

            # Check preview images
            preview = post_data.get('preview', {})
            images = preview.get('images', [])
            if images and 'source' in images[0]:
                return html.unescape(images[0]['source']['url'])
        except Exception:
            pass
        return None

    def _resolve_url_to_image(self, url: str) -> tuple[str | None, str | None]:
        """
        Try to resolve a URL to a direct image URL.
        Returns (image_url, error_message) - one will be None.
        """
        url = html.unescape(url)
        parsed = urlparse(url)

        # Skip wiki/rules/meta links (but not share links)
        if '/wiki/' in url or '/message/' in url:
            return None, None  # Not an error, just skip
        # Skip subreddit links that aren't posts or share links
        if 'reddit.com/r/' in url and '/comments/' not in url and '/s/' not in url:
            return None, None

        # Already a direct image URL
        if self._is_direct_image_url(url):
            return url, None

        # ibb.co / imgbb.com
        if parsed.netloc in ('ibb.co', 'www.ibb.co', 'imgbb.com', 'www.imgbb.com'):
            resolved = self._resolve_ibb_url(url)
            if resolved:
                return resolved, None
            return None, f"Failed to resolve ibb.co URL: {url}"

        # imgur (non-direct links)
        if 'imgur.com' in parsed.netloc and not self._is_direct_image_url(url):
            # Try adding .jpg extension for simple imgur links
            if re.match(r'^/[a-zA-Z0-9]+$', parsed.path):
                direct = f"https://i.imgur.com{parsed.path}.jpg"
                return direct, None
            return None, f"Could not resolve imgur URL: {url}"

        # Reddit share links (/s/ or /u/.../s/)
        if 'reddit.com' in parsed.netloc and '/s/' in url:
            resolved = self._resolve_reddit_share_url(url)
            if resolved:
                return resolved, None
            return None, f"Failed to resolve Reddit share URL: {url}"

        # Reddit user/subreddit post links (not share links)
        if 'reddit.com' in parsed.netloc and '/comments/' in url:
            resolved = self._resolve_reddit_share_url(url)  # Same logic works
            if resolved:
                return resolved, None
            return None, f"Failed to resolve Reddit post URL: {url}"

        # Unknown URL type - skip silently if not from known image hosts
        return None, None

    def _extract_all_urls_from_text(self, text: str) -> list[str]:
        """Find ALL URLs in text."""
        if not text:
            return []
        return ALL_URL_PATTERN.findall(text)

    def _extract_urls_from_text(self, text: str) -> list[str]:
        """Find image URLs in text (comments, selftext)."""
        if not text:
            return []
        return IMAGE_URL_PATTERN.findall(text)

    def _format_comment_tree(self, comments: list, depth: int = 0) -> list[str]:
        """Recursively format comment tree with indentation."""
        lines = []
        indent = "  " * depth

        for comment in comments:
            if comment.get('kind') != 't1':
                continue
            data = comment.get('data', {})
            author = data.get('author', '[deleted]')
            body = data.get('body', '[removed]')

            lines.append(f"{indent}[{author}]")
            for line in body.split('\n'):
                lines.append(f"{indent}  {line}")
            lines.append("")

            # Recurse into replies
            replies = data.get('replies')
            if replies and isinstance(replies, dict):
                children = replies.get('data', {}).get('children', [])
                lines.extend(self._format_comment_tree(children, depth + 1))

        return lines

    def _extract_comment_urls(self, comments: list) -> list[str]:
        """Extract ALL URLs from all comments recursively."""
        urls = []
        for comment in comments:
            if comment.get('kind') != 't1':
                continue
            data = comment.get('data', {})
            body = data.get('body', '')
            urls.extend(self._extract_all_urls_from_text(body))

            replies = data.get('replies')
            if replies and isinstance(replies, dict):
                children = replies.get('data', {}).get('children', [])
                urls.extend(self._extract_comment_urls(children))

        return urls

    def _extract_comment_images(self, comments: list) -> list[str]:
        """Extract image URLs from all comments recursively (legacy - direct images only)."""
        urls = []
        for comment in comments:
            if comment.get('kind') != 't1':
                continue
            data = comment.get('data', {})
            body = data.get('body', '')
            urls.extend(self._extract_urls_from_text(body))

            replies = data.get('replies')
            if replies and isinstance(replies, dict):
                children = replies.get('data', {}).get('children', [])
                urls.extend(self._extract_comment_images(children))

        return urls

    def _count_comments(self, comments: list) -> int:
        """Count total comments recursively."""
        count = 0
        for comment in comments:
            if comment.get('kind') != 't1':
                continue
            count += 1
            replies = comment.get('data', {}).get('replies')
            if replies and isinstance(replies, dict):
                count += self._count_comments(replies['data']['children'])
        return count

    def scrape_post(self, post_id: str, post_title: str) -> bool:
        """Scrape a single post: content, comments, images."""
        post_dir = self.output_dir / post_id
        post_dir.mkdir(parents=True, exist_ok=True)
        errors = []

        # Fetch full post with comments
        url = f"https://www.reddit.com/r/{self.subreddit}/comments/{post_id}.json"
        data = self._fetch_json(url)
        if not data:
            errors.append(f"Failed to fetch post JSON: {url}")
            (post_dir / "errors.txt").write_text('\n'.join(errors))
            return False

        time.sleep(self.delay)

        post_data = data[0]['data']['children'][0]['data']
        comments_data = data[1]['data']['children']

        # Write post.txt
        post_lines = [
            f"Title: {post_data.get('title', '')}",
            f"Author: {post_data.get('author', '[deleted]')}",
            f"Score: {post_data.get('score', 0)}",
            f"URL: {post_data.get('url', '')}",
            f"Permalink: https://reddit.com{post_data.get('permalink', '')}",
            "",
            "--- Content ---",
            post_data.get('selftext', '') or "(no text content)",
        ]
        (post_dir / "post.txt").write_text('\n'.join(post_lines))

        # Write comments.txt
        comment_lines = self._format_comment_tree(comments_data)
        (post_dir / "comments.txt").write_text('\n'.join(comment_lines))

        # Collect all image URLs
        image_urls = []

        # 1. Gallery images (media_metadata)
        if post_data.get('media_metadata'):
            for mid, meta in post_data['media_metadata'].items():
                if 's' in meta and 'u' in meta['s']:
                    img_url = html.unescape(meta['s']['u'])
                    ext = meta.get('m', 'image/png').split('/')[-1]
                    image_urls.append((img_url, f"{mid}.{ext}"))

        # 2. Direct image URL in post
        post_url = post_data.get('url', '')
        if any(post_url.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
            fname = os.path.basename(urlparse(post_url).path)
            image_urls.append((post_url, fname))

        # 3. Images linked in selftext
        selftext = post_data.get('selftext', '')
        for img_url in self._extract_urls_from_text(selftext):
            fname = os.path.basename(urlparse(img_url).path) or f"linked_{len(image_urls)}.jpg"
            image_urls.append((img_url, fname))

        # 4. Images/links in comments - resolve page URLs to direct image URLs
        comment_urls = self._extract_comment_urls(comments_data)
        for raw_url in comment_urls:
            resolved_url, error = self._resolve_url_to_image(raw_url)
            if resolved_url:
                # Generate filename from resolved URL
                parsed = urlparse(resolved_url)
                fname = os.path.basename(parsed.path) or f"comment_{len(image_urls)}.jpg"
                # Ensure valid extension
                if not any(fname.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
                    fname += '.jpg'
                image_urls.append((resolved_url, fname))
            elif error:
                errors.append(error)

        # Download images (dedupe by URL)
        seen_urls = set()
        img_count = 0
        for img_url, fname in image_urls:
            if img_url in seen_urls:
                continue
            seen_urls.add(img_url)

            save_path = post_dir / fname
            if save_path.exists():
                img_count += 1
                continue

            if self._download_image(img_url, save_path):
                img_count += 1
            else:
                errors.append(f"Failed to download: {img_url}")
            time.sleep(0.5)  # Small delay between image downloads

        # Write errors if any
        if errors:
            (post_dir / "errors.txt").write_text('\n'.join(errors))

        comment_count = self._count_comments(comments_data)
        print(f"  -> Saved: {comment_count} comments, {img_count} images", end="")
        if errors:
            print(f", {len(errors)} errors", end="")
        print()

        return True

    def get_posts(self, after: str = None) -> tuple[list[dict], str | None]:
        """Fetch a page of posts from the subreddit."""
        url = f"https://www.reddit.com/r/{self.subreddit}/new.json?limit=100"
        if after:
            url += f"&after={after}"

        data = self._fetch_json(url)
        if not data:
            return [], None

        posts = data.get('data', {}).get('children', [])
        next_after = data.get('data', {}).get('after')
        return posts, next_after

    def get_posts_pullpush(self, before: int = None) -> tuple[list[dict], int | None]:
        """Fetch posts using Pullpush API (time-windowed, no 1000 post limit)."""
        url = f"https://api.pullpush.io/reddit/search/submission?subreddit={self.subreddit}&size=100&sort=desc&sort_type=created_utc"
        if before:
            url += f"&before={before}"
        if self.start_date:
            start_ts = int(datetime.strptime(self.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            url += f"&after={start_ts}"

        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [!] Pullpush API error: {e}")
            return [], None

        posts = data.get('data', [])
        if not posts:
            return [], None

        # Convert to Reddit-like format and get next cursor
        converted = []
        next_before = None
        for p in posts:
            converted.append({'data': p})
            next_before = p.get('created_utc')

        return converted, int(next_before) if next_before else None

    def get_posts_arcticshift(self, before: int = None) -> tuple[list[dict], int | None]:
        """Fetch posts using Arctic Shift API (alternative to Pullpush)."""
        url = f"https://arctic-shift.photon-reddit.com/api/posts/search?subreddit={self.subreddit}&limit=100&sort=desc"
        if before:
            url += f"&before={before}"
        if self.start_date:
            url += f"&after={self.start_date}"
        if self.end_date and not before:
            url += f"&before={self.end_date}"

        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [!] Arctic Shift API error: {e}")
            return [], None

        posts = data.get('data', [])
        if not posts:
            return [], None

        # Convert to Reddit-like format and get next cursor
        converted = []
        next_before = None
        for p in posts:
            converted.append({'data': p})
            next_before = p.get('created_utc')

        return converted, int(next_before) if next_before else None

    def run(self):
        """Main loop - scrape forever until interrupted."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.archive_api:
            mode = f"{self.archive_api.title()} (time-windowed)"
        else:
            mode = "Reddit API"
        print(f"Scraping r/{self.subreddit} via {mode}")
        if self.archive_api:
            date_range = f"{self.start_date or 'beginning'} to {self.end_date or 'now'}"
            print(f"Date range: {date_range}")
        else:
            print(f"Min age: {self.min_age_days} days")
        if self.min_score is not None:
            print(f"Min score: {self.min_score}")
        if self.min_comments is not None:
            print(f"Min comments: {self.min_comments}")
        print(f"Output: {self.output_dir}")
        print(f"Already seen: {len(self.seen_posts)} posts")
        print("-" * 60)

        if self.archive_api:
            self._run_archive_api()
        else:
            self._run_reddit_api()

    def _run_archive_api(self):
        """Scrape using archive API (time-windowed, unlimited posts)."""
        # Select fetch function based on API
        if self.archive_api == "arcticshift":
            fetch_fn = self.get_posts_arcticshift
        else:
            fetch_fn = self.get_posts_pullpush

        # Set initial 'before' cursor
        if self.end_date:
            before = int(datetime.strptime(self.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        else:
            before = None

        page = 0
        new_posts = 0
        skipped_seen = 0
        skipped_score = 0
        skipped_comments = 0
        total_fetched = 0

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting {self.archive_api} scan...")

        while True:
            page += 1
            posts, next_before = fetch_fn(before)

            if not posts:
                print(f"  No more posts on page {page}")
                break

            total_fetched += len(posts)
            print(f"  Page {page}: {len(posts)} posts (total: {total_fetched})")

            for post in posts:
                data = post['data']
                post_id = data['id']
                title = data.get('title', '')[:50]
                created = data.get('created_utc', 0)
                post_date = datetime.fromtimestamp(created, timezone.utc).strftime('%Y-%m-%d')

                if post_id in self.seen_posts:
                    skipped_seen += 1
                    continue

                # Skip broken Reddit posts
                if title.startswith('[image processing failed]') or title.startswith('[removed]'):
                    continue

                # Skip if too new
                age_days = (datetime.now(timezone.utc).timestamp() - created) / 86400
                if age_days < self.min_age_days:
                    continue

                # Skip if score too low
                if self.min_score is not None and data.get('score', 0) < self.min_score:
                    skipped_score += 1
                    continue

                # Skip if not enough comments
                if self.min_comments is not None and data.get('num_comments', 0) < self.min_comments:
                    skipped_comments += 1
                    continue

                print(f"  [{post_id}] {post_date} - {title}...")

                if self.scrape_post(post_id, title):
                    self._save_seen_post(post_id)
                    new_posts += 1

                time.sleep(self.delay)

            if not next_before:
                break

            before = next_before
            time.sleep(self.delay)

        skipped_parts = [f"{skipped_seen} seen"]
        if skipped_score:
            skipped_parts.append(f"{skipped_score} low score")
        if skipped_comments:
            skipped_parts.append(f"{skipped_comments} low comments")
        print(f"\nScan complete: {new_posts} new, skipped: {', '.join(skipped_parts)}")
        print(f"Total posts discovered: {total_fetched}")

    def _run_reddit_api(self):
        """Scrape using Reddit's API (limited to ~1000 posts)."""
        now = datetime.now(timezone.utc)

        while True:
            after = None
            page = 0
            new_posts = 0
            skipped_too_new = 0
            skipped_seen = 0
            skipped_score = 0
            skipped_comments = 0

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting new scan...")

            while True:
                page += 1
                posts, after = self.get_posts(after)

                if not posts:
                    print(f"  No more posts on page {page}")
                    break

                print(f"  Page {page}: {len(posts)} posts (rate limit: {self.rate_limit_remaining:.0f} remaining)")

                for post in posts:
                    data = post['data']
                    post_id = data['id']
                    title = data.get('title', '')[:50]
                    created = data.get('created_utc', 0)
                    age_days = (now.timestamp() - created) / 86400

                    # Skip if already seen
                    if post_id in self.seen_posts:
                        skipped_seen += 1
                        continue

                    # Skip if too new
                    if age_days < self.min_age_days:
                        skipped_too_new += 1
                        continue

                    # Skip if score too low
                    if self.min_score is not None and data.get('score', 0) < self.min_score:
                        skipped_score += 1
                        continue

                    # Skip if not enough comments
                    if self.min_comments is not None and data.get('num_comments', 0) < self.min_comments:
                        skipped_comments += 1
                        continue

                    # Skip broken Reddit posts
                    if title.startswith('[image processing failed]') or title.startswith('[removed]'):
                        continue

                    print(f"  [{post_id}] {title}... ({age_days:.1f}d old)")

                    if self.scrape_post(post_id, title):
                        self._save_seen_post(post_id)
                        new_posts += 1

                    time.sleep(self.delay)

                if not after:
                    break

                time.sleep(self.delay)

            skipped_parts = [f"{skipped_seen} seen", f"{skipped_too_new} new"]
            if skipped_score:
                skipped_parts.append(f"{skipped_score} low score")
            if skipped_comments:
                skipped_parts.append(f"{skipped_comments} low comments")
            print(f"\nScan complete: {new_posts} new, skipped: {', '.join(skipped_parts)}")
            print(f"Waiting 5 minutes before next scan... (Ctrl+C to stop)")

            try:
                time.sleep(300)
            except KeyboardInterrupt:
                print("\nStopping...")
                break


def main():
    parser = argparse.ArgumentParser(description="Scrape Reddit subreddit")
    parser.add_argument("subreddit", help="Subreddit name (without r/)")
    parser.add_argument("--min-age", type=int, default=7, help="Minimum post age in days (default: 7)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds (default: 1.0)")
    parser.add_argument("--output", "-o", default=None, help="Output directory (default: ./data/<subreddit>)")
    parser.add_argument("--pullpush", action="store_true", help="Use Pullpush API for unlimited historical posts")
    parser.add_argument("--arcticshift", action="store_true", help="Use Arctic Shift API for unlimited historical posts")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (with --pullpush/--arcticshift)")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (with --pullpush/--arcticshift)")
    parser.add_argument("--min-score", type=int, default=None, help="Minimum post score/upvotes")
    parser.add_argument("--min-comments", type=int, default=None, help="Minimum number of comments")

    args = parser.parse_args()

    if args.pullpush and args.arcticshift:
        parser.error("Cannot use both --pullpush and --arcticshift")

    archive_api = None
    if args.pullpush:
        archive_api = "pullpush"
    elif args.arcticshift:
        archive_api = "arcticshift"

    output_dir = args.output or f"./data/{args.subreddit}"

    scraper = RedditScraper(
        subreddit=args.subreddit,
        output_dir=output_dir,
        min_age_days=args.min_age,
        delay=args.delay,
        archive_api=archive_api,
        start_date=args.start,
        end_date=args.end,
        min_score=args.min_score,
        min_comments=args.min_comments
    )

    try:
        scraper.run()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
