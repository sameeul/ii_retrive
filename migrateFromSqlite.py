import sqlite3
import psycopg2
import requests
import os
import re
from bs4 import BeautifulSoup
from PIL import Image
from slugify import slugify
from datetime import datetime
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import traceback
import html

# --- Database Configuration ---
# Replace with your SQLite file path
SQLITE_DB_PATH = 'dump.sqlite'

# Replace with your PostgreSQL connection details
POSTGRES_DB_NAME = 'IndustryInsider2'
POSTGRES_USER = 'postgres'
POSTGRES_PASSWORD = 'MinhajPostgres'
POSTGRES_HOST = 'localhost'
POSTGRES_PORT = '5432'

default_email = "default@email.com"

# --- File System Configuration ---
# Base directory for all downloaded article images
BASE_IMAGE_DIR = './images'
ARTICLE_IMAGE_DIR = os.path.join(BASE_IMAGE_DIR, 'articles')
CONTENT_IMAGE_DIR = os.path.join(BASE_IMAGE_DIR, 'articleContents')

def connect_dbs():
    """Establishes connections to SQLite and PostgreSQL databases."""
    try:
        sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
        print("Connected to SQLite database successfully.")
    except sqlite3.Error as e:
        print(f"Error connecting to SQLite: {e}")
        return None, None

    try:
        postgres_conn = psycopg2.connect(
            dbname=POSTGRES_DB_NAME,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT
        )
        print("Connected to PostgreSQL database successfully.")
        return sqlite_conn, postgres_conn
    except psycopg2.OperationalError as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return sqlite_conn, None

def create_directories():
    """Creates the necessary local directories for storing images."""
    if not os.path.exists(ARTICLE_IMAGE_DIR):
        os.makedirs(ARTICLE_IMAGE_DIR)
        print(f"Created directory: {ARTICLE_IMAGE_DIR}")
    if not os.path.exists(CONTENT_IMAGE_DIR):
        os.makedirs(CONTENT_IMAGE_DIR)
        print(f"Created directory: {CONTENT_IMAGE_DIR}")

def download_and_save_image(url, destination_folder, filename=None, convert_to_webp=False):
    """
    Downloads an image from a URL and saves it.
    If convert_to_webp=True → saves as .webp
    Returns final absolute file path or None.
    """
    if not url or not url.startswith(("http://", "https://")):
        print(f"❌ Invalid URL: {url}")
        return None

    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        image_bytes = response.content
    except Exception as e:
        print(f"❌ Failed to download: {url} → {e}")
        return None

    # ----------------------------
    # Filename handling
    # ----------------------------
    if not filename:
        filename = os.path.basename(urlparse(url).path)
        filename = re.sub(r"[^a-zA-Z0-9_.-]", "", filename)
        if not filename:
            filename = "image.jpg"

    os.makedirs(destination_folder, exist_ok=True)

    # ----------------------------
    # Convert to WebP if needed
    # ----------------------------
    if convert_to_webp:
        temp_path = os.path.join(destination_folder, filename + ".tmp")

        try:
            with open(temp_path, "wb") as f:
                f.write(image_bytes)

            img = Image.open(temp_path).convert("RGB")
            webp_filename = os.path.splitext(filename)[0] + ".webp"
            webp_path = os.path.join(destination_folder, webp_filename)

            img.save(webp_path, "webp", quality=85)
            os.remove(temp_path)

            print(f"✅ Converted to WEBP: {webp_path}")
            return webp_path

        except Exception as e:
            print(f"❌ WEBP conversion failed for {url}: {e}")
            traceback.print_exc()
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return None

    # ----------------------------
    # Save as original (no conversion)
    # ----------------------------
    try:
        full_path = os.path.join(destination_folder, filename)
        with open(full_path, "wb") as f:
            f.write(image_bytes)

        print(f"✅ Image saved: {full_path}")
        return full_path

    except Exception as e:
        print(f"❌ Failed to save image {filename}: {e}")
        return None

def migrate_tags(sqlite_cur, postgres_cur, postgres_conn):
    """Migrates tags from SQLite to PostgreSQL, adding a slug."""
    print("\n--- Migrating Tags ---")
    sqlite_cur.execute("SELECT id, tag FROM Tag")
    tags = sqlite_cur.fetchall()
    
    for tag_id, tag in tags:
        # Generate slug from the tag name
        slug = slugify(tag)
        try:
            postgres_cur.execute(
                'INSERT INTO "Tags" (id, tag, slug) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING',
                (tag_id, tag, slug)
            )
            print(f"Added tag: {tag}")
        except psycopg2.Error as e:
            print(f"Error adding tag {tag}: {e}")
            postgres_conn.rollback()

    postgres_conn.commit()
    print("Tag migration complete.")

