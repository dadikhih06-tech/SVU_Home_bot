"""
🔍 سكربت تشخيص مشاكل التشغيل على المنصة
ارفعه بجانب ملفات البوت وشغّله:  python diagnose.py
يطبع فحوصات متسلسلة ويحدد المشكلة بالضبط
"""
import os
import sys
import traceback

print("=" * 55)
print("🔍 تشخيص بوت الخدمات الأكاديمية")
print("=" * 55)

# ═══ 1) إصدار بايثون ═══
ver = sys.version_info
print(f"\n[1] إصدار Python: {sys.version.split()[0]}", end=" ")
if ver >= (3, 9):
    print("✅")
else:
    print("❌ مطلوب 3.9+ — هذه مشكلتك الأولى")
    sys.exit(1)

# ═══ 2) متغيرات البيئة ═══
print("\n[2] متغيرات البيئة (السبب الأشهر للمشاكل):")
env_ok = True
for var in ["BOT_TOKEN", "ADMIN_CHAT_ID", "ADMIN_GROUP_ID"]:
    val = os.environ.get(var)
    if val:
        shown = val[:6] + "..." if var == "BOT_TOKEN" else val
        print(f"    {var} = {shown} ✅")
    else:
        print(f"    {var} = غير موجود ❌ ← اضبطه في إعدادات المنصة!")
        env_ok = False
if not env_ok:
    print("\n🔴 المشكلة هنا: البوت يتوقف فوراً عند الإقلاع بدون هذه المتغيرات.")
    print("   اضبطها في لوحة المنصة (Environment Variables / Secrets) ثم أعد التشغيل.")
    sys.exit(1)

# ═══ 3) المكتبات ═══
print("\n[3] المكتبات:")
libs = [("telegram", "python-telegram-bot[job-queue]"),
        ("aiosqlite", "aiosqlite"),
        ("openpyxl", "openpyxl"),
        ("apscheduler", "يأتي مع job-queue")]
missing = []
for module, pkg in libs:
    try:
        m = __import__(module)
        v = getattr(m, "__version__", "")
        print(f"    {module} {v} ✅")
    except ImportError:
        print(f"    {module} ❌ ← ثبّت: pip install {pkg}")
        missing.append(pkg)
if missing:
    print(f"\n🔴 نفّذ: pip install {' '.join(missing)}")
    sys.exit(1)

# ═══ 4) استيراد ملفات البوت ═══
print("\n[4] استيراد ملفات البوت:")
try:
    import config
    print(f"    config.py ✅ (الأدمن: {config.ADMIN_IDS} | المجموعة: {config.ADMIN_GROUP_ID})")
except Exception as e:
    print(f"    config.py ❌ {e}")
    traceback.print_exc()
    sys.exit(1)

try:
    import main  # noqa
    print("    main.py ✅ (كل المعالجات تُحمّل)")
except SystemExit:
    print("    main.py ⚠️ (config رفض متغيرات ناقصة)")
    sys.exit(1)
except Exception as e:
    print(f"    main.py ❌ {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

# ═══ 5) قاعدة البيانات ═══
print("\n[5] قاعدة البيانات:")
import asyncio


async def check_db():
    try:
        from database import init_database
        db = await init_database()
        await db.close()
        print("    SQLite ✅ (الجداول تُنشأ بنجاح)")
        return True
    except Exception as e:
        print(f"    SQLite ❌ {e}")
        return False


if not asyncio.run(check_db()):
    sys.exit(1)

# ═══ 6) التوكن صالح؟ (اتصال حقيقي بتيليجرام) ═══
print("\n[6] الاتصال بتيليجرام (فحص التوكن الحقيقي):")


async def check_token():
    import httpx
    token = os.environ["BOT_TOKEN"]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
            if data.get("ok"):
                bot_user = data["result"]
                print(f"    ✅ التوكن صالح — البوت: @{bot_user.get('username')}")
                return True, bot_user
            else:
                print(f"    ❌ تيليجرام رفض التوكن: {data.get('description')}")
                return False, None
    except Exception as e:
        print(f"    ❌ فشل الاتصال بتيليجرام: {e}")
        print("       (قد تكون شبكة المنصة محجوبة عن تيليجرام!)")
        return False, None


ok, bot_user = asyncio.run(check_token())

# ═══ 7) تعارض التوكن (بوتان بنفس التوكن!) ═══
if ok:
    print("\n[7] فحص تعارض polling (سبب شائع جداً):")

    async def check_conflict():
        import httpx
        token = os.environ["BOT_TOKEN"]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&limit=1")
                data = r.json()
                if data.get("ok"):
                    print("    ✅ لا يوجد تعارض — channel polling متاح")
                    return True
                else:
                    desc = data.get("description", "")
                    print(f"    ❌ {desc}")
                    if "Conflict" in desc:
                        print("       🔴 البوت يعمل في مكان آخر بنفس التوكن!")
                        print("          أوقف النسخة القديمة (أو التوكن القديم للبوت المختبر)")
                    return False
        except Exception as e:
            print(f"    ⚠️ {e}")
            return False

    asyncio.run(check_conflict())

# ═══ الخلاصة ═══
print("\n" + "=" * 55)
print("📌 الخلاصة:")
print("- الملفات سليمة ✅")
if not ok:
    print("- المشكلة في التوكن/الشبكة — انتبه: إذا كان البوت القديم")
    print("  (أو حساب BotFather تجريبي) يعمل بنفس التوكن على خادم آخر،")
    print("  التوأمان يتقاتلان و'/start' لن يصل أبداً!")
else:
    print("- كل شيء جاهز! إن كان '/start' لا يرد رغم ذلك:")
    print("  1) تأكد أن أمر التشغيل هو: python main.py")
    print("  2) راقب سجلات المنصة (Logs) وابحث عن Traceback")
    print("  3) بعض المنصات تقتل البوت لعدم فتح PORT — أخبر مطورك")
    print("     لإضافة health-check HTTP")
print("=" * 55)
