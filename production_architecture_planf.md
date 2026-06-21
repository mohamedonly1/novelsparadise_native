# خطة معمارية الإنتاج وتحمل الضغط لموقع Novels Paradise

هذه الوثيقة تضع خطة عملية لنقل مشروع `LightNovel / Novels Paradise` من نموذج محلي مبني على Flask + SQLite إلى بنية إنتاج تتحمل آلاف الروايات، عشرات أو مئات آلاف الفصول، وعشرات آلاف الزيارات اليومية.

الموقع المستهدف: `https://novelsparadise.site/`

---

## 1. الهدف التشغيلي

النطاق المطلوب:

| البند | الهدف |
| --- | --- |
| عدد الروايات | آلاف الروايات |
| عدد الفصول | عشرات إلى مئات آلاف الفصول |
| الزيارات اليومية | 20,000 إلى 100,000+ زيارة يوميا |
| صفحات القراءة | أعلى نقطة ضغط في النظام |
| لوحة الإدارة | أدوار Admin / Publisher / Translator / Reviewer |
| الاشتراكات | VIP وقيود محتوى |
| الأمان | حماية صلاحيات، XSS، rate limits، سجلات تدقيق |

تقدير تقريبي للحمل:

| السيناريو | Pageviews يومية | متوسط الطلبات/ثانية | ذروة متوقعة |
| --- | ---: | ---: | ---: |
| 20 ألف زيارة × 4 صفحات | 80,000 | حوالي 1 req/s | 20-40 req/s |
| 50 ألف زيارة × 5 صفحات | 250,000 | حوالي 3 req/s | 60-120 req/s |
| 100 ألف زيارة × 5 صفحات | 500,000 | حوالي 6 req/s | 120-250 req/s |

> المتوسط اليومي ليس هو المهم وحده. المهم هو وقت الذروة، خاصة عند نشر فصل جديد أو وجود زحف من bots أو فتح صفحة رواية بها آلاف الفصول.

---

## 2. حالة المشروع الحالية والمشكلة الأساسية

المشروع الحالي مناسب للتطوير والاختبار، لكنه غير مناسب للإنتاج على نفس الشكل.

| المكون الحالي | المشكلة عند الضغط |
| --- | --- |
| SQLite | قفل عند الكتابة المتزامنة، وظهرت فعلا رسالة `database is locked` أثناء الاختبارات |
| تشغيل `python app.py` | سيرفر تطوير، ليس لإنتاج حقيقي |
| ملفات محلية للصور | يصعب التوسع لأكثر من سيرفر، والتحميل يضغط على نفس الجهاز |
| زيادة views داخل قاعدة البيانات مباشرة | تكتب كثيرا جدا مع كل فتح صفحة |
| لا توجد طبقة كاش | كل زائر يضغط على قاعدة البيانات |
| بحث SQL بسيط | سيصبح بطيئا مع آلاف الروايات والفصول |
| مهام audit/logging متزامنة | يمكن أن تؤخر الطلبات وتزيد مشاكل القفل |

أكبر عنق زجاجة حاليا: **SQLite + الكتابات المتكررة مثل views و audit logs**.

---

## 3. المعمارية المقترحة للإنتاج

```mermaid
graph TD
    U["Users / Browsers"] --> CF["Cloudflare CDN + WAF"]
    CF --> NX["Nginx Reverse Proxy"]
    NX --> APP["Gunicorn / Flask API Workers"]
    APP --> PG["PostgreSQL"]
    APP --> REDIS["Redis Cache + Counters"]
    APP --> OBJ["Cloudflare R2 / S3 Media Storage"]
    WORKER["Background Worker: RQ or Celery"] --> REDIS
    WORKER --> PG
    WORKER --> OBJ
    MON["Monitoring + Logs"] --> APP
    MON --> PG
```

المكونات الأساسية:

| المكون | الاختيار المقترح | السبب |
| --- | --- | --- |
| Web/API | Flask خلف Gunicorn أو Waitress | مناسب كبداية بدون تغيير جذري |
| Reverse Proxy | Nginx | ضغط، SSL، timeouts، rate limiting |
| CDN/WAF | Cloudflare | تخفيف ضغط، حماية bots، cache للصور والصفحات العامة |
| Database | PostgreSQL Managed | يتحمل concurrency وفهارس ونسخ احتياطي |
| Cache | Redis | كاش للروايات، الفصول، الإعلانات، العدادات |
| Media | Cloudflare R2 أو S3 | تخزين صور وأغلفة خارج السيرفر |
| Workers | RQ أو Celery | audit logs، views batching، emails، معالجة صور |
| Search | PostgreSQL Full Text كبداية، ثم Meilisearch | بحث أسرع وتجربة مستخدم أفضل |

