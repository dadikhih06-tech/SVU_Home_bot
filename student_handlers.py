"""
معالجات واجهة الطالب - التسلسل الجديد مع سلة المشتريات
التدفق: طلب خدمة ← التخصص ← الخدمة (سعر) ← كتلة ← مواد
السلة: تجمع المختارات عبر الكتل جميعها مع الإجمالي، إزالة عنصر، مسح، ثم الدفع
"""
import os
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, ConversationHandler
from telegram.constants import ParseMode, ChatType
from telegram.error import BadRequest

from config import (
    logger, ADMIN_IDS, ADMIN_GROUP_ID, MAIN_ADMIN_ID,
    MAX_COURSES_PER_ORDER, MAX_RECEIPT_SIZE_MB
)
from states import (
    WELCOME, CHOOSE_SPEC, CHOOSE_SERVICE, CHOOSE_BLOCK, CHOOSE_COURSES,
    CHOOSE_PAYMENT, GET_PAYMENT_INFO, CONFIRM_ORDER, SEARCH_COURSES, VIEW_CART
)
from data_manager import get_data_manager
from keyboards import (
    build_student_main_keyboard, build_support_keyboard,
    build_back_to_start_keyboard
)
from ban_manager import check_if_banned
from cooldown_manager import (
    check_order_cooldown, start_cooldown_timer,
    schedule_order_timeout, schedule_cooldown_reminder, cancel_user_jobs
)
from utils import chunks, cb_hash, course_cb
from rate_limiter import get_rate_limiter
from input_validator import InputValidator
from bot_ui import student_welcome_text


# ═══════════════ القائمة الرئيسية ═══════════════

async def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return ConversationHandler.END

    dm = get_data_manager()

    if user.id not in ADMIN_IDS and chat.type == ChatType.PRIVATE:
        if await dm.is_banned(user.id):
            return await _reject_banned(update, context)

    await dm.add_user(user.id, {
        'username': user.username,
        'first_name': user.first_name
    })

    canceled = False
    if user.id not in ADMIN_IDS:
        if context.user_data:
            context.user_data.clear()
            canceled = True
        cancel_user_jobs(context, user.id)
        if canceled and update.message:
            await update.message.reply_text("✅ تم إلغاء أي طلبات سابقة غير مكتملة. مرحبًا بك!")

    if user.id in ADMIN_IDS and chat.type == ChatType.PRIVATE:
        from bot_ui import show_admin_menu
        await show_admin_menu(update, context)
        return ConversationHandler.END

    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return ConversationHandler.END

    if await dm.get_maintenance_mode():
        maintenance_text = "🔧 البوت تحت الصيانة حالياً لإجراء تحديثات. يرجى المحاولة لاحقاً."
        if update.message:
            await update.message.reply_text(maintenance_text, reply_markup=build_support_keyboard())
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                maintenance_text, reply_markup=build_support_keyboard())
        return ConversationHandler.END

    prices = await dm.get_prices()
    services_list = (
        "\n".join([f"- {service} ({price} ل.س للمادة)" for service, price in prices.items()])
        if prices else "لا توجد خدمات متاحة حالياً."
    )
    student_text = student_welcome_text(user.first_name, services_list)
    reply_markup = build_student_main_keyboard()

    if update.message:
        await update.message.reply_text(student_text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(student_text, reply_markup=reply_markup)

    return WELCOME


async def _reject_banned(update: Update, context: CallbackContext) -> int:
    text = "🚫 لقد تم حظرك من استخدام هذا البوت. للاستفسار، يرجى التواصل مع الدعم."
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text, reply_markup=build_support_keyboard())
        else:
            await update.message.reply_text(text, reply_markup=build_support_keyboard())
    except BadRequest:
        pass
    return ConversationHandler.END


# ═══════════════ القوائم الفرعية ═══════════════

async def my_orders_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    dm = get_data_manager()
    user_orders = await dm.get_orders_by_user(query.from_user.id, limit=10)

    if not user_orders:
        await query.edit_message_text(
            "📭 ليس لديك أي طلبات مسجلة حاليًا.",
            reply_markup=build_back_to_start_keyboard())
        return

    message_text = "📋 <b>سجل طلباتك (آخر 10 طلبات):</b>\n\n"
    user_orders.sort(key=lambda item: int(item[0].split('_')[-1]), reverse=True)

    STATUS_DISPLAY = {
        'قيد المراجعة': ('⏳', 'قيد المراجعة'),
        'موافق عليه': ('✅', 'موافق عليه'),
        'مكتمل': ('✅', 'مكتمل'),
        'مرفوض': ('❌', 'مرفوض')
    }

    for key, order in user_orders:
        status_key = order.get('status', 'قيد المراجعة')
        status_emoji, status_text = STATUS_DISPLAY.get(status_key, ('⏳', 'قيد المراجعة'))
        message_text += (
            f"<b>🔑 الطلب:</b> <code>{key}</code>\n"
            f"<b>🎓 التخصص:</b> {order.get('specialization') or 'غير محدد'}\n"
            f"<b>📦 الخدمة:</b> {order.get('service', 'N/A')}\n"
            f"<b>📊 الحالة:</b> {status_emoji} {status_text}\n"
            f"{'─' * 20}\n"
        )

    await query.edit_message_text(
        message_text, reply_markup=build_back_to_start_keyboard(), parse_mode=ParseMode.HTML)


