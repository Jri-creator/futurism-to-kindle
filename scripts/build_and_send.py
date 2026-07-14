#!/usr/bin/env python3
"""
Fetch the latest Futurism articles, skip ones already sent, bundle the
newest batch into a single EPUB, and email it to a Kindle address via
the Resend API.

State (which article URLs have already been sent) is kept in
sent.json at the repo root, and is expected to be committed back to
the repo by the workflow after this script runs.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from ebooklib import epub

FEED_URL = "https://futurism.com/feed"
STATE_FILE = Path("sent.json")
MAX_ARTICLES_PER_RUN = int(os.environ.get("MAX_ARTICLES_PER_RUN", "10"))
REQUEST_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; FuturismKindleBot/1.0)"

RESEND_API_KEY = os.environ.get("REK")
KINDLE_EMAIL = os.environ.get("KINDLE_EMAIL")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")


def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()).get("sent_urls", []))
    return set()


def save_state(sent_urls):
    STATE_FILE.write_text(json.dumps({"sent_urls": sorted(sent_urls)}, indent=2))


def fetch_feed_entries():
    feed = feedparser.parse(FEED_URL)
    entries = []
    for e in feed.entries:
        url = e.get("link")
        if not url:
            continue
        # published_parsed is a time.struct_time in UTC (per feedparser docs)
        if e.get("published_parsed"):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        else:
            published = datetime.now(timezone.utc)
        entries.append(
            {
                "url": url,
                "title": e.get("title", "Untitled"),
                "published": published,
            }
        )
    # Newest first
    entries.sort(key=lambda x: x["published"], reverse=True)
    return entries


def scrape_article(url):
    """Fetch an article page and extract title, byline, main content HTML,
    and image URLs, using the site's article markup."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Futurism articles render their body inside an <article> tag;
    # fall back to common content containers if that ever changes.
    article_tag = soup.find("article") or soup.find(
        "div", class_=lambda c: c and "article-body" in c
    )
    if not article_tag:
        raise ValueError(f"Could not locate article body for {url}")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else url

    # Strip out obvious non-content elements (ads, related-article widgets,
    # scripts, forms, social embeds) before we keep what's left.
    for tag in article_tag.find_all(["script", "style", "form", "iframe", "aside"]):
        tag.decompose()
    for tag in article_tag.find_all(
        class_=lambda c: c and any(
            kw in c.lower()
            for kw in ("newsletter", "related", "ad-", "advert", "share", "social")
        )
    ):
        tag.decompose()

    # Resolve relative image URLs to absolute ones so they can be downloaded.
    images = []
    for img in article_tag.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            img.decompose()
            continue
        abs_src = urljoin(url, src)
        images.append(abs_src)
        img["src"] = abs_src

    paragraphs = article_tag.find_all(["p", "h2", "h3", "blockquote", "img", "figure"])
    body_html = "".join(str(p) for p in paragraphs) or str(article_tag)

    return {
        "title": title,
        "url": url,
        "body_html": body_html,
        "images": images,
    }


def download_image(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        return None


def build_epub(articles, output_path):
    book = epub.EpubBook()
    today = datetime.now().strftime("%Y-%m-%d")
    book.set_identifier(f"futurism-digest-{today}-{hashlib.sha1(today.encode()).hexdigest()[:8]}")
    book.set_title(f"Futurism Digest — {today}")
    book.set_language("en")
    book.add_author("Futurism (via automated digest)")

    chapters = []
    image_counter = 0

    for i, article in enumerate(articles):
        body_html = article["body_html"]

        # Download each image, embed it in the epub, and rewrite the <img>
        # src to point at the embedded copy.
        soup = BeautifulSoup(body_html, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            data = download_image(src)
            if not data:
                img.decompose()
                continue
            image_counter += 1
            ext = src.split(".")[-1].split("?")[0].lower()
            if ext not in ("jpg", "jpeg", "png", "gif"):
                ext = "jpg"
            img_filename = f"images/img_{image_counter}.{ext}"
            epub_img = epub.EpubItem(
                uid=f"img_{image_counter}",
                file_name=img_filename,
                media_type=f"image/{'jpeg' if ext == 'jpg' else ext}",
                content=data,
            )
            book.add_item(epub_img)
            img["src"] = img_filename

        chapter_filename = f"chap_{i+1}.xhtml"
        chapter = epub.EpubHtml(
            title=article["title"], file_name=chapter_filename, lang="en"
        )
        chapter.content = (
            f"<h1>{article['title']}</h1>"
            f"<p><em><a href='{article['url']}'>{article['url']}</a></em></p>"
            f"{str(soup)}"
        )
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    epub.write_epub(str(output_path), book)


def send_via_resend(epub_path):
    if not RESEND_API_KEY:
        print("ERROR: REK secret (Resend API key) is not set.", file=sys.stderr)
        sys.exit(1)
    if not KINDLE_EMAIL:
        print("ERROR: KINDLE_EMAIL secret is not set.", file=sys.stderr)
        sys.exit(1)

    import base64

    content_b64 = base64.b64encode(epub_path.read_bytes()).decode()

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [KINDLE_EMAIL],
            "subject": "Futurism Digest",
            "text": "Attached: your Futurism article digest.",
            "attachments": [
                {
                    "filename": epub_path.name,
                    "content": content_b64,
                }
            ],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"ERROR sending email: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    print(f"Email sent: {resp.json()}")


def main():
    sent_urls = load_state()
    entries = fetch_feed_entries()

    new_entries = [e for e in entries if e["url"] not in sent_urls]
    batch = new_entries[:MAX_ARTICLES_PER_RUN]

    if not batch:
        print("No new articles to send.")
        return

    print(f"Found {len(new_entries)} new articles; sending the newest {len(batch)}.")

    articles = []
    for entry in batch:
        try:
            print(f"Scraping: {entry['url']}")
            article = scrape_article(entry["url"])
            articles.append(article)
            time.sleep(1)  # be polite to the server
        except Exception as exc:
            print(f"Skipping {entry['url']} due to error: {exc}", file=sys.stderr)

    if not articles:
        print("Nothing successfully scraped; aborting without sending or updating state.")
        return

    output_path = Path("digest.epub")
    build_epub(articles, output_path)
    print(f"Built {output_path} ({output_path.stat().st_size} bytes)")

    send_via_resend(output_path)

    # Only mark as sent the articles that were actually scraped+emailed.
    sent_urls.update(a["url"] for a in articles)
    save_state(sent_urls)
    print(f"Updated {STATE_FILE} with {len(articles)} new entries.")


if __name__ == "__main__":
    main()
