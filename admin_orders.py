"""
إدارة الطلبات (أدمن)
يتضمن: الموافقة/الرفض، الرد على الطالب، الإغلاق، القوائم مع pagination، طلب التقييم
"""
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import logger, ORDERS_PER_PAGE
from data_manager import get_data_manager
from keyboards import build_back_to_admin_menu_keyboard
from utils import is_admin_in_group


async def admin_action_callback(update: Update, context: CallbackContext) -> None:
    """أزرار الموافقة/الرفض من مجموعة الأدمن (يدعم caption للصور وPDF)"""
    query = update.callback_query

    if not await is_admin_in_group(query.from_user.id, context):
        await query.answer("⛔ ليس لديك الصلاحية.", show_alert=True)
        return

    await query.answer()

    if query.data.startswith("admin_approve_"):
        action_type = "approve"
        order_key = query.data[len("admin_approve_"):]
    elif query.data.startswith("admin_reject_"):
        action_type = "reject"
        order_key = query.data[len("admin_reject_"):]
    else:
        await query.answer("❌ إجراء غير معروف.", show_alert=True)
        return

    dm = get_data_manager()
    order_data = await dm.get_order(order_key)
    admin_user = query.from_user

    if not order_data:
        await query.edit_message_text("❌ لم يعد هذا الطلب موجوداً.")
        return

    target_user_id = order_data['user_id']
    original_content = query.message.caption or query.message.text
    status_text = ""

    if action_type == "approve":
        await dm.update_order_status(order_key, 'موافق عليه', changed_by=admin_user.id)
        status_text = (
            f"\n\n<b>---\n✅ تم تأكيد الدفع بواسطة: {admin_user.first_name}\n"
            f"💬 يمكنك الآن الرد على هذه الرسالة لإرسال الملفات للطالب.</b>\n---"
        )
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"🎉 تهانينا! تم تأكيد طلبك رقم {order_key} وجاري تحضيره.")
        except Exception as e:
            status_text += f"\n⚠️ فشل إرسال الإشعار للطالب: {e}"

    elif action_type == "reject":
        await dm.update_order_status(order_key, 'مرفوض بانتظار سبب', changed_by=admin_user.id)
        status_text = (
            f"\n\n<b>---\n❌ تم رفض الدفع بواسطة: {admin_user.first_name}\n"
            f"📝 يرجى الرد على هذه الرسالة لإرسال سبب الرفض للطالب.</b>\n---"
        )

    new_content = original_content + status_text

    # صورة أو مستند ← edit_caption | نص ← edit_text (إصلاح PDF المطبق)
    if query.message.photo or query.message.document:
        try:
            await query.edit_message_caption(
                caption=new_content, reply_markup=None, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id, text=new_content,
                    parse_mode=ParseMode.HTML)
    else:
        try:
            await query.edit_message_text(
                text=new_content, reply_markup=None, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id, text=new_content,
                    parse_mode=ParseMode.HTML)


def schedule_completion_notification(context: CallbackContext, user_id: int, order_key: str):
    """جدولة إشعار اكتمال + طلب تقييم (وظيفة واحدة عبر JobQueue - آمنة)"""
    context.job_queue.run_once(
        callback=send_completion_notification,
        when=5,
        data={'user_id': user_id, 'order_key': order_key},
        name=f"completion_notify_{order_key}")


async def send_completion_notification(context: CallbackContext):
    """إشعار الاكتمال للطالب + طلب تقييم"""
    data = context.job.data
    user_id = data['user_id']
    order_key = data['order_key']

    dm = get_data_manager()
    order_data = await dm.get_order(order_key)
    service_name = order_data.get('service', '') if order_data else ''

    try:
        from notification_retry import NotificationRetryManager
        await NotificationRetryManager.safe_send_message(
            bot=context.bot,
            chat_id=user_id,
            text=f"✅ تم اكتمال طلبك رقم {order_key}. شكراً لثقتك بنا!",
            notification_type="order_completed",
            reference_id=order_key)
    except Exception as e:
        logger.error(f"Failed to send completion notification to {user_id}: {e}")

    try:
        from rating_system import request_rating_after_completion
        await request_rating_after_completion(context, user_id, order_key, service_name)
    except Exception as e:
        logger.error(f"Failed to send rating request: {e}")