def migrate_authors(sqlite_cur, postgres_cur, postgres_conn, default_email="default@email.com"):
    """Migrates authors from SQLite to PostgreSQL with proper error handling."""
    print("\n--- Migrating Authors ---")
    try:
        sqlite_cur.execute("SELECT id, name, bio, slug FROM Author")
        authors = sqlite_cur.fetchall()
    except sqlite3.Error as e:
        print(f"❌ SQLite error while fetching authors: {e}")
        return

    for author_id, name, bio, slug in authors:
        try:
            dhaka_tz = timezone(timedelta(hours=6))
            now = datetime.now(dhaka_tz)
            postgres_cur.execute(
                'INSERT INTO "Authors" (id, name, bio, slug, "createdAt", "updatedAt") VALUES (%s, %s, %s, %s, %s, %s) '
                'ON CONFLICT (id) DO NOTHING',
                (author_id, name, bio, slug,  now, now)
            )
            print(f"✅ Added author: {name} (id={author_id})")

        except psycopg2.IntegrityError as e:
            postgres_conn.rollback()
            print(f"⚠️ Integrity error for author {name} (id={author_id}): {e}")
        except psycopg2.ProgrammingError as e:
            postgres_conn.rollback()
            print(f"⚠️ Programming error (check SQL/columns) for author {name}: {e}")
        except psycopg2.Error as e:
            postgres_conn.rollback()
            print(f"❌ General Postgres error for author {name}: {e}")

    try:
        postgres_conn.commit()
        print("🎉 Author migration complete.")
    except psycopg2.Error as e:
        print(f"❌ Failed to commit author migration: {e}")
        postgres_conn.rollback()


def migrate_articles_and_content(sqlite_cur, postgres_cur, postgres_conn):
    print("\n--- Migrating Articles & Content ---")

    try:
        sqlite_cur.execute("""
            SELECT Article.id, Article.title, Article.slug, Article.image,
                   Article."subTitle", Article.description, Article."authorId",
                   Article."createdAt", Article."updatedAt",
                   Content.id, Content.Content
            FROM Article
            JOIN Content ON Article."contentId" = Content.id
        """)
        articles_data = sqlite_cur.fetchall()

    except Exception:
        print("❌ SQLite fetch error")
        traceback.print_exc()
        return

    # --------------------------------------------------------------------
    # MAIN LOOP — process all articles
    # --------------------------------------------------------------------
    for (id, title, slug, image_url, subTitle, description,
         author_id, created_at, updated_at, content_id, content_html) in articles_data:

        print(f"\n📝 Processing: {title} (ID {id})")

        try:
            folder_name = slug if slug else str(id)
            article_content_folder = os.path.join(CONTENT_IMAGE_DIR, folder_name)
            os.makedirs(article_content_folder, exist_ok=True)

            # --------------------------------------------------------
            # ARTICLE FEATURE IMAGE
            # --------------------------------------------------------
            new_image_path = None
            if image_url:
                filename = os.path.basename(urlparse(image_url).path)
                saved_path = download_and_save_image(
                    image_url,
                    ARTICLE_IMAGE_DIR,
                    filename=filename,
                    convert_to_webp=False
                )

                if saved_path:
                    new_image_path = os.path.join("articles", os.path.basename(saved_path))

            # --------------------------------------------------------
            # CONTENT HTML REWRITE
            # --------------------------------------------------------
            soup = BeautifulSoup(content_html or "", "html.parser")

            for img_tag in soup.find_all("img"):
                src = img_tag.get("src")
                if not src:
                    continue

                saved_path = download_and_save_image(
                    src,
                    article_content_folder,
                    convert_to_webp=True
                )

                if saved_path:
                    new_src = f"articleContents/{folder_name}/{os.path.basename(saved_path)}"
                    img_tag["src"] = new_src

                # Fix image alignment classes
                classes = img_tag.get("class", [])
                fixed_classes = []

                for cls in classes:
                    if cls == "alignright": fixed_classes.append("image-right")
                    elif cls == "alignleft": fixed_classes.append("image-left")
                    elif cls == "aligncenter": fixed_classes.append("image-center")
                    else: fixed_classes.append(cls)

                img_tag["class"] = fixed_classes

            modified_content_html = str(soup)

            # --------------------------------------------------------
            # INSERT INTO CONTENT TABLE
            # --------------------------------------------------------
            try:
                postgres_cur.execute("""
                    INSERT INTO "Contents" (id, content)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content
                """, (content_id, modified_content_html))

            except Exception:
                print(f"❌ Content insert failed for article {id}")
                traceback.print_exc()
                postgres_conn.rollback()
                continue

            # --------------------------------------------------------
            # INSERT INTO ARTICLES TABLE
            # --------------------------------------------------------
            try:
                postgres_cur.execute("""
                    INSERT INTO "Articles"
                    (id, title, slug, description, image, "authorId",
                     "contentId", "imageFolder", "isPublished",
                     "createdById", "updatedById",
                     "createdAt", "updatedAt", "subTitle")
                    VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, TRUE,
                     1, 1, %s, %s, %s)
                    ON CONFLICT (id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        image = EXCLUDED.image,
                        "updatedAt" = EXCLUDED."updatedAt"
                """, (
                    id,
                    html.unescape(title),
                    slug,
                    description,
                    new_image_path,
                    author_id,
                    content_id,
                    folder_name,
                    created_at,
                    updated_at,
                    subTitle
                ))

            except Exception:
                print(f"❌ Article insert failed for ID {id}")
                traceback.print_exc()
                postgres_conn.rollback()

        except Exception:
            print(f"⚠️ Unexpected error on article {id}")
            traceback.print_exc()
            postgres_conn.rollback()

    # --------------------------------------------------------
    # FINAL COMMIT
    # --------------------------------------------------------
    try:
        postgres_conn.commit()
        print("\n🎉 Migration completed successfully!")

    except Exception:
        print("❌ Commit failed!")
        traceback.print_exc()
        postgres_conn.rollback()

