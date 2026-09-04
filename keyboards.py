"""
لوحات الأزرار المركزية (بدون الإحالة والدليل - حسب الطلب)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import SUPPORT_URL


# --- لوحات الأدمن ---

def build_admin_main_keyboard(maintenance_status: str = "✅ غير مفعل") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 الطلبات", callback_data="admin_orders_menu")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("⛔ إدارة المحظورين", callback_data="admin_manage_bans")],
        [InlineKeyboardButton("📢 إرسال رسالة للجميع", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("📁 إدارة المحتوى", callback_data="admin_content_menu")],
        [InlineKeyboardButton("💳 إدارة طرق الدفع", callback_data="admin_payment_menu")],
        [InlineKeyboardButton(f"🔧 وضع الصيانة ({maintenance_status})",
                              callback_data="admin_toggle_maintenance")],
    ])


def build_service_action_keyboard() -> list:
    """أزرار إجراءات الخدمة (إدارة المحتوى)"""
    return [
        [InlineKeyboardButton("💰 تغيير السعر", callback_data="action_change_price")],
        [InlineKeyboardButton("📝 إعادة تسمية الخدمة", callback_data="action_rename")],
        [InlineKeyboardButton("📚 إدارة الأقسام والمواد", callback_data="action_manage_sections")],
        [InlineKeyboardButton("🗑 حذف الخدمة", callback_data="action_delete")],
        [InlineKeyboardButton("🔙 العودة لاختيار خدمة", callback_data="back_to_service_list")],
    ]


# --- لوحات الطالب ---

def build_student_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 طلب خدمة جديدة", callback_data="start_order")],
        [InlineKeyboardButton("📋 طلباتي", callback_data="my_orders")],
        [InlineKeyboardButton("👤 حسابي", callback_data="my_profile")],
        [InlineKeyboardButton("❓ أسئلة شائعة", callback_data="faq")],
        [InlineKeyboardButton("📞 التواصل مع الدعم", url=SUPPORT_URL)],
    ])


def build_support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 التواصل مع الدعم", url=SUPPORT_URL)]
    ])


def build_back_to_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_start")]
    ])


def build_back_to_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="admin_main_menu")]
    ])


def build_apply_and_end_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 تطبيق وإنهاء", callback_data="apply_and_end")]
    ])
