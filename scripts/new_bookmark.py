#!/usr/bin/env python3
import os, re, sys, json, pathlib
import requests
from bs4 import BeautifulSoup
from slugify import slugify

# ---- Config
USE_AI = bool(os.environ.get('OPENAI_API_KEY'))
ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / 'index.html'
CATEGORIES_DIR = ROOT / 'categories'
DATA_DIR = ROOT / 'data'
TEMPLATES_DIR = ROOT / 'templates'
CATEGORY_TEMPLATE = TEMPLATES_DIR / 'category.html'

# (Markers kept for backward-compat; cards are JSON-driven now)
CARD_START = '<!-- AUTO-CARDS:START -->'
CARD_END   = '<!-- AUTO-CARDS:END -->'
CAT_START  = '<!-- AUTO-CATEGORIES:START -->'
CAT_END    = '<!-- AUTO-CATEGORIES:END -->'

session = requests.Session()
session.headers.update({'User-Agent': 'DomainBookmarksBot/1.1'})

# ---- Issue overrides
CATEGORY_RE = re.compile(r'^Category:\s*(.+)$', re.I | re.M)
GROUP_RE    = re.compile(r'^Group:\s*(.+)$', re.I | re.M)
DESC_RE     = re.compile(r'^Description:\s*(.+)$', re.I | re.M)
TITLE_RE    = re.compile(r'^Title:\s*(.+)$', re.I | re.M)

def read_override(rx, text):
    m = rx.search(text or '')
    return m.group(1).strip() if m else None

# ---- URL & metadata
def first_url(text):
    m = re.search(r'(https?://\S+)', text or '')
    return m.group(1) if m else None

def fetch_meta(url):
    r = session.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'lxml')  # use lxml parser
    def pick(selectors):
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get('content') or el.get_text(strip=True)
        return None
    title = pick(['meta[property="og:title"]', 'meta[name="twitter:title"]', 'title'])
    desc  = pick(['meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]'])
    return (title or url).strip(), (desc or '').strip(), r.url

