"""
سكربت ترحيل بيانات بوت SVU القديم (bot_data.pickle) إلى قاعدة SQLite الجديدة
بهيكل: تخصص ← خدمة ← كتلة ← مادة

القرارات المطبقة (بحسب طلب المالك):
- ❌ حذف الخدمة الاختبارية "احمد"
- 🟢 وضع الصيانة = OFF بعد الترحيل
- ✅ ترحيل: المستخدمين (مع تواريخ الانضمام) + الطلبات (قسمها القديم → تخصص)
        + طرق الدفع (مع صورة QR) + رابط الدعم
- 📚 الأقسام والكتل والمواد تُبنى من initial_data بالقوائم الرسمية الجديدة
  (كتل المقررات الخمس لكل تخصص)

الاستخدام:
    python migrate_pickle_to_db.py [مسار_pickle]
    python migrate_pickle_to_db.py --force     # البدء من جديد
"""
import asyncio
import json
import pickle
import os
import sys
from datetime import datetime

os.environ.setdefault("BOT_TOKEN", "0:migration")
os.environ.setdefault("ADMIN_CHAT_ID", "1")
os.environ.setdefault("ADMIN_GROUP_ID", "-1")

import aiosqlite

DB_PATH = "bot_database.db"
DEFAULT_PICKLE = "bot_data.pickle"

STATUS_MAP = {
    'pending': 'قيد المراجعة', 'approved': 'موافق عليه',
    'completed': 'مكتمل', 'rejected': 'مرفوض',
    'قيد المراجعة': 'قيد المراجعة', 'موافق عليه': 'موافق عليه',
    'مكتمل': 'مكتمل', 'مرفوض': 'مرفوض', 'مرفوض بانتظار سبب': 'مرفوض بانتظار سبب',
}

EXCLUDED_SERVICES = {'احمد'}


def find_pickle_path() -> str:
    if len(sys.argv) > 1 and sys.argv[1] != '--force':
        return sys.argv[1]
    candidates = [
        DEFAULT_PICKLE,
        os.path.join(os.path.dirname(__file__), DEFAULT_PICKLE),
        "/home/z/my-project/upload/bot_data.pickle",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


async def main():
    force = '--force' in sys.argv

    pickle_path = find_pickle_path()
    if not pickle_path:
        print("❌ لم يتم العثور على ملف bot_data.pickle")
        sys.exit(1)

    print(f"📥 قراءة: {pickle_path}")
    with open(pickle_path, 'rb') as f:
        raw = pickle.load(f)

    bot_data = raw.get('bot_data', raw)

    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("🗑 --force: تم حذف قاعدة البيانات القديمة")

    from database import init_database, migrate_database
    db = await init_database()
    await db.close()
    await migrate_database()

    # الهيكل الجديد (تخصصات/كتل/مواد/خدمات) من initial_data
    from initial_data import populate_initial_data, INITIAL_SPECS
    await populate_initial_data()

    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row

    # منع الترحيل المزدوج (الطلبات علامة الفحص)
    cursor = await db.execute("SELECT COUNT(*) as c FROM orders")
    if (await cursor.fetchone())['c'] > 0:
        print("⚠️ الطلبات مهاجرة مسبقاً. استخدم --force للبدء من جديد.")
        await db.close()
        sys.exit(1)

    stats = {'users': 0, 'orders': 0}

    # ═══ 1) المستخدمون ═══
    users = bot_data.get('users', {})
    for uid, info in users.items():
        await db.execute(
            """INSERT OR IGNORE INTO users (user_id, username, first_name, is_active, created_at)
               VALUES (?, ?, ?, 1, ?)""",
            (uid, info.get('username'), info.get('first_name'),
             info.get('join_date') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        stats['users'] += 1

    # ═══ 2) الطلبات (قسم قديم → تخصص) ═══
    # الأقسام القديمة كانت: المواد المشتركة / إدارة أعمال / علوم الإدارة
    # القسمان الأخيران هما التخصصان الجديدان؛ المشتركة تُسجل بلا تخصص
    spec_set = set(INITIAL_SPECS)

    order_items = [(k, v) for k, v in bot_data.items()
                   if k.startswith("order_") and isinstance(v, dict)]
    for key, o in order_items:
        ts = int(key.split('_')[-1])
        created = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        status = STATUS_MAP.get(o.get('status', 'pending'), 'قيد المراجعة')
        old_section = o.get('section')
        spec = old_section if old_section in spec_set else None

        await db.execute(
            """INSERT OR IGNORE INTO orders
               (order_key, user_id, full_name, username, specialization, service, courses,
                payment_method, total_price, receipt_file_id, receipt_type,
                receipt_file_name, transaction_id, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, o.get('user_id'), o.get('full_name'), o.get('username'),
             spec,
             o.get('service'),
             json.dumps(o.get('courses', []), ensure_ascii=False),
             o.get('payment_method'), o.get('total_price', 0),
             o.get('receipt_file_id'), 'photo', None,
             o.get('transaction_id'), status, created, created))
        stats['orders'] += 1

    # ═══ 3) طرق الدفع (مع صورة QR إن وجدت) - فوق الأولية ═══
    payments = bot_data.get('payment_methods', {})
    for name, info in payments.items():
        details = info.get('details', '')
        image = info.get('image') or info.get('image_file_id')
        await db.execute(
            """INSERT OR REPLACE INTO payment_methods (name, details, image_file_id)
               VALUES (?, ?, ?)""", (name, details, image))

    # ═══ 4) الإعدادات ═══
    support_url = bot_data.get('support_bot_url', 'https://t.me/Contact77Bot_bot')
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('support_bot_url', ?)",
        (support_url,))
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('maintenance_mode', '0')")

    # ═══ 5) فترات الانتظار ═══
    for uid, epoch in (bot_data.get('last_order_time', {}) or {}).items():
        try:
            dt = datetime.fromtimestamp(epoch)
            await db.execute(
                "INSERT OR REPLACE INTO user_cooldowns (user_id, last_order_time) VALUES (?, ?)",
                (uid, dt.isoformat()))
        except (ValueError, OSError):
            pass

    await db.commit()

    print("\n" + "=" * 50)
    print("✅ اكتمل الترحيل - ملخص:")
    print(f"   مستخدمون مهاجرون: {stats['users']}")
    print(f"   طلبات مهاجرة: {stats['orders']} (قسمها القديم → تخصص)")
    print(f"   الصيانة: OFF")

    # تحقق
    print("\n📊 تحقق:")
    for label, q in {
        'users': "SELECT COUNT(*) c FROM users",
        'specializations': "SELECT COUNT(*) c FROM specializations",
        'services': "SELECT COUNT(*) c FROM services",
        'blocks': "SELECT COUNT(*) c FROM spec_blocks",
        'course_links': "SELECT COUNT(*) c FROM block_courses",
        'orders': "SELECT COUNT(*) c FROM orders",
    }.items():
        cursor = await db.execute(q)
        print(f"   {label}: {(await cursor.fetchone())['c']}")

    cursor = await db.execute(
        "SELECT spec_name, COUNT(DISTINCT block_name) as blocks, "
        "SUM(1) as links FROM block_courses GROUP BY spec_name")
    print("\n📚 الكتل لكل تخصص:")
    for r in await cursor.fetchall():
        print(f"   {r['spec_name']}: {r['blocks']} كتل، {r['links']} ربط مادة")

    await db.close()
    print("\n🎉 تم بنجاح! شغّل البوت: python main.py")


if __name__ == "__main__":
    asyncio.run(main())