---

## 4. خطة قاعدة البيانات

### 4.1 الانتقال من SQLite إلى PostgreSQL

يجب أن يكون أول انتقال إنتاجي هو PostgreSQL.

المطلوب:

1. إضافة طبقة ORM أو على الأقل دعم driver PostgreSQL.
2. استخدام migrations مثل Alembic / Flask-Migrate.
3. نقل البيانات من SQLite إلى PostgreSQL.
4. منع تشغيل الإنتاج على SQLite نهائيا.

اقتراح بيئة:

```env
LIGHTNOVEL_DATABASE_URL=postgresql://user:password@host:5432/lightnovel
LIGHTNOVEL_REDIS_URL=redis://host:6379/0
LIGHTNOVEL_ENV=production
```

### 4.2 الفهارس المهمة

فهارس يجب إضافتها عند الانتقال:

```sql
CREATE INDEX idx_chapters_novel_status ON chapters(novel_id, status);
CREATE INDEX idx_chapters_novel_order ON chapters(novel_id, volume_number, chapter_number);
CREATE INDEX idx_chapters_updated ON chapters(updated_at DESC);

CREATE INDEX idx_novels_status ON novels(status);
CREATE INDEX idx_novels_updated ON novels(updated_on DESC);
CREATE INDEX idx_novels_views ON novels(views DESC);
CREATE INDEX idx_novels_title ON novels(title);

CREATE INDEX idx_bookmarks_user ON bookmarks(user_id);
CREATE INDEX idx_history_user_updated ON history(user_id, updated_at DESC);
CREATE INDEX idx_assignments_user ON novel_assignments(user_id);
CREATE INDEX idx_assignments_novel ON novel_assignments(novel_id);
CREATE INDEX idx_audit_timestamp ON audit_logs(timestamp DESC);
```

### 4.3 تقسيم قراءة الفصول

لا يجب إرجاع آلاف الفصول داخل `/api/novels/<novel_id>` مرة واحدة.

المطلوب:

```text
GET /api/novels/<novel_id>
يرجع بيانات الرواية فقط + آخر 20 فصل مثلا

GET /api/novels/<novel_id>/chapters?page=1&per_page=50
يرجع الفصول بصفحات
```

هذا مهم جدا للروايات التي تحتوي 1000-5000 فصل.

---

## 5. الكاش وتقليل ضغط قاعدة البيانات

### 5.1 Redis Cache

كاش مقترح:

| المفتاح | المحتوى | TTL |
| --- | --- | --- |
| `homepage:latest` | آخر التحديثات | 1-5 دقائق |
| `novel:detail:<id>` | بيانات الرواية العامة | 5-15 دقيقة |
| `novel:chapters:<id>:<page>` | صفحة فصول | 5-15 دقيقة |
| `chapter:public:<id>` | الفصل المنشور للقراء | 10-60 دقيقة |
| `ads:active` | الإعلانات النشطة | 1-5 دقائق |
| `genres:list` | التصنيفات | 1 ساعة |

يجب عدم كاش الصفحات الحساسة بنفس المفتاح للجميع إذا كانت تختلف حسب الدور أو VIP. استخدم مفاتيح منفصلة مثل:

```text
chapter:<chapter_id>:public
chapter:<chapter_id>:vip
chapter:<chapter_id>:staff:<user_id>
```

أو تجنب كاش المحتوى الخاص في البداية.

### 5.2 عدادات المشاهدات

لا تحدث `views` في PostgreSQL مع كل طلب.

النموذج الصحيح:

1. عند فتح رواية:
   ```text
   Redis INCR novel:views:<novel_id>
   ```
2. كل دقيقة Worker يجمع الزيادات ويكتبها دفعة واحدة:
   ```sql
   UPDATE novels SET views = views + :count WHERE id = :novel_id;
   ```

هذا يقلل آلاف الكتابات اليومية إلى عشرات أو مئات فقط.

### 5.3 سجلات التدقيق Audit Logs

لا تجعل `log_action` يفتح اتصال قاعدة بيانات منفصل أثناء transaction مفتوحة. هذا سبب محتمل لتحذير `database is locked`.

البدائل:

1. تمرير نفس `conn` إلى `log_action`.
2. أو إرسال audit event إلى Redis Queue.
3. أو تسجيل audit بعد `commit`.

للإنتاج الأفضل: Worker منفصل يكتب audit logs.

---

## 6. الملفات والصور و CDN

الأغلفة والصور لا يجب أن تخزن على نفس سيرفر التطبيق في الإنتاج.