async def my_profile_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    dm = get_data_manager()
    profile = await dm.get_user_profile(query.from_user.id)

    if not profile:
        await query.edit_message_text(
            "❌ لم يتم العثور على بياناتك. اضغط /start للتسجيل.",
            reply_markup=build_back_to_start_keyboard())
        return

    joined_at = profile.get('joined_at', 'غير معروف')
    if joined_at and joined_at != 'غير معروف':
        try:
            dt = datetime.fromisoformat(str(joined_at).replace(' ', 'T').split('.')[0])
            joined_at = dt.strftime('%Y-%m-%d')
        except Exception:
            pass

    last_order = profile.get('last_order_date')
    if last_order:
        try:
            dt = datetime.fromisoformat(str(last_order).replace(' ', 'T').split('.')[0])
            last_order = dt.strftime('%Y-%m-%d')
        except Exception:
            pass
    else:
        last_order = "لا يوجد"

    profile_text = (
        f"👤 <b>حسابي</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 <b>الاسم:</b> {profile.get('first_name', 'غير معروف')}\n"
        f"🆔 <b>المعرف:</b> @{profile.get('username') or 'لا يوجد'}\n"
        f"📅 <b>مسجل منذ:</b> {joined_at}\n\n"
        f"📊 <b>إحصائيات الطلبات:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>إجمالي الطلبات:</b> {profile.get('total_orders', 0)}\n"
        f"  ⏳ قيد المراجعة: {profile.get('pending_orders', 0)}\n"
        f"  ✅ موافق عليها: {profile.get('approved_orders', 0)}\n"
        f"  ✅ مكتملة: {profile.get('completed_orders', 0)}\n"
        f"  ❌ مرفوضة: {profile.get('rejected_orders', 0)}\n\n"
        f"💰 <b>إجمالي المدفوع:</b> {profile.get('total_spent', 0):,} ل.س\n"
        f"🕐 <b>آخر طلب:</b> {last_order}"
    )

    await query.edit_message_text(
        profile_text, reply_markup=build_back_to_start_keyboard(), parse_mode=ParseMode.HTML)


async def faq_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    dm = get_data_manager()
    prices = await dm.get_prices()
    payment_methods = await dm.get_payment_methods()

    prices_list_faq = "\n".join(
        [f"- <b>{service}</b>: {price} ل.س للمادة" for service, price in prices.items()])
    payment_methods_faq = ", ".join(payment_methods.keys()) if payment_methods else "لا يوجد"

    faq_text = (
        f"<b>❓ الأسئلة الشائعة</b>\n\n"
        f"<b>س: كيف أقدّم طلباً؟</b>\n"
        f"ج: طلب خدمة ← اختر تخصصك (علوم الإدارة / إدارة أعمال) ← اختر الخدمة "
        f"(ملخصات أو اختبارات) ← اختر المواد من كتل المقررات وأضفها للسلة ← إتمام الطلب والدفع.\n\n"
        f"<b>س: ما هي أسعار الخدمات؟</b>\nج: للمادة الواحدة:\n{prices_list_faq}\n\n"
        f"<b>س: ما هي كتل المقررات؟</b>\n"
        f"ج: خمس كتل لكل تخصص: المقررات العامة، الأساسية، "
        f"اختصاص التسويق، اختصاص إدارة الموارد البشرية، اختصاص المالية والمصارف.\n\n"
        f"<b>س: ما هي سلة المشتريات؟</b>\n"
        f"ج: يمكنك التنقل بين الكتل وجمع المواد في سلة واحدة، "
        f"ثم الدفع لكل المحتوى مرة واحدة.\n\n"
        f"<b>س: ما هي طرق الدفع؟</b>\nج: {payment_methods_faq}.\n\n"
        f"<b>س: كم يستغرق وصول الطلب؟</b>\n"
        f"ج: خلال 24 ساعة كحد أقصى بعد إرسال إشعار الدفع."
    )

    keyboard = [
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_start")]
    ]
    await query.edit_message_text(
        faq_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def main_menu_callback_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query

    if query.data == "my_orders":
        await my_orders_handler(update, context)
    elif query.data == "my_profile":
        await my_profile_handler(update, context)
    elif query.data == "faq":
        await faq_handler(update, context)
    elif query.data == "back_to_start":
        context.user_data.clear()
        await start(update, context)
        return WELCOME
    return WELCOME


# ═══════════════ بدء الطلب: التخصص ═══════════════

async def start_order_conversation(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    try:
        if await check_if_banned(update, context):
            return ConversationHandler.END

        user_id = update.effective_user.id

        rl = get_rate_limiter()
        allowed, reason = rl.check_rate(user_id, "order")
        if not allowed:
            await query.answer(reason, show_alert=True)
            return ConversationHandler.END

        await query.answer()

        if not await check_order_cooldown(user_id, context):
            await start_cooldown_timer(update, context, user_id)
            return ConversationHandler.END

        dm = get_data_manager()
        specs = await dm.get_specializations()
        if not specs:
            await query.edit_message_text(
                "❌ عذراً، لا توجد تخصصات متاحة حالياً. يرجى مراجعة الأدمن.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 العودة", callback_data="back_to_start")]]))
            return ConversationHandler.END

        context.user_data['specs_map'] = {cb_hash(s): s for s in specs}

        keyboard = [
            [InlineKeyboardButton(f"🎓 {s}", callback_data=f"spec_{cb_hash(s)}")]
            for s in specs
        ]
        keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="back_to_start")])

        await query.edit_message_text(
            text="🎓 اختر تخصصك أولاً:",
            reply_markup=InlineKeyboardMarkup(keyboard))

        schedule_order_timeout(context, user_id)
        return CHOOSE_SPEC

    except Exception as e:
        logger.error(f"Error in start_order_conversation: {e}", exc_info=True)
        try:
            await query.edit_message_text("❌ حدث خطأ ما، يرجى المحاولة بالضغط على /start")
        except Exception:
            pass
        return ConversationHandler.END


