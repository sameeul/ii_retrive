import requests
import pandas as pd
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime

# ----------------------------
# CONFIG — change these
# ----------------------------
BASE_URL = "https://industryinsiderbd.com"  # <-- your WP site, no trailing slash
USERNAME = ""                     # Optional: for private/drafts use an Application Password
APP_PASSWORD = ""                 # Optional: create in WP: Users > Profile > Application Passwords
STATUS = "publish"                # e.g. "publish" or "any" (requires auth)
PER_PAGE = 50                    # WP max is usually 100
OUT_CSV = "wordpress_posts.csv"   # Keep for backwards compatibility
OUT_SQLITE = "dump.sqlite"        # e.g. "wordpress_posts.sqlite" or None to skip
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

def author_info_from_embedded(p):
    a = p.get("_embedded", {}).get("author", [])
    if a and isinstance(a, list):
        author = a[0]
        return {
            "id": author.get("id"),
            "name": author.get("name"),
            "slug": author.get("slug"),
            "description": author.get("description", ""),
            "avatar_url": author.get("avatar_urls", {}).get("96", ""),
            "author_email": author.get("email", "")
        }
    return None

class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None
        
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.create_tables()
        
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Create AUTHOR table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS AUTHOR (
                id INTEGER PRIMARY KEY,
                name TEXT,
                email TEXT,
                image TEXT,
                bio TEXT,
                slug TEXT UNIQUE
            )
        ''')
        
        # Create CONTENT table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS CONTENT (
                id INTEGER PRIMARY KEY,
                content TEXT
            )
        ''')
        
        # Create USER table (simplified for WordPress context)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS USER (
                id INTEGER PRIMARY KEY,
                email TEXT,
                name TEXT
            )
        ''')
        
        # Create ARTICLE table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ARTICLE (
                id INTEGER PRIMARY KEY,
                title TEXT,
                subTitle TEXT,
                shoulder TEXT,
                description TEXT,
                authorId INTEGER,
                contentId INTEGER,
                image TEXT,
                imageFolder TEXT,
                readCount INTEGER DEFAULT 0,
                slug TEXT UNIQUE,
                isPublished BOOLEAN,
                createdById INTEGER,
                updatedById INTEGER,
                created_date TEXT,
                modified_date TEXT,
                wp_link TEXT,
                FOREIGN KEY (authorId) REFERENCES AUTHOR(id),
                FOREIGN KEY (contentId) REFERENCES CONTENT(id),
                FOREIGN KEY (createdById) REFERENCES USER(id),
                FOREIGN KEY (updatedById) REFERENCES USER(id)
            )
        ''')
        
        # Create TAG table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS TAG (
                id INTEGER PRIMARY KEY,
                tag TEXT UNIQUE
            )
        ''')
        
        # Create ARTICLE_TAG junction table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ARTICLE_TAG (
                articleId INTEGER,
                tagId INTEGER,
                PRIMARY KEY (articleId, tagId),
                FOREIGN KEY (articleId) REFERENCES ARTICLE(id),
                FOREIGN KEY (tagId) REFERENCES TAG(id)
            )
        ''')
        
        self.conn.commit()
    
    def insert_author(self, author_data):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO AUTHOR (id, name, email, image, bio, slug)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            author_data["id"],
            author_data["name"],
            author_data["author_email"],
            author_data["avatar_url"],
            author_data["description"],
            author_data["slug"]
        ))
        self.conn.commit()
        return author_data["id"]
    
    def insert_content(self, content_text):
        cursor = self.conn.cursor()
        cursor.execute('INSERT INTO CONTENT (content) VALUES (?)', (content_text,))
        self.conn.commit()
        return cursor.lastrowid
    
    def insert_user(self, user_id, name):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO USER (id, name, email)
            VALUES (?, ?, ?)
        ''', (user_id, name, ""))
        self.conn.commit()
        return user_id
    
    def insert_tag(self, tag_name):
        cursor = self.conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO TAG (tag) VALUES (?)', (tag_name,))
        cursor.execute('SELECT id FROM TAG WHERE tag = ?', (tag_name,))
        result = cursor.fetchone()
        self.conn.commit()
        return result[0] if result else None
    
    def insert_article(self, article_data):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO ARTICLE (
                id, title, subTitle, shoulder, description, authorId, contentId,
                image, imageFolder, readCount, slug, isPublished, createdById,
                updatedById, created_date, modified_date, wp_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', article_data)
        self.conn.commit()
        return article_data[0]  # return article id
    
    def insert_article_tag(self, article_id, tag_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO ARTICLE_TAG (articleId, tagId)
            VALUES (?, ?)
        ''', (article_id, tag_id))
        self.conn.commit()
    
    def close(self):
        if self.conn:
            self.conn.close()

