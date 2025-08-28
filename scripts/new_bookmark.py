#!/usr/bin/env python3
import os, re, sys, json, pathlib
import requests
from bs4 import BeautifulSoup
from slugify import slugify

# Optional AI
USE_AI = bool(os.environ.get('OPENAI_API_KEY'))

# ---- Helpers ----
ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / 'index.html'
CATEGORIES_DIR = ROOT / 'categories'
TEMPLATES_DIR = ROOT / 'templates'
CATEGORY_TEMPLATE = (TEMPLATES_DIR / 'category.html')

CARD_START = '<!-- AUTO-CARDS:START -->'
CARD_END   = '<!-- AUTO-CARDS:END -->'
CAT_START  = '<!-- AUTO-CATEGORIES:START -->'
CAT_END    = '<!-- AUTO-CATEGORIES:END -->'

session = requests.Session()
session.headers.update({'User-Agent': 'DomainBookmarksBot/1.0'})


def first_url(text):
    m = re.search(r'(https?://\S+)', text or '')
    return m.group(1) if m else None


def fetch_meta(url):
    r = session.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    def pick(selectors):
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get('content') or el.get_text(strip=True)
        return None
    title = pick(['meta[property="og:title"]', 'meta[name="twitter:title"]', 'title'])
    desc = pick(['meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]'])
    return title or url, (desc or '').strip(), r.url


# Basic keyword rules as a fallback if no AI key
KEYWORD_MAP = [
    ('Domain Blogs', ['blog', 'news', 'journal']),
    ('Marketplaces', ['marketplace', 'buy domains', 'afternic', 'dan.com', 'sedo']),
    ('Appraisal Tools', ['appraisal', 'valuation', 'worth']),
    ('Name Generators', ['generator', 'brainstorm', 'ideas']),
    ('WHOIS / Research', ['whois', 'dns', 'lookup']),
    ('Drops & Auctions', ['expired', 'auction', 'backorder', 'drop', 'closeout']),
    ('Brandable Marketplaces', ['brandable', 'brandbucket', 'atom', 'squadhelp']),
]


def fallback_category(title, desc, url):
    t = f"{title} {desc} {url}".lower()
    for name, kws in KEYWORD_MAP:
        if any(kw in t for kw in kws):
            return name
    return 'General'


def ai_categorize(title, desc, url):
    import openai
    openai.api_key = os.environ['OPENAI_API_KEY']
    prompt = {
        "role": "system",
        "content": "You classify and summarize domain-name resources for a public directory. Return strict JSON only."
    }
    user = {
        "role": "user",
        "content": (
            "Given this resource, produce JSON with fields: "
            "category_name (title case, 1–3 words), "
            "short_title (max 60 chars), "
            "description (20–30 words, plain), "
            "suggested_category_slug (kebab-case).\n\n"
            f"URL: {url}\nTITLE: {title}\nDESC: {desc}"
        )
    }
    # Using Chat Completions for broad compatibility
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[prompt, user],
        temperature=0.2,
    )
    text = resp.choices[0].message["content"].strip()
    try:
        data = json.loads(text)
    except Exception:
        # If the model returned prose, try to extract a JSON object
        m = re.search(r"\{[\s\S]*\}", text)
        data = json.loads(m.group(0)) if m else {}
    # Guardrails
    data.setdefault('category_name', fallback_category(title, desc, url))
    data.setdefault('suggested_category_slug', slugify(data['category_name']))
    data.setdefault('short_title', title[:60])
    if desc:
        data.setdefault('description', desc[:220])
    return data


def ensure_category_page(name, slug):
    CATEGORIES_DIR.mkdir(exist_ok=True)
    path = CATEGORIES_DIR / f"{slug}.html"
    if not path.exists():
        html = (CATEGORY_TEMPLATE.read_text(encoding='utf-8')
                .replace('{{CATEGORY_NAME}}', name))
        # ensure card markers exist
        if CARD_START not in html:
            html += f"\n{CARD_START}\n<ul class=\"cards-grid\">\n</ul>\n{CARD_END}\n"
        path.write_text(html, encoding='utf-8')
    return path


def insert_between(mark_start, mark_end, whole, new_block, dedupe_line=None):
    start = whole.find(mark_start)
    end = whole.find(mark_end)
    if start == -1 or end == -1 or end < start:
        raise RuntimeError('Missing AUTO markers')
    before = whole[:start+len(mark_start)]
    middle = whole[start+len(mark_start):end]
    after = whole[end:]

    middle_lines = [l for l in middle.splitlines() if l.strip()]
    if dedupe_line and dedupe_line.strip() in [l.strip() for l in middle_lines]:
        return whole  # already present

    if middle and not middle.endswith('\n'):
        middle += '\n'
    middle += new_block.rstrip() + '\n'
    return before + "\n" + middle + after


def add_card_to_category(cat_path, card_html, identity_line):
    html = cat_path.read_text(encoding='utf-8')
    new_html = insert_between(CARD_START, CARD_END, html, card_html, dedupe_line=identity_line)
    if new_html != html:
        cat_path.write_text(new_html, encoding='utf-8')


def ensure_category_link_on_index(name, slug):
    if not INDEX.exists():
        return
    html = INDEX.read_text(encoding='utf-8')
    link_line = f'<li><a href="categories/{slug}.html">{name}</a></li>'
    try:
        new_html = insert_between(CAT_START, CAT_END, html, link_line, dedupe_line=link_line)
    except RuntimeError:
        return  # no markers on index; skip
    if new_html != html:
        INDEX.write_text(new_html, encoding='utf-8')


def make_card_html(url, short_title, description):
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ''
    identity = f"<a href=\"{url}\""
    card = f'''<li class="card">
  <a href="{url}" target="_blank" rel="nofollow noopener">
    <strong>{short_title}</strong><br>
    <em>{host}</em>
    <p>{description}</p>
  </a>
</li>'''
    return card, identity


# ---- Main ----
if __name__ == '__main__':
    issue_title = sys.argv[1] if len(sys.argv) > 1 else ''
    issue_body  = sys.argv[2] if len(sys.argv) > 2 else ''

    url = first_url(issue_title) or first_url(issue_body)
    if not url:
        print('::error::No URL found in issue')
        sys.exit(1)

    title, meta_desc, final_url = fetch_meta(url)

    if USE_AI:
        data = ai_categorize(title, meta_desc, final_url)
    else:
        cat = fallback_category(title, meta_desc, final_url)
        data = {
            'category_name': cat,
            'suggested_category_slug': slugify(cat),
            'short_title': title[:60],
            'description': meta_desc[:220] if meta_desc else f'Resource: {title}',
        }

    category_name = data['category_name']
    category_slug = data['suggested_category_slug']
    short_title   = data['short_title']
    description   = data['description']

    # Ensure files and inject content
    cat_path = ensure_category_page(category_name, category_slug)
    card_html, identity_line = make_card_html(final_url, short_title, description)
    add_card_to_category(cat_path, card_html, identity_line)
    ensure_category_link_on_index(category_name, category_slug)

    # Expose a few outputs to the workflow
    print(f"::set-output name=short_title::{short_title}")
    print(f"::set-output name=category_name::{category_name}")
    print(f"::set-output name=url::{final_url}")