async def _show_blocks_menu(update: Update, context: CallbackContext) -> int:
    """عرض كتل المقررات للتخصص المحدد (السلة محفوظة)"""
    query = update.callback_query
    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')
    dm = get_data_manager()

    blocks = await dm.get_blocks_for_spec(spec)
    blocks_with_counts = []
    for b in blocks:
        n = len(await dm.get_courses_for_block(spec, b))
        blocks_with_counts.append((b, n))

    context.user_data['blocks_map'] = {cb_hash(b): b for b, _ in blocks_with_counts}

    cart = context.user_data.get('cart', [])
    prices = await dm.get_prices()
    price = prices.get(service, 0)
    total = price * len(cart)

    keyboard = [
        [InlineKeyboardButton(f"📚 {b} ({n} مادة)", callback_data=f"block_{cb_hash(b)}")]
        for b, n in blocks_with_counts
    ]
    cart_btn_text = f"🛒 السلة ({len(cart)}/{MAX_COURSES_PER_ORDER})" + \
                    (f" — {total:,} ل.س" if cart else "")
    keyboard.append([InlineKeyboardButton(cart_btn_text, callback_data="view_cart")])
    if cart:
        keyboard.append([InlineKeyboardButton("💳 إتمام الطلب", callback_data="done_selecting_courses")])
    keyboard.append([InlineKeyboardButton("🔙 العودة للخدمات", callback_data="back_to_services")])

    text = (
        f"🎓 التخصص: <b>{spec}</b>\n"
        f"📦 الخدمة: <b>{service}</b> ({price} ل.س للمادة)\n\n"
        f"اختر كتلة المقررات لإضافة المواد إلى سلتك:"
    )
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return CHOOSE_BLOCK


