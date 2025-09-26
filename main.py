def report_pg_notnull_columns(sqlite_db_path, pg_conn_params):
    """
    Compare each table in SQLite and PostgreSQL, and report columns in PostgreSQL that are NOT NULL but nullable or missing in SQLite.
    """
    import collections
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg2.connect(**pg_conn_params)
    pg_cur = pg_conn.cursor()

    tables = ["Authors", "Contents", "Users", "Articles", "Tags", "ArticleTags"]
    print("\n--- PostgreSQL NOT NULL columns report ---")
    for table in tables:
        print(f"\nTable: {table}")
        # Get SQLite columns
        sqlite_cur.execute(f'PRAGMA table_info({table})')
        sqlite_cols = {row[1]: row for row in sqlite_cur.fetchall()}  # row[1]=name, row[3]=notnull
        # Get PostgreSQL columns
        pg_cur.execute(f'''
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = '{table.lower()}'
            ORDER BY ordinal_position
        ''')
        pg_cols = collections.OrderedDict((row[0], row[1]) for row in pg_cur.fetchall())
        # Report NOT NULL columns in PostgreSQL
        found = False
        for col, nullable in pg_cols.items():
            if nullable == 'NO':
                sqlite_notnull = sqlite_cols.get(col, (None, None, None, 0))[3]  # 1 if notnull in sqlite
                if not sqlite_notnull:
                    print(f"  - {col} is NOT NULL in PostgreSQL but nullable in SQLite or missing in SQLite")
                    found = True
        if not found:
            print("  All NOT NULL columns in PostgreSQL are also NOT NULL in SQLite.")
    sqlite_cur.close()
    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()
    print("\n--- End of report ---\n")
def clear_postgres_tables(pg_cur, pg_conn):
    """
    Delete all records from the relevant tables in the correct order to avoid FK issues.
    """
    tables = [
        "ArticleTags",
        "Articles",
        "Tags",
        "Authors",
        "Contents",
        "Users"
    ]
    for table in tables:
        try:
            pg_cur.execute(f'DELETE FROM "{table}";')
            pg_conn.commit()
            print(f"Cleared table {table}")
        except Exception as e:
            print(f"Error clearing table {table}: {e}")
            pg_conn.rollback()

import psycopg2
from psycopg2.extras import execute_values

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

BASE_IMAGE_DIR = os.getenv('BASE_IMAGE_DIR', 'images')
ARTICLE_IMAGE_DIR = os.path.join(BASE_IMAGE_DIR, 'articles')
CONTENT_IMAGE_DIR = os.path.join(BASE_IMAGE_DIR, 'articleContents')

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
                slug TEXT UNIQUE,
                created_date TEXT,
                modified_date TEXT
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
                name TEXT,
                created_date TEXT,
                modified_date TEXT
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
            INSERT OR IGNORE INTO Authors (id, name, email, image, bio, slug, created_date, modified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            author_data["id"],
            author_data["name"],
            author_data["author_email"],
            author_data["avatar_url"],
            author_data["description"],
            author_data["slug"],
            datetime.now().isoformat(),
            datetime.now().isoformat()
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
            INSERT OR IGNORE INTO Users (id, name, email, created_date, modified_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, name, "", datetime.now().isoformat(), datetime.now().isoformat()))
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
                updatedById, created_date, modified_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    print(f"Inserted content for post ID {post.get('id')} with content ID {content_id}")
    # Insert user (using author info for simplicity, as WP doesn't expose user details easily)
    created_by_id = post.get("author")
    if created_by_id and author_info:
        db_manager.insert_user(created_by_id, author_info["name"])
    
    # Prepare article data
    title = post.get("title", {}).get("rendered", "")
    excerpt = html_to_text(post.get("excerpt", {}).get("rendered", ""))
    featured_image = featured_media_url(post)
    if featured_image:
        featured_image_base = os.path.basename(featured_image)
        featured_image = os.path.join(ARTICLE_IMAGE_DIR, featured_image_base)

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
                CONTENT_IMAGE_DIR,
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

def retrieve_from_wordpress():
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


