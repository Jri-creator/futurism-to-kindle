# Futurism → Kindle Digest

Daily GitHub Action that pulls the newest Futurism articles, bundles up to
10 unread ones into a single EPUB, and emails it to your Kindle via Resend.

## Setup

1. **Create this repo on GitHub** (private is fine) and push these files.

2. **Get a Resend API key**
   - Sign up at https://resend.com (free tier: 3,000 emails/month).
   - Create an API key.
   - ~~If you don't have a custom domain to verify, you can send from
     Resend's shared test address `onboarding@resend.dev` — good enough
     for personal automation like this.~~

3. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `REK` — your Resend API key.
   - `KINDLE_EMAIL` — your `@kindle.com` send-to-Kindle address.
   - `FROM_EMAIL` — the address you're sending from (e.g.
     ~~`onboarding@resend.dev`, or~~ `you@yourdomain.com` if you verified a
     domain in Resend).

4. **Approve the sender on Amazon**
   - Go to Amazon → Manage Your Content and Devices → Preferences →
     Personal Document Settings.
   - Add whatever address you put in `FROM_EMAIL` to your "Approved
     Personal Document E-mail List." Kindle silently drops mail from
     unapproved senders, so this step is required.

5. **Enable Actions** on the repo if prompted, and optionally run the
   workflow once manually (Actions tab → Futurism Kindle Digest → Run
   workflow) to confirm it works before waiting for the schedule.

## How it works

- `scripts/build_and_send.py` fetches `futurism.com/feed`, filters out
  any article URL already recorded in `sent.json`, and takes the newest
  10 (configurable via `MAX_ARTICLES_PER_RUN`).
- Each article is scraped for its title, body text, and images, and
  bundled into a single `digest.epub` with one chapter per article.
- The EPUB is emailed as an attachment via the Resend API.
- `sent.json` is updated with the URLs that were actually sent, and the
  workflow commits that file back to the repo so future runs don't
  resend the same articles.

## Adjusting

- Change the cron schedule in `.github/workflows/futurism-digest.yml`
  (currently daily at 12:00 UTC).
- Change `MAX_ARTICLES_PER_RUN` to send more/fewer articles per email.
- If Futurism changes their page markup and scraping breaks, the
  `scrape_article()` function in `build_and_send.py` is the place to
  fix the CSS/tag selectors.
