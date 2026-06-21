import os
import time
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from html.parser import HTMLParser
from html import escape

# Load .env file if present
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ[key] = val

# Configure settings from Environment Variables for Security
# Fail-fast: Stop execution if LIGHTNOVEL_SECRET_KEY is not defined in production/environment
SECRET_KEY = os.environ.get("LIGHTNOVEL_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("CRITICAL SECURITY ERROR: LIGHTNOVEL_SECRET_KEY environment variable is NOT set. Run failed to start. You must set a strong, unique secret key.")

DATABASE_FILE = os.environ.get("LIGHTNOVEL_DATABASE_FILE", "lightnovel.db")
ALLOWED_CORS_ORIGINS = os.environ.get("LIGHTNOVEL_CORS_ORIGINS", "")

app = Flask(__name__, static_url_path='', static_folder='../lightnovel-native')

# Restrict CORS to specific configurations (Disabled by default for production safety)
if not ALLOWED_CORS_ORIGINS:
    # No CORS allowed by default
    pass
elif ALLOWED_CORS_ORIGINS == "*":
    # Developer warning
    print("[WARNING] CORS is configured to allow ALL origins (*). Do not use this in production!")
    CORS(app)
else:
    CORS(app, origins=ALLOWED_CORS_ORIGINS.split(","))

# Initialize Limiter
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["2000 per hour", "100 per minute"],
    storage_uri=os.environ.get("LIGHTNOVEL_REDIS_URL", "memory://"),
    enabled=not (os.environ.get("LIGHTNOVEL_DEBUG", "False").lower() == "true")
)

@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if not app.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "frame-ancestors 'none';"
    )
    return response


@app.route('/')
@app.route('/index')
def index_page():
    return app.send_static_file('index.html')

serializer = URLSafeTimedSerializer(SECRET_KEY)

# --- SECURITY UTILITIES: HTML SANITIZATION (XSS PROTECTION) ---
class SafeHTMLParser(HTMLParser):
    def __init__(self, allowed_tags, allowed_attrs):
        super().__init__()
        self.allowed_tags = allowed_tags
        self.allowed_attrs = allowed_attrs
        self.result = []
        self.tag_stack = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        is_allowed = tag_lower in self.allowed_tags
        self.tag_stack.append((tag_lower, is_allowed))
        
        if is_allowed:
            filtered_attrs = []
            for attr, val in attrs:
                attr_lower = attr.lower()
                if attr_lower in self.allowed_attrs.get(tag_lower, []):
                    val_lower = val.lower().strip()
                    # Prevent dangerous protocols in href/src
                    if attr_lower in ['href', 'src'] and any(val_lower.startswith(p) for p in ['javascript:', 'data:', 'vbscript:', 'file:']):
                        continue
                    # Safe check style attribute
                    if attr_lower == 'style':
                        if any(x in val_lower for x in ['javascript:', 'expression', 'url(', 'behavior']):
                            continue
                    filtered_attrs.append(f'{attr_lower}="{escape(val)}"')
            
            attrs_str = f" {' '.join(filtered_attrs)}" if filtered_attrs else ""
            self.result.append(f"<{tag_lower}{attrs_str}>")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        while self.tag_stack:
            t_name, t_allowed = self.tag_stack.pop()
            if t_name == tag_lower:
                if t_allowed:
                    self.result.append(f"</{tag_lower}>")
                break

    def handle_data(self, data):
        # Skip script/style block contents
        if any(t[0] in ['script', 'style', 'iframe', 'object', 'embed', 'noscript'] for t in self.tag_stack):
            return
        self.result.append(escape(data))

