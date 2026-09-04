"""
دوال مساعدة عامة
يتضمن: تقسيم القوائم، تنظيف الأرقام، تجزئة أزرار callback، التحقق من صلاحيات الأدمن
"""
import hashlib
import re

from telegram.ext import CallbackContext
from telegram.error import Forbidden, BadRequest

from config import ADMIN_IDS, ADMIN_GROUP_ID


def chunks(lst: list, n: int):
    """تقسيم قائمة إلى أجزاء بحجم n"""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def normalize_and_clean_number(text: str) -> str:
    """تحويل الأرقام العربية والفارسية إلى إنجليزية وإزالة الفواصل والمسافات"""
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    result = []
    for char in text:
        if char in arabic_digits:
            result.append(str(arabic_digits.index(char)))
        elif char in persian_digits:
            result.append(str(persian_digits.index(char)))
        else:
            result.append(char)
    return re.sub(r"[.,٬\s]", "", "".join(result))


def cb_hash(text: str) -> str:
    """تجزئة قصيرة آمنة لأسماء الخدمات والأقسام في callback_data

    محسّن: الأسماء العربية الطويلة قد تتجاوز حد تيليجرام (64 بايت)
    في callback_data، لذا نستخدم hash ثابت الطول بدلاً من الاسم الصريح
    """
    return hashlib.md5(text.encode()).hexdigest()[:12]


def course_cb(course_name: str) -> str:
    """تجزئة كاملة لأسماء المواد (للتوافق مع نمط course_*)"""
    return hashlib.md5(course_name.encode()).hexdigest()


async def is_admin_in_group(user_id: int, context: CallbackContext) -> bool:
    """التحقق مما إذا كان المستخدم أدمن في المجموعة"""
    try:
        member = await context.bot.get_chat_member(chat_id=ADMIN_GROUP_ID, user_id=user_id)
        return member.status in ['creator', 'administrator']
    except (BadRequest, Forbidden):
        return user_id in ADMIN_IDS
