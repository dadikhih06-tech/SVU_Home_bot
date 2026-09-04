"""
واجهة المستخدم المركزية - نصوص هوية البوت الجديد
"""
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.error import BadRequest
from telegram.constants import ChatType

from config import logger, ADMIN_IDS
from keyboards import build_admin_main_keyboard, build_student_main_keyboard
from data_manager import get_data_manager

BOT_IDENTITY = (
    "بوت الخدمات الأكاديمية الخاص بطالب الجامعة الافتراضية السورية\n"
    "كلية علوم الإدارة وإدارة الأعمال"
)


def student_welcome_text(first_name: str, services_list: str) -> str:
    return (
        f"👋 مرحباً {first_name} في {BOT_IDENTITY}.\n\n"
        f"📚 نقدم لك الخدمات التالية بأسعار رمزية:\n{services_list}\n\n"
        f"✨ اختر أحد الخيارات أدناه:"
    )


async def show_main_menu(update: Update, context: CallbackContext) -> None:
    """عرض القائمة الرئيسية (للأدمن أو الطالب) - يحل الاستيراد الدائري"""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    if user.id in ADMIN_IDS and chat.type == ChatType.PRIVATE:
        dm = get_data_manager()
        maintenance_status = "🔧 مفعل" if await dm.get_maintenance_mode() else "✅ غير مفعل"
        text = f"👨‍💼 أهلاً بك أيها الأدمن {user.first_name}!"
        reply_markup = build_admin_main_keyboard(maintenance_status)
    else:
        dm = get_data_manager()
        prices = await dm.get_prices()
        services_list = (
            "\n".join([f"- {service}" for service in prices.keys()])
            if prices else "لا توجد خدمات متاحة حالياً."
        )
        text = student_welcome_text(user.first_name, services_list)
        reply_markup = build_student_main_keyboard()

    await _send_or_edit(update, text, reply_markup)


async def show_admin_menu(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    dm = get_data_manager()
    maintenance_status = "🔧 مفعل" if await dm.get_maintenance_mode() else "✅ غير مفعل"
    text = f"👨‍💼 أهلاً بك أيها الأدمن {user.first_name}!"
    reply_markup = build_admin_main_keyboard(maintenance_status)
    await _send_or_edit(update, text, reply_markup)


async def _send_or_edit(update: Update, text: str,
                        reply_markup: InlineKeyboardMarkup, parse_mode: str = None) -> None:
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode)
        except BadRequest as e:
            if "message to edit not found" in str(e):
                await update.effective_chat.send_message(
                    text, reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                raise e