async def admin_reply_handler(update: Update, context: CallbackContext) -> None:
    """رد الأدمن على رسالة طلب في المجموعة (نسخ للطالب / إغلاق تلقائي / سبب رفض)"""
    if not await is_admin_in_group(update.effective_user.id, context):
        return

    if not update.message.reply_to_message:
        return

    content_to_parse = update.message.reply_to_message.text \
        or update.message.reply_to_message.caption
    order_key_match = re.search(r"Order Key:\s*(order_\d+_\d+)", content_to_parse or "")

    if not order_key_match:
        await update.message.reply_text("❌ لم أتمكن من تحديد الطلب من هذه الرسالة.")
        return

    order_key = order_key_match.group(1)
    dm = get_data_manager()
    order_data = await dm.get_order(order_key)

    if not order_data:
        await update.message.reply_text("❌ بيانات الطلب غير موجودة.")
        return

    target_user_id = order_data.get('user_id')
    order_status = order_data.get('status')

    if not target_user_id:
        await update.message.reply_text("❌ لم أجد معرف الطالب لهذا الطلب.")
        return

    try:
        if order_status == 'مرفوض بانتظار سبب':
            reason = update.message.text if update.message.text else "غير محدد"
            await dm.update_order_status(order_key, 'مرفوض', changed_by=update.effective_user.id)

            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"❌ نعتذر، تم رفض طلبك رقم {order_key}.\n📝 سبب الرفض: {reason}")
            await update.message.reply_text("✅ تم إرسال سبب الرفض للطالب.")

        else:
            await context.bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id)

            # إرسال ملفات على طلب موافق عليه → اكتمال تلقائي + تقييم
            if (order_status == 'موافق عليه'
                    and (update.message.document or update.message.photo
                         or update.message.video or update.message.audio)):
                schedule_completion_notification(context, target_user_id, order_key)
                await dm.update_order_status(
                    order_key, 'مكتمل', changed_by=update.effective_user.id)
                await update.message.reply_text(
                    "✅ تم إرسال الملفات وجدولة إشعار الاكتمال وطلب التقييم.")
            else:
                await update.message.reply_text("✅ تم إرسال الرسالة للطالب.")

    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الرسالة. الخطأ: {e}")


async def complete_order_handler(update: Update, context: CallbackContext) -> None:
    """أمر /complete لإغلاق طلب يدوياً (نص عادي - بلا Markdown لتفادي أخطاء التهريب)"""
    if not await is_admin_in_group(update.effective_user.id, context):
        return

    if not context.args:
        await update.message.reply_text("ℹ️ الاستخدام: /complete <order_id>")
        return

    order_key = context.args[0]
    dm = get_data_manager()
    if await dm.update_order_status(order_key, 'مكتمل', changed_by=update.effective_user.id):
        order_data = await dm.get_order(order_key)
        if order_data and order_data.get('user_id'):
            schedule_completion_notification(context, order_data['user_id'], order_key)
        await update.message.reply_text(
            f"✅ تم تغيير حالة الطلب {order_key} إلى 'مكتمل'.")
    else:
        await update.message.reply_text(
            f"❌ لم يتم العثور على طلب بالمعرف {order_key}.")