def copy_sqlite_to_postgres(sqlite_db_path, pg_conn_params):
    """
    Copy all data from the SQLite database to a PostgreSQL database.
    pg_conn_params: dict with keys dbname, user, password, host, port
    """
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    sqlite_cur = sqlite_conn.cursor()

    # Connect to PostgreSQL and sanity check
    pg_conn = psycopg2.connect(**pg_conn_params)
    pg_cur = pg_conn.cursor()
    try:
        pg_cur.execute("SELECT version();")
        version = pg_cur.fetchone()[0]
        print(f"Connected to PostgreSQL: {version}")

        # List all tables in the public schema
        print("\nTables in public schema:")
        pg_cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables = pg_cur.fetchall()
        for t in tables:
            table_name = t[0]
            print(f"  {table_name}")
            # Print columns for each table
            try:
                pg_cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table_name}';")
                columns = pg_cur.fetchall()
                for col, dtype in columns:
                    print(f"    - {col}: {dtype}")
            except Exception as e:
                print(f"    Could not fetch columns for {table_name}: {e}")

        # Print table schemas for expected tables
        print("\nPostgreSQL table schemas (expected tables):")
        for table in ["Authors", "Contents", "Articles", "Tags", "ArticleTags"]:
            try:
                pg_cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table.lower()}';")
                columns = pg_cur.fetchall()
                print(f"Table {table}:")
                for col, dtype in columns:
                    print(f"  {col}: {dtype}")
            except Exception as e:
                print(f"  Could not fetch schema for {table}: {e}")

        # Clear all records from the tables before insertion
        print("\nClearing all records from relevant tables...")
        # clear_postgres_tables(pg_cur, pg_conn)

        # --- INSERT DATA ---
        print("\nCopying data from SQLite to PostgreSQL...")
        table_order = [
            # "Authors",
            # "Contents",
            "Users",
            "Articles",
            # "Tags",
            "ArticleTags"
        ]
        for table in table_order:
            print(f"Copying table {table}...")
            try:
                copy_table_with_column_mapping(sqlite_cur, pg_cur, pg_conn, table, SQLITE_TO_PG_COL_MAP[table])
                print(f"  Done copying {table}")
            except Exception as e:
                print(f"Error copying table {table}: {e}")

    except Exception as e:
        print(f"PostgreSQL sanity check failed: {e}")
    finally:
        sqlite_cur.close()
        sqlite_conn.close()
        pg_cur.close()
        pg_conn.close()
SQLITE_TO_PG_COL_MAP = {
    "Authors": {
        "id": "id",
        "name": "name",
        "email": "email",
        "image": "image",
        "bio": "bio",
        "slug": "slug",
        "created_date": "createdAt",
        "modified_date": "updatedAt"
    },
    "Contents": {
        "id": "id",
        "content": "content"
    },
    "Users": {
        "id": "id",
        "email": "email",
        "name": "name",
        "created_date": "createdAt",
        "modified_date": "updatedAt",
    },
    "Articles": {
        "id": "id",
        "title": "title",
        "subTitle": "subTitle",         # Case-sensitive!
        "shoulder": "shoulder",
        "description": "description",
        "authorId": "authorId",
        "contentId": "contentId",
        "image": "image",
        "imageFolder": "imageFolder",
        "readCount": "readCount",
        "slug": "slug",
        "isPublished": "isPublished",
        "createdById": "createdById",
        "updatedById": "updatedById",
        "created_date": "createdAt",
        "modified_date": "updatedAt",
    },
    "Tags": {
        "id": "id",
        "tag": "tag",
        "slug": "slug"
    },
    "ArticleTags": {
        "articleId": "ArticleId",
        "tagId": "TagId"
    }
}

def copy_table_with_column_mapping(sqlite_cur, pg_cur, pg_conn, table, col_map):
    sqlite_cur.execute(f"SELECT * FROM {table}")
    rows = sqlite_cur.fetchall()
    sqlite_columns = [desc[0] for desc in sqlite_cur.description]

    # Map columns for PostgreSQL
    pg_columns = [col_map[col] for col in sqlite_columns if col in col_map]
    pg_columns_sql = ', '.join(f'"{col}"' for col in pg_columns)
    placeholders = ', '.join(['%s'] * len(pg_columns))

    for row in rows:
        mapped_row = [row[sqlite_columns.index(sql_col)] for sql_col in sqlite_columns if sql_col in col_map]
        # Convert isPublished to boolean if present in Articles
        if table == "Articles" and "isPublished" in pg_columns:
            idx = pg_columns.index("isPublished")
            mapped_row[idx] = bool(mapped_row[idx]) if mapped_row[idx] is not None else False
        try:
            pg_cur.execute(
                f'INSERT INTO "{table}" ({pg_columns_sql}) VALUES ({placeholders})',
                mapped_row
            )
        except Exception as e:
            print(f"Error inserting row into {table}: {e}")
            pg_conn.rollback()
        else:
            pg_conn.commit()

