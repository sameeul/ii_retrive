import requests
import pandas as pd
from bs4 import BeautifulSoup

# ----------------------------
# CONFIG — change these
# ----------------------------
BASE_URL = "https://industryinsiderbd.com"  # <-- your WP site, no trailing slash
USERNAME = ""                     # Optional: for private/drafts use an Application Password
APP_PASSWORD = ""                 # Optional: create in WP: Users > Profile > Application Passwords
STATUS = "publish"                # e.g. "publish" or "any" (requires auth)
PER_PAGE = 100                    # WP max is usually 100
OUT_CSV = "wordpress_posts.csv"
OUT_SQLITE = "dump.sqlite"                 # e.g. "wordpress_posts.sqlite" or None to skip
SQLITE_TABLE = "wp_posts_export"
# ----------------------------

API = f"{BASE_URL}/wp-json/wp/v2/posts"

session = requests.Session()
if USERNAME and APP_PASSWORD:
    session.auth = (USERNAME, APP_PASSWORD)

def html_to_text(html):
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)

def term_names_from_embedded(p, taxonomy):
    """
    taxonomy: 'category' or 'post_tag'
    """
    names, ids = [], []
    for term_group in p.get("_embedded", {}).get("wp:term", []):
        for t in term_group:
            if t.get("taxonomy") == taxonomy:
                names.append(t.get("name"))
                ids.append(t.get("id"))
    return ids, names

def featured_media_url(p):
    media = p.get("_embedded", {}).get("wp:featuredmedia", [])
    if media and isinstance(media, list) and media[0].get("source_url"):
        return media[0]["source_url"]
    return None

def author_name_from_embedded(p):
    a = p.get("_embedded", {}).get("author", [])
    if a and isinstance(a, list):
        return a[0].get("name")
    return None

def flatten_post(p):
    # Core fields
    row = {
        "id": p.get("id"),
        "slug": p.get("slug"),
        "status": p.get("status"),
        "type": p.get("type"),
        "link": p.get("link"),
        "date": p.get("date"),
        "modified": p.get("modified"),
        "title": p.get("title", {}).get("rendered"),
        "excerpt": html_to_text(p.get("excerpt", {}).get("rendered")),
        "content_text": html_to_text(p.get("content", {}).get("rendered")),
        "author_id": p.get("author"),
        "author_name": author_name_from_embedded(p),
        "featured_media_url": featured_media_url(p),
    }

    # Categories & tags
    cat_ids, cat_names = term_names_from_embedded(p, "category")
    tag_ids, tag_names = term_names_from_embedded(p, "post_tag")
    row["categories_ids"] = ",".join(map(str, cat_ids)) if cat_ids else ""
    row["categories_names"] = ", ".join(cat_names) if cat_names else ""
    row["tags_ids"] = ",".join(map(str, tag_ids)) if tag_ids else ""
    row["tags_names"] = ", ".join(tag_names) if tag_names else ""

    # ---- Custom fields / ACF example (uncomment if exposed in REST)
    # if "acf" in p:
    #     row["acf_field_x"] = p["acf"].get("field_x")

    return row

def fetch_all_posts():
    all_rows = []
    page = 1

    while True:
        params = {
            "per_page": PER_PAGE,
            "page": page,
            "_embed": "1",
            "status": STATUS,  # requires auth for anything other than publish
            # Optional: select only needed fields to speed things up
            # "_fields": "id,slug,status,type,link,date,modified,title,excerpt,content,author,_embedded"
        }
        r = session.get(API, params=params, timeout=60)
        if r.status_code == 400 and "rest_post_invalid_page_number" in r.text:
            break
        r.raise_for_status()

        items = r.json()
        if not items:
            break

        for p in items:
            all_rows.append(flatten_post(p))

        total_pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    return all_rows

def main():
    rows = fetch_all_posts()
    df = pd.DataFrame(rows)

    # Save CSV
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"Saved {len(df)} posts to {OUT_CSV}")

    # Optionally save to SQLite
    if OUT_SQLITE:
        import sqlite3
        con = sqlite3.connect(OUT_SQLITE)
        df.to_sql(SQLITE_TABLE, con, if_exists="replace", index=False)
        con.close()
        print(f"Wrote {len(df)} rows to SQLite {OUT_SQLITE}:{SQLITE_TABLE}")

if __name__ == "__main__":
    main()