def migrate_articletags(sqlite_cur, postgres_cur, postgres_conn):
    """Migrates the many-to-many relationship between articles and tags."""
    print("\n--- Migrating Article Tags ---")
    sqlite_cur.execute('SELECT "articleId", "tagId" FROM "Article_Tag"')
    article_tags = sqlite_cur.fetchall()
    
    for article_id, tag_id in article_tags:
        try:
            postgres_cur.execute(
                'INSERT INTO "ArticleTags" ("ArticleId", "TagId") VALUES (%s, %s) ON CONFLICT DO NOTHING',
                (article_id, tag_id)
            )
        except psycopg2.Error as e:
            print(f"Error adding ArticleTag for ArticleID {article_id} and TagID {tag_id}: {e}")
            postgres_conn.rollback()
    
    postgres_conn.commit()
    print("ArticleTag migration complete.")


def fix_postgres_sequences(postgres_conn, table_column_map):
    """
    Fixes the sequences for given tables so the next value is set to MAX(id) + 1.
    
    Parameters:
        postgres_conn : psycopg2 connection object
        table_column_map : dict
            Dictionary where key=table name (case-sensitive), value=primary key column name
            Example: {"Articles": "id", "Contents": "id", "Authors": "id"}
    """
    try:
        cursor = postgres_conn.cursor()
        for table, column in table_column_map.items():
            # Get sequence name for case-sensitive table and column
            cursor.execute(f'SELECT pg_get_serial_sequence(\'"{table}"\', \'{column}\');')
            seq_name = cursor.fetchone()[0]
            if seq_name:
                # Get max id from table
                cursor.execute(f'SELECT COALESCE(MAX("{column}"), 0) FROM "{table}";')
                max_id = cursor.fetchone()[0]
                # Set sequence to max_id
                cursor.execute(f'SELECT setval(\'{seq_name}\', {max_id});')
                print(f"✅ Updated sequence for {table}.{column} to start from {max_id + 1}")
            else:
                print(f"⚠️ No sequence found for {table}.{column}, skipping.")
        postgres_conn.commit()
        cursor.close()
        print("🎉 All sequences updated successfully!")
    except Exception as e:
        print("❌ Error while updating sequences:", e)
        postgres_conn.rollback()






def main():
    # Create local directories first
    create_directories()

    # Connect to databases
    sqlite_conn, postgres_conn = connect_dbs()
    if not sqlite_conn or not postgres_conn:
        return

    try:
        sqlite_cur = sqlite_conn.cursor()
        postgres_cur = postgres_conn.cursor()

        # Execute migration steps in the correct order
        migrate_authors(sqlite_cur, postgres_cur, postgres_conn)
        migrate_tags(sqlite_cur, postgres_cur, postgres_conn)
        migrate_articles_and_content(sqlite_cur, postgres_cur, postgres_conn)
        migrate_articletags(sqlite_cur, postgres_cur, postgres_conn)

        print("\nAll data migration completed successfully.")


        table_column_map = {
            "Articles": "id",
            "Contents": "id",
            "Authors": "id",
            "Tags": "id"
        }

        fix_postgres_sequences(postgres_conn, table_column_map)

    except Exception as e:
        print(f"An unexpected error occurred during migration: {e}")
        postgres_conn.rollback()

    finally:
        if sqlite_conn:
            sqlite_conn.close()
        if postgres_conn:
            postgres_conn.close()

if __name__ == "__main__":
    main()
