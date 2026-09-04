"""
إعدادات البوت الأساسية - نسخة محسّنة
يتضمن: متغيرات البيئة، الثوابت، إعدادات التسجيل
التحسينات عن بوت الحقوق:
- مهلة الطلب 15 دقيقة (900 ثانية) بدلاً من 10
- رابط الدعم قابل للتغيير عبر متغير بيئة SUPPORT_URL
"""
import logging
import os
from datetime import timedelta, timezone
from pathlib import Path

# --- إعدادات التسجيل ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- متغيرات البيئة ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID_STR = os.environ.get("ADMIN_CHAT_ID")
ADMIN_GROUP_ID_STR = os.environ.get("ADMIN_GROUP_ID")
SUPPORT_URL = os.environ.get("SUPPORT_URL", "https://t.me/Contact77Bot_bot")

if not BOT_TOKEN:
    raise ValueError("خطأ: متغير البيئة BOT_TOKEN غير موجود")
if not ADMIN_CHAT_ID_STR:
    raise ValueError("خطأ: متغير البيئة ADMIN_CHAT_ID غير موجود")
if not ADMIN_GROUP_ID_STR:
    raise ValueError("خطأ: متغير البيئة ADMIN_GROUP_ID غير موجود")

try:
    ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_CHAT_ID_STR.split(',')]
    MAIN_ADMIN_ID = ADMIN_IDS[0]
    ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_STR)
except ValueError:
    raise ValueError("خطأ في قراءة أرقام الأدمين أو المجموعة. تأكد أنها أرقام صحيحة")

# --- ثوابت التطبيق ---
ORDERS_PER_PAGE = 5
BROADCAST_BATCH_SIZE = 20
BROADCAST_BATCH_DELAY = 10
COOLDOWN_UPDATE_INTERVAL = 10
LOCAL_TZ = timezone(timedelta(hours=3))          # توقيت سوريا
COURSES_PER_PAGE = 10
ORDER_TIMEOUT_SECONDS = 900        # 15 دقيقة (محسّن: كانت 600 في بوت الحقوق)
ORDER_TIMEOUT_MINUTES = ORDER_TIMEOUT_SECONDS // 60
COOLDOWN_MINUTES = 5               # 5 دقائق بين الطلبات
MAX_COURSES_PER_ORDER = 12         # الحد الأقصى للمواد في الطلب الواحد
MAX_RECEIPT_SIZE_MB = 20           # الحد الأقصى لحجم ملف الإيصال

# --- إعدادات النسخ الاحتياطي ---
BACKUP_DIR = Path("backups")
BACKUP_INTERVAL = 432000           # كل 5 أيام