النموذج المقترح:

1. رفع الصورة إلى Cloudflare R2 أو AWS S3.
2. إنشاء نسخ متعددة:
   - `small`: للقوائم.
   - `medium`: للكروت والبحث.
   - `large`: لصفحة الرواية.
3. تحويل الصور إلى WebP أو AVIF.
4. خدمة الصور عبر Cloudflare CDN.

فوائد ذلك:

| الفائدة | النتيجة |
| --- | --- |
| تخفيف ضغط السيرفر | التطبيق يخدم API فقط |
| سرعة تحميل أعلى | الصور من CDN قريب من المستخدم |
| توسع أفقي أسهل | أكثر من app server بدون مشكلة ملفات |

---

## 7. السيرفرات والموارد المقترحة

### 7.1 بداية إنتاج صغيرة

مناسبة حتى 20-50 ألف زيارة يوميا إذا الكاش مضبوط.

| المكون | الموارد |
| --- | --- |
| App Server | 2 vCPU / 4GB RAM |
| PostgreSQL | Managed 2 vCPU / 4GB RAM |
| Redis | 512MB - 1GB |
| Storage | R2/S3 |
| CDN | Cloudflare Free/Pro |

تشغيل app:

```text
Nginx -> Gunicorn -> Flask
```

Gunicorn مبدئيا:

```bash
gunicorn -w 3 -k gthread --threads 4 -b 127.0.0.1:8000 app:app
```

### 7.2 نمو متوسط

مناسب تقريبا لـ 50-150 ألف زيارة يوميا.

| المكون | الموارد |
| --- | --- |
| App Servers | 2 سيرفر، كل واحد 2-4 vCPU / 4-8GB RAM |
| Load Balancer | Cloudflare + Nginx أو managed LB |
| PostgreSQL | 4 vCPU / 8-16GB RAM |
| Redis | 2GB |
| Workers | 1-2 worker nodes |

### 7.3 نمو كبير

لو الموقع وصل لمئات آلاف الزيارات يوميا:

| المكون | التغيير |
| --- | --- |
| Database | Read replicas |
| Search | Meilisearch / OpenSearch |
| App | Autoscaling أو عدة VPS |
| Cache | Redis managed مع persistence |
| Analytics | ClickHouse أو BigQuery لاحقا |

---

## 8. متطلبات الأمان للإنتاج

قائمة إلزامية قبل الإطلاق التجاري:

| البند | الحالة المطلوبة |
| --- | --- |
| `LIGHTNOVEL_SECRET_KEY` | قيمة قوية ولا توجد default |
| Admin default | ممنوع `admin/admin123` في الإنتاج |
| Debug | `False` دائما |
| Host | خلف Nginx، وليس `0.0.0.0` مباشرة للعامة إلا عبر service صحيح |
| CORS | domains محددة فقط |
| HTTPS | إجباري |
| Cookies/Auth | يفضل HttpOnly cookies بدلا من localStorage لاحقا |
| Rate Limit | login/register/subscribe/admin actions |
| CSP Header | تقليل XSS |
| Backups | يومية مع اختبار restore |
| Audit Logs | غير قابلة للتلاعب بسهولة |
| Payments | Webhooks حقيقية من Stripe/PayPal أو مزود محلي |

ملاحظة مهمة: `PROD_SECURE_TXN_` أو أي payment token محاكى لا يصلح لإنتاج تجاري. يجب أن يكون الدفع عبر webhook موثوق من بوابة الدفع.

---

## 9. إمكانيات المستخدمين المطلوبة

### 9.1 القارئ Free/VIP

مهم للموقع:

- بحث سريع بالرواية، المؤلف، النوع، التصنيف.
- فلاتر: الحالة، اللغة، الترتيب، الأكثر مشاهدة، الأحدث.
- صفحة رواية لا تحمل كل الفصول مرة واحدة.
- متابعة الروايات.
- سجل قراءة.
- علامات مرجعية.
- إشعارات عند نزول فصل جديد.
- تجربة قراءة خفيفة وسريعة على الموبايل.
- حماية VIP بدون تسريب عبر API.

### 9.2 المترجم Translator

- إنشاء فصل كمسودة.
- تعديل فصوله في حالات `Draft` و `Needs Changes`.
- إرسال للمراجعة.
- رؤية الروايات المعين عليها فقط.
- سجل ملاحظات الReviewer.

### 9.3 المراجع Reviewer

- رؤية الفصول `In Review` للروايات المعين عليها.
- قبول/رفض الفصل.
- كتابة feedback عند الرفض.
- عدم امتلاك صلاحيات حذف عامة.

