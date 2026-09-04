"""
إدارة المحتوى (أدمن) - الهيكل الجديد
المناطق: 🎓 التخصصات | 💰 الخدمات والأسعار | 📚 كتل ومواد كل تخصص
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import logger, COURSES_PER_PAGE
from states import (
    CONTENT_AREA, SELECT_SPEC, SELECT_SERVICE_ADMIN, SELECT_ACTION,
    AWAIT_INPUT, AWAIT_CONFIRMATION, ADMIN_BLOCK_SELECT,
    BLOCK_MANAGE_COURSES, AWAIT_NEW_COURSE_NAME, MANAGE_COURSES_PAGE
)
from data_manager import get_data_manager
from keyboards import build_apply_and_end_keyboard
from bot_ui import show_admin_menu
from utils import cb_hash, normalize_and_clean_number
from input_validator import InputValidator


# ═════════ القائمة الرئيسية للمحتوى ═════════

async def admin_content_menu(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    keyboard = [
        [InlineKeyboardButton("🎓 إدارة التخصصات", callback_data="area_specs")],
        [InlineKeyboardButton("💰 إدارة الخدمات والأسعار", callback_data="area_services")],
        [InlineKeyboardButton("📚 إدارة كتل ومواد التخصص", callback_data="area_blocks")],
        [InlineKeyboardButton("🔚 إنهاء", callback_data="end_admin_convo")],
    ]

    text = ("<b>📁 إدارة المحتوى</b>\n\n"
            "اختر منطقة الإدارة:")
    try:
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "message to edit not found" in str(e) and update.effective_chat:
            await update.effective_chat.send_message(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            raise e

    return CONTENT_AREA


async def content_area_handler(update: Update, context: CallbackContext) -> int:
    """توجيه حسب المنطقة المختارة"""
    query = update.callback_query
    await query.answer()
    area = query.data

    if area == "area_specs":
        return await _show_specs_admin(update, context)
    elif area == "area_services":
        return await _show_services_admin(update, context)
    elif area == "area_blocks":
        return await _pick_spec_for_blocks(update, context)
    return CONTENT_AREA


# ═════════ التخصصات ═════════

async def _show_specs_admin(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    dm = get_data_manager()
    specs = await dm.get_specializations()

    context.user_data['admin_specs_map'] = {cb_hash(s): s for s in specs}

    keyboard = [
        [InlineKeyboardButton(f"🎓 {s} (🗑)", callback_data=f"delete_spec_{cb_hash(s)}")]
        for s in specs
    ]
    keyboard.append([InlineKeyboardButton("➕ إضافة تخصص", callback_data="add_spec")])
    keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="admin_content_menu")])

    text = ("<b>🎓 إدارة التخصصات</b>\n\n"
            f"عدد التخصصات: {len(specs)}\n"
            "اضغط 🗑 لحذف تخصص (مع كتله ومواده).")
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return SELECT_SPEC


async def admin_spec_actions(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data

    dm = get_data_manager()

    if action == "add_spec":
        context.user_data['action'] = 'add_spec'
        await query.edit_message_text(
            "🎓 أرسل اسم التخصص الجديد.\n\n🚫 أرسل /cancel للإلغاء.")
        return AWAIT_INPUT

    if action.startswith("delete_spec_"):
        h = action.split('delete_spec_', 1)[1]
        spec = context.user_data.get('admin_specs_map', {}).get(h)
        if not spec:
            await query.answer("❌ غير موجود.", show_alert=True)
            return SELECT_SPEC
        context.user_data['pending_delete_spec'] = spec
        await query.edit_message_text(
            f"⚠️ حذف تخصص '<b>{spec}</b>' مع كل كتله ومواده؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ نعم، احذف", callback_data="confirm_del_spec_yes"),
                 InlineKeyboardButton("❌ تراجع", callback_data="area_specs")]
            ]),
            parse_mode=ParseMode.HTML)
        return SELECT_SPEC

    if action == "confirm_del_spec_yes":
        spec = context.user_data.pop('pending_delete_spec', None)
        if spec:
            await dm.delete_specialization(spec)
        await query.answer(f"🗑 تم حذف '{spec}'")
        return await _show_specs_admin(update, context)

    if action == "area_specs":
        return await _show_specs_admin(update, context)

    return SELECT_SPEC


# ═════════ الخدمات والأسعار ═════════

async def _show_services_admin(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    dm = get_data_manager()
    prices = await dm.get_prices()

    context.user_data['admin_services_map'] = {cb_hash(n): n for n in prices}

    keyboard = [
        [InlineKeyboardButton(f"💰 {n} — {p:,} ل.س للمادة",
                              callback_data=f"sel_service_{cb_hash(n)}")]
        for n, p in prices.items()
    ]
    keyboard.append([InlineKeyboardButton("➕ إضافة خدمة", callback_data="add_service")])
    keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="admin_content_menu")])

    text = "<b>💰 الخدمات والأسعار</b>\n\nاختر خدمة لتعديلها:"
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return SELECT_SERVICE_ADMIN


async def admin_service_actions(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data
    dm = get_data_manager()

    if action == "add_service":
        context.user_data['action'] = 'add_service_name'
        await query.edit_message_text(
            "📝 أرسل اسم الخدمة الجديدة.\n\n🚫 أرسل /cancel للإلغاء.")
        return AWAIT_INPUT

    if action.startswith("sel_service_"):
        h = action.split('sel_service_', 1)[1]
        name = context.user_data.get('admin_services_map', {}).get(h)
        if not name:
            await query.answer("❌ غير موجودة.", show_alert=True)
            return SELECT_SERVICE_ADMIN
        context.user_data['selected_service'] = name
        price = (await dm.get_prices()).get(name, 0)
        await query.edit_message_text(
            f"💰 <b>{name}</b> — السعر الحالي: {price:,} ل.س للمادة\n\nاختر الإجراء:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💵 تغيير السعر", callback_data="action_change_price")],
                [InlineKeyboardButton("📝 إعادة التسمية", callback_data="action_rename")],
                [InlineKeyboardButton("🗑 حذف الخدمة", callback_data="action_delete_service")],
                [InlineKeyboardButton("🔙 العودة", callback_data="area_services")],
            ]),
            parse_mode=ParseMode.HTML)
        return SELECT_ACTION

    if action == "action_change_price":
        context.user_data['action'] = 'change_price'
        await query.edit_message_text(
            "💵 أرسل السعر الجديد (للمادة الواحدة).\n\n🚫 /cancel للإلغاء.")
        return AWAIT_INPUT

    if action == "action_rename":
        context.user_data['action'] = 'rename_service'
        await query.edit_message_text(
            "📝 أرسل الاسم الجديد.\n\n🚫 /cancel للإلغاء.")
        return AWAIT_INPUT

    if action == "action_delete_service":
        name = context.user_data.get('selected_service')
        await dm.delete_service(name)
        await query.answer(f"🗑 تم حذف '{name}'")
        return await _show_services_admin(update, context)

    if action == "area_services":
        return await _show_services_admin(update, context)

    return SELECT_SERVICE_ADMIN


# ═════════ كتل ومواد التخصص ═════════

async def _pick_spec_for_blocks(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    dm = get_data_manager()
    specs = await dm.get_specializations()

    context.user_data['admin_specs_map'] = {cb_hash(s): s for s in specs}

    keyboard = [
        [InlineKeyboardButton(f"🎓 {s}", callback_data=f"blocks_spec_{cb_hash(s)}")]
        for s in specs
    ]
    keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="admin_content_menu")])

    await query.edit_message_text(
        "<b>📚 إدارة الكتل والمواد</b>\n\nاختر التخصص:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return ADMIN_BLOCK_SELECT


async def _show_blocks_admin(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    spec = context.user_data.get('selected_spec')
    dm = get_data_manager()
    blocks = await dm.get_blocks_for_spec(spec)

    context.user_data['admin_blocks_map'] = {cb_hash(b): b for b in blocks}

    keyboard = []
    for b in blocks:
        n_active = len(await dm.get_courses_for_block(spec, b))
        keyboard.append([
            InlineKeyboardButton(f"📚 {b} ({n_active} مادة)",
                                 callback_data=f"sel_block_{cb_hash(b)}"),
            InlineKeyboardButton("🗑", callback_data=f"del_block_{cb_hash(b)}"),
        ])
    keyboard.append([InlineKeyboardButton("➕ إضافة كتلة", callback_data="add_block")])
    keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="area_blocks")])

    text = (f"<b>📚 كتل تخصص: {spec}</b>\n\n"
            f"اختر كتلة لإدارة موادها (🗑 لحذف الكتلة):")
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return ADMIN_BLOCK_SELECT


async def admin_block_actions(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data
    dm = get_data_manager()
    spec = context.user_data.get('selected_spec')

    if action.startswith("blocks_spec_"):
        h = action.split('blocks_spec_', 1)[1]
        s = context.user_data.get('admin_specs_map', {}).get(h)
        if not s:
            await query.answer("❌ غير موجود.", show_alert=True)
            return ADMIN_BLOCK_SELECT
        context.user_data['selected_spec'] = s
        return await _show_blocks_admin(update, context)

    if not spec:
        return await _pick_spec_for_blocks(update, context)

    if action == "add_block":
        context.user_data['action'] = 'add_block'
        await query.edit_message_text(
            "📚 أرسل اسم الكتلة الجديدة (مثال: سادساً - كتلة مقررات التدريب).\n\n🚫 /cancel للإلغاء.")
        return AWAIT_INPUT

    if action.startswith("del_block_"):
        h = action.split('del_block_', 1)[1]
        b = context.user_data.get('admin_blocks_map', {}).get(h)
        if not b:
            await query.answer("❌ غير موجودة.", show_alert=True)
            return ADMIN_BLOCK_SELECT
        context.user_data['pending_delete_block'] = b
        await query.edit_message_text(
            f"⚠️ حذف كتلة '<b>{b}</b>' وموادها من '<b>{spec}</b>'؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ نعم، احذف", callback_data="confirm_del_block_yes"),
                 InlineKeyboardButton("❌ تراجع", callback_data="back_to_blocks_list")]
            ]),
            parse_mode=ParseMode.HTML)
        return ADMIN_BLOCK_SELECT

    if action == "confirm_del_block_yes":
        b = context.user_data.pop('pending_delete_block', None)
        if b:
            await dm.delete_block_from_spec(spec, b)
        return await _show_blocks_admin(update, context)

    if action == "back_to_blocks_list":
        return await _show_blocks_admin(update, context)

    if action == "area_blocks":
        return await _pick_spec_for_blocks(update, context)

    if action.startswith("sel_block_"):
        h = action.split('sel_block_', 1)[1]
        b = context.user_data.get('admin_blocks_map', {}).get(h)
        if not b:
            await query.answer("❌ غير موجودة.", show_alert=True)
            return ADMIN_BLOCK_SELECT
        context.user_data['selected_block'] = b
        await admin_manage_courses_menu(update, context, page=0)
        return BLOCK_MANAGE_COURSES

    return ADMIN_BLOCK_SELECT


# ═════════ مواد الكتلة ═════════

async def admin_manage_courses_menu(update: Update, context: CallbackContext, page: int = 0):
    query = (update.callback_query
             if hasattr(update, 'callback_query') and update.callback_query else None)

    spec = context.user_data.get('selected_spec')
    block = context.user_data.get('selected_block')
    dm = get_data_manager()

    all_courses = await dm.get_all_courses_for_block(spec, block)
    context.user_data['block_courses_map'] = {
        cb_hash(c['course_name']): c['course_name'] for c in all_courses}

    total_pages = ((len(all_courses) + COURSES_PER_PAGE - 1) // COURSES_PER_PAGE
                   if all_courses else 1)
    page = max(0, min(page, total_pages - 1))
    start = page * COURSES_PER_PAGE
    page_courses = all_courses[start:start + COURSES_PER_PAGE]

    keyboard = []
    for c in page_courses:
        is_active = "✅" if c['is_active'] else "⬜"
        h = cb_hash(c['course_name'])
        keyboard.append([
            InlineKeyboardButton(f"{is_active} {c['course_name']}",
                                 callback_data=f"toggle_course_{h}"),
            InlineKeyboardButton("🗑", callback_data=f"delete_course_{h}")
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"courses_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"courses_page_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("✅ تفعيل كل مواد الكتلة",
                                          callback_data="select_all_courses")])
    keyboard.append([InlineKeyboardButton("➕ إضافة مادة", callback_data="add_course")])
    keyboard.append([InlineKeyboardButton("🔙 العودة للكتل", callback_data="back_to_blocks_list")])

    text = (
        f"<b>📚 مواد كتلة '{block}'</b>\n"
        f"<b>🎓 التخصص: {spec}</b> "
        f"(صفحة {page + 1}/{total_pages} — {len(all_courses)} مادة)\n\n"
        f"✅ = مفعلة للطلاب | ⬜ = معطلة\n"
        f"🗑 = حذف من هذه الكتلة"
    )

    if query:
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def admin_courses_page_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('_')[-1])
    await admin_manage_courses_menu(update, context, page=page)
    return BLOCK_MANAGE_COURSES


async def admin_toggle_course(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    h = query.data.split('toggle_course_', 1)[1]
    course = context.user_data.get('block_courses_map', {}).get(h)
    if not course:
        return BLOCK_MANAGE_COURSES

    dm = get_data_manager()
    spec = context.user_data['selected_spec']
    block = context.user_data['selected_block']
    new_status = await dm.toggle_course_in_block(spec, block, course)
    await query.answer(f"{'✅ مفعلة' if new_status else '⬜ معطلة'}: {course}")

    await admin_manage_courses_menu(update, context)
    return BLOCK_MANAGE_COURSES


async def admin_select_all_courses(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    dm = get_data_manager()
    spec = context.user_data['selected_spec']
    block = context.user_data['selected_block']
    await dm.activate_all_courses_in_block(spec, block)
    await query.answer("✅ تم تفعيل جميع مواد الكتلة")
    await admin_manage_courses_menu(update, context)
    return BLOCK_MANAGE_COURSES


async def admin_add_course_prompt(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['action'] = 'add_course'
    await query.edit_message_text(
        "📝 أرسل اسم المادة الجديدة.\n\n🚫 أرسل /cancel لإلغاء العملية.")
    return AWAIT_NEW_COURSE_NAME


async def admin_delete_course_confirm(update: Update, context: CallbackContext) -> int:
    """حذف المادة من هذه الكتلة فقط"""
    query = update.callback_query
    await query.answer()

    h = query.data.split('delete_course_', 1)[1]
    course = context.user_data.get('block_courses_map', {}).get(h)
    if not course:
        await query.answer("❌ المادة غير موجودة.", show_alert=True)
        return BLOCK_MANAGE_COURSES

    dm = get_data_manager()
    spec = context.user_data['selected_spec']
    block = context.user_data['selected_block']
    await dm.remove_course_from_block(spec, block, course)

    await query.answer(f"🗑 حُذفت '{course}' من الكتلة.")
    await admin_manage_courses_menu(update, context)
    return BLOCK_MANAGE_COURSES


# ═════════ معالج الإدخال النصي ═════════

async def admin_handle_input(update: Update, context: CallbackContext) -> int:
    action = context.user_data.get('action')
    if not action or not update.message or not update.message.text:
        return ConversationHandler.END

    dm = get_data_manager()
    text_input = update.message.text.strip()

    # --- تخصصات ---
    if action == 'add_spec':
        valid, err = InputValidator.validate(text_input, 'section_name')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_INPUT
        if text_input in await dm.get_specializations():
            await update.message.reply_text("❌ هذا التخصص موجود بالفعل.")
            return AWAIT_INPUT
        await dm.add_specialization(text_input)
        await update.message.reply_text(
            f"✅ تم إضافة التخصص '{text_input}'.\n"
            f"الآن أضف كتله ومواده من: إدارة المحتوى ← كتل ومواد التخصص.",
            reply_markup=build_apply_and_end_keyboard())
        return AWAIT_CONFIRMATION

    # --- خدمات ---
    elif action == 'add_service_name':
        valid, err = InputValidator.validate(text_input, 'service_name')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_INPUT
        if text_input in (await dm.get_prices()):
            await update.message.reply_text("❌ هذا الاسم مستخدم بالفعل.")
            return AWAIT_INPUT
        context.user_data['new_service_name'] = text_input
        context.user_data['action'] = 'add_service_price'
        await update.message.reply_text("💰 أرسل سعر الخدمة (للمادة، أرقام فقط).")
        return AWAIT_INPUT

    elif action == 'add_service_price':
        valid, err = InputValidator.validate(text_input, 'price')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_INPUT
        try:
            price = int(normalize_and_clean_number(text_input))
        except ValueError:
            await update.message.reply_text("❌ أرسل السعر كأرقام فقط.")
            return AWAIT_INPUT

        name = context.user_data['new_service_name']
        await dm.set_service_price(name, price)
        # إتاحة الخدمة لكل التخصصات تلقائياً
        for spec in await dm.get_specializations():
            await dm.link_service_to_spec(spec, name)

        await update.message.reply_text(
            f"✅ تمت إضافة خدمة '{name}' بسعر {price:,} ل.س للمادة "
            f"وإتاحتها لكل التخصصات.",
            reply_markup=build_apply_and_end_keyboard())
        return AWAIT_CONFIRMATION

    elif action == 'change_price':
        valid, err = InputValidator.validate(text_input, 'price')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_INPUT
        try:
            price = int(normalize_and_clean_number(text_input))
        except ValueError:
            await update.message.reply_text("❌ أرسل السعر كأرقام فقط.")
            return AWAIT_INPUT
        name = context.user_data['selected_service']
        await dm.set_service_price(name, price)
        await update.message.reply_text(
            f"✅ سعر '{name}' أصبح {price:,} ل.س للمادة.",
            reply_markup=build_apply_and_end_keyboard())
        return AWAIT_CONFIRMATION

    elif action == 'rename_service':
        valid, err = InputValidator.validate(text_input, 'service_name')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_INPUT
        old = context.user_data['selected_service']
        if text_input != old and text_input in (await dm.get_prices()):
            await update.message.reply_text("❌ الاسم مستخدم.")
            return AWAIT_INPUT
        await dm.rename_service(old, text_input)
        await update.message.reply_text(
            f"✅ أصبح الاسم '{text_input}'.",
            reply_markup=build_apply_and_end_keyboard())
        return AWAIT_CONFIRMATION

    # --- كتل ---
    elif action == 'add_block':
        valid, err = InputValidator.validate(text_input, 'section_name')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_INPUT
        spec = context.user_data['selected_spec']
        if text_input in await dm.get_blocks_for_spec(spec):
            await update.message.reply_text("❌ الكتلة موجودة بالفعل.")
            return AWAIT_INPUT
        await dm.add_block_to_spec(spec, text_input)
        await update.message.reply_text(
            f"✅ أُضيفت كتلة '{text_input}' لتخصص '{spec}'. أضف موادها الآن.",
            reply_markup=build_apply_and_end_keyboard())
        return AWAIT_CONFIRMATION

    # --- مواد ---
    elif action == 'add_course':
        valid, err = InputValidator.validate(text_input, 'course_name')
        if not valid:
            await update.message.reply_text(f"❌ {err}")
            return AWAIT_NEW_COURSE_NAME
        spec = context.user_data['selected_spec']
        block = context.user_data['selected_block']
        existing = [c['course_name'] for c in
                    await dm.get_all_courses_for_block(spec, block)]
        if text_input in existing:
            await update.message.reply_text("❌ المادة موجودة في هذه الكتلة.")
            return AWAIT_NEW_COURSE_NAME

        await dm.add_course(text_input)
        await dm.add_course_to_block(spec, block, text_input, is_active=True)
        await update.message.reply_text(
            f"✅ أُضيفت مادة '{text_input}' إلى '{block}' ({spec}).",
            reply_markup=build_apply_and_end_keyboard())
        return AWAIT_CONFIRMATION

    return ConversationHandler.END


async def admin_end_conversation(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await show_admin_menu(update, context)
    return ConversationHandler.END


async def admin_cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await show_admin_menu(update, context)
    return ConversationHandler.END
