"""
إدارة طرق الدفع (أدمن)
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, ConversationHandler
from telegram.constants import ParseMode

from states import (
    PAYMENT_MENU, SELECT_PAYMENT_ACTION, AWAIT_PAYMENT_NAME,
    AWAIT_PAYMENT_DETAILS, AWAIT_EDIT_PAYMENT_DETAILS,
    AWAIT_PAYMENT_CONFIRMATION, AWAIT_DELETE_CONFIRMATION,
    AWAIT_PAYMENT_IMAGE
)
from data_manager import get_data_manager
from bot_ui import show_admin_menu


async def admin_payment_menu(update: Update, context: CallbackContext) -> int:
    query = (update.callback_query
             if hasattr(update, 'callback_query') and update.callback_query else None)
    message = update.message if update.message else None

    if query:
        await query.answer()

    context.user_data.clear()
    dm = get_data_manager()
    payment_methods = await dm.get_payment_methods()
    keyboard = []

    text = "<b>💳 إدارة طرق الدفع</b>\n\nاختر طريقة لتعديلها أو أضف واحدة جديدة:"

    for name in payment_methods.keys():
        keyboard.append([InlineKeyboardButton(f"💳 {name}", callback_data=f"payment_select_{name}")])

    keyboard.append([InlineKeyboardButton("➕ إضافة طريقة دفع جديدة", callback_data="payment_add")])
    keyboard.append([InlineKeyboardButton("🔚 إنهاء", callback_data="end_admin_convo")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    elif message:
        await message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    return PAYMENT_MENU


async def admin_select_payment_method(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    method_name = query.data.split("payment_select_", 1)[1]
    context.user_data['selected_payment_method'] = method_name

    dm = get_data_manager()
    method_info = await dm.get_payment_method(method_name) \
        or {'details': "N/A", 'image_file_id': None}

    text = (
        f"<b>💳 طريقة الدفع:</b> {method_name}\n"
        f"<b>📝 التفاصيل الحالية:</b>\n{method_info['details']}\n"
        f"<b>🖼 صورة:</b> {'✅ نعم' if method_info.get('image_file_id') else '❌ لا'}\n\n"
        f"اختر الإجراء:"
    )

    keyboard = [
        [InlineKeyboardButton("📝 تعديل التفاصيل والصورة", callback_data="payment_action_edit")],
        [InlineKeyboardButton("🗑 حذف الطريقة", callback_data="payment_action_delete")],
        [InlineKeyboardButton("🔙 العودة", callback_data="payment_back_to_menu")]
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return SELECT_PAYMENT_ACTION


async def admin_payment_action_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    action = query.data.split("payment_action_", 1)[1]
    method_name = context.user_data['selected_payment_method']

    if action == 'edit':
        await query.edit_message_text(
            f"📝 أرسل التفاصيل الجديدة لطريقة الدفع '{method_name}'.\n"
            "يمكنك استخدام تنسيق HTML مثل <b></b> و<code></code>.",
            parse_mode=ParseMode.HTML)
        return AWAIT_EDIT_PAYMENT_DETAILS

    elif action == 'delete':
        text = f"⚠️ هل أنت متأكد من أنك تريد حذف طريقة الدفع '{method_name}'؟"
        keyboard = [
            [InlineKeyboardButton("✅ نعم، قم بالحذف", callback_data="payment_confirm_delete_yes")],
            [InlineKeyboardButton("❌ لا، تراجع", callback_data="payment_back_to_menu")]
        ]
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return AWAIT_DELETE_CONFIRMATION


async def admin_payment_delete_confirm_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    method_name = context.user_data.get('selected_payment_method')
    dm = get_data_manager()
    await dm.delete_payment_method(method_name)

    text = f"✅ تم حذف طريقة الدفع '{method_name}' بنجاح."
    keyboard = [[InlineKeyboardButton("🔚 تطبيق وإنهاء", callback_data="payment_apply_and_end")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAIT_PAYMENT_CONFIRMATION


async def admin_add_payment_prompt_name(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 أرسل <b>اسم</b> طريقة الدفع الجديدة (مثال: USDT Wallet).",
        parse_mode=ParseMode.HTML)
    return AWAIT_PAYMENT_NAME


async def admin_handle_payment_name(update: Update, context: CallbackContext) -> int:
    method_name = update.message.text.strip()
    dm = get_data_manager()

    if method_name in (await dm.get_payment_methods()):
        await update.message.reply_text("❌ هذا الاسم مستخدم بالفعل. الرجاء اختيار اسم آخر.")
        return AWAIT_PAYMENT_NAME

    context.user_data['new_payment_name'] = method_name
    await update.message.reply_text(
        f"💳 الاسم: '{method_name}'.\n"
        "الآن أرسل <b>التفاصيل والتعليمات</b> لهذه الطريقة.\n"
        "يمكنك استخدام تنسيق HTML.",
        parse_mode=ParseMode.HTML)
    return AWAIT_PAYMENT_DETAILS


async def admin_handle_payment_details(update: Update, context: CallbackContext) -> int:
    details = update.message.text
    context.user_data['temp_payment_details'] = details

    await update.message.reply_text(
        "🖼 الآن أرسل صورة (مثل QR code) إن أردت إضافتها إلى هذه الطريقة، "
        "أو أرسل /skip للتخطي بدون صورة.")
    return AWAIT_PAYMENT_IMAGE


async def admin_handle_payment_image(update: Update, context: CallbackContext) -> int:
    details = context.user_data.get('temp_payment_details')
    image_file_id = None
    dm = get_data_manager()

    if update.message.photo:
        image_file_id = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.lower() == '/skip':
        image_file_id = None
    else:
        await update.message.reply_text("🖼 يرجى إرسال صورة أو /skip.")
        return AWAIT_PAYMENT_IMAGE

    text_to_show = ""

    if 'new_payment_name' in context.user_data:
        name = context.user_data['new_payment_name']
        await dm.add_payment_method(name, details, image_file_id)
        text_to_show = (f"✅ تم إضافة طريقة الدفع {name} بنجاح. "
                        + ("(مع صورة)" if image_file_id else "(بدون صورة)"))
    elif 'selected_payment_method' in context.user_data:
        name = context.user_data['selected_payment_method']
        await dm.update_payment_method(name, details, image_file_id)
        text_to_show = (f"✅ تم تعديل تفاصيل {name} بنجاح. "
                        + ("(مع صورة)" if image_file_id else "(بدون صورة)"))

    if text_to_show:
        keyboard = [[InlineKeyboardButton("🔚 تطبيق وإنهاء", callback_data="payment_apply_and_end")]]
        await update.message.reply_text(text_to_show, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data.pop('temp_payment_details', None)
        return AWAIT_PAYMENT_CONFIRMATION
    else:
        await update.message.reply_text("❌ حدث خطأ ما. تم إلغاء العملية.")
        return ConversationHandler.END


async def admin_payment_end_conversation(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await show_admin_menu(update, context)
    return ConversationHandler.END
