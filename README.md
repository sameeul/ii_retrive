# WordPress Post Retrieval and Database Normalization

This script fetches posts from a WordPress site via the REST API and stores them in a normalized SQLite database structure.

## Database Schema

The script creates a normalized database with the following tables:
- **AUTHOR**: Author information (name, email, bio, avatar)
- **CONTENT**: Post content stored separately
- **ARTICLE**: Main article metadata with foreign key relationships
- **TAG**: Unique tags (combines WordPress categories and tags)
- **ARTICLE_TAG**: Junction table for article-tag relationships
- **USER**: User information for tracking creators/updaters

## Setup Instructions

### 1. Create Virtual Environment

```bash
# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
# On Linux/macOS:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate
```

### 2. Install Required Packages

```bash
# Install all required dependencies
pip install requests pandas beautifulsoup4
```

### 3. Configure the Script

Edit the configuration section in `main.py`:

```python
# ----------------------------
# CONFIG — change these
# ----------------------------
BASE_URL = "https://yourdomain.com"  # Your WordPress site URL
USERNAME = ""                        # Optional: WordPress username
APP_PASSWORD = ""                    # Optional: Application Password
STATUS = "publish"                   # Post status to fetch
PER_PAGE = 50                       # Posts per API request (max 100)
OUT_CSV = "wordpress_posts.csv"     # Summary CSV output
OUT_SQLITE = "dump.sqlite"          # SQLite database output
```

### 4. WordPress Application Password (Optional)

If you need to access private posts or drafts:

1. Log into your WordPress admin dashboard
2. Go to **Users → Your Profile**
3. Scroll down to **Application Passwords**
4. Enter a name (e.g., "Post Retrieval Script")
5. Click **Add New Application Password**
6. Copy the generated password and add it to the config

### 5. Run the Script

```bash
# Make sure your virtual environment is activated
python main.py
```

## Output

The script will create:
- **SQLite database** (`dump.sqlite`): Normalized database with all post data
- **CSV file** (`wordpress_posts.csv`): Summary of processed posts
- **Console output**: Progress information and statistics

## Features

- **Normalized Database Structure**: Separates content, authors, tags, and articles
- **Error Handling**: Continues processing even if individual posts fail
- **Progress Tracking**: Shows processing progress every 10 posts
- **Embedded Data**: Extracts author info, featured images, and tags
- **HTML Content**: Preserves original HTML content and creates text excerpts
- **Foreign Key Relationships**: Maintains data integrity with proper relationships

## Troubleshooting

### Common Issues

1. **Import Error**: Make sure all packages are installed in your virtual environment
2. **Connection Timeout**: Increase timeout value in the `session.get()` call
3. **Rate Limiting**: Reduce `PER_PAGE` value if you encounter rate limits
4. **SSL Errors**: Add `verify=False` to requests (not recommended for production)

### Debug Mode

To see more detailed error information, modify the exception handling:

```python
except Exception as e:
    import traceback
    print(f"Error processing post {post.get('id', 'unknown')}: {e}")
    traceback.print_exc()
```

## Database Queries

Once the data is imported, you can query the database:

```sql
-- Get all articles with author names
SELECT a.title, au.name as author, a.created_date 
FROM ARTICLE a 
JOIN AUTHOR au ON a.authorId = au.id;

-- Get articles with their tags
SELECT a.title, GROUP_CONCAT(t.tag) as tags
FROM ARTICLE a
JOIN ARTICLE_TAG at ON a.id = at.articleId
JOIN TAG t ON at.tagId = t.id
GROUP BY a.id, a.title;

-- Get content for a specific article
SELECT a.title, c.content
FROM ARTICLE a
JOIN CONTENT c ON a.contentId = c.id
WHERE a.slug = 'your-post-slug';
```

## Deactivating Virtual Environment

When you're done:

```bash