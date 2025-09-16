from slugify import slugify
import requests
import pandas as pd
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import os

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
        # Create Authors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Authors (
                id INTEGER PRIMARY KEY,
                name TEXT,
                email TEXT,
                image TEXT,
                bio TEXT,
                slug TEXT UNIQUE
            )
        ''')

        # Create Contents table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Contents (
                id INTEGER PRIMARY KEY,
                content TEXT
            )
        ''')

        # Create Users table (simplified for WordPress context)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                name TEXT
            )
        ''')

        # Create Articles table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Articles (
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
                FOREIGN KEY (authorId) REFERENCES Authors(id),
                FOREIGN KEY (contentId) REFERENCES Contents(id),
                FOREIGN KEY (createdById) REFERENCES Users(id),
                FOREIGN KEY (updatedById) REFERENCES Users(id)
            )
        ''')

        # Create Tags table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT UNIQUE NOT NULL,
                slug TEXT UNIQUE NOT NULL
            )
        ''')

        # Create ArticleTags junction table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ArticleTags (
                articleId INTEGER,
                tagId INTEGER,
                PRIMARY KEY (articleId, tagId),
                FOREIGN KEY (articleId) REFERENCES Articles(id),
                FOREIGN KEY (tagId) REFERENCES Tags(id)
            )
        ''')
        self.conn.commit()
    
    def insert_author(self, author_data):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO Authors (id, name, email, image, bio, slug)
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
        cursor.execute('INSERT INTO Contents (content) VALUES (?)', (content_text,))
        self.conn.commit()
        return cursor.lastrowid
    
    def insert_user(self, user_id, name):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO Users (id, name, email)
            VALUES (?, ?, ?)
        ''', (user_id, name, ""))
        self.conn.commit()
        return user_id
    
    def insert_tag(self, tag_name, slug):
        cursor = self.conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO Tags (tag, slug) VALUES (?, ?)', (tag_name, slug))
        cursor.execute('SELECT id FROM Tags WHERE tag = ?', (tag_name,))
        result = cursor.fetchone()
        self.conn.commit()
        return result[0] if result else None
    
    def insert_article(self, article_data):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO Articles (
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
            INSERT OR IGNORE INTO ArticleTags (articleId, tagId)
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
    content_html = post.get("content", {}).get("rendered", "")
    # Use article slug or id for unique folder name
    unique_folder_name = post.get("slug") or str(post.get("id"))
    processed_content_html = update_image_tags(content_html, unique_folder_name)
    content_id = db_manager.insert_content(processed_content_html)
    
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
        tag_name_clean = tag_name.strip()
        if tag_name_clean:
            slug = slugify(tag_name_clean)
            tag_id = db_manager.insert_tag(tag_name_clean, slug)
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

def update_image_tags(content_html, unique_folder_name):
    soup = BeautifulSoup(content_html, 'html.parser')
    image_tags = soup.find_all('img')

    for img_tag in image_tags:
        src = img_tag.get('src')
        if src:
            # Simulate new image path (as if it was saved)
            new_src = os.path.join(
                "/images/articleContents",
                unique_folder_name,
                os.path.basename(src)
            )
            img_tag['src'] = new_src
            # Set loading and decoding attributes as in the diff
            img_tag['loading'] = 'lazy'
            img_tag['decoding'] = 'async'

        # --- Fix image alignment classes ---
        current_class = img_tag.get('class')
        if current_class:
            if isinstance(current_class, str):
                current_class = [current_class]

            updated_classes = []
            for cls in current_class:
                if cls == "alignright":
                    updated_classes.append("image-right")
                elif cls == "alignleft":
                    updated_classes.append("image-left")
                elif cls == "aligncenter":
                    updated_classes.append("image-center")
                else:
                    updated_classes.append(cls)
            img_tag['class'] = updated_classes

    return str(soup)

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
    
    print("Database structure created with tables: Authors, Contents, Articles, Tags, ArticleTags, Users")

if __name__ == "__main__":
    main()