"""
نظام تقييم الخدمة بعد الاكتمال
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from config import logger, ADMIN_GROUP_ID
from data_manager import get_data_manager

RATING_STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}
RATING_LABELS = {1: "سيء جداً", 2: "سيء", 3: "مقبول", 4: "جيد", 5: "ممتاز"}


async def request_rating_after_completion(
    context: CallbackContext, user_id: int, order_key: str, service_name: str
):
    """إرسال طلب تقييم بعد إكمال الطلب"""
    dm = get_data_manager()

    existing = await dm.get_order_rating(order_key)
    if existing:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐", callback_data=f"rate_1_{order_key}"),
            InlineKeyboardButton("⭐⭐", callback_data=f"rate_2_{order_key}"),
            InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_3_{order_key}"),
        ],
        [
            InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_4_{order_key}"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_5_{order_key}"),
        ],
        [InlineKeyboardButton("تخطي", callback_data=f"rate_skip_{order_key}")],
    ])

    rating_text = (
        f"✅ <b>تم إكمال طلبك بنجاح!</b>\n\n"
        f"📦 الخدمة: {service_name}\n"
        f"🔑 الطلب: <code>{order_key}</code>\n\n"
        f"📝 <b>كيف تقيّم تجربتك معنا؟</b>\n"
        f"تقييمك يساعدنا في تحسين خدماتنا."
    )

    try:
        await context.bot.send_message(
            chat_id=user_id, text=rating_text, reply_markup=keyboard,
            parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send rating request to {user_id}: {e}")


async def handle_rating_callback(update: Update, context: CallbackContext):
    """معالج أزرار التقييم (مجموعة أولوية -1)"""
    query = update.callback_query
    await query.answer()

    data = query.data  # rate_N_orderkey أو rate_skip_orderkey
    try:
        parts = data.split("_", 2)
        action = parts[1]
        order_key = parts[2] if len(parts) > 2 else ""
    except (IndexError, ValueError):
        await query.edit_message_text("❌ حدث خطأ في معالجة التقييم.")
        return

    if action == "skip":
        await query.edit_message_text(
            "✅ لا بأس! شكراً لاستخدامك خدمتنا.\n"
            "يمكنك دائماً تقديم طلب جديد من القائمة الرئيسية.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_start")]
            ]))
        return

    try:
        rating = int(action)
        if rating < 1 or rating > 5:
            raise ValueError
    except ValueError:
        await query.edit_message_text("❌ تقييم غير صالح.")
        return

    dm = get_data_manager()
    user_id = query.from_user.id

    if await dm.get_order_rating(order_key):
        await query.edit_message_text("✅ لقد قيّمت هذا الطلب مسبقاً. شكراً لك!")
        return

    await dm.save_rating(order_key, user_id, rating)

    stars = RATING_STARS.get(rating, "⭐")
    label = RATING_LABELS.get(rating, "")

    await query.edit_message_text(
        f"شكراً لتقييمك! {stars}\n"
        f"تقييمك: <b>{label}</b> ({rating}/5)\n\n"
        f"نقدّر ملاحظاتك ونسعى دائماً لتحسين خدماتنا. 🙏",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_start")]
        ]),
        parse_mode=ParseMode.HTML)

    # إشعار المشرف بالتقييمات المهمة فقط (سيئة جداً أو ممتازة)
    if rating <= 2 or rating == 5:
        try:
            order = await dm.get_order(order_key)
            order_info = order.get('service', 'غير معروف') if order else 'غير معروف'
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"{'⚠️' if rating <= 2 else '🎉'} <b>تقييم جديد</b>\n\n"
                    f"👤 المستخدم: {query.from_user.full_name} (@{query.from_user.username or 'N/A'})\n"
                    f"🔑 الطلب: <code>{order_key}</code>\n"
                    f"📦 الخدمة: {order_info}\n"
                    f"⭐ التقييم: {stars} ({rating}/5) - {label}"
                ),
                parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send rating notification to admin: {e}")


async def show_ratings_stats(update: Update, context: CallbackContext):
    """إحصائيات التقييمات للمشرف"""
    query = update.callback_query
    await query.answer()

    dm = get_data_manager()
    stats = await dm.get_rating_stats()

    if not stats or stats.get('total_ratings', 0) == 0:
        await query.edit_message_text(
            "📊 <b>إحصائيات التقييمات</b>\n\nلا توجد تقييمات مسجلة بعد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="admin_stats")]
            ]),
            parse_mode=ParseMode.HTML)
        return

    total = stats['total_ratings']
    avg = stats['average_rating']
    distribution = stats.get('distribution', {})

    dist_text = ""
    for star in range(5, 0, -1):
        count = distribution.get(str(star), 0)
        percentage = (count / total * 100) if total > 0 else 0
        bar_length = int(percentage / 5)
        bar = "█" * bar_length + "░" * (20 - bar_length)
        dist_text += f"{star}⭐ {bar} {count} ({percentage:.0f}%)\n"

    stats_text = (
        f"📊 <b>إحصائيات التقييمات</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ <b>متوسط التقييم:</b> {avg:.1f}/5\n"
        f"📋 <b>إجمالي التقييمات:</b> {total}\n\n"
        f"<b>التوزيع:</b>\n{dist_text}"
    )

    await query.edit_message_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_stats")]
        ]),
        parse_mode=ParseMode.HTML)