# ---- Fallback classification (no-API)
KEYWORD_MAP = [
    ('Domain Blogs', ['blog','news','journal']),
    ('Marketplaces', ['marketplace','afternic','sedo','dan.com','buy domains']),
    ('Appraisal Tools', ['appraisal','valuation','worth']),
    ('Name Generators', ['generator','brainstorm','ideas']),
    ('WHOIS / Research', ['whois','dns','lookup']),
    ('Drops & Auctions', ['expired','auction','backorder','drop','closeout']),
    ('Brandable Marketplaces', ['brandable','brandbucket','atom','squadhelp']),
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
    sys_prompt = {"role":"system","content":"Classify and summarize domain resources for a public directory. Return strict JSON only."}
    user = {"role":"user","content":(
        "Return JSON with fields: "
        "category_name (Title Case, 1–3 words), "
        "group_name (Title Case, 1–3 words, optional), "
        "short_title (≤60 chars), "
        "description (20–30 words), "
        "suggested_category_slug (kebab-case). \n\n"
        f"URL: {url}\nTITLE: {title}\nDESC: {desc}"
    )}
    resp = openai.ChatCompletion.create(model="gpt-4o-mini", messages=[sys_prompt, user], temperature=0.2)
    txt = resp.choices[0].message["content"].strip()
    try:
        data = json.loads(txt)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", txt)
        data = json.loads(m.group(0)) if m else {}
    if 'category_name' not in data:
        data['category_name'] = fallback_category(title, desc, url)
    if 'suggested_category_slug' not in data:
        data['suggested_category_slug'] = slugify(data['category_name'])
    if 'short_title' not in data:
        data['short_title'] = title[:60]
    if desc and 'description' not in data:
        data['description'] = desc[:220]
    return data

# ---- JSON helpers
def load_category_json(slug, category_name):
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{slug}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        data.setdefault('category', category_name or data.get('category') or slug)
        data.setdefault('groups', [])
    else:
        data = {'category': category_name, 'groups': []}
    return data, path

def upsert_item(data, group_name, item):
    # ensure group exists
    group_name = (group_name or 'General').strip()
    groups = data['groups']
    g = next((g for g in groups if g['name'].lower() == group_name.lower()), None)
    if not g:
        g = {'name': group_name, 'items': []}
        groups.append(g)
    # upsert by URL
    items = g['items']
    existing = next((x for x in items if x['url'].rstrip('/') == item['url'].rstrip('/')), None)
    if existing:
        existing.update({k: v for k, v in item.items() if v})
    else:
        items.append(item)
    # sort items and groups (alphabetical, case-insensitive)
    items.sort(key=lambda x: (x.get('title') or '').lower())
    groups.sort(key=lambda x: x['name'].lower())

def ensure_category_page(name, slug):
    CATEGORIES_DIR.mkdir(exist_ok=True)
    path = CATEGORIES_DIR / f"{slug}.html"
    if not path.exists():
        html = (CATEGORY_TEMPLATE.read_text(encoding='utf-8')
                .replace('{{CATEGORY_NAME}}', name)
                .replace('{{CATEGORY_SLUG}}', slug))
        path.write_text(html, encoding='utf-8')
    return path

def insert_between(mark_start, mark_end, whole, new_li, dedupe_line=None):
    start = whole.find(mark_start); end = whole.find(mark_end)
    if start == -1 or end == -1 or end < start:
        raise RuntimeError('Missing AUTO markers')
    before = whole[:start+len(mark_start)]
    middle = whole[start+len(mark_start):end]
    after = whole[end:]
    lines = [l for l in middle.splitlines() if l.strip()]
    if dedupe_line and dedupe_line.strip() in [l.strip() for l in lines]:
        return whole
    if middle and not middle.endswith('\n'):
        middle += '\n'
    middle += new_li.rstrip() + '\n'
    return before + "\n" + middle + after

def ensure_category_link_on_index(name, slug):
    if not INDEX.exists():
        return
    html = INDEX.read_text(encoding='utf-8')
    link_li = f'<li><a href="categories/{slug}.html">{name}</a></li>'
    try:
        # alphabetical insert inside markers
        start = html.find(CAT_START); end = html.find(CAT_END)
        if start == -1 or end == -1 or end < start:
            raise RuntimeError('Missing AUTO markers')
        before = html[:start+len(CAT_START)]
        middle = html[start+len(CAT_START):end]
        after = html[end:]
        lis = re.findall(r'<li>.*?</li>', middle, flags=re.I | re.S)
        if not any(f'href="categories/{slug}.html"' in li for li in lis):
            lis.append(link_li)
        lis_sorted = sorted(lis, key=lambda li: re.sub(r'<.*?>', '', li).strip().lower())
        new_middle = "\n" + "\n".join(lis_sorted) + "\n"
        new_html = before + new_middle + after
    except RuntimeError:
        new_html = insert_between(CAT_START, CAT_END, html, link_li, dedupe_line=link_li)
    if new_html != html:
        INDEX.write_text(new_html, encoding='utf-8')

# ---- Main
if __name__ == '__main__':
    issue_title = sys.argv[1] if len(sys.argv) > 1 else ''
    issue_body  = sys.argv[2] if len(sys.argv) > 2 else ''

    url = first_url(issue_title) or first_url(issue_body)
    if not url:
        print('::error::No URL found in issue')
        sys.exit(1)

    title, meta_desc, final_url = fetch_meta(url)

    # classify (AI or fallback) and allow overrides
    if USE_AI:
        data_ai = ai_categorize(title, meta_desc, final_url)
        category_name = data_ai['category_name']
        category_slug = data_ai['suggested_category_slug']
        short_title   = data_ai['short_title']
        description   = data_ai.get('description', '')
        group_name    = data_ai.get('group_name')
    else:
        category_name = fallback_category(title, meta_desc, final_url)
        category_slug = slugify(category_name)
        short_title   = title[:60]
        description   = (meta_desc or f'Resource: {title}')[:220]
        group_name    = None

    # issue-level overrides (explicit)
    override_cat   = read_override(CATEGORY_RE, issue_body)
    override_grp   = read_override(GROUP_RE,    issue_body)
    override_desc  = read_override(DESC_RE,     issue_body)
    override_title = read_override(TITLE_RE,    issue_body)
    if override_cat:
        category_name = override_cat
        category_slug = slugify(override_cat)
    if override_grp:
        group_name = override_grp
    if override_desc:
        description = override_desc[:220]
    if override_title:
        short_title = override_title[:60]

    # ensure page and JSON
    ensure_category_page(category_name, category_slug)
    data_json, json_path = load_category_json(category_slug, category_name)

    item = {'title': short_title, 'url': final_url, 'description': description}
    upsert_item(data_json, group_name or 'General', item)
    json_path.write_text(json.dumps(data_json, indent=2, ensure_ascii=False), encoding='utf-8')

    # homepage link
    ensure_category_link_on_index(category_name, category_slug)

    # Expose outputs for the workflow step (modern way)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"short_title={short_title}\n")
            f.write(f"category_name={category_name}\n")
            f.write(f"url={final_url}\n")
