"""
إدارة الحظر (Ban Management)
"""
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.error import BadRequest

from config import logger
from data_manager import get_data_manager
from keyboards import build_support_keyboard
from utils import is_admin_in_group


async def check_if_banned(update: Update, context: CallbackContext) -> bool:
    """التحقق من الحظر. يعيد True إذا كان المستخدم محظوراً."""
    user_id = update.effective_user.id
    dm = get_data_manager()

    if await dm.is_banned(user_id):
        logger.warning(f"Banned user {user_id} tried to interact.")
        ban_message = "🚫 لقد تم حظرك من استخدام هذا البوت. للاستفسار، يرجى التواصل مع الدعم."
        try:
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.edit_message_text(
                    ban_message, reply_markup=build_support_keyboard())
            else:
                await update.message.reply_text(
                    ban_message, reply_markup=build_support_keyboard())
        except BadRequest:
            await context.bot.send_message(
                chat_id=user_id, text=ban_message,
                reply_markup=build_support_keyboard())
        return True
    return False


async def ban_user_command(update: Update, context: CallbackContext) -> None:
    """أمر /ban (مجموعة الأدمن) - بالرد على رسالة الطلب أو بمعرف رقمي"""
    if not await is_admin_in_group(update.effective_user.id, context):
        await update.message.reply_text("⛔ ليس لديك الصلاحية.")
        return

    user_id_to_ban, user_name_to_ban = None, None
    dm = get_data_manager()

    if update.message.reply_to_message:
        content = update.message.reply_to_message.text or update.message.reply_to_message.caption
        if content:
            id_match = re.search(r"ID:\s*(\d+)", content)
            name_match = re.search(r"من:\s*([^\n]+)", content)
            if id_match:
                user_id_to_ban = int(id_match.group(1))
                user_name_to_ban = (
                    name_match.group(1).strip() if name_match
                    else (await dm.get_user_info(user_id_to_ban) or {}).get(
                        'first_name', f"المستخدم {user_id_to_ban}")
                )
    elif context.args:
        try:
            user_id_to_ban = int(context.args[0])
            user_name_to_ban = f"المستخدم {user_id_to_ban}"
        except (ValueError, IndexError):
            await update.message.reply_text("❌ خطأ. يرجى إدخال معرف مستخدم رقمي صحيح.")
            return

    if user_id_to_ban:
        await dm.ban_user(user_id_to_ban)
        await dm.remove_user(user_id_to_ban)  # soft-delete
        await update.message.reply_text(
            f"🚫 تم حظر {user_name_to_ban} من استخدام البوت.")
    else:
        await update.message.reply_text(
            "ℹ️ الاستخدام: `/ban <user_id>` أو بالرد على رسالة الطلب.")


async def unban_user_command(update: Update, context: CallbackContext) -> None:
    """أمر /unban (مجموعة الأدمن)"""
    if not await is_admin_in_group(update.effective_user.id, context):
        await update.message.reply_text("⛔ ليس لديك الصلاحية.")
        return

    user_id_to_unban, user_name_to_unban = None, None
    dm = get_data_manager()

    if update.message.reply_to_message:
        content = update.message.reply_to_message.text or update.message.reply_to_message.caption
        if content:
            id_match = re.search(r"ID:\s*(\d+)", content)
            name_match = re.search(r"من:\s*([^\n]+)", content)
            if id_match:
                user_id_to_unban = int(id_match.group(1))
                user_name_to_unban = (
                    name_match.group(1).strip() if name_match
                    else (await dm.get_user_info(user_id_to_unban) or {}).get(
                        'first_name', f"المستخدم {user_id_to_unban}")
                )
    elif context.args:
        try:
            user_id_to_unban = int(context.args[0])
            user_name_to_unban = f"المستخدم {user_id_to_unban}"
        except (ValueError, IndexError):
            await update.message.reply_text("❌ خطأ. يرجى إدخال معرف مستخدم رقمي صحيح.")
            return

    if user_id_to_unban:
        await dm.unban_user(user_id_to_unban)
        user_info = await dm.get_user_info(user_id_to_unban)
        if user_info and not user_info.get('is_active', 1):
            await dm.add_user(user_id_to_unban, {
                'username': user_info.get('username'),
                'first_name': user_info.get('first_name')
            })
        await update.message.reply_text(f"✅ تم فك الحظر عن {user_name_to_unban}.")
    else:
        await update.message.reply_text(
            "ℹ️ الاستخدام: `/unban <user_id>` أو بالرد على رسالة الطلب.")


async def manage_bans_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    dm = get_data_manager()
    banned_users = await dm.get_banned_users()
    message_text = f"⛔ <b>إدارة المحظورين ({len(banned_users)} مستخدم)</b>\n\n"
    keyboard = []

    if not banned_users:
        message_text += "✅ لا يوجد مستخدمون محظورون حالياً."
    else:
        message_text += "اختر مستخدماً لفك الحظر عنه:"
        for user_id in banned_users:
            user_info = (await dm.get_user_info(user_id)) or {}
            display_name = user_info.get('first_name', f"ID: {user_id}")
            keyboard.append([
                InlineKeyboardButton(
                    f"🔓 فك الحظر عن {display_name}",
                    callback_data=f"admin_unban_{user_id}")
            ])

    keyboard.append([
        InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="admin_main_menu")])

    await query.edit_message_text(
        message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')


async def unban_user_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer(text="✅ تم فك الحظر.")

    user_id_to_unban = int(query.data.split('_')[-1])
    dm = get_data_manager()
    await dm.unban_user(user_id_to_unban)
    user_info = await dm.get_user_info(user_id_to_unban)
    if user_info and not user_info.get('is_active', 1):
        await dm.add_user(user_id_to_unban, {
            'username': user_info.get('username'),
            'first_name': user_info.get('first_name')
        })
    await manage_bans_handler(update, context)