def process_post(post, db_manager):
    # Extract author information
    author_info = author_info_from_embedded(post)
    author_id = None
    if author_info:
        author_id = db_manager.insert_author(author_info)
    
    # Insert content
    # content_text = html_to_text(post.get("content", {}).get("rendered", ""))
    content_text = post.get("content", {}).get("rendered", "")
    content_id = db_manager.insert_content(content_text)
    
    # Insert user (using author info for simplicity, as WP doesn't expose user details easily)
    created_by_id = post.get("author")
    if created_by_id and author_info:
        db_manager.insert_user(created_by_id, author_info["name"])
    
    # Prepare article data
    title = post.get("title", {}).get("rendered", "")
    excerpt = html_to_text(post.get("excerpt", {}).get("rendered", ""))
    featured_image = featured_media_url(post)
    
    article_data = (
        post.get("id"),                    # id
        title,                             # title
        "",                                # subTitle (not available in WP)
        "",                                # shoulder (not available in WP)
        excerpt,                           # description (using excerpt)
        author_id,                         # authorId
        content_id,                        # contentId
        featured_image,                    # image
        "",                                # imageFolder (not available)
        0,                                 # readCount (default)
        post.get("slug"),                  # slug
        post.get("status") == "publish",   # isPublished
        created_by_id,                     # createdById
        created_by_id,                     # updatedById (same as created)
        post.get("date"),                  # created_date
        post.get("modified"),              # modified_date
        post.get("link")                   # wp_link
    )
    
    # Insert article
    article_id = db_manager.insert_article(article_data)
    
    # Process tags (using both categories and tags from WordPress)
    _, cat_names = term_names_from_embedded(post, "category")
    _, tag_names = term_names_from_embedded(post, "post_tag")
    
    all_tags = cat_names + tag_names
    for tag_name in all_tags:
        if tag_name.strip():
            tag_id = db_manager.insert_tag(tag_name.strip())
            if tag_id:
                db_manager.insert_article_tag(article_id, tag_id)
    
    return {
        "id": post.get("id"),
        "title": title,
        "slug": post.get("slug"),
        "author": author_info["name"] if author_info else "Unknown",
        "tags_count": len(all_tags)
    }

def fetch_all_posts():
    all_posts = []
    page = 1

    while True:
        params = {
            "per_page": PER_PAGE,
            "page": page,
            "_embed": "1",
            "status": STATUS,
        }
        print(f"Fetching page {page}...")
        r = session.get(API, params=params, timeout=60)
        if r.status_code == 400 and "rest_post_invalid_page_number" in r.text:
            break
        r.raise_for_status()

        items = r.json()
        if not items:
            break

        all_posts.extend(items)

        total_pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    return all_posts

def main():
    print("Fetching posts from WordPress API...")
    posts = fetch_all_posts()
    print(f"Found {len(posts)} posts")
    
    # Initialize database
    if OUT_SQLITE:
        db_manager = DatabaseManager(OUT_SQLITE)
        db_manager.connect()
        
        print("Processing posts into normalized database structure...")
        processed_posts = []
        
        for i, post in enumerate(posts, 1):
            try:
                result = process_post(post, db_manager)
                processed_posts.append(result)
                if i % 10 == 0:
                    print(f"Processed {i}/{len(posts)} posts...")
            except Exception as e:
                print(f"Error processing post {post.get('id', 'unknown')}: {e}")
        
        db_manager.close()
        print(f"Successfully processed {len(processed_posts)} posts into {OUT_SQLITE}")
        
        # Create summary CSV for backwards compatibility
        if processed_posts:
            df = pd.DataFrame(processed_posts)
            df.to_csv(OUT_CSV, index=False, encoding="utf-8")
            print(f"Created summary CSV: {OUT_CSV}")
    
    print("Database structure created with tables: AUTHOR, CONTENT, ARTICLE, TAG, ARTICLE_TAG, USER")

if __name__ == "__main__":
    main()