def compare_column_types(sqlite_db_path, pg_conn_params):
    """
    Compare column data types between SQLite and PostgreSQL for each table.
    Reports columns with mismatched or missing types.
    """
    import collections
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg2.connect(**pg_conn_params)
    pg_cur = pg_conn.cursor()

    tables = ["Authors", "Contents", "Users", "Articles", "Tags", "ArticleTags"]
    print("\n--- Column Type Comparison Report ---")
    for table in tables:
        print(f"\nTable: {table}")

        # Get SQLite columns and types
        sqlite_cur.execute(f'PRAGMA table_info({table})')
        sqlite_cols = {row[1]: row[2].upper() for row in sqlite_cur.fetchall()}  # row[1]=name, row[2]=type

        # Get PostgreSQL columns and types
        pg_cur.execute(f'''
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = '{table.lower()}'
            ORDER BY ordinal_position
        ''')
        pg_cols = collections.OrderedDict((row[0], row[1].upper()) for row in pg_cur.fetchall())

        # Compare types
        for col, pg_type in pg_cols.items():
            sqlite_type = sqlite_cols.get(col)
            if sqlite_type is None:
                print(f"  - {col}: present in PostgreSQL ({pg_type}), missing in SQLite")
            elif sqlite_type != pg_type:
                print(f"  - {col}: type mismatch (SQLite: {sqlite_type}, PostgreSQL: {pg_type})")
        for col, sqlite_type in sqlite_cols.items():
            if col not in pg_cols:
                print(f"  - {col}: present in SQLite ({sqlite_type}), missing in PostgreSQL")
        if not pg_cols:
            print("  (No columns found in PostgreSQL for this table)")
    sqlite_cur.close()
    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()
    print("\n--- End of type comparison report ---\n")

    
def main():
    # take two option one to retrieve from wordpress and create sqlite db
    # second to copy from sqlite to postgres
    import argparse 
    parser = argparse.ArgumentParser(description="WordPress to SQLite/PostgreSQL migration tool")
    parser.add_argument('--retrieve', action='store_true', help="Retrieve posts from WordPress and create SQLite database")
    parser.add_argument('--copy', action='store_true', help="Copy data from SQLite to PostgreSQL")
    parser.add_argument('--report-notnull', action='store_true', help="Report NOT NULL columns in PostgreSQL that are nullable or missing in SQLite")
    parser.add_argument('--diff', action='store_true', help="Compare column data types between SQLite and PostgreSQL")

    args = parser.parse_args()

    if args.retrieve:
        retrieve_from_wordpress()
    elif args.copy:
        # Define your SQLite and PostgreSQL connection parameters
        sqlite_db_path = "dump.sqlite"
        pg_conn_params = {
            "dbname": "railway",
            "user": "postgres",
            "password": "FePMWGCGONpzSaWpXOqcstDodnKujLLy",
            "host": "turntable.proxy.rlwy.net",
            "port": "12414"
        }
        # Call the function to copy data
        copy_sqlite_to_postgres(sqlite_db_path, pg_conn_params)
    elif args.report_notnull:
        # Define your SQLite and PostgreSQL connection parameters
        sqlite_db_path = "dump.sqlite"
        pg_conn_params = {
            "dbname": "railway",
            "user": "postgres",
            "password": "FePMWGCGONpzSaWpXOqcstDodnKujLLy",
            "host": "turntable.proxy.rlwy.net",
            "port": "12414"
        }
        report_pg_notnull_columns(sqlite_db_path, pg_conn_params)
    elif args.diff:
        # Define your SQLite and PostgreSQL connection parameters
        sqlite_db_path = "dump.sqlite"
        pg_conn_params = {
            "dbname": "railway",
            "user": "postgres",
            "password": "FePMWGCGONpzSaWpXOqcstDodnKujLLy",
            "host": "turntable.proxy.rlwy.net",
            "port": "12414"
        }
        compare_column_types(sqlite_db_path, pg_conn_params)

if __name__ == "__main__":
    main()