### 9.4 الناشر Publisher

- إنشاء روايات.
- إدارة الروايات المعين عليها.
- اعتماد الفصول أو إرجاعها.
- تعيين مترجمين/مراجعين حسب السياسة.
- رؤية إحصائيات رواياته: views، followers، آخر التحديثات.

### 9.5 المدير Admin

- إدارة المستخدمين والأدوار.
- إدارة كل الروايات والفصول.
- إدارة الإعلانات.
- إدارة الاشتراكات.
- مراجعة audit logs.
- رؤية مؤشرات النظام.
- تعطيل مستخدم أو إبطال جلسات عند الاشتباه.

---

## 10. المراقبة والقياس

بدون Analytics لا يمكن معرفة الزيارات الحقيقية للموقع بدقة. لمعرفة الرقم الحقيقي استخدم:

1. Cloudflare Analytics.
2. Google Analytics 4.
3. Nginx access logs.
4. Server metrics.

مؤشرات يجب مراقبتها:

| المؤشر | الهدف |
| --- | --- |
| p95 response time | أقل من 300-700ms للAPI |
| DB CPU | أقل من 70% غالب الوقت |
| Redis hit rate | أعلى من 80% للصفحات العامة |
| 5xx errors | قريبة من صفر |
| login failures | مراقبة هجمات brute force |
| cache memory | لا تصل للحد الأقصى |
| slow queries | مراجعة أسبوعية |

أدوات مناسبة:

- Sentry للأخطاء.
- Prometheus + Grafana للمقاييس.
- UptimeRobot أو Better Stack للمراقبة الخارجية.
- Cloudflare Analytics للزيارات والbots.

---

## 11. خطة التنفيذ المرحلية

### المرحلة 0: تنظيف قبل الإنتاج

- منع الإنتاج بدون `SECRET_KEY`.
- منع debug في production.
- ضبط CORS.
- إصلاح audit logging بحيث لا يسبب locks.
- إضافة rate limiting.
- إضافة headers أمنية: CSP, HSTS, X-Frame-Options.

### المرحلة 1: PostgreSQL

- إضافة `DATABASE_URL`.
- نقل schema إلى migrations.
- إضافة الفهارس.
- نقل البيانات.
- اختبار كل APIs على PostgreSQL.

### المرحلة 2: Redis

- كاش الصفحة الرئيسية.
- كاش تفاصيل الرواية.
- كاش صفحات الفصول.
- views counters في Redis ثم batch write.

### المرحلة 3: Media/CDN

- نقل الأغلفة إلى R2/S3.
- ضغط الصور.
- تحديث روابط الصور في قاعدة البيانات.
- تفعيل Cloudflare caching.

### المرحلة 4: Workers

- audit logs async.
- notifications async.
- image processing async.
- scheduled jobs للعدادات والتنظيف.

### المرحلة 5: Search

- PostgreSQL full-text كبداية.
- عند زيادة البيانات: Meilisearch.
- فهرسة الروايات والتصنيفات والمؤلفين.

### المرحلة 6: Load Testing

اختبار الضغط قبل الإطلاق:

```text
100 مستخدم متزامن لمدة 10 دقائق
300 مستخدم متزامن لمدة 10 دقائق
1000 مستخدم متزامن لصفحات القراءة فقط بعد تفعيل الكاش
```

أدوات:

- k6
- Locust
- Artillery

مثال سيناريو:

```text
70% فتح فصول
20% فتح صفحات روايات
8% بحث وتصفح
2% login/admin actions
```

---

## 12. أولويات التنفيذ

الأولوية القصوى:

1. PostgreSQL بدلا من SQLite.
2. Pagination للفصول.
3. Redis للكاش والviews.
4. Nginx + Gunicorn/Waitress بدلا من `python app.py`.
5. Cloudflare CDN/WAF.
6. Rate limiting وsecurity headers.

الأولوية التالية:

1. R2/S3 للصور.
2. Workers للaudit والnotifications.
3. Full-text search.
4. Monitoring حقيقي.
5. Backups واختبار restore.

---

## 13. قرار معماري مختصر

لو الهدف هو موقع إنتاج واقعي يتحمل نمو `novelsparadise.site`، فلا ننصح بتكبير السيرفر فقط مع SQLite. الحل الصحيح هو:

```text
Cloudflare -> Nginx -> Gunicorn Flask -> PostgreSQL
                                -> Redis
                                -> R2/S3
                                -> Background Workers
```

بهذا الشكل، الموقع يستطيع البدء بتكلفة معقولة، ثم التوسع تدريجيا بدون إعادة بناء كاملة.