async def choose_spec_callback(update: Update, context: CallbackContext) -> int:
    """اختيار التخصص ← عرض الخدمات"""
    query = update.callback_query

    rl = get_rate_limiter()
    allowed, reason = rl.check_rate(query.from_user.id, "callback")
    if not allowed:
        await query.answer(reason, show_alert=True)
        return CHOOSE_SPEC

    await query.answer()
    action = query.data

    if action == "back_to_start":
        context.user_data.clear()
        cancel_user_jobs(context, query.from_user.id)
        await start(update, context)
        return ConversationHandler.END

    if action.startswith("spec_"):
        h = action.split('spec_', 1)[1]
        spec = context.user_data.get('specs_map', {}).get(h)
        if not spec:
            await query.answer("❌ التخصص غير موجود، أعد المحاولة.", show_alert=True)
            return CHOOSE_SPEC

        # تغيير التخصص = تصفير السلة (كتالوج مختلف)
        if context.user_data.get('specialization') != spec:
            context.user_data['cart'] = []
        context.user_data['specialization'] = spec

        dm = get_data_manager()
        services = await dm.get_active_services_for_spec(spec)
        if not services:
            await query.edit_message_text(
                "❌ لا توجد خدمات متاحة لهذا التخصص حالياً. يرجى إبلاغ الأدمن.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 العودة", callback_data="back_to_start")]]))
            return CHOOSE_SPEC

        prices = await dm.get_prices()
        context.user_data['services_map'] = {cb_hash(s): s for s in services}

        keyboard = [
            [InlineKeyboardButton(
                f"📦 {s} ({prices.get(s, '?'):,} ل.س للمادة)",
                callback_data=f"service_{cb_hash(s)}")]
            for s in services
        ]
        keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="back_to_start")])

        await query.edit_message_text(
            text=f"🎓 التخصص: <b>{spec}</b>\n\n📦 اختر الخدمة المطلوبة:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML)
        return CHOOSE_SERVICE

    return CHOOSE_SPEC


async def choose_service_callback(update: Update, context: CallbackContext) -> int:
    """اختيار الخدمة ← عرض الكتل"""
    query = update.callback_query

    rl = get_rate_limiter()
    allowed, reason = rl.check_rate(query.from_user.id, "callback")
    if not allowed:
        await query.answer(reason, show_alert=True)
        return CHOOSE_SERVICE

    await query.answer()
    action = query.data
    spec = context.user_data.get('specialization')

    if not spec:
        return await start(update, context)

    if action == "back_to_start":
        context.user_data.clear()
        cancel_user_jobs(context, query.from_user.id)
        await start(update, context)
        return ConversationHandler.END

    if action == "back_to_services":
        dm = get_data_manager()
        services = await dm.get_active_services_for_spec(spec)
        prices = await dm.get_prices()
        context.user_data['services_map'] = {cb_hash(s): s for s in services}
        keyboard = [
            [InlineKeyboardButton(
                f"📦 {s} ({prices.get(s, '?'):,} ل.س للمادة)",
                callback_data=f"service_{cb_hash(s)}")]
            for s in services
        ]
        keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="back_to_start")])
        await query.edit_message_text(
            f"🎓 التخصص: <b>{spec}</b>\n\n📦 اختر الخدمة المطلوبة:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return CHOOSE_SERVICE

    if action.startswith("service_"):
        h = action.split('service_', 1)[1]
        service = context.user_data.get('services_map', {}).get(h)
        if not service:
            await query.answer("❌ الخدمة غير موجودة، أعد المحاولة.", show_alert=True)
            return CHOOSE_SERVICE

        context.user_data['service'] = service
        # بناء خريطة كل مواد التخصص (للبحث والتحديد)
        dm = get_data_manager()
        spec_map = {}
        for b in await dm.get_blocks_for_spec(spec):
            for c in await dm.get_courses_for_block(spec, b):
                spec_map[course_cb(c)] = c
        context.user_data['spec_courses_map'] = spec_map

        return await _show_blocks_menu(update, context)

    return CHOOSE_SERVICE


# ═══════════════ الكتلة ← المواد ═══════════════

def _courses_header(spec: str, service: str, block: str, prices: dict, cart: list) -> str:
    price = prices.get(service, 0)
    total = price * len(cart)
    return (
        f"🎓 <b>{spec}</b> ← 📦 <b>{service}</b>\n"
        f"📚 <b>{block}</b>\n\n"
        f"🛒 السلة: {len(cart)}/{MAX_COURSES_PER_ORDER} مادة — "
        f"💰 {total:,} ل.س\n\n"
        f"اضغط على المادة لإضافتها/إزالتها من السلة."
    )


def _build_courses_keyboard(block_courses: list, cart: list,
                            is_search: bool = False, block_label: str = None) -> list:
    keyboard = []
    for chunk in chunks(block_courses, 2):
        row = []
        for label in chunk:
            course = label[1] if isinstance(label, tuple) else label
            button_text = f"✓ {course}" if course in cart else course
            row.append(InlineKeyboardButton(
                button_text, callback_data=f"course_{course_cb(course)}"))
        keyboard.append(row)

    # صف الإجراءات: بحث + سلة
    action_row = [InlineKeyboardButton("🛒 السلة", callback_data="view_cart")]
    if not is_search:
        action_row.insert(0, InlineKeyboardButton("🔍 بحث", callback_data="search_courses"))
    else:
        action_row.insert(0, InlineKeyboardButton("📋 عرض الكل", callback_data="clear_search"))
    keyboard.append(action_row)

    # صف الإتمام
    keyboard.append([
        InlineKeyboardButton("💳 إتمام الطلب", callback_data="done_selecting_courses")])
    # صف التنقل
    nav_row = []
    if cart:
        nav_row.append(InlineKeyboardButton("🗑 مسح السلة", callback_data="clear_all_courses"))
    if is_search:
        nav_row.append(InlineKeyboardButton("📚 الكتل", callback_data="back_to_blocks"))
    else:
        nav_row.append(InlineKeyboardButton("🔙 الكتل", callback_data="back_to_blocks"))
    keyboard.append(nav_row)
    return keyboard


async def _show_courses(update: Update, context: CallbackContext,
                        is_search: bool = False) -> None:
    query = update.callback_query
    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')
    block = context.user_data.get('current_block', '')
    courses = context.user_data.get('current_block_courses', [])
    cart = context.user_data.get('cart', [])

    dm = get_data_manager()
    prices = await dm.get_prices()

    text = _courses_header(spec, service, block, prices, cart)
    keyboard = _build_courses_keyboard(courses, cart, is_search=is_search)
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def choose_block_callback(update: Update, context: CallbackContext) -> int:
    """اختيار الكتلة ← عرض موادها"""
    query = update.callback_query

    rl = get_rate_limiter()
    allowed, reason = rl.check_rate(query.from_user.id, "callback")
    if not allowed:
        await query.answer(reason, show_alert=True)
        return CHOOSE_BLOCK

    await query.answer()
    action = query.data
    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')

    if not spec or not service:
        return await start(update, context)

    if action == "back_to_services":
        return await _show_blocks_menu(update, context)

    if action == "view_cart":
        return await _show_cart(update, context)

    if action == "done_selecting_courses":
        return await _done_selecting(update, context)

    if action == "back_to_start":
        context.user_data.clear()
        cancel_user_jobs(context, query.from_user.id)
        await start(update, context)
        return ConversationHandler.END

    if action.startswith("block_"):
        h = action.split('block_', 1)[1]
        block = context.user_data.get('blocks_map', {}).get(h)
        if not block:
            await query.answer("❌ الكتلة غير موجودة، أعد المحاولة.", show_alert=True)
            return CHOOSE_BLOCK

        dm = get_data_manager()
        courses = await dm.get_courses_for_block(spec, block)

        if not courses:
            await query.answer(f"⚠️ لا توجد مواد مفعلة في هذه الكتلة.", show_alert=True)
            return CHOOSE_BLOCK

        courses.sort()
        context.user_data['current_block'] = block
        context.user_data['current_block_courses'] = courses
        context.user_data.pop('search_filter', None)

        await _show_courses(update, context)
        return CHOOSE_COURSES

    return CHOOSE_BLOCK


async def choose_courses_callback(update: Update, context: CallbackContext) -> int:
    """تحديد/إزالة المواد في السلة (يعمل من شاشة الكتلة والبحث)"""
    query = update.callback_query

    rl = get_rate_limiter()
    allowed, reason = rl.check_rate(query.from_user.id, "callback")
    if not allowed:
        await query.answer(reason, show_alert=True)
        return CHOOSE_COURSES

    action = query.data
    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')

    if not spec or not service:
        return await start(update, context)

    if action == "back_to_blocks":
        context.user_data.pop('search_filter', None)
        return await _show_blocks_menu(update, context)

    if action == "view_cart":
        return await _show_cart(update, context)

    if action == "done_selecting_courses":
        return await _done_selecting(update, context)

    if action == "search_courses":
        await query.answer()
        await query.edit_message_text(
            "🔍 <b>بحث عن مادة في كل كتل التخصص</b>\n\n"
            "اكتب جزءاً من اسم المادة.\n"
            "أو اضغط /cancel للعودة.",
            parse_mode=ParseMode.HTML)
        return SEARCH_COURSES

    if action == "clear_search":
        context.user_data.pop('search_filter', None)
        await _show_courses(update, context)
        return CHOOSE_COURSES

    if action == "clear_all_courses":
        context.user_data['cart'] = []
        await query.answer("🗑 تم مسح السلة")
        await _show_courses(update, context)
        return CHOOSE_COURSES

    if action.startswith("course_"):
        chash = action.split('course_', 1)[1]
        course_name = context.user_data.get('spec_courses_map', {}).get(chash)

        if course_name:
            cart = context.user_data.setdefault('cart', [])
            if course_name not in cart:
                if len(cart) < MAX_COURSES_PER_ORDER:
                    cart.append(course_name)
                    await query.answer(f"✅ أُضيفت للسلة: {course_name}")
                else:
                    await query.answer(
                        f"⚠️ السلة ممتلئة ({MAX_COURSES_PER_ORDER} مواد). "
                        f"افتح السلة للحذف أو الإتمام.", show_alert=True)
            else:
                cart.remove(course_name)
                await query.answer(f"↩️ أُزيلت من السلة: {course_name}")

            # إعادة عرض الشاشة الحالية (بحث أو كتلة)
            if context.user_data.get('search_filter'):
                search_filter = context.user_data['search_filter']
                dm = get_data_manager()
                results = await dm.search_courses_in_spec(spec, search_filter)
                prices = await dm.get_prices()
                text = (
                    f"🔍 نتائج البحث عن \"{search_filter}\" ({len(results)} مادة)\n\n"
                    + _courses_header(spec, service, '', prices, cart)
                )
                keyboard = _build_courses_keyboard(results, cart, is_search=True)
                try:
                    await query.edit_message_text(
                        text, reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.HTML)
                except BadRequest:
                    pass
                return SEARCH_COURSES

            await _show_courses(update, context)
        return CHOOSE_COURSES

    return CHOOSE_COURSES


# ═══════════════ البحث ═══════════════

async def search_courses_handler(update: Update, context: CallbackContext) -> int:
    search_text = update.message.text.strip()

    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')
    if not spec or not service:
        return await start(update, context)

    rl = get_rate_limiter()
    allowed, reason = rl.check_rate(update.effective_user.id, "search")
    if not allowed:
        await update.message.reply_text(reason)
        return SEARCH_COURSES

    valid, err = InputValidator.validate_search_query(search_text)
    if not valid:
        await update.message.reply_text(err)
        return SEARCH_COURSES

    dm = get_data_manager()
    results = await dm.search_courses_in_spec(spec, search_text)
    context.user_data['search_filter'] = search_text
    cart = context.user_data.get('cart', [])
    prices = await dm.get_prices()

    if not results:
        await update.message.reply_text(
            f"🔍 لم يتم العثور على مواد مطابقة لـ \"{search_text}\".\n"
            f"جرّب كلمة أخرى أو اضغط /cancel للعودة.")
        return SEARCH_COURSES

    text = (
        f"🔍 <b>نتائج البحث عن:</b> \"{search_text}\" ({len(results)} مادة "
        f"من كل الكتل)\n\n" + _courses_header(spec, service, '', prices, cart)
    )
    keyboard = _build_courses_keyboard(results, cart, is_search=True)

    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return SEARCH_COURSES


# ═══════════════ سلة المشتريات ═══════════════

async def _show_cart(update: Update, context: CallbackContext) -> int:
    """شاشة السلة: العناصر مع أزرار إزالة + الإجمالي + الدفع/التسوق"""
    query = update.callback_query

    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')
    cart = context.user_data.get('cart', [])

    dm = get_data_manager()
    prices = await dm.get_prices()
    price = prices.get(service, 0)
    total = price * len(cart)

    if not cart:
        await query.answer()
        await query.edit_message_text(
            f"🛒 <b>سلتك فارغة</b>\n\n"
            f"تصفح كتل المقررات وأضف المواد المطلوبة.\n"
            f"💰 سعر المادة: {price:,} ل.س",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 متابعة التسوق", callback_data="back_to_blocks")],
                [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")],
            ]),
            parse_mode=ParseMode.HTML)
        return VIEW_CART

    text = (
        f"🛒 <b>سلة المشتريات</b>\n"
        f"🎓 {spec} ← 📦 {service}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
    )
    for i, course in enumerate(cart, 1):
        text += f"{i}. {course}\n"
    text += (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 العدد: {len(cart)}/{MAX_COURSES_PER_ORDER} مادة\n"
        f"💵 سعر المادة: {price:,} ل.س\n"
        f"💰 <b>الإجمالي: {total:,} ل.س</b>\n\n"
        f"اضغط ❌ لإزالة مادة، أو تابع للدفع."
    )

    keyboard = []
    for course in cart:
        keyboard.append([InlineKeyboardButton(
            f"❌ {course}", callback_data=f"cart_remove_{course_cb(course)}")])

    keyboard.append([InlineKeyboardButton(
        f"💳 متابعة للدفع ({total:,} ل.س)", callback_data="done_selecting_courses")])
    keyboard.append([
        InlineKeyboardButton("🗑 مسح السلة", callback_data="clear_all_courses"),
        InlineKeyboardButton("📚 متابعة التسوق", callback_data="back_to_blocks"),
    ])
    keyboard.append([InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")])

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return VIEW_CART


async def cart_callback_handler(update: Update, context: CallbackContext) -> int:
    """معالجات أزرار السلة (إزالة عنصر)"""
    query = update.callback_query
    action = query.data

    cart = context.user_data.get('cart', [])

    if action.startswith("cart_remove_"):
        chash = action.split('cart_remove_', 1)[1]
        course = context.user_data.get('spec_courses_map', {}).get(chash)
        if course and course in cart:
            cart.remove(course)
            await query.answer(f"↩️ أُزيلت: {course}")
        return await _show_cart(update, context)

    if action == "back_to_blocks":
        await query.answer()
        return await _show_blocks_menu(update, context)

    if action == "view_cart":
        await query.answer()
        return await _show_cart(update, context)

    if action == "clear_all_courses":
        context.user_data['cart'] = []
        await query.answer("🗑 تم مسح السلة")
        return await _show_cart(update, context)

    if action == "done_selecting_courses":
        return await _done_selecting(update, context)

    return VIEW_CART


async def _done_selecting(update: Update, context: CallbackContext) -> int:
    """من السلة إلى اختيار طريقة الدفع"""
    query = update.callback_query

    cart = context.user_data.get('cart', [])
    if not cart:
        await query.answer("⚠️ سلتك فارغة! أضف مادة واحدة على الأقل.", show_alert=True)
        return await _show_blocks_menu(update, context)

    spec = context.user_data.get('specialization')
    service = context.user_data.get('service')

    dm = get_data_manager()
    prices = await dm.get_prices()
    price = prices.get(service, 0)
    total_price = price * len(cart)
    context.user_data['total_price'] = total_price

    payment_methods = await dm.get_payment_methods()
    payment_buttons = [
        InlineKeyboardButton(f"💳 {name}", callback_data=f"payment_{name}")
        for name in payment_methods
    ]
    keyboard = list(chunks(payment_buttons, 2))
    keyboard.append([InlineKeyboardButton("🛒 عودة للسلة", callback_data="view_cart")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")])

    payment_message = (
        f"🧾 <b>ملخص الطلب:</b>\n\n"
        f"<b>🎓 التخصص:</b> '{spec}'\n"
        f"<b>📦 الخدمة:</b> '{service}' ({price:,} ل.س للمادة)\n"
        f"<b>📚 المواد ({len(cart)}):</b> {', '.join(cart)}\n"
        f"<b>💰 السعر الإجمالي:</b> <code>{total_price:,}</code> ل.س\n\n"
        f"💳 اختر طريقة الدفع:"
    )

    await query.edit_message_text(
        text=payment_message, reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML)
    return CHOOSE_PAYMENT


# ═══════════════ الدفع ═══════════════

async def choose_payment_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data

    if not context.user_data.get('service'):
        return await start(update, context)

    if action == "view_cart":
        return await _show_cart(update, context)

    if action.startswith("payment_"):
        payment_method = action.split('_', 1)[1]
        context.user_data['payment_method'] = payment_method

        dm = get_data_manager()
        method_info = await dm.get_payment_method(payment_method) \
            or {'details': "تفاصيل الدفع غير متوفرة حالياً.", 'image_file_id': None}
        payment_details = method_info['details']
        image_file_id = method_info.get('image_file_id')

        if payment_method == "حوالة مالية":
            await query.edit_message_text(
                payment_details, reply_markup=build_support_keyboard())
            context.user_data.clear()
            cancel_user_jobs(context, update.effective_user.id)
            return ConversationHandler.END

        payment_text = (
            f"💳 اخترت الدفع بـ: <b>{payment_method}</b>.\n\n"
            f"{payment_details}\n\n"
            f"📎 بعد الدفع، أرسل إشعار التحويل (صورة أو ملف PDF) أو رقم العملية.\n\n"
            f"❌ للإلغاء، اضغط /cancel."
        )

        if image_file_id:
            try:
                await query.delete_message()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=image_file_id,
                    caption=payment_text,
                    parse_mode=ParseMode.HTML)
                context.user_data['is_photo_message'] = True
            except Exception as e:
                logger.error(f"Failed to send payment photo: {e}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=payment_text,
                    parse_mode=ParseMode.HTML)
                context.user_data['is_photo_message'] = False
        else:
            try:
                await query.edit_message_text(payment_text, parse_mode=ParseMode.HTML)
            except BadRequest:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=payment_text,
                    parse_mode=ParseMode.HTML)
            context.user_data['is_photo_message'] = False

        return GET_PAYMENT_INFO

    elif action == "cancel_order":
        return await _show_cancel_confirmation(update, context)

    return CHOOSE_PAYMENT


async def get_payment_info_callback(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    if not context.user_data.get('service'):
        await update.message.reply_text("❌ خطأ، يرجى البدء من جديد /start.")
        return ConversationHandler.END

    rl = get_rate_limiter()
    allowed, reason = rl.check_rate(user.id, "message")
    if not allowed:
        await update.message.reply_text(reason)
        return GET_PAYMENT_INFO

    if update.message.photo:
        context.user_data['receipt_file_id'] = update.message.photo[-1].file_id
        context.user_data['receipt_type'] = 'photo'
        context.user_data.pop('transaction_id', None)
        context.user_data.pop('receipt_file_name', None)

    elif update.message.document:
        file_obj = update.message.document
        file_name = file_obj.file_name or ""
        file_ext = os.path.splitext(file_name)[1].lower() if file_name else ""

        allowed_extensions = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
        if file_ext and file_ext not in allowed_extensions:
            await update.message.reply_text(
                "❌ نوع الملف غير مدعوم.\n"
                "أرسل صورة (PNG/JPG) أو ملف PDF أو رقم العملية كنص.")
            return GET_PAYMENT_INFO

        if file_obj.file_size and file_obj.file_size > MAX_RECEIPT_SIZE_MB * 1024 * 1024:
            await update.message.reply_text(
                f"❌ حجم الملف كبير جداً (الحد الأقصى {MAX_RECEIPT_SIZE_MB} ميغا).")
            return GET_PAYMENT_INFO

        context.user_data['receipt_file_id'] = file_obj.file_id
        context.user_data['receipt_type'] = 'document'
        context.user_data['receipt_file_name'] = file_name
        context.user_data.pop('transaction_id', None)

    elif update.message.text:
        valid, err = InputValidator.validate(update.message.text.strip(), 'transaction_id')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return GET_PAYMENT_INFO
        context.user_data['transaction_id'] = update.message.text.strip()
        context.user_data['receipt_type'] = 'text'
        context.user_data.pop('receipt_file_id', None)
        context.user_data.pop('receipt_file_name', None)

    else:
        await update.message.reply_text(
            "📎 يرجى إرسال:\n"
            "• صورة إشعار التحويل\n"
            "• ملف PDF لإشعار التحويل\n"
            "• أو رقم العملية كنص")
        return GET_PAYMENT_INFO

    # --- شاشة التأكيد النهائي ---
    spec = context.user_data.get('specialization')
    service = context.user_data['service']
    cart = context.user_data['cart']
    payment_method = context.user_data['payment_method']
    total_price = context.user_data['total_price']
    receipt_type = context.user_data.get('receipt_type', 'photo')

    if context.user_data.get('receipt_file_id'):
        if receipt_type == 'document':
            fname = context.user_data.get('receipt_file_name', 'ملف')
            receipt_status = f"✅ مرفق (ملف: {fname})"
        else:
            receipt_status = "✅ مرفق (صورة)"
    elif context.user_data.get('transaction_id'):
        receipt_status = f"✅ رقم العملية: {context.user_data.get('transaction_id')}"
    else:
        receipt_status = "❌ غير مرفق"

    confirm_text = (
        f"🧾 <b>تأكيد الطلب النهائي</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🎓 <b>التخصص:</b> {spec}\n"
        f"📦 <b>الخدمة:</b> {service}\n"
        f"📚 <b>المواد ({len(cart)}):</b> {', '.join(cart)}\n"
        f"💰 <b>السعر الإجمالي:</b> {total_price:,} ل.س\n"
        f"💳 <b>طريقة الدفع:</b> {payment_method}\n"
        f"🧾 <b>الإيصال:</b> {receipt_status}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"هل تريد تأكيد إرسال الطلب؟"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="confirm_order_yes"),
         InlineKeyboardButton("❌ إلغاء الطلب", callback_data="confirm_order_no")]
    ])

    await update.message.reply_text(
        confirm_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return CONFIRM_ORDER


async def confirm_order_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_order_yes":
        return await _submit_order(update, context)
    elif query.data == "confirm_order_no":
        await query.edit_message_text("❌ تم إلغاء الطلب.")
        cancel_user_jobs(context, update.effective_user.id)
        context.user_data.clear()
        return ConversationHandler.END
    return CONFIRM_ORDER


async def _submit_order(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    query = update.callback_query

    if not context.user_data.get('service'):
        await query.edit_message_text("❌ خطأ، يرجى البدء من جديد /start.")
        return ConversationHandler.END

    order_info = {
        'specialization': context.user_data.get('specialization'),
        'service': context.user_data['service'],
        'courses': context.user_data['cart'],
        'payment_method': context.user_data['payment_method'],
        'total_price': context.user_data['total_price'],
        'receipt_file_id': context.user_data.get('receipt_file_id'),
        'receipt_type': context.user_data.get('receipt_type', 'photo'),
        'receipt_file_name': context.user_data.get('receipt_file_name'),
        'transaction_id': context.user_data.get('transaction_id')
    }

    order_key = f"order_{user.id}_{int(datetime.now().timestamp())}"

    dm = get_data_manager()
    completed_orders_count = await dm.get_user_completed_orders_count(user.id)

    await dm.create_order(order_key, {
        'user_id': user.id,
        'full_name': user.full_name,
        'username': user.username,
        'status': 'قيد المراجعة',
        **order_info
    })

    await _send_admin_notification(context, user, order_key, order_info,
                                   completed_orders_count)

    await query.edit_message_text(
        f"✅ شكراً لك، تم استلام طلبك للمراجعة.\n\n"
        f"🔑 رقم الطلب: <code>{order_key}</code>\n"
        f"⏱ سيتم التواصل معك خلال 24 ساعة.",
        parse_mode=ParseMode.HTML)

    await dm.set_last_order_time(user.id, datetime.now())
    schedule_cooldown_reminder(context, user.id)
    cancel_user_jobs(context, user.id)
    context.user_data.clear()
    return ConversationHandler.END


async def _send_admin_notification(context, user, order_key, order_info,
                                   completed_orders_count):
    receipt_type = order_info.get('receipt_type', 'photo')
    receipt_file_name = order_info.get('receipt_file_name', '')

    if order_info.get('receipt_file_id'):
        if receipt_type == 'document':
            receipt_label = f"📄 ملف ({receipt_file_name})" if receipt_file_name else "📄 ملف"
        else:
            receipt_label = "🖼 صورة"
    elif order_info.get('transaction_id'):
        receipt_label = None
    else:
        receipt_label = "❌ بدون إيصال"

    admin_message_text = (
        f"🔔 <b>طلب جديد</b>\n\n"
        f"<b>👤 من:</b> {user.full_name} (@{user.username or 'N/A'})\n"
        f"<b>🆔 ID:</b> {user.id}\n"
        f"<b>🔑 Order Key:</b> <code>{order_key}</code>\n\n"
        f"<b><u>📋 تفاصيل الطلب:</u></b>\n"
        f"<b>🎓 التخصص:</b> {order_info.get('specialization') or 'غير محدد'}\n"
        f"<b>📦 الخدمة:</b> {order_info['service']}\n"
        f"<b>📚 المواد ({len(order_info['courses'])}):</b> "
        f"{', '.join(order_info['courses'])}\n"
        f"<b>💳 الدفع:</b> {order_info['payment_method']} "
        f"({order_info['total_price']:,} ل.س)\n"
    )

    if order_info.get('transaction_id'):
        admin_message_text += f"<b>🔢 رقم العملية:</b> {order_info['transaction_id']}\n"
    if receipt_label:
        admin_message_text += f"<b>🧾 الإيصال:</b> {receipt_label}\n"

    admin_message_text += (
        f"\n<b>📊 عدد الطلبات المكتملة السابقة لهذا الطالب:</b> {completed_orders_count}"
    )

    keyboard = [[
        InlineKeyboardButton("✅ تأكيد", callback_data=f"admin_approve_{order_key}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_{order_key}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if order_info.get('receipt_file_id'):
            if receipt_type == 'document':
                await context.bot.send_document(
                    chat_id=ADMIN_GROUP_ID, document=order_info['receipt_file_id'],
                    caption=admin_message_text, reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_photo(
                    chat_id=ADMIN_GROUP_ID, photo=order_info['receipt_file_id'],
                    caption=admin_message_text, reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID, text=admin_message_text,
                reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send order notification to admin group: {e}")
        try:
            await context.bot.send_message(
                chat_id=MAIN_ADMIN_ID,
                text=f"⚠️ فشل إرسال إشعار طلب جديد إلى مجموعة الأدمن. الخطأ: {e}")
        except Exception:
            pass


# ═══════════════ الإلغاء ═══════════════

async def _show_cancel_confirmation(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⚠️ <b>هل أنت متأكد من إلغاء الطلب؟</b>\n\n"
        "سيتم فقدان جميع البيانات المدخلة.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، إلغاء", callback_data="cancel_confirm_yes"),
             InlineKeyboardButton("❌ لا، متابعة", callback_data="cancel_confirm_no")]
        ]),
        parse_mode=ParseMode.HTML)
    return CHOOSE_PAYMENT


async def cancel_confirm_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_confirm_yes":
        cancel_user_jobs(context, update.effective_user.id)
        context.user_data.clear()
        await query.edit_message_text("❌ تم إلغاء طلبك.")
        return ConversationHandler.END

    elif query.data == "cancel_confirm_no":
        spec = context.user_data.get('specialization')
        service = context.user_data.get('service')
        cart = context.user_data.get('cart', [])
        total_price = context.user_data.get('total_price', 0)

        if not service:
            await query.edit_message_text("❌ حدث خطأ، يرجى البدء من جديد /start.")
            return ConversationHandler.END

        dm = get_data_manager()
        payment_methods = await dm.get_payment_methods()
        payment_buttons = [
            InlineKeyboardButton(f"💳 {name}", callback_data=f"payment_{name}")
            for name in payment_methods
        ]
        keyboard = list(chunks(payment_buttons, 2))
        keyboard.append([InlineKeyboardButton("🛒 عودة للسلة", callback_data="view_cart")])
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_order")])

        payment_message = (
            f"🧾 <b>ملخص الطلب:</b>\n\n"
            f"<b>🎓 التخصص:</b> '{spec}'\n"
            f"<b>📦 الخدمة:</b> '{service}'\n"
            f"<b>📚 المواد ({len(cart)}):</b> {', '.join(cart)}\n"
            f"<b>💰 السعر الإجمالي:</b> <code>{total_price:,}</code> ل.س\n\n"
            f"💳 اختر طريقة الدفع:"
        )

        await query.edit_message_text(
            payment_message, reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML)
        return CHOOSE_PAYMENT

    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    context.user_data.clear()
    cancel_user_jobs(context, user_id)

    user = update.effective_user
    if user.id in ADMIN_IDS:
        from bot_ui import show_admin_menu
        await show_admin_menu(update, context)
    else:
        dm = get_data_manager()
        prices = await dm.get_prices()
        services_list = (
            "\n".join([f"- {service} ({price} ل.س للمادة)"
                       for service, price in prices.items()])
            if prices else "لا توجد خدمات متاحة حالياً."
        )
        student_text = student_welcome_text(user.first_name, services_list)
        reply_markup = build_student_main_keyboard()

        if update.message:
            await update.message.reply_text(student_text, reply_markup=reply_markup)
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                student_text, reply_markup=reply_markup)

    return ConversationHandler.END


async def support_command(update: Update, context: CallbackContext) -> None:
    if await check_if_banned(update, context):
        return
    await update.message.reply_text(
        "📞 للتواصل مع فريق الدعم، اضغط على الزر.",
        reply_markup=build_support_keyboard())