def sanitize_html(html_content, allowed_tags=None, allowed_attrs=None):
    if not html_content:
        return ""
    if allowed_tags is None:
        allowed_tags = {'p', 'br', 'strong', 'em', 'span', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li'}
    if allowed_attrs is None:
        allowed_attrs = {
            'span': {'style', 'class'},
            'div': {'style', 'class'},
            'p': {'style', 'class'},
            'h1': {'style'}, 'h2': {'style'}, 'h3': {'style'}, 'h4': {'style'},
            'ul': {'style'}, 'ol': {'style'}, 'li': {'style'}
        }
    parser = SafeHTMLParser(allowed_tags, allowed_attrs)
    parser.feed(html_content)
    return "".join(parser.result)


from urllib.parse import urlparse

# Detect Database Type
db_url = os.environ.get("LIGHTNOVEL_DATABASE_URL")
if db_url and (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
    DB_TYPE = "postgresql"
    parsed = urlparse(db_url)
    DB_USER = parsed.username
    DB_PASSWORD = parsed.password
    DB_HOST = parsed.hostname
    DB_PORT = parsed.port or 5432
    DB_NAME = parsed.path.lstrip('/')
else:
    DB_TYPE = "sqlite"

class PgRow(dict):
    """A dictionary subclass that mimics sqlite3.Row for postgresql."""
    def __init__(self, row_dict):
        super().__init__(row_dict)
        
    def __getitem__(self, key):
        if isinstance(key, int):
            # Support positional index row[0]
            try:
                return self[list(self.keys())[key]]
            except IndexError:
                raise IndexError("row index out of range")
        return super().__getitem__(key)

class PgCursorWrapper:
    def __init__(self, raw_cursor):
        self.cursor = raw_cursor

    def execute(self, query, params=None):
        # Translate placeholder ? to %s
        query = query.replace("?", "%s")
        
        # Translate SQLITE specific commands to PostgreSQL
        if "INSERT OR IGNORE INTO users" in query:
            query = query.replace("INSERT OR IGNORE INTO users", "INSERT INTO users") + " ON CONFLICT (username) DO NOTHING"
        elif "INSERT OR IGNORE INTO ads" in query:
            query = query.replace("INSERT OR IGNORE INTO ads", "INSERT INTO ads") + " ON CONFLICT (zone) DO NOTHING"
        elif "INSERT OR IGNORE INTO genres" in query:
            query = query.replace("INSERT OR IGNORE INTO genres", "INSERT INTO genres") + " ON CONFLICT (novel_id, genre) DO NOTHING"
        elif "INSERT OR IGNORE INTO novel_assignments" in query:
            query = query.replace("INSERT OR IGNORE INTO novel_assignments", "INSERT INTO novel_assignments") + " ON CONFLICT (novel_id, user_id, role) DO NOTHING"
        elif "INSERT OR IGNORE INTO novels" in query:
            query = query.replace("INSERT OR IGNORE INTO novels", "INSERT INTO novels") + " ON CONFLICT (id) DO NOTHING"
        elif "INSERT OR IGNORE INTO chapters" in query:
            query = query.replace("INSERT OR IGNORE INTO chapters", "INSERT INTO chapters") + " ON CONFLICT (id) DO NOTHING"
            
        elif "INSERT OR REPLACE INTO ads" in query:
            query = "INSERT INTO ads (zone, ad_code, is_active) VALUES (%s, %s, %s) ON CONFLICT (zone) DO UPDATE SET ad_code = EXCLUDED.ad_code, is_active = EXCLUDED.is_active"
        elif "INSERT OR REPLACE INTO novel_assignments" in query:
            query = "INSERT INTO novel_assignments (novel_id, user_id, role) VALUES (%s, %s, %s) ON CONFLICT (novel_id, user_id, role) DO UPDATE SET role = EXCLUDED.role"
            
        if "INTEGER PRIMARY KEY AUTOINCREMENT" in query:
            query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            
        if "PRAGMA" in query:
            if "table_info" in query:
                import re
                match = re.search(r"table_info\((.*?)\)", query)
                if match:
                    table_name = match.group(1).strip()
                    query = f"SELECT column_name AS name FROM information_schema.columns WHERE table_name = '{table_name}'"
                else:
                    query = "SELECT 1 AS dummy"
            else:
                query = "SELECT 1 AS dummy"

        # Execute query
        if params is not None:
            if isinstance(params, list):
                params = tuple(params)
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)

    def _wrap_row(self, row):
        if row is None:
            return None
        desc = self.cursor.description
        if not desc:
            return row
        row_dict = {}
        for i, col in enumerate(desc):
            col_name = col[0]
            if isinstance(col_name, bytes):
                col_name = col_name.decode('utf-8')
            row_dict[col_name] = row[i]
        return PgRow(row_dict)

    def fetchone(self):
        row = self.cursor.fetchone()
        return self._wrap_row(row)

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [self._wrap_row(r) for r in rows]

    @property
    def lastrowid(self):
        return None

    def close(self):
        self.cursor.close()

import queue

class PgConnectionPool:
    def __init__(self, size=10):
        self.pool = queue.Queue(maxsize=size)
        
    def get_conn(self):
        try:
            conn_wrapper = self.pool.get_nowait()
            try:
                cur = conn_wrapper.conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
            except Exception:
                import pg8000
                raw = pg8000.connect(
                    user=DB_USER,
                    password=DB_PASSWORD,
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME
                )
                conn_wrapper = PgConnectionWrapper(raw, self)
        except queue.Empty:
            import pg8000
            raw = pg8000.connect(
                user=DB_USER,
                password=DB_PASSWORD,
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME
            )
            conn_wrapper = PgConnectionWrapper(raw, self)
        return conn_wrapper
        
    def release_connection(self, conn_wrapper):
        try:
            self.pool.put_nowait(conn_wrapper)
        except queue.Full:
            conn_wrapper.conn.close()

class PgConnectionWrapper:
    def __init__(self, raw_conn, pool=None):
        self.conn = raw_conn
        self.pool = pool

    def cursor(self):
        return PgCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        if self.pool:
            self.pool.release_connection(self)
        else:
            self.conn.close()


import redis
import json
import threading

redis_url = os.environ.get("LIGHTNOVEL_REDIS_URL")

class RedisCache:
    def __init__(self, url):
        self.client = redis.from_url(url)
        
    def get(self, key):
        try:
            val = self.client.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            pass
        return None
        
    def set(self, key, value, ttl=300):
        try:
            self.client.setex(key, ttl, json.dumps(value))
        except Exception as e:
            pass
            
    def delete(self, key):
        try:
            self.client.delete(key)
        except Exception as e:
            pass
            
    def incr(self, key):
        try:
            return self.client.incr(key)
        except Exception as e:
            pass
        return None

class MockCache:
    def __init__(self):
        self.data = {}
        self.views = {}
        
    def get(self, key):
        return self.data.get(key)
        
    def set(self, key, value, ttl=300):
        self.data[key] = value
        
    def delete(self, key):
        if key in self.data:
            del self.data[key]
            
    def incr(self, key):
        self.views[key] = self.views.get(key, 0) + 1
        return self.views[key]

if redis_url:
    cache = RedisCache(redis_url)
    print("[INFO] Redis Cache initialized.")
else:
    cache = MockCache()
    print("[INFO] Redis URL not set. Using Mock memory cache.")


def flush_views_to_db():
    if os.environ.get("LIGHTNOVEL_NO_FLUSHER", "False").lower() == "true":
        return
    # Detect Gunicorn worker environment - disable automatic daemon thread to prevent multiple worker flushers
    import sys
    is_gunicorn = any("gunicorn" in arg for arg in sys.argv) or "gunicorn" in sys.modules
    if is_gunicorn:
        print("[INFO] Gunicorn detected. Background thread view flusher is disabled. Run 'flask flush-views' via a cron job/celery task.")
        return

    while True:
        time.sleep(60)
        try:
            flush_views_once()
        except Exception:
            pass

def flush_views_once():
    views_to_update = {}
    if isinstance(cache, RedisCache):
        # Use SCAN instead of KEYS to prevent event-loop blockage
        keys = []
        cursor = 0
        while True:
            cursor, chunk = cache.client.scan(cursor, match="novel:views:*", count=100)
            keys.extend(chunk)
            if cursor == 0:
                break
        for k in keys:
            k_str = k.decode('utf-8') if isinstance(k, bytes) else k
            novel_id = k_str.split(":")[-1]
            val = cache.client.getset(k, 0)
            if val:
                count = int(val)
                if count > 0:
                    views_to_update[novel_id] = count
    else:
        for k, v in list(cache.views.items()):
            novel_id = k.split(":")[-1]
            if v > 0:
                views_to_update[novel_id] = v
                cache.views[k] = 0
                
    if views_to_update:
        conn = get_db_connection()
        cursor = conn.cursor()
        for nid, count in views_to_update.items():
            cursor.execute("UPDATE novels SET views = views + ? WHERE id = ?", (count, nid))
        conn.commit()
        conn.close()

flush_thread = threading.Thread(target=flush_views_to_db, daemon=True)
flush_thread.start()


# --- CACHE INVALIDATION HELPERS ---
def invalidate_novel_cache(novel_id):
    if novel_id:
        cache.delete(f"novel:detail:{novel_id}")
    cache.delete("homepage:latest")

def invalidate_chapter_cache(chapter_id, novel_id=None):
    if chapter_id:
        cache.delete(f"chapter:public:{chapter_id}")
    if novel_id:
        invalidate_novel_cache(novel_id)


# Initialize PostgreSQL Connection Pool
pg_pool = None
if DB_TYPE == "postgresql":
    pg_pool = PgConnectionPool(size=15)

# --- DATABASE HELPERS ---
def get_db_connection():
    if DB_TYPE == "postgresql":
        return pg_pool.get_conn()
    else:
        conn = sqlite3.connect(DATABASE_FILE, timeout=20.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    db_exists = os.path.exists(DATABASE_FILE)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create Tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'Free', -- 'Free', 'VIP', 'Admin'
            vip_expires_at TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS novels (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            alt_title TEXT,
            cover TEXT,
            author TEXT,
            artist TEXT,
            type TEXT, -- 'رواية ويب', 'رواية خفيفة'
            status TEXT, -- 'مستمرة', 'مكتملة'
            rating REAL DEFAULT 5.0,
            views INTEGER DEFAULT 0,
            followers INTEGER DEFAULT 0,
            native_language TEXT,
            released TEXT,
            updated_on TEXT,
            synopsis TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS genres (
            novel_id TEXT,
            genre TEXT,
            FOREIGN KEY (novel_id) REFERENCES novels (id) ON DELETE CASCADE,
            PRIMARY KEY (novel_id, genre)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chapters (
            id TEXT PRIMARY KEY,
            novel_id TEXT,
            volume_number INTEGER,
            volume_title TEXT,
            chapter_number INTEGER,
            title TEXT,
            release_date TEXT,
            content TEXT,
            is_locked INTEGER DEFAULT 0, -- 0 = Free, 1 = Locked (VIP only)
            FOREIGN KEY (novel_id) REFERENCES novels (id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            user_id INTEGER,
            novel_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (novel_id) REFERENCES novels (id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, novel_id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            novel_id TEXT,
            chapter_id TEXT,
            timestamp TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (novel_id) REFERENCES novels (id) ON DELETE CASCADE,
            FOREIGN KEY (chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            zone TEXT PRIMARY KEY, -- 'header', 'sidebar', 'reader'
            ad_code TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    
    # Create novel team assignments table (Ownership and assignment controls)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS novel_assignments (
            novel_id TEXT,
            user_id INTEGER,
            role TEXT, -- 'Publisher', 'Translator', 'Reviewer'
            FOREIGN KEY (novel_id) REFERENCES novels (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            PRIMARY KEY (novel_id, user_id, role)
        )
    """)

    # Create audit logs table (Security compliance logging)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
        )
    """)

    # Safe column migrations for chapters table
    cursor.execute("PRAGMA table_info(chapters)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "status" not in columns:
        cursor.execute("ALTER TABLE chapters ADD COLUMN status TEXT DEFAULT 'Published'")
    if "published_at" not in columns:
        cursor.execute("ALTER TABLE chapters ADD COLUMN published_at TEXT")
    if "scheduled_at" not in columns:
        cursor.execute("ALTER TABLE chapters ADD COLUMN scheduled_at TEXT")

    # Create production indexes for performance optimization
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapters_novel_status ON chapters(novel_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapters_novel_vol_ch ON chapters(novel_id, volume_number, chapter_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_novels_updated_views ON novels(updated_on, views)")

    conn.commit()

    # Populate Mock Data if DB is new
    cursor.execute("SELECT COUNT(*) FROM novels")
    if cursor.fetchone()[0] == 0:
        print("Populating initial database with mock data...")
        populate_initial_data(conn)
    else:
        # Run XSS cleaning migration on existing legacy content to ensure security compliance
        print("Running security migration to sanitize existing database contents...")
        cursor.execute("SELECT id, content FROM chapters")
        chapters = cursor.fetchall()
        for ch in chapters:
            cleaned = sanitize_html(ch["content"])
            if cleaned != ch["content"]:
                cursor.execute("UPDATE chapters SET content = ? WHERE id = ?", (cleaned, ch["id"]))
                
        cursor.execute("SELECT zone, ad_code FROM ads")
        ads = cursor.fetchall()
        for ad in ads:
            cleaned = sanitize_html(ad["ad_code"], allowed_tags={'div', 'p', 'span', 'a', 'img', 'br'}, allowed_attrs={
                'div': {'style', 'class'},
                'p': {'style', 'class'},
                'span': {'style', 'class'},
                'a': {'href', 'target', 'style', 'class'},
                'img': {'src', 'alt', 'style', 'class'}
            })
            if cleaned != ad["ad_code"]:
                cursor.execute("UPDATE ads SET ad_code = ? WHERE zone = ?", (cleaned, ad["zone"]))
        conn.commit()
        
    conn.close()


def populate_initial_data(conn):
    cursor = conn.cursor()
    
    # Insert Default Admin User from Environment Variables for Security
    # In secure environments, fail-fast if default insecure configurations are used
    admin_user = os.environ.get("LIGHTNOVEL_ADMIN_USER")
    admin_email = os.environ.get("LIGHTNOVEL_ADMIN_EMAIL", "admin@novelsparadise.site")
    admin_password = os.environ.get("LIGHTNOVEL_ADMIN_PASSWORD")
    
    # Check if security credentials are unset or default
    is_development = os.environ.get("LIGHTNOVEL_DEBUG", "False").lower() == "true"
    
    if not admin_user or not admin_password:
        if is_development:
            # Fallback allowed ONLY in debug/development mode for testing
            admin_user = "admin"
            admin_password = "admin123"
            print("[WARNING] SECURITY: Using default admin credentials (admin/admin123) in development mode. Set LIGHTNOVEL_ADMIN_USER and LIGHTNOVEL_ADMIN_PASSWORD for production!")
        else:
            raise ValueError("CRITICAL SECURITY ERROR: Admin user and password credentials must be set in environment variables (LIGHTNOVEL_ADMIN_USER & LIGHTNOVEL_ADMIN_PASSWORD) for production!")
            
    if admin_user.lower() == "admin" and admin_password == "admin123" and not is_development:
        raise ValueError("CRITICAL SECURITY ERROR: Cannot use default admin credentials ('admin' / 'admin123') in production mode.")
        
    admin_pass = generate_password_hash(admin_password)
    cursor.execute(
        "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (admin_user, admin_email, admin_pass, "Admin")
    )


    
    # Insert Initial Ads
    cursor.execute("INSERT OR IGNORE INTO ads (zone, ad_code, is_active) VALUES (?, ?, ?)", 
                   ("header", '<div style="background:rgba(99,102,241,0.1); border:1px dashed var(--primary); padding:1rem; text-align:center; border-radius:8px; margin-bottom:2rem;"><p style="color:var(--primary); font-weight:bold;">مساحة إعلانية علوية (تختفي للمشتركين VIP)</p></div>', 1))
    cursor.execute("INSERT OR IGNORE INTO ads (zone, ad_code, is_active) VALUES (?, ?, ?)", 
                   ("sidebar", '<div style="background:rgba(168,85,247,0.1); border:1px dashed var(--secondary); padding:2rem 1rem; text-align:center; border-radius:12px;"><p style="color:var(--secondary); font-weight:bold;">إعلان جانبي ممول</p></div>', 1))
    cursor.execute("INSERT OR IGNORE INTO ads (zone, ad_code, is_active) VALUES (?, ?, ?)", 
                   ("reader", '<div style="background:rgba(244,63,94,0.1); border:1px dashed var(--accent); padding:1.5rem; text-align:center; border-radius:8px; margin:2rem 0;"><p style="color:var(--accent); font-weight:bold;">إعلان داخل القارئ (ادعمنا بالاشتراك لإزالته)</p></div>', 1))

    # Insert Mock Novels and Chapters (based on data.js)
    mock_novels = [
      {
        "id": "shadow-alchemist",
        "title": "الخيميائي الظل",
        "alt_title": "The Shadow Alchemist",
        "cover": "assets/shadow_alchemist.png",
        "author": "إيلينا روستوفا",
        "artist": "شينغو ك.",
        "type": "رواية ويب",
        "status": "مستمرة",
        "rating": 4.85,
        "views": 14250,
        "followers": 1205,
        "native_language": "اليابانية",
        "released": "2024",
        "updated_on": "يونيو 20, 2026",
        "synopsis": "في عالم تحكمه قوانين صارمة للضوء في استخدام الخيمياء، يكتشف ليو دفتراً قديماً ومنسياً لوالده الراحل. يشرح الدفتر تفاصيل فن خيمياء الظلال المحرم...",
        "genres": ["اكشن", "خيال", "مغامره", "سحر"],
        "chapters": [
          {
            "id": "shadow-alchemist-v1-ch1",
            "volume_number": 1,
            "volume_title": "الدفتر المحرم",
            "chapter_number": 1,
            "title": "همسات الظلام",
            "release_date": "2026-06-19",
            "is_locked": 0,
            "content": "<p>كان المطر يقرع بإيقاع لا يهدأ على الزجاج الملون المتصدع لمختبر الخيمياء...</p><p>فجأة، تمددت الظلال في زوايا الغرفة...</p>"
          },
          {
            "id": "shadow-alchemist-v1-ch2",
            "volume_number": 1,
            "volume_title": "الدفتر المحرم",
            "chapter_number": 2,
            "title": "كسوف متكافئ",
            "release_date": "2026-06-20",
            "is_locked": 1, # Locked for VIP demonstration
            "content": "<p>حدق ليو في لهب الشمعة المتجمد. لقد فقد العالم صوته وحركته...</p><p>خيمياء الظل، أدرك ليو، وجف حلقه...</p>"
          }
        ]
      },
      {
        "id": "immortality-investment",
        "title": "الخلود يبدأ بالاستثمار",
        "alt_title": "Immortality Starts with Investment",
        "cover": "assets/immortality_investment.png",
        "author": "تشينغ فنغ",
        "artist": "شين يانغ",
        "type": "رواية ويب",
        "status": "مستمرة",
        "rating": 4.90,
        "views": 32050,
        "followers": 4120,
        "native_language": "الصينية",
        "released": "2025",
        "updated_on": "يونيو 20, 2026",
        "synopsis": "في عالم الزراعة الخالدة القاسي حيث يتنافس الجميع على الموارد الشحيحة، يستيقظ لين هان ليرث نظام استثمار فريد من نوعه...",
        "genres": ["اكشن", "خيال", "شبه بشرية", "مغامره", "ووشيا"],
        "chapters": [
          {
            "id": "immortality-investment-v1-ch1",
            "volume_number": 1,
            "volume_title": "رأس المال الأول",
            "chapter_number": 1,
            "title": "نظام استثمار داو الخالد",
            "release_date": "2026-06-20",
            "is_locked": 0,
            "content": "<p>مرحباً بك في طائفة السحابة الإلهية، المكان الذي يُحدد فيه مصيرك بناءً على مدى نقاء عروقك الروحية...</p>"
          }
        ]
      },
      {
        "id": "emperor-death",
        "title": "إمبراطور الموت الإلهي",
        "alt_title": "Divine Emperor of Death",
        "cover": "assets/emperor_death.png",
        "author": "فانغ دونغ",
        "artist": "لي وي",
        "type": "رواية ويب",
        "status": "مستمرة",
        "rating": 4.88,
        "views": 28400,
        "followers": 3890,
        "native_language": "الصينية",
        "released": "2024",
        "updated_on": "يونيو 19, 2026",
        "synopsis": "ولد ديفيس من جديد في عائلة إمبراطورية قوية تُعرف باسم إمبراطورية اللوتس الأسود...",
        "genres": ["اكشن", "حريم", "خيال", "شبه بشرية", "مغامره"],
        "chapters": [
          {
            "id": "emperor-death-v1-ch1",
            "volume_number": 1,
            "volume_title": "النهوض الإمبراطوري",
            "chapter_number": 1,
            "title": "كتاب الموت في القصر الإمبراطوري",
            "release_date": "2026-06-18",
            "is_locked": 0,
            "content": "<p>في قصر اللوتس الأسود الشاسع والمزين بالذهب والفضة، كان ديفيس يبلغ من العمر ثماني سنوات...</p>"
          }
        ]
      }
    ]

    for n in mock_novels:
        cursor.execute("""
            INSERT INTO novels (id, title, alt_title, cover, author, artist, type, status, rating, views, followers, native_language, released, updated_on, synopsis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (n["id"], n["title"], n["alt_title"], n["cover"], n["author"], n["artist"], n["type"], n["status"], n["rating"], n["views"], n["followers"], n["native_language"], n["released"], n["updated_on"], n["synopsis"]))
        
        for g in n["genres"]:
            cursor.execute("INSERT INTO genres (novel_id, genre) VALUES (?, ?)", (n["id"], g))
            
        for c in n["chapters"]:
            cursor.execute("""
                INSERT INTO chapters (id, novel_id, volume_number, volume_title, chapter_number, title, release_date, content, is_locked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (c["id"], n["id"], c["volume_number"], c["volume_title"], c["chapter_number"], c["title"], c["release_date"], c["content"], c["is_locked"]))
            
    conn.commit()

# --- AUTH MIDDLEWARE ---
def log_action(user_id, username, action, details=None, conn=None):
    try:
        own_conn = False
        if conn is None:
            conn = get_db_connection()
            own_conn = True
        cursor = conn.cursor()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Safe fallback if not in a request context (like tests/startup)
        try:
            ip_address = request.remote_addr or "127.0.0.1"
        except RuntimeError:
            ip_address = "system"
        cursor.execute("""
            INSERT INTO audit_logs (timestamp, user_id, username, action, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, user_id, username, action, details, ip_address))
        if own_conn:
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[WARNING] Failed to write audit log: {e}")

def is_assigned(user_id, novel_id, role=None):
    # Admins are automatically assigned to everything
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    u_row = cursor.fetchone()
    if u_row and u_row["role"] == "Admin":
        conn.close()
        return True
        
    if role:
        cursor.execute("""
            SELECT 1 FROM novel_assignments 
            WHERE user_id = ? AND novel_id = ? AND role = ?
        """, (user_id, novel_id, role))
    else:
        cursor.execute("""
            SELECT 1 FROM novel_assignments 
            WHERE user_id = ? AND novel_id = ?
        """, (user_id, novel_id))
    assigned = cursor.fetchone() is not None
    conn.close()
    return assigned

def get_user_from_request():
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        data = serializer.loads(token, max_age=86400 * 30) # Token good for 30 days
        user_id = data.get("user_id")
        if not user_id:
            return None
            
        # Fetch role and status dynamically from the database (prevents role forgery)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role, vip_expires_at FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return None
            
        user_dict = dict(user)
        
        # Check VIP expiration on every request (resolves VIP expiration logic flaw)
        if user_dict["role"] == "VIP" and user_dict["vip_expires_at"]:
            try:
                exp_time = float(user_dict["vip_expires_at"])
                if time.time() > exp_time:
                    # Downgrade role in DB
                    cursor.execute("UPDATE users SET role = 'Free', vip_expires_at = NULL WHERE id = ?", (user_id,))
                    conn.commit()
                    user_dict["role"] = "Free"
                    user_dict["vip_expires_at"] = None
            except (ValueError, TypeError):
                # If timestamp is corrupt, reset to Free
                cursor.execute("UPDATE users SET role = 'Free', vip_expires_at = NULL WHERE id = ?", (user_id,))
                conn.commit()
                user_dict["role"] = "Free"
                user_dict["vip_expires_at"] = None
                
        conn.close()
        return {
            "user_id": user_dict["id"],
            "username": user_dict["username"],
            "role": user_dict["role"],
            "vip_expires_at": user_dict["vip_expires_at"]
        }
    except (SignatureExpired, BadSignature, ValueError, TypeError):
        return None

def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_user_from_request()
            if not user or user["role"] not in allowed_roles:
                return jsonify({"error": "🔒 دخول غير مصرح. صلاحيات غير كافية لربط هذا الإجراء."}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    return role_required(["Admin"])(f)

def publisher_required(f):
    return role_required(["Admin", "Publisher"])(f)

def translator_required(f):
    return role_required(["Admin", "Translator"])(f)

def reviewer_required(f):
    return role_required(["Admin", "Reviewer"])(f)

def reviewer_or_publisher_required(f):
    return role_required(["Admin", "Reviewer", "Publisher"])(f)



# --- API ENDPOINTS ---

# 1. AUTHENTICATION
@app.route("/api/auth/register", methods=["POST"])
@limiter.limit("3 per minute")
def register():
    data = request.json
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    
    if not username or not email or not password:
        return jsonify({"error": "جميع الحقول مطلوبة"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    password_hash = generate_password_hash(password)
    try:
        if DB_TYPE == "postgresql":
            cursor.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?) RETURNING id",
                (username, email, password_hash)
            )
            user_id = cursor.fetchone()["id"]
        else:
            cursor.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username, email, password_hash)
            )
            user_id = cursor.lastrowid
        conn.commit()
        token = serializer.dumps({"user_id": user_id, "role": "Free"})
        return jsonify({"token": token, "username": username, "role": "Free"})
    except Exception as e:
        err_msg = str(e).lower()
        if "unique" in err_msg or "already exists" in err_msg or "integrityerror" in err_msg or "duplicate" in err_msg:
            return jsonify({"error": "اسم المستخدم أو البريد الإلكتروني مسجل بالفعل"}), 400
        return jsonify({"error": f"حدث خطأ أثناء التسجيل: {str(e)}"}), 500
    finally:
        conn.close()

@app.route("/api/auth/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    data = request.json
    username_or_email = data.get("username")
    password = data.get("password")
    
    if not username_or_email or not password:
        return jsonify({"error": "يرجى إدخال اسم المستخدم وكلمة المرور"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (username_or_email, username_or_email)
    )
    user = cursor.fetchone()
    conn.close()
    
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "اسم المستخدم أو كلمة المرور غير صحيحة"}), 401
        
    # Check if VIP has expired
    role = user["role"]
    if role == "VIP" and user["vip_expires_at"]:
        exp_time = float(user["vip_expires_at"])
        if time.time() > exp_time:
            # Downgrade user role
            conn = get_db_connection()
            conn.cursor().execute("UPDATE users SET role = 'Free', vip_expires_at = NULL WHERE id = ?", (user["id"],))
            conn.commit()
            conn.close()
            role = "Free"

    token = serializer.dumps({"user_id": user["id"], "role": role})
    return jsonify({
        "token": token,
        "username": user["username"],
        "role": role,
        "vip_expires_at": user["vip_expires_at"]
    })

# 2. NOVELS LIST (WITH ADVANCED FILTERS)
@app.route("/api/novels", methods=["GET"])
def get_novels():
    status = request.args.get("status", "all")
    type_val = request.args.get("type", "all")
    order = request.args.get("order", "latest")
    query = request.args.get("query", "")
    genres_param = request.args.get("genres", "")  # Comma-separated
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    sql = "SELECT DISTINCT n.* FROM novels n"
    params = []
    where_clauses = []
    
    if genres_param:
        genre_list = [g.strip() for g in genres_param.split(",") if g.strip()]
        for idx, g in enumerate(genre_list):
            sql += f" JOIN genres g{idx} ON n.id = g{idx}.novel_id AND g{idx}.genre = ?"
            params.append(g)
            
    if status != "all":
        where_clauses.append("n.status = ?")
        params.append(status)
        
    if type_val != "all":
        where_clauses.append("n.type = ?")
        params.append(type_val)
        
    if query.strip():
        where_clauses.append("(n.title LIKE ? OR n.alt_title LIKE ? OR n.author LIKE ?)")
        search_q = f"%{query.strip()}%"
        params.extend([search_q, search_q, search_q])
        
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
        
    # Ordering
    if order == "rating":
        sql += " ORDER BY n.rating DESC"
    elif order == "views":
        sql += " ORDER BY n.views DESC"
    elif order == "az":
        sql += " ORDER BY n.title ASC"
    else:
        sql += " ORDER BY n.updated_on DESC"
        
    # Pagination query parameters
    paginated = request.args.get("paginated", "false").lower() == "true"
    page_param = request.args.get("page")
    per_page_param = request.args.get("per_page")
    
    if paginated or page_param or per_page_param:
        page = int(page_param) if page_param else 1
        per_page = int(per_page_param) if per_page_param else 12
        offset = (page - 1) * per_page
        
        # Get total items count
        count_sql = "SELECT COUNT(DISTINCT n.id) FROM novels n"
        if genres_param:
            for idx, g in enumerate(genre_list):
                count_sql += f" JOIN genres g{idx} ON n.id = g{idx}.novel_id AND g{idx}.genre = ?"
        if where_clauses:
            count_sql += " WHERE " + " AND ".join(where_clauses)
            
        cursor.execute(count_sql, params)
        total_items = cursor.fetchone()[0]
        total_pages = (total_items + per_page - 1) // per_page
        
        # Append limit and offset to select query
        sql += " LIMIT ? OFFSET ?"
        select_params = list(params) + [per_page, offset]
        
        cursor.execute(sql, select_params)
        novels_rows = cursor.fetchall()
        
        results = []
        for row in novels_rows:
            n_dict = dict(row)
            # Fetch genres for each novel
            cursor.execute("SELECT genre FROM genres WHERE novel_id = ?", (n_dict["id"],))
            n_dict["genres"] = [r["genre"] for r in cursor.fetchall()]
            
            # Fetch latest 3 chapters
            cursor.execute("""
                SELECT id, chapter_number, title, volume_number 
                FROM chapters 
                WHERE novel_id = ? 
                ORDER BY volume_number DESC, chapter_number DESC LIMIT 3
            """, (n_dict["id"],))
            n_dict["latest_chapters"] = [dict(r) for r in cursor.fetchall()]
            results.append(n_dict)
            
        conn.close()
        return jsonify({
            "items": results,
            "page": page,
            "per_page": per_page,
            "total_items": total_items,
            "total_pages": total_pages
        })
    else:
        cursor.execute(sql, params)
        novels_rows = cursor.fetchall()
        
        results = []
        for row in novels_rows:
            n_dict = dict(row)
            # Fetch genres for each novel
            cursor.execute("SELECT genre FROM genres WHERE novel_id = ?", (n_dict["id"],))
            n_dict["genres"] = [r["genre"] for r in cursor.fetchall()]
            
            # Fetch latest 3 chapters
            cursor.execute("""
                SELECT id, chapter_number, title, volume_number 
                FROM chapters 
                WHERE novel_id = ? 
                ORDER BY volume_number DESC, chapter_number DESC LIMIT 3
            """, (n_dict["id"],))
            n_dict["latest_chapters"] = [dict(r) for r in cursor.fetchall()]
            results.append(n_dict)
            
        conn.close()
        return jsonify(results)

# 3. NOVEL DETAIL PAGE
@app.route("/api/novels/<novel_id>", methods=["GET"])
def get_novel_detail(novel_id):
    # Increment views count in cache background flusher
    cache.incr(f"novel:views:{novel_id}")
    
    # Determine authorization to see if we can serve cached version
    user = get_user_from_request()
    is_staff = False
    if user:
        is_staff = (user["role"] == "Admin" or is_assigned(user["user_id"], novel_id))
        
    if not is_staff:
        cached = cache.get(f"novel:detail:{novel_id}")
        if cached:
            return jsonify(cached)
            
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM novels WHERE id = ?", (novel_id,))
    novel_row = cursor.fetchone()
    if not novel_row:
        conn.close()
        return jsonify({"error": "الرواية غير موجودة"}), 404
        
    novel = dict(novel_row)
    
    # Get Genres
    cursor.execute("SELECT genre FROM genres WHERE novel_id = ?", (novel_id,))
    novel["genres"] = [r["genre"] for r in cursor.fetchall()]
    
    # Get Chapters grouped by volume (latest 20 for preview)
    if is_staff:
        cursor.execute("""
            SELECT volume_number, volume_title, id as chapter_id, chapter_number, title as chapter_title, release_date, is_locked, status
            FROM chapters
            WHERE novel_id = ?
            ORDER BY volume_number DESC, chapter_number DESC
            LIMIT 20
        """, (novel_id,))
    else:
        cursor.execute("""
            SELECT volume_number, volume_title, id as chapter_id, chapter_number, title as chapter_title, release_date, is_locked, status
            FROM chapters
            WHERE novel_id = ? AND status = 'Published'
            ORDER BY volume_number DESC, chapter_number DESC
            LIMIT 20
        """, (novel_id,))
        
    chapters_rows = list(cursor.fetchall())
    # Sort ascending for display
    chapters_rows.sort(key=lambda r: (r["volume_number"], r["chapter_number"]))
    
    volumes_dict = {}
    for r in chapters_rows:
        vol_num = r["volume_number"]
        if vol_num not in volumes_dict:
            volumes_dict[vol_num] = {
                "volumeNumber": vol_num,
                "title": r["volume_title"] or f"المجلد {vol_num}",
                "chapters": []
            }
        volumes_dict[vol_num]["chapters"].append({
            "id": r["chapter_id"],
            "chapterNumber": r["chapter_number"],
            "title": r["chapter_title"],
            "releaseDate": r["release_date"],
            "is_locked": bool(r["is_locked"]),
            "status": r["status"]
        })
        
    novel["volumes"] = list(volumes_dict.values())
    conn.close()
    
    # Save cache for non-staff
    if not is_staff:
        cache.set(f"novel:detail:{novel_id}", novel, ttl=300)
        
    return jsonify(novel)

# 3.5. PAGINATED CHAPTERS ENDPOINT
@app.route("/api/novels/<novel_id>/chapters", methods=["GET"])
def get_novel_chapters(novel_id):
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
        
    try:
        per_page = min(100, max(1, int(request.args.get("per_page", 50))))
    except (ValueError, TypeError):
        per_page = 50
        
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM novels WHERE id = ?", (novel_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "الرواية غير موجودة"}), 404
        
    user = get_user_from_request()
    is_staff = False
    if user:
        if user["role"] == "Admin" or is_assigned(user["user_id"], novel_id):
            is_staff = True
            
    if is_staff:
        cursor.execute("SELECT COUNT(*) FROM chapters WHERE novel_id = ?", (novel_id,))
        total = cursor.fetchone()[0] or 0
        
        cursor.execute("""
            SELECT volume_number, volume_title, id as chapter_id, chapter_number, title as chapter_title, release_date, is_locked, status
            FROM chapters
            WHERE novel_id = ?
            ORDER BY volume_number ASC, chapter_number ASC
            LIMIT ? OFFSET ?
        """, (novel_id, per_page, offset))
    else:
        cursor.execute("SELECT COUNT(*) FROM chapters WHERE novel_id = ? AND status = 'Published'", (novel_id,))
        total = cursor.fetchone()[0] or 0
        
        cursor.execute("""
            SELECT volume_number, volume_title, id as chapter_id, chapter_number, title as chapter_title, release_date, is_locked, status
            FROM chapters
            WHERE novel_id = ? AND status = 'Published'
            ORDER BY volume_number ASC, chapter_number ASC
            LIMIT ? OFFSET ?
        """, (novel_id, per_page, offset))
        
    rows = cursor.fetchall()
    conn.close()
    
    chapters = []
    for r in rows:
        chapters.append({
            "id": r["chapter_id"],
            "volumeNumber": r["volume_number"],
            "volumeTitle": r["volume_title"] or f"المجلد {r['volume_number']}",
            "chapterNumber": r["chapter_number"],
            "title": r["chapter_title"],
            "releaseDate": r["release_date"],
            "is_locked": bool(r["is_locked"]),
            "status": r["status"]
        })
        
    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "chapters": chapters
    })

# 4. READER VIEW (CHECK PREMIUM LOCKS)
@app.route("/api/chapters/<chapter_id>", methods=["GET"])
def get_chapter(chapter_id):
    # Try fetching from cache first
    cached_ch = cache.get(f"chapter:public:{chapter_id}")
    if cached_ch:
        return jsonify(cached_ch)
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT c.*, n.title as novel_title 
        FROM chapters c 
        JOIN novels n ON c.novel_id = n.id 
        WHERE c.id = ?
    """, (chapter_id,))
    ch_row = cursor.fetchone()
    
    if not ch_row:
        conn.close()
        return jsonify({"error": "الفصل غير موجود"}), 404
        
    chapter = dict(ch_row)
    conn.close()
    
    # Check if the chapter status is Published
    if chapter["status"] != "Published":
        user = get_user_from_request()
        is_staff = False
        if user:
            is_staff = (user["role"] == "Admin" or is_assigned(user["user_id"], chapter["novel_id"]))
        if not is_staff:
            return jsonify({"error": "🔒 هذا الفصل غير منشور بعد أو في انتظار المراجعة."}), 403
            
    # Defense-in-depth: Sanitize the output HTML content on read as well
    chapter["content"] = sanitize_html(chapter.get("content"))
    
    # Check subscription lock
    if chapter["is_locked"] == 1:
        user = get_user_from_request()
        is_allowed = False
        if user:
            is_allowed = (user["role"] in ["VIP", "Admin"] or is_assigned(user["user_id"], chapter["novel_id"]))
        if not is_allowed:
            return jsonify({
                "error": "🔒 هذا الفصل مقفل للأعضاء المشتركين VIP فقط. يرجى تسجيل الدخول والاشتراك لتتمكن من القراءة.",
                "is_locked": True
            }), 403
            
    # If the chapter is published and free (not locked), cache it for 1 hour
    if chapter["status"] == "Published" and chapter["is_locked"] == 0:
        cache.set(f"chapter:public:{chapter_id}", chapter, ttl=3600)
        
    return jsonify(chapter)

# 5. BOOKMARKS SYNC
@app.route("/api/bookmarks", methods=["GET"])
def get_bookmarks():
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "غير مصرح"}), 401
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT n.* FROM novels n
        JOIN bookmarks b ON n.id = b.novel_id
        WHERE b.user_id = ?
    """, (user["user_id"],))
    bookmarks_rows = cursor.fetchall()
    
    results = [dict(r) for r in bookmarks_rows]
    conn.close()
    return jsonify(results)

@app.route("/api/bookmarks/toggle", methods=["POST"])
def toggle_bookmark_db():
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "غير مصرح"}), 401
        
    novel_id = request.json.get("novel_id")
    if not novel_id:
        return jsonify({"error": "novel_id مطلوب"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM bookmarks WHERE user_id = ? AND novel_id = ?", (user["user_id"], novel_id))
    exists = cursor.fetchone()
    
    if exists:
        cursor.execute("DELETE FROM bookmarks WHERE user_id = ? AND novel_id = ?", (user["user_id"], novel_id))
        cursor.execute("UPDATE novels SET followers = MAX(0, followers - 1) WHERE id = ?", (novel_id,))
        status = "removed"
    else:
        cursor.execute("INSERT INTO bookmarks (user_id, novel_id) VALUES (?, ?)", (user["user_id"], novel_id))
        cursor.execute("UPDATE novels SET followers = followers + 1 WHERE id = ?", (novel_id,))
        status = "added"
        
    conn.commit()
    conn.close()
    return jsonify({"status": status})

# 6. HISTORY SYNC
@app.route("/api/history", methods=["GET", "POST"])
def handle_history():
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "غير مصرح"}), 401
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == "POST":
        data = request.json
        novel_id = data.get("novel_id")
        chapter_id = data.get("chapter_id")
        
        if not novel_id or not chapter_id:
            conn.close()
            return jsonify({"error": "الحقول المطلوبة مفقودة"}), 400
            
        # Clean older history for this novel
        cursor.execute("DELETE FROM history WHERE user_id = ? AND novel_id = ?", (user["user_id"], novel_id))
        
        # Insert new
        cursor.execute(
            "INSERT INTO history (user_id, novel_id, chapter_id, timestamp) VALUES (?, ?, ?, ?)",
            (user["user_id"], novel_id, chapter_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        )
        
        # Keep top 5 only
        cursor.execute("""
            DELETE FROM history WHERE id NOT IN (
                SELECT id FROM history WHERE user_id = ? 
                ORDER BY timestamp DESC LIMIT 5
            ) AND user_id = ?
        """, (user["user_id"], user["user_id"]))
        
        conn.commit()
        conn.close()
        return jsonify({"status": "success"})
        
    else: # GET
        cursor.execute("""
            SELECT h.timestamp, h.chapter_id, c.chapter_number, c.title as chapter_title, n.id as novel_id, n.title as novel_title, n.cover as novel_cover
            FROM history h
            JOIN novels n ON h.novel_id = n.id
            JOIN chapters c ON h.chapter_id = c.id
            WHERE h.user_id = ?
            ORDER BY h.timestamp DESC LIMIT 5
        """, (user["user_id"],))
        rows = cursor.fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

# 7. VIP SUBSCRIPTION
@app.route("/api/subscribe", methods=["POST"])
@limiter.limit("5 per minute")
def subscribe():
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "غير مصرح"}), 401
        
    data = request.json or {}
    payment_token = data.get("payment_token")
    
    is_development = os.environ.get("LIGHTNOVEL_DEBUG", "False").lower() == "true"
    
    # In production, enforce server-to-server or secure token checks. Mock is strictly forbidden.
    if not is_development:
        # Simulate check against a production payment API / database log
        if not payment_token or not payment_token.startswith("PROD_SECURE_TXN_"):
            return jsonify({"error": "فشل التحقق من الدفع. المعاملة الإنتاجية غير صالحة أو لم يتم تأكيدها."}), 400
    else:
        # Allow MOCK only in debug/development environments
        if not payment_token or (not payment_token.startswith("MOCK_PAYMENT_SUCCESS_") and not payment_token.startswith("PROD_SECURE_TXN_")):
            return jsonify({"error": "فشل التحقق من الدفع. رمز المعاملة التجريبي غير صالح."}), 400
        
    # Simulate successful payment confirmation
    expires_at = time.time() + (86400 * 30) # 30 days VIP
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET role = 'VIP', vip_expires_at = ? WHERE id = ?",
        (str(expires_at), user["user_id"])
    )
    conn.commit()
    conn.close()
    
    # Return new token
    new_token = serializer.dumps({"user_id": user["user_id"], "role": "VIP"})
    return jsonify({
        "success": True,
        "token": new_token,
        "role": "VIP",
        "vip_expires_at": str(expires_at)
    })

# 8. ACTIVE ADS FETCH (ADS HIDE FOR VIP MEMBERS)
@app.route("/api/ads", methods=["GET"])
def get_ads():
    user = get_user_from_request()
    # Ad-blocker logic: VIP and Admin see zero ads
    if user and user["role"] in ["VIP", "Admin"]:
        return jsonify([]) # No ads
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT zone, ad_code FROM ads WHERE is_active = 1")
    rows = cursor.fetchall()
    conn.close()
    
    # Defense-in-depth: Sanitize the output HTML ad code on delivery
    sanitized_ads = {}
    for r in rows:
        sanitized_ads[r["zone"]] = sanitize_html(r["ad_code"], allowed_tags={'div', 'p', 'span', 'a', 'img', 'br'}, allowed_attrs={
            'div': {'style', 'class'},
            'p': {'style', 'class'},
            'span': {'style', 'class'},
            'a': {'href', 'target', 'style', 'class'},
            'img': {'src', 'alt', 'style', 'class'}
        })
    return jsonify(sanitized_ads)


# MEDIA UPLOAD & CDN INTEGRATION
@app.route("/api/admin/upload", methods=["POST"])
def admin_upload_file():
    user = get_user_from_request()
    if not user or user["role"] not in ["Admin", "Publisher", "Translator", "Reviewer"]:
        return jsonify({"error": "🔒 دخول غير مصرح. يتطلب هذا الإجراء صلاحيات الفريق."}), 403
        
    novel_id = request.form.get("novel_id") or request.args.get("novel_id")
    if novel_id and user["role"] != "Admin":
        if not is_assigned(user["user_id"], novel_id):
            return jsonify({"error": "🔒 غير مصرح بالرفع لهذه الرواية. لست عضواً في فريق العمل."}), 403
            
    if "file" not in request.files:
        return jsonify({"error": "لم يتم إرسال أي ملف"}), 400
        
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "اسم الملف فارغ"}), 400
        
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        return jsonify({"error": "نوع الملف غير مدعوم. الأنواع المسموحة فقط هي الصور: JPG, PNG, WEBP, GIF"}), 400
        
    file_bytes = file.read(5 * 1024 * 1024 + 1)
    if len(file_bytes) > 5 * 1024 * 1024:
        return jsonify({"error": "حجم الملف كبير جداً. الحد الأقصى هو 5 ميجابايت."}), 413
        
    has_pil = False
    try:
        from PIL import Image
        has_pil = True
    except ImportError:
        pass

    if has_pil:
        try:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(file_bytes))
            img.verify()
            
            img = Image.open(io.BytesIO(file_bytes))
            width, height = img.size
            if width > 4096 or height > 4096:
                return jsonify({"error": "أبعاد الصورة كبيرة جداً. الحد الأقصى للأبعاد هو 4096x4096 بكسل."}), 400
                
            webp_io = io.BytesIO()
            img.save(webp_io, format="WEBP", quality=80)
            file_bytes = webp_io.getvalue()
            filename = os.path.splitext(filename)[0] + ".webp"
            content_type = "image/webp"
        except Exception:
            return jsonify({"error": "الملف المرفوع ليس صورة صالحة أو أنه تالف."}), 400
    else:
        # Fallback to magic bytes check if Pillow is not installed in the current environment
        is_valid_image_header = False
        if file_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            is_valid_image_header = True
            content_type = "image/png"
        elif file_bytes.startswith(b'\xff\xd8\xff'):
            is_valid_image_header = True
            content_type = "image/jpeg"
        elif file_bytes.startswith(b'GIF87a') or file_bytes.startswith(b'GIF89a'):
            is_valid_image_header = True
            content_type = "image/gif"
        elif b'WEBP' in file_bytes[:16]:
            is_valid_image_header = True
            content_type = "image/webp"
            
        if not is_valid_image_header:
            return jsonify({"error": "الملف المرفوع ليس صورة صالحة."}), 400
        
    import uuid
    unique_filename = f"{uuid.uuid4()}_{filename}"
    
    r2_bucket = os.environ.get("LIGHTNOVEL_R2_BUCKET")
    r2_endpoint = os.environ.get("LIGHTNOVEL_R2_ENDPOINT")
    r2_key_id = os.environ.get("LIGHTNOVEL_R2_ACCESS_KEY_ID")
    r2_secret = os.environ.get("LIGHTNOVEL_R2_SECRET_ACCESS_KEY")
    r2_public_url = os.environ.get("LIGHTNOVEL_R2_PUBLIC_URL")
    
    if r2_bucket and r2_endpoint and r2_key_id and r2_secret:
        try:
            import boto3
            s3 = boto3.client(
                "s3",
                endpoint_url=r2_endpoint,
                aws_access_key_id=r2_key_id,
                aws_secret_access_key=r2_secret
            )
            s3.put_object(
                Bucket=r2_bucket,
                Key=unique_filename,
                Body=file_bytes,
                ContentType=content_type
            )
            public_base = r2_public_url or f"{r2_endpoint}/{r2_bucket}"
            url = f"{public_base.rstrip('/')}/{unique_filename}"
            return jsonify({"url": url, "filename": unique_filename})
        except Exception as e:
            return jsonify({"error": f"فشل الرفع إلى التخزين السحابي: {str(e)}"}), 500
    else:
        upload_dir = os.environ.get("LIGHTNOVEL_UPLOAD_DIR", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        local_path = os.path.join(upload_dir, unique_filename)
        try:
            with open(local_path, "wb") as f:
                f.write(file_bytes)
            url = f"/uploads/{unique_filename}"
            return jsonify({"url": url, "filename": unique_filename})
        except Exception as e:
            return jsonify({"error": f"فشل الرفع المحلي: {str(e)}"}), 500

@app.route("/uploads/<filename>")
def serve_upload(filename):
    upload_dir = os.environ.get("LIGHTNOVEL_UPLOAD_DIR", "uploads")
    from flask import send_from_directory
    return send_from_directory(os.path.abspath(upload_dir), filename)


# A. ADMIN: NOVEL MANAGEMENT
@app.route("/api/admin/novels", methods=["POST"])
def admin_create_novel():
    user = get_user_from_request()
    if not user or user["role"] not in ["Admin", "Publisher"]:
        return jsonify({"error": "🔒 دخول غير مصرح. يتطلب هذا الإجراء صلاحيات إنشاء روايات."}), 403
        
    data = request.json
    n_id = data.get("id")
    title = data.get("title")
    
    if not n_id or not title:
        return jsonify({"error": "معرف الرواية والعنوان حقول مطلوبة"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO novels (id, title, alt_title, cover, author, artist, type, status, rating, native_language, released, updated_on, synopsis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            n_id, title, data.get("alt_title"), data.get("cover", "assets/shadow_alchemist.png"),
            data.get("author"), data.get("artist"), data.get("type", "رواية ويب"),
            data.get("status", "مستمرة"), data.get("rating", 5.0), data.get("native_language"),
            data.get("released"), time.strftime("%Y-%m-%d"), data.get("synopsis")
        ))
        
        # Add genres
        genres = data.get("genres", [])
        for g in genres:
            cursor.execute("INSERT OR IGNORE INTO genres (novel_id, genre) VALUES (?, ?)", (n_id, g))
            
        # Auto-assign the creator (if Publisher) to the novel
        if user["role"] == "Publisher":
            cursor.execute("INSERT OR IGNORE INTO novel_assignments (novel_id, user_id, role) VALUES (?, ?, ?)", (n_id, user["user_id"], "Publisher"))
            
        log_action(user["user_id"], user["username"], "CREATE_NOVEL", f"Created novel: {n_id}", conn=conn)
        conn.commit()
        invalidate_novel_cache(n_id)
        return jsonify({"status": "success", "novel_id": n_id})
    except Exception as e:
        err_msg = str(e).lower()
        if "unique" in err_msg or "already exists" in err_msg or "integrityerror" in err_msg or "duplicate" in err_msg:
            return jsonify({"error": "معرف الرواية (ID) مسجل بالفعل"}), 400
        return jsonify({"error": f"حدث خطأ أثناء إنشاء الرواية: {str(e)}"}), 500
    finally:
        conn.close()

@app.route("/api/admin/novels/<novel_id>", methods=["PUT", "DELETE"])
def admin_modify_novel(novel_id):
    user = get_user_from_request()
    if not user or user["role"] not in ["Admin", "Publisher"]:
        return jsonify({"error": "🔒 دخول غير مصرح. يتطلب هذا الإجراء صلاحيات تعديل الروايات."}), 403
        
    if user["role"] != "Admin":
        if not is_assigned(user["user_id"], novel_id):
            return jsonify({"error": "🔒 دخول غير مصرح. لست مسنداً للعمل على هذه الرواية."}), 403
        if request.method == "DELETE":
            return jsonify({"error": "🔒 دخول غير مصرح. فقط مدير النظام يمكنه حذف الروايات."}), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == "DELETE":
        cursor.execute("DELETE FROM novels WHERE id = ?", (novel_id,))
        log_action(user["user_id"], user["username"], "DELETE_NOVEL", f"Deleted novel: {novel_id}", conn=conn)
        conn.commit()
        conn.close()
        invalidate_novel_cache(novel_id)
        return jsonify({"status": "deleted"})
        
    # PUT
    data = request.json
    cursor.execute("""
        UPDATE novels 
        SET title = ?, alt_title = ?, cover = ?, author = ?, artist = ?, type = ?, status = ?, native_language = ?, released = ?, synopsis = ?
        WHERE id = ?
    """, (
        data.get("title"), data.get("alt_title"), data.get("cover"), data.get("author"), data.get("artist"),
        data.get("type"), data.get("status"), data.get("native_language"), data.get("released"), data.get("synopsis"),
        novel_id
    ))
    
    # Refresh genres
    cursor.execute("DELETE FROM genres WHERE novel_id = ?", (novel_id,))
    for g in data.get("genres", []):
        cursor.execute("INSERT INTO genres (novel_id, genre) VALUES (?, ?)", (novel_id, g))
        
    log_action(user["user_id"], user["username"], "MODIFY_NOVEL", f"Modified novel: {novel_id}", conn=conn)
    conn.commit()
    conn.close()
    invalidate_novel_cache(novel_id)
    return jsonify({"status": "updated"})

# B. ADMIN: CHAPTER MANAGEMENT
@app.route("/api/admin/chapters", methods=["POST"])
def admin_create_chapter():
    user = get_user_from_request()
    if not user or user["role"] not in ["Admin", "Publisher", "Translator"]:
        return jsonify({"error": "🔒 دخول غير مصرح. يتطلب هذا الإجراء صلاحيات كتابة فصول."}), 403
        
    data = request.json
    novel_id = data.get("novel_id")
    vol_num = data.get("volume_number", 1)
    ch_num = data.get("chapter_number")
    title = data.get("title")
    content = data.get("content")
    is_locked = 1 if data.get("is_locked") else 0
    
    if not novel_id or not ch_num or not title or not content:
        return jsonify({"error": "حقول الفصل المطلوبة ناقصة"}), 400
        
    # Check ownership assignment if not Admin
    if user["role"] != "Admin":
        if not is_assigned(user["user_id"], novel_id):
            return jsonify({"error": "🔒 دخول غير مصرح. لست عضواً في فريق العمل لهذه الرواية."}), 403

    status = data.get("status")
    if not status:
        if user["role"] == "Translator":
            status = "Draft"
        else:
            status = "Published"
    else:
        if user["role"] not in ["Admin", "Publisher"] and status == "Published":
            status = "Draft"

    ch_id = f"{novel_id}-v{vol_num}-ch{ch_num}"
    
    # Sanitize content to prevent XSS (Stored XSS mitigation)
    sanitized_content = sanitize_html(content)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO chapters (id, novel_id, volume_number, volume_title, chapter_number, title, release_date, content, is_locked, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ch_id, novel_id, vol_num, data.get("volume_title", f"المجلد {vol_num}"),
            ch_num, title, time.strftime("%Y-%m-%d"), sanitized_content, is_locked, status
        ))
        
        # Update novel update date
        cursor.execute("UPDATE novels SET updated_on = ? WHERE id = ?", (time.strftime("%Y-%m-%d"), novel_id))
        log_action(user["user_id"], user["username"], "CREATE_CHAPTER", f"Created chapter: {ch_id} with status {status}", conn=conn)
        conn.commit()
        invalidate_chapter_cache(ch_id, novel_id)
        return jsonify({"status": "success", "chapter_id": ch_id})
    except Exception as e:
        err_msg = str(e).lower()
        if "unique" in err_msg or "already exists" in err_msg or "integrityerror" in err_msg or "duplicate" in err_msg:
            return jsonify({"error": "رقم الفصل مسجل بالفعل في هذا المجلد"}), 400
        return jsonify({"error": f"حدث خطأ أثناء إنشاء الفصل: {str(e)}"}), 500
    finally:
        conn.close()

@app.route("/api/admin/chapters/<chapter_id>", methods=["PUT", "DELETE"])
def admin_modify_chapter(chapter_id):
    user = get_user_from_request()
    if not user or user["role"] not in ["Admin", "Publisher", "Translator", "Reviewer"]:
        return jsonify({"error": "🔒 دخول غير مصرح. يتطلب هذا الإجراء صلاحيات تعديل فصول."}), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Fetch current chapter to check novel_id and status
    cursor.execute("SELECT novel_id, status FROM chapters WHERE id = ?", (chapter_id,))
    ch_row = cursor.fetchone()
    if not ch_row:
        conn.close()
        return jsonify({"error": "الفصل غير موجود"}), 404
        
    novel_id = ch_row["novel_id"]
    current_status = ch_row["status"]
    
    # Ownership/Assignment Check if not Admin
    if user["role"] != "Admin":
        if not is_assigned(user["user_id"], novel_id):
            conn.close()
            return jsonify({"error": "🔒 دخول غير مصرح. لست عضواً في فريق العمل لهذه الرواية."}), 403
            
        # Role-based action constraint checks:
        if request.method == "DELETE" and user["role"] != "Publisher":
            conn.close()
            return jsonify({"error": "🔒 دخول غير مصرح. لا يمكنك حذف الفصول."}), 403
            
        if user["role"] == "Translator" and current_status not in ["Draft", "Needs Changes"]:
            conn.close()
            return jsonify({"error": "🔒 دخول غير مصرح. لا يمكنك تعديل الفصل أثناء المراجعة أو بعد النشر."}), 403

    if request.method == "DELETE":
        cursor.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))
        log_action(user["user_id"], user["username"], "DELETE_CHAPTER", f"Deleted chapter: {chapter_id}", conn=conn)
        conn.commit()
        conn.close()
        invalidate_chapter_cache(chapter_id, novel_id)
        return jsonify({"status": "deleted"})
        
    # PUT
    data = request.json
    is_locked = 1 if data.get("is_locked") else 0
    # Sanitize content to prevent XSS (Stored XSS mitigation)
    sanitized_content = sanitize_html(data.get("content"))
    
    new_status = data.get("status", current_status)
    if user["role"] not in ["Admin", "Publisher"] and new_status == "Published":
        new_status = current_status

    cursor.execute("""
        UPDATE chapters 
        SET title = ?, content = ?, is_locked = ?, volume_number = ?, volume_title = ?, chapter_number = ?, status = ?
        WHERE id = ?
    """, (
        data.get("title"), sanitized_content, is_locked, int(data.get("volume_number", 1)), data.get("volume_title"), int(data.get("chapter_number")),
        new_status, chapter_id
    ))
    log_action(user["user_id"], user["username"], "MODIFY_CHAPTER", f"Modified chapter: {chapter_id} with status {new_status}", conn=conn)
    conn.commit()
    conn.close()
    invalidate_chapter_cache(chapter_id, novel_id)
    return jsonify({"status": "updated"})

# C. ADMIN: USER MANAGEMENT
@app.route("/api/admin/users", methods=["GET"])
@admin_required
def admin_get_users():
    page_param = request.args.get("page")
    per_page_param = request.args.get("per_page")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if page_param or per_page_param:
        page = int(page_param) if page_param else 1
        per_page = int(per_page_param) if per_page_param else 20
        offset = (page - 1) * per_page
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_items = cursor.fetchone()[0]
        total_pages = (total_items + per_page - 1) // per_page
        
        cursor.execute("SELECT id, username, email, role, vip_expires_at FROM users LIMIT ? OFFSET ?", (per_page, offset))
        rows = cursor.fetchall()
        conn.close()
        return jsonify({
            "items": [dict(r) for r in rows],
            "page": page,
            "per_page": per_page,
            "total_items": total_items,
            "total_pages": total_pages
        })
    else:
        cursor.execute("SELECT id, username, email, role, vip_expires_at FROM users")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

@app.route("/api/admin/users/<int:user_id>/role", methods=["PUT"])
@admin_required
def admin_update_user_role(user_id):
    data = request.json
    role = data.get("role")
    vip_days = data.get("vip_days", 0)
    
    if role not in ["Free", "VIP", "Admin", "Translator", "Publisher", "Reviewer"]:
        return jsonify({"error": "رتبة غير صالحة"}), 400
        
    expires_at = None
    if role == "VIP":
        if vip_days > 0:
            expires_at = str(time.time() + (86400 * vip_days))
        else:
            expires_at = str(time.time() + (86400 * 30)) # default 30 days
            
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET role = ?, vip_expires_at = ? WHERE id = ?",
        (role, expires_at, user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# D. ADMIN: ADS MANAGEMENT
@app.route("/api/admin/ads", methods=["GET"])
@admin_required
def admin_get_all_ads():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ads")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/admin/ads", methods=["POST"])
@admin_required
def admin_save_ad():
    data = request.json
    zone = data.get("zone")
    is_active = 1 if data.get("is_active", True) else 0
    
    if zone not in ["header", "sidebar", "reader"]:
        return jsonify({"error": "منطقة إعلانية غير صالحة"}), 400
        
    # Sanitize ad_code to only allow layout tags and inline styles (Stored XSS mitigation)
    ad_code = sanitize_html(data.get("ad_code"), allowed_tags={'div', 'p', 'span', 'a', 'img', 'br'}, allowed_attrs={
        'div': {'style', 'class'},
        'p': {'style', 'class'},
        'span': {'style', 'class'},
        'a': {'href', 'target', 'style', 'class'},
        'img': {'src', 'alt', 'style', 'class'}
    })
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO ads (zone, ad_code, is_active)
        VALUES (?, ?, ?)
    """, (zone, ad_code, is_active))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


# E. PUBLISHER & TRANSLATOR WORKFLOWS

@app.route("/api/admin/assign", methods=["POST"])
@publisher_required
def assign_team_member():
    user = get_user_from_request()
    data = request.json or {}
    novel_id = data.get("novel_id")
    target_user_id = data.get("user_id")
    role = data.get("role") # 'Publisher', 'Translator', 'Reviewer'
    
    if not novel_id or not target_user_id or not role:
        return jsonify({"error": "جميع الحقول (novel_id, user_id, role) مطلوبة"}), 400
        
    if role not in ["Publisher", "Translator", "Reviewer"]:
        return jsonify({"error": "الدور المحدد غير صالح"}), 400
        
    # Check if caller is Admin or is assigned as Publisher to this novel
    if user["role"] != "Admin" and not is_assigned(user["user_id"], novel_id, "Publisher"):
        return jsonify({"error": "غير مصرح لك بتعيين أعضاء فريق لهذه الرواية"}), 403
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if target user exists
    cursor.execute("SELECT username, role FROM users WHERE id = ?", (target_user_id,))
    target_user = cursor.fetchone()
    if not target_user:
        conn.close()
        return jsonify({"error": "المستخدم المستهدف غير موجود"}), 404
        
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO novel_assignments (novel_id, user_id, role)
            VALUES (?, ?, ?)
        """, (novel_id, target_user_id, role))
        
        # Auto-promote the user's global role if they are a reader or have lower privilege
        current_global_role = target_user["role"]
        if current_global_role in ["Free", "VIP"]:
            cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, target_user_id))
            log_action(user["user_id"], user["username"], "PROMOTE_USER_ROLE", f"Promoted {target_user['username']} global role from {current_global_role} to {role} due to assignment", conn=conn)
        elif current_global_role == "Translator" and role in ["Publisher", "Reviewer"]:
            cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, target_user_id))
            log_action(user["user_id"], user["username"], "PROMOTE_USER_ROLE", f"Promoted {target_user['username']} global role from Translator to {role} due to assignment", conn=conn)
        elif current_global_role == "Reviewer" and role == "Publisher":
            cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, target_user_id))
            log_action(user["user_id"], user["username"], "PROMOTE_USER_ROLE", f"Promoted {target_user['username']} global role from Reviewer to Publisher due to assignment", conn=conn)
            
        # Log action
        log_action(user["user_id"], user["username"], "ASSIGN_TEAM", f"Assigned {target_user['username']} as {role} to novel {novel_id}", conn=conn)
        conn.commit()
        return jsonify({"status": "success", "message": f"تم تعيين {target_user['username']} بنجاح وتحديث صلاحياته"})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/admin/assignments/<novel_id>", methods=["GET"])
@publisher_required
def get_novel_assignments(novel_id):
    user = get_user_from_request()
    if user["role"] != "Admin" and not is_assigned(user["user_id"], novel_id):
        return jsonify({"error": "🔒 غير مصرح لك بمشاهدة فريق هذه الرواية"}), 403
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.user_id, u.username, u.email, a.role
        FROM novel_assignments a
        JOIN users u ON a.user_id = u.id
        WHERE a.novel_id = ?
    """, (novel_id,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/chapters/<chapter_id>/submit", methods=["POST"])
@translator_required
def submit_chapter_for_review(chapter_id):
    user = get_user_from_request()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT novel_id, title FROM chapters WHERE id = ?", (chapter_id,))
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        return jsonify({"error": "الفصل غير موجود"}), 404
        
    novel_id = ch["novel_id"]
    if not is_assigned(user["user_id"], novel_id, "Translator"):
        conn.close()
        return jsonify({"error": "غير مصرح لك. لست المترجم المعين لهذه الرواية"}), 403
        
    cursor.execute("UPDATE chapters SET status = 'In Review' WHERE id = ?", (chapter_id,))
    log_action(user["user_id"], user["username"], "SUBMIT_CHAPTER", f"Submitted chapter {chapter_id} ({ch['title']}) for review", conn=conn)
    conn.commit()
    conn.close()
    invalidate_chapter_cache(chapter_id, novel_id)
    return jsonify({"status": "success", "message": "تم إرسال الفصل للمراجعة بنجاح"})

@app.route("/api/chapters/<chapter_id>/approve", methods=["POST"])
@reviewer_or_publisher_required
def approve_chapter(chapter_id):
    user = get_user_from_request()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT novel_id, title FROM chapters WHERE id = ?", (chapter_id,))
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        return jsonify({"error": "الفصل غير موجود"}), 404
        
    novel_id = ch["novel_id"]
    if user["role"] != "Admin" and not is_assigned(user["user_id"], novel_id, "Reviewer") and not is_assigned(user["user_id"], novel_id, "Publisher"):
        conn.close()
        return jsonify({"error": "غير مصرح لك بالموافقة على هذا الفصل"}), 403
        
    cursor.execute("UPDATE chapters SET status = 'Published', published_at = ? WHERE id = ?", 
                   (time.strftime("%Y-%m-%d %H:%M:%S"), chapter_id))
    log_action(user["user_id"], user["username"], "APPROVE_CHAPTER", f"Approved and published chapter {chapter_id} ({ch['title']})", conn=conn)
    conn.commit()
    conn.close()
    invalidate_chapter_cache(chapter_id, novel_id)
    return jsonify({"status": "success", "message": "تمت الموافقة ونشر الفصل بنجاح"})

@app.route("/api/chapters/<chapter_id>/reject", methods=["POST"])
@reviewer_or_publisher_required
def reject_chapter(chapter_id):
    user = get_user_from_request()
    data = request.json or {}
    feedback = data.get("feedback", "")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT novel_id, title FROM chapters WHERE id = ?", (chapter_id,))
    ch = cursor.fetchone()
    if not ch:
        conn.close()
        return jsonify({"error": "الفصل غير موجود"}), 404
        
    novel_id = ch["novel_id"]
    if user["role"] != "Admin" and not is_assigned(user["user_id"], novel_id, "Reviewer") and not is_assigned(user["user_id"], novel_id, "Publisher"):
        conn.close()
        return jsonify({"error": "غير مصرح لك برفض هذا الفصل"}), 403
        
    cursor.execute("UPDATE chapters SET status = 'Needs Changes' WHERE id = ?", (chapter_id,))
    log_action(user["user_id"], user["username"], "REJECT_CHAPTER", f"Requested changes on chapter {chapter_id} ({ch['title']}). Feedback: {feedback}", conn=conn)
    conn.commit()
    conn.close()
    invalidate_chapter_cache(chapter_id, novel_id)
    return jsonify({"status": "success", "message": "تم إرجاع الفصل للمترجم لطلب التعديلات"})

@app.route("/api/admin/audit", methods=["GET"])
@admin_required
def get_audit_logs():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM audit_logs")
    total_items = cursor.fetchone()[0]
    total_pages = (total_items + per_page - 1) // per_page
    
    cursor.execute("""
        SELECT * FROM audit_logs 
        ORDER BY timestamp DESC 
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    rows = cursor.fetchall()
    conn.close()
    
    return jsonify({
        "items": [dict(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages
    })

@app.route("/api/my-work", methods=["GET"])
def get_my_work():
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "يجب تسجيل الدخول"}), 401
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get assigned novels
    cursor.execute("""
        SELECT n.id, n.title, n.cover, a.role
        FROM novel_assignments a
        JOIN novels n ON a.novel_id = n.id
        WHERE a.user_id = ?
    """, (user["user_id"],))
    novels = [dict(r) for r in cursor.fetchall()]
    
    chapters = []
    for nov in novels:
        role = nov["role"]
        if role == "Translator":
            cursor.execute("""
                SELECT id, volume_number, chapter_number, title, status 
                FROM chapters 
                WHERE novel_id = ? AND status IN ('Draft', 'Needs Changes')
            """, (nov["id"],))
            chapters.extend([dict(r) for r in cursor.fetchall()])
        elif role == "Reviewer":
            cursor.execute("""
                SELECT id, volume_number, chapter_number, title, status 
                FROM chapters 
                WHERE novel_id = ? AND status = 'In Review'
            """, (nov["id"],))
            chapters.extend([dict(r) for r in cursor.fetchall()])
        elif role == "Publisher":
            cursor.execute("""
                SELECT id, volume_number, chapter_number, title, status 
                FROM chapters 
                WHERE novel_id = ?
            """, (nov["id"],))
            chapters.extend([dict(r) for r in cursor.fetchall()])
            
    conn.close()
    return jsonify({
        "novels": novels,
        "chapters": chapters
    })



@app.cli.command("flush-views")
def flush_views_command():
    """Flush pending novel views from Redis to the database."""
    print("[INFO] Flushing views from cache to database...")
    flush_views_once()
    print("[INFO] Views flush completed.")


if __name__ == "__main__":
    init_db()
    host = os.environ.get("LIGHTNOVEL_HOST", "127.0.0.1")
    port = int(os.environ.get("LIGHTNOVEL_PORT", 5000))
    debug = os.environ.get("LIGHTNOVEL_DEBUG", "False").lower() == "true"
    app.run(host=host, port=port, debug=debug)