async def admin_orders_menu(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🆕 طلبات جديدة (قيد المراجعة)", callback_data="orders_list_new_0")],
        [InlineKeyboardButton("✅ طلبات مؤكدة (مفتوحة)", callback_data="orders_list_open_0")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="admin_main_menu")]
    ]

    await query.edit_message_text(
        "<b>📋 قائمة الطلبات</b>\n\nاختر القسم الذي تريد عرضه:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML)


async def orders_list_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split('_')
    order_type = data_parts[2]
    page = int(data_parts[3])

    status = "قيد المراجعة" if order_type == "new" else "موافق عليه"
    callback_prefix = "orders_list_new" if order_type == "new" else "orders_list_open"

    message_text, reply_markup = await build_orders_paginated_keyboard(
        page, status, callback_prefix)
    await query.edit_message_text(
        message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def build_orders_paginated_keyboard(page: int, status: str, callback_prefix: str):
    dm = get_data_manager()
    orders = await dm.get_orders_by_status(status)

    if not orders:
        return (
            "📭 لا توجد طلبات في هذا القسم حالياً.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 عودة لقائمة الطلبات", callback_data="admin_orders_menu")]
            ]))

    orders_dict = dict(orders)
    order_keys = sorted((k for k, _ in orders), key=lambda k: int(k.split('_')[-1]),
                        reverse=True)
    start_index = page * ORDERS_PER_PAGE
    end_index = start_index + ORDERS_PER_PAGE
    paginated_keys = order_keys[start_index:end_index]

    keyboard = [
        [InlineKeyboardButton(
            f"👤 طلب من {orders_dict.get(key, {}).get('full_name', 'N/A')}",
            callback_data=f"orders_view_{key}")]
        for key in paginated_keys
    ]

    pagination_row = []
    if page > 0:
        pagination_row.append(
            InlineKeyboardButton("⬅️ السابق", callback_data=f"{callback_prefix}_{page - 1}"))
    if end_index < len(order_keys):
        pagination_row.append(
            InlineKeyboardButton("التالي ➡️", callback_data=f"{callback_prefix}_{page + 1}"))
    if pagination_row:
        keyboard.append(pagination_row)

    keyboard.append([
        InlineKeyboardButton("🔙 عودة لقائمة الطلبات", callback_data="admin_orders_menu")])

    total_pages = -(-len(order_keys) // ORDERS_PER_PAGE)
    title = ("🆕 الطلبات الجديدة (قيد المراجعة)"
             if status == "قيد المراجعة" else "✅ الطلبات المفتوحة (المؤكدة)")

    return (
        f"<b>{title} (صفحة {page + 1} من {total_pages})</b>",
        InlineKeyboardMarkup(keyboard))


async def orders_view_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    order_key = query.data.replace("orders_view_", "")
    dm = get_data_manager()
    order_data = await dm.get_order(order_key)

    if not order_data:
        await query.edit_message_text("❌ هذا الطلب لم يعد موجوداً.")
        return

    details_text = (
        f"<b>🧾 تفاصيل الطلب</b> <code>{order_key}</code>\n"
        f"<b>👤 الطالب:</b> {order_data.get('full_name', 'N/A')} "
        f"(@{order_data.get('username', 'N/A')})\n"
        f"<b>🆔 ID:</b> <code>{order_data.get('user_id', 'N/A')}</code>\n\n"
        f"<b><u>📋 الطلب:</u></b>\n"
        f"<b>🎓 التخصص:</b> {order_data.get('specialization') or 'غير محدد'}\n"
        f"<b>📦 الخدمة:</b> {order_data.get('service', 'N/A')}\n"
        f"<b>📚 المواد ({len(order_data.get('courses', []))}):</b> "
        f"{', '.join(order_data.get('courses', []))}\n"
        f"<b>💳 الدفع:</b> {order_data.get('payment_method', 'N/A')} "
        f"({order_data.get('total_price', 0)} ل.س)\n"
    )

    keyboard = []
    if order_data.get('status') == 'موافق عليه':
        keyboard.append([
            InlineKeyboardButton("💬 الرد على الطالب",
                                 callback_data=f"orders_remind_reply_{order_key}")])
        keyboard.append([
            InlineKeyboardButton("✅ إغلاق الطلب يدوياً",
                                 callback_data=f"orders_complete_{order_key}")])

    keyboard.append([
        InlineKeyboardButton("🔙 عودة لقائمة الطلبات", callback_data="admin_orders_menu")])

    await query.edit_message_text(
        details_text, reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML)


async def orders_action_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    data = query.data

    if data.startswith("orders_complete_"):
        order_key = data[len("orders_complete_"):]
    elif data.startswith("orders_remind_reply_"):
        order_key = data[len("orders_remind_reply_"):]
    else:
        await query.answer("❌ إجراء غير معروف.", show_alert=True)
        return

    if data.startswith("orders_remind_reply_"):
        await query.answer(
            "💬 قم الآن بالرد على رسالة الطلب في المجموعة لإرسال الملفات للطالب.",
            show_alert=True)
    elif data.startswith("orders_complete_"):
        dm = get_data_manager()
        if await dm.update_order_status(order_key, 'مكتمل', changed_by=query.from_user.id):
            order_data = await dm.get_order(order_key)
            if order_data and order_data.get('user_id'):
                schedule_completion_notification(context, order_data['user_id'], order_key)
            await query.answer("✅ تم إغلاق الطلب يدوياً.", show_alert=True)
        await admin_orders_menu(update, context)
