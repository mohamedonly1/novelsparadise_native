import os
import unittest
import json
import tempfile
import sqlite3
import time

# Set env keys before imports to prevent startup failures
os.environ["LIGHTNOVEL_SECRET_KEY"] = "SECURE_TEST_KEY_987654321_LONG_STRONG_SECRET_FOR_TESTS"
os.environ["LIGHTNOVEL_DEBUG"] = "True" # Force test to allow mock endpoints and defaults

from app import app, init_db, get_db_connection, sanitize_html, serializer

class LightNovelSecurityTestCase(unittest.TestCase):
    def setUp(self):
        # Create a fresh temp database for each test to ensure isolation
        self.db_fd, self.temp_db_path = tempfile.mkstemp()
        import app as app_module
        app_module.DATABASE_FILE = self.temp_db_path
        
        self.app = app.test_client()
        self.app.testing = True
        init_db()

    def tearDown(self):
        # Close database temp file descriptor and delete the temp file
        os.close(self.db_fd)
        try:
            os.unlink(self.temp_db_path)
        except OSError:
            pass

    def test_html_sanitization_xss(self):
        # Test basic allowed HTML tags
        content = "<p>Hello <strong>World</strong>!</p>"
        self.assertEqual(sanitize_html(content), "<p>Hello <strong>World</strong>!</p>")

        # Test script tag removal
        xss_script = "<script>alert('xss')</script><p>Safe text</p>"
        self.assertEqual(sanitize_html(xss_script), "<p>Safe text</p>")

        # Test onload/onerror event handler removal
        xss_event = '<img src="x" onerror="alert(1)"><p>Safe text</p>'
        self.assertEqual(sanitize_html(xss_event), "<p>Safe text</p>")

        # Test dangerous javascript protocol removal
        xss_js_protocol = '<a href="javascript:alert(1)">Click Me</a>'
        # The <a> tag is not allowed in chapter content sanitizer, so it'll get stripped completely, leaving text
        self.assertEqual(sanitize_html(xss_js_protocol), "Click Me")

    def test_registration_and_login(self):
        # Test successful registration
        res = self.app.post("/api/auth/register", json={
            "username": "testuser",
            "email": "testuser@example.com",
            "password": "testpassword123"
        })
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("token", data)
        self.assertEqual(data["role"], "Free")

        # Test successful login
        res = self.app.post("/api/auth/login", json={
            "username": "testuser",
            "password": "testpassword123"
        })
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("token", data)
        self.assertEqual(data["role"], "Free")

    def test_vip_chapter_lock(self):
        # Create a VIP locked chapter directly in the db
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO novels (id, title, rating, views, followers) VALUES (?, ?, ?, ?, ?)",
            ("test-novel", "Test Novel", 5.0, 100, 10)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO chapters (id, novel_id, volume_number, chapter_number, title, content, is_locked) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-novel-ch1", "test-novel", 1, 1, "Locked Chapter", "VIP Secret Content", 1)
        )
        conn.commit()
        conn.close()

        # Try to read locked chapter anonymously -> should return 403
        res = self.app.get("/api/chapters/test-novel-ch1")
        self.assertEqual(res.status_code, 403)

        # Register a free user and try to read -> should return 403
        res = self.app.post("/api/auth/register", json={
            "username": "freeuser",
            "email": "freeuser@example.com",
            "password": "password123"
        })
        token = json.loads(res.data)["token"]
        headers = {"Authorization": f"Bearer {token}"}
        res = self.app.get("/api/chapters/test-novel-ch1", headers=headers)
        self.assertEqual(res.status_code, 403)

        # Upgrade free user to VIP using the simulated subscribe endpoint
        res = self.app.post("/api/subscribe", headers=headers, json={
            "payment_token": "MOCK_PAYMENT_SUCCESS_12345"
        })
        self.assertEqual(res.status_code, 200)
        vip_token = json.loads(res.data)["token"]
        vip_headers = {"Authorization": f"Bearer {vip_token}"}

        # Try reading locked chapter as VIP user -> should return 200
        res = self.app.get("/api/chapters/test-novel-ch1", headers=vip_headers)
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["content"], "VIP Secret Content")

    def test_token_role_forgery_mitigation(self):
        # Create a free user in the DB
        res = self.app.post("/api/auth/register", json={
            "username": "forgeryuser",
            "email": "forgery@example.com",
            "password": "password123"
        })
        # Get user ID from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = 'forgeryuser'")
        user_id = cursor.fetchone()["id"]
        conn.close()
        
        # Forge a token payload that contains role="Admin" or role="VIP"
        forged_payload = {"user_id": user_id, "role": "Admin"}
        forged_token = serializer.dumps(forged_payload)
        headers = {"Authorization": f"Bearer {forged_token}"}
        
        # Access an admin endpoint -> should fail because DB lookup verifies the real role is Free
        res = self.app.get("/api/admin/users", headers=headers)
        self.assertEqual(res.status_code, 403)
        self.assertIn("error", json.loads(res.data))

    def test_vip_expiration(self):
        # Create a VIP user with an expired subscription timestamp in DB
        conn = get_db_connection()
        cursor = conn.cursor()
        expired_time = time.time() - 3600 # Expired 1 hour ago
        cursor.execute(
            "INSERT INTO users (username, email, password_hash, role, vip_expires_at) VALUES (?, ?, ?, ?, ?)",
            ("expiredvip", "expired@vip.com", "dummy_hash", "VIP", str(expired_time))
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Make a token for this user
        user_token = serializer.dumps({"user_id": user_id, "role": "VIP"})
        headers = {"Authorization": f"Bearer {user_token}"}
        
        # Trigger an API call (e.g. get bookmarks or ads) which executes get_user_from_request
        res = self.app.get("/api/bookmarks", headers=headers)
        self.assertEqual(res.status_code, 200) # Endpoint returns 200 even if empty
        
        # Check that user role has been downgraded to 'Free' in the DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT role, vip_expires_at FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        self.assertEqual(user["role"], "Free")
        self.assertIsNone(user["vip_expires_at"])
        conn.close()

    def test_startup_database_migration_sanitization(self):
        # Create a dirty DB file manually before starting the app to simulate existing dirty content
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Insert a clean novel
        cursor.execute(
            "INSERT OR IGNORE INTO novels (id, title, rating, views, followers) VALUES (?, ?, ?, ?, ?)",
            ("migration-novel", "Migration Novel", 5.0, 100, 10)
        )
        # Insert a chapter with Stored XSS inside
        dirty_content = "<p>Safe text</p><script>alert('legacy-xss')</script>"
        cursor.execute(
            "INSERT OR IGNORE INTO chapters (id, novel_id, volume_number, chapter_number, title, content, is_locked) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("migration-novel-ch1", "migration-novel", 1, 1, "Legacy Chapter", dirty_content, 0)
        )
        # Insert an ad with Stored XSS inside
        dirty_ad_code = '<div style="color:red" onclick="exploit()">Legacy Ad</div>'
        cursor.execute(
            "INSERT OR REPLACE INTO ads (zone, ad_code, is_active) VALUES (?, ?, ?)",
            ("sidebar", dirty_ad_code, 1)
        )
        conn.commit()
        conn.close()
        
        # Force a database re-init/migration check
        init_db()
        
        # Verify content was sanitized by the migration logic
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM chapters WHERE id = 'migration-novel-ch1'")
        ch_content = cursor.fetchone()["content"]
        self.assertEqual(ch_content, "<p>Safe text</p>")
        
        cursor.execute("SELECT ad_code FROM ads WHERE zone = 'sidebar'")
        ad_content = cursor.fetchone()["ad_code"]
        self.assertEqual(ad_content, '<div style="color:red">Legacy Ad</div>')
        conn.close()

    def test_phase3_publishing_workflow(self):
        # 1. Create a dummy test novel
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO novels (id, title, rating, views, followers) VALUES (?, ?, ?, ?, ?)",
            ("wf-novel", "Workflow Novel", 5.0, 100, 10)
        )
        # Create a VIP locked draft chapter
        cursor.execute("""
            INSERT OR IGNORE INTO chapters (id, novel_id, volume_number, chapter_number, title, content, is_locked, status) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("wf-novel-ch1", "wf-novel", 1, 1, "Draft VIP Chapter", "Secret Workflow Content", 1, "Draft"))
        conn.commit()
        conn.close()

        # 2. Try to view details or read chapter as a public user -> should fail or be filtered out
        res = self.app.get("/api/novels/wf-novel")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(len(data["volumes"]), 0) # Draft chapter is filtered out for public

        res = self.app.get("/api/chapters/wf-novel-ch1")
        self.assertEqual(res.status_code, 403) # Access denied for draft for public

        # 3. Create a free user
        res = self.app.post("/api/auth/register", json={
            "username": "staffuser",
            "email": "staff@example.com",
            "password": "password123"
        })
        staff_data = json.loads(res.data)
        staff_token = staff_data["token"]
        headers = {"Authorization": f"Bearer {staff_token}"}

        # Get user ID from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = 'staffuser'")
        staff_id = cursor.fetchone()["id"]
        conn.close()

        # Verify global role is initially "Free"
        self.assertEqual(staff_data["role"], "Free")

        # 4. As Admin, assign this free user as a Translator to the novel
        # First, generate Admin token (default admin in dev is admin/admin123)
        res = self.app.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123"
        })
        admin_token = json.loads(res.data)["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Call assign endpoint
        res = self.app.post("/api/admin/assign", headers=admin_headers, json={
            "novel_id": "wf-novel",
            "user_id": staff_id,
            "role": "Translator"
        })
        self.assertEqual(res.status_code, 200)

        # 5. Verify the user's global role has been promoted to "Translator"
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE id = ?", (staff_id,))
        self.assertEqual(cursor.fetchone()["role"], "Translator")
        conn.close()

        # Generate a fresh token for the promoted user to reflect their new Translator role
        res = self.app.post("/api/auth/login", json={
            "username": "staffuser",
            "password": "password123"
        })
        staff_token = json.loads(res.data)["token"]
        headers = {"Authorization": f"Bearer {staff_token}"}

        # 6. Verify that as an assigned Translator, they can see the draft chapter
        res = self.app.get("/api/novels/wf-novel", headers=headers)
        data = json.loads(res.data)
        self.assertEqual(len(data["volumes"]), 1)
        self.assertEqual(data["volumes"][0]["chapters"][0]["title"], "Draft VIP Chapter")

        # 7. Verify they can read the VIP locked draft chapter (assigned bypass)
        res = self.app.get("/api/chapters/wf-novel-ch1", headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.data)["content"], "Secret Workflow Content")

        # 8. Test Submit for Review
        res = self.app.post("/api/chapters/wf-novel-ch1/submit", headers=headers)
        self.assertEqual(res.status_code, 200)

        # Verify status became In Review
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM chapters WHERE id = 'wf-novel-ch1'")
        self.assertEqual(cursor.fetchone()["status"], "In Review")
        conn.close()

        # 9. Create a Publisher user and assign them to the novel
        res = self.app.post("/api/auth/register", json={
            "username": "pubuser",
            "email": "pub@example.com",
            "password": "password123"
        })
        pub_data = json.loads(res.data)
        
        # Get pub ID from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = 'pubuser'")
        pub_id = cursor.fetchone()["id"]
        conn.close()
        
        res = self.app.post("/api/admin/assign", headers=admin_headers, json={
            "novel_id": "wf-novel",
            "user_id": pub_id,
            "role": "Publisher"
        })
        self.assertEqual(res.status_code, 200)

        # Login to get promoted publisher token
        res = self.app.post("/api/auth/login", json={
            "username": "pubuser",
            "password": "password123"
        })
        pub_token = json.loads(res.data)["token"]
        pub_headers = {"Authorization": f"Bearer {pub_token}"}

        # 10. Test Publisher Approve Chapter
        res = self.app.post("/api/chapters/wf-novel-ch1/approve", headers=pub_headers)
        self.assertEqual(res.status_code, 200)

        # Verify status became Published
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM chapters WHERE id = 'wf-novel-ch1'")
        self.assertEqual(cursor.fetchone()["status"], "Published")
        conn.close()

    def test_production_architecture_scaling_phase4(self):
        # 1. Test paginated chapters endpoint
        res = self.app.get("/api/novels/shadow-alchemist/chapters?page=1&per_page=2")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("total", data)
        self.assertIn("chapters", data)
        self.assertIn("page", data)
        
        # 2. Test views counter increments in Cache
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT views FROM novels WHERE id = 'shadow-alchemist'")
        initial_views = cursor.fetchone()["views"] or 0
        conn.close()
        
        # Call get_novel_detail to increment view in cache
        res = self.app.get("/api/novels/shadow-alchemist")
        self.assertEqual(res.status_code, 200)
        
        # Verify that view in database hasn't changed synchronously (proves view batching works)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT views FROM novels WHERE id = 'shadow-alchemist'")
        current_views = cursor.fetchone()["views"] or 0
        conn.close()
        self.assertEqual(current_views, initial_views)
        
        # Manually invoke cache flush to mimic background thread flush
        import app as app_module
        views_to_update = {}
        for k, v in list(app_module.cache.views.items()):
            novel_id = k.split(":")[-1]
            if v > 0:
                views_to_update[novel_id] = v
                app_module.cache.views[k] = 0
                
        if views_to_update:
            conn = get_db_connection()
            cursor = conn.cursor()
            for nid, count in views_to_update.items():
                cursor.execute("UPDATE novels SET views = views + ? WHERE id = ?", (count, nid))
            conn.commit()
            conn.close()
            
        # Verify that view is now updated in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT views FROM novels WHERE id = 'shadow-alchemist'")
        final_views = cursor.fetchone()["views"] or 0
        conn.close()
        self.assertEqual(final_views, initial_views + 1)

    def test_production_security_headers_and_upload_rules(self):
        # 1. Verify Security Headers
        res = self.app.get("/api/novels/shadow-alchemist")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'self'", res.headers.get("Content-Security-Policy", ""))
        self.assertNotIn("unsafe-eval", res.headers.get("Content-Security-Policy", ""))
        
        # 2. Verify Cache Invalidation
        import app as app_module
        app_module.cache.set("chapter:public:shadow-alchemist-v1-ch1", {"content": "Old Cached Content"})
        self.assertIsNotNone(app_module.cache.get("chapter:public:shadow-alchemist-v1-ch1"))
        
        # Admin login
        res = self.app.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123"
        })
        admin_token = json.loads(res.data)["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Modify chapter to trigger invalidation
        res = self.app.put("/api/admin/chapters/shadow-alchemist-v1-ch1", headers=admin_headers, json={
            "title": "همسات الظلام المعدلة",
            "content": "<p>محتوى جديد</p>",
            "is_locked": False,
            "volume_number": 1,
            "volume_title": "الدفتر المحرم",
            "chapter_number": 1,
            "status": "Published"
        })
        self.assertEqual(res.status_code, 200)
        # Check cache is now deleted
        self.assertIsNone(app_module.cache.get("chapter:public:shadow-alchemist-v1-ch1"))

        # 3. Verify Upload Restrictions
        import io
        
        # Unauthorized upload (no auth)
        res = self.app.post("/api/admin/upload", data={"file": (io.BytesIO(b"dummy data"), "test.png")})
        self.assertEqual(res.status_code, 403)
        
        # Invalid file type (text file)
        res = self.app.post("/api/admin/upload", headers=admin_headers, data={"file": (io.BytesIO(b"dummy data"), "test.txt")})
        self.assertEqual(res.status_code, 400)
        self.assertIn("نوع الملف غير مدعوم", json.loads(res.data)["error"])
        
        # Large file (exceeds 5MB)
        large_bytes = b"0" * (5 * 1024 * 1024 + 10)
        res = self.app.post("/api/admin/upload", headers=admin_headers, data={"file": (io.BytesIO(large_bytes), "test.png")})
        self.assertEqual(res.status_code, 413)
        self.assertIn("حجم الملف كبير جدا", json.loads(res.data)["error"])

        # Invalid image bytes (e.g. PNG extension but corrupt data)
        res = self.app.post("/api/admin/upload", headers=admin_headers, data={"file": (io.BytesIO(b"not an image"), "test.png")})
        self.assertEqual(res.status_code, 400)
        self.assertIn("الملف المرفوع ليس صورة صالحة", json.loads(res.data)["error"])

        # Valid 1x1 transparent PNG upload
        valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        res = self.app.post("/api/admin/upload", headers=admin_headers, data={"file": (io.BytesIO(valid_png), "test.png")})
        self.assertEqual(res.status_code, 200)
        self.assertIn("url", json.loads(res.data))

if __name__ == "__main__":
    unittest.main()
