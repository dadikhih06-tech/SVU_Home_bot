"""
البث والإشعارات الجماعية (Broadcast)
يتضمن: بث فوري/للنشطين/مجدول، شريط تقدم، إلغاء، تقرير نهائي

التحسين الجوهري عن بوت الحقوق:
- ✅ حفظ مرجع مهمة البث الخلفية `_active_broadcast_task`
  (في بوت الحقوق كانت المهمة تُنشأ بـ create_task دون الاحتفاظ بمرجع،
  مما يعرّضها لحذفها من الذاكرة بواسطة garbage collector - مشكلة asyncio معروفة)
- البث حلقة asyncio واحدة متصلة (حل race condition في JobQueue الخاص بـ PTB v20.7)
"""
import asyncio
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

from config import logger, MAIN_ADMIN_ID, BROADCAST_BATCH_SIZE, BROADCAST_BATCH_DELAY, LOCAL_TZ
from states import ADMIN_BROADCAST, BROADCAST_PREVIEW, BROADCAST_SCHEDULE
from data_manager import get_data_manager
from keyboards import build_back_to_admin_menu_keyboard
from utils import is_admin_in_group

# --- بيانات البث النشط (للإلغاء) ---
_active_broadcast_data: dict = None
_active_broadcast_task: asyncio.Task = None  # ✅ مرجع المهمة (إصلاح GC)


def _set_active_broadcast_data(data: dict):
    global _active_broadcast_data
    _active_broadcast_data = data


def _get_active_broadcast_data() -> dict:
    return _active_broadcast_data


def _clear_active_broadcast_data():
    global _active_broadcast_data, _active_broadcast_task
    _active_broadcast_data = None
    _active_broadcast_task = None


def _build_progress_bar(percentage: float, width: int = 15) -> str:
    filled = int(width * percentage / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {percentage:.1f}%"


# --- معالجات البث ---

async def broadcast_start_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📢 أرسل الآن الرسالة أو الوسائط التي تود بثها للجميع.\n\n"
        "يمكنك إرسال:\n"
        "- نص عادي\n"
        "- صورة مع تعليق\n"
        "- فيديو / ملف / رسالة صوتية\n\n"
        "🚫 للإلغاء أرسل /cancel."
    )
    return ADMIN_BROADCAST


async def broadcast_preview_handler(update: Update, context: CallbackContext) -> int:
    message = update.message
    context.user_data['broadcast_message_id'] = message.message_id
    context.user_data['broadcast_chat_id'] = message.chat_id

    dm = get_data_manager()
    user_count = await dm.get_user_count()

    preview_text = (
        "👁 <b>معاينة الرسالة التي سيتم بثها:</b>\n\n"
        f"👥 سيتم الإرسال إلى <b>{user_count}</b> مستخدم\n\n"
        "اختر طريقة الإرسال:"
    )

    keyboard = [
        [InlineKeyboardButton("✅ إرسال فوري للجميع", callback_data="broadcast_confirm_now")],
        [InlineKeyboardButton("📊 إرسال للمستخدمين النشطين فقط", callback_data="broadcast_active_only")],
        [InlineKeyboardButton("⏰ جدولة البث (تأجيل)", callback_data="broadcast_schedule")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="broadcast_cancel")]
    ]

    await message.reply_text(
        preview_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BROADCAST_PREVIEW


async def broadcast_confirm_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "broadcast_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم إلغاء عملية البث.")
        return ConversationHandler.END

    elif query.data == "broadcast_confirm_now":
        await query.edit_message_text("🚀 جاري بدء عملية البث...")
        message_id = context.user_data.get('broadcast_message_id')
        chat_id = context.user_data.get('broadcast_chat_id')
        context.user_data.clear()
        # ✅ نسخ المعطيات قبل مسح user_data ثم بدء البث كخلفية
        asyncio.create_task(
            broadcast_execute(context, datetime.now(LOCAL_TZ), filter_type='all',
                              message_id=message_id, chat_id=chat_id))
        return ConversationHandler.END

    elif query.data == "broadcast_active_only":
        await query.edit_message_text("🚀 جاري بدء عملية البث للمستخدمين النشطين...")
        message_id = context.user_data.get('broadcast_message_id')
        chat_id = context.user_data.get('broadcast_chat_id')
        context.user_data.clear()
        asyncio.create_task(
            broadcast_execute(context, datetime.now(LOCAL_TZ), filter_type='active',
                              message_id=message_id, chat_id=chat_id))
        return ConversationHandler.END

    elif query.data == "broadcast_schedule":
        await query.edit_message_text(
            "⏰ <b>جدولة البث</b>\n\n"
            "أدخل مدة التأجيل:\n"
            "- رقم بالدقائق (مثال: <code>30</code> = 30 دقيقة)\n"
            "- رقم بالساعات (مثال: <code>2h</code> = ساعتين)\n\n"
            "🚫 للإلغاء أرسل /cancel.",
            parse_mode=ParseMode.HTML
        )
        context.user_data['broadcast_filter_type'] = 'all'
        return BROADCAST_SCHEDULE

    return BROADCAST_PREVIEW


async def broadcast_schedule_handler(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()

    delay_seconds = None
    try:
        hours_match = re.match(r'^(\d+(?:\.\d+)?)\s*[hH]$', text)
        if hours_match:
            delay_seconds = int(float(hours_match.group(1)) * 3600)
        else:
            minutes_match = re.match(r'^(\d+(?:\.\d+)?)\s*[mM]?$', text)
            if minutes_match:
                delay_seconds = int(float(minutes_match.group(1)) * 60)
    except (ValueError, TypeError):
        pass

    if not delay_seconds or delay_seconds < 60:
        await update.message.reply_text(
            "⚠️ صيغة غير صحيحة. أدخل مدة صالحة (الحد الأدنى دقيقة واحدة):\n"
            "- <code>30</code> = 30 دقيقة\n"
            "- <code>2h</code> = ساعتين\n\n"
            "🚫 للإلغاء أرسل /cancel.",
            parse_mode=ParseMode.HTML)
        return BROADCAST_SCHEDULE

    if delay_seconds > 86400 * 7:
        await update.message.reply_text("⚠️ الحد الأقصى للجدولة هو 7 أيام. أدخل مدة أقل.")
        return BROADCAST_SCHEDULE

    filter_type = context.user_data.get('broadcast_filter_type', 'all')
    schedule_time = datetime.now(LOCAL_TZ) + timedelta(seconds=delay_seconds)

    if delay_seconds >= 3600:
        hours = delay_seconds // 3600
        minutes = (delay_seconds % 3600) // 60
        delay_text = f"{hours} ساعة" + (f" و {minutes} دقيقة" if minutes > 0 else "")
    else:
        delay_text = f"{delay_seconds // 60} دقيقة"

    message_id = context.user_data.get('broadcast_message_id')
    chat_id = context.user_data.get('broadcast_chat_id')

    await update.message.reply_text(
        f"⏰ <b>تم جدولة البث بنجاح!</b>\n\n"
        f"📅 سيتم الإرسال في: {schedule_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"⏱ المدة المتبقية: {delay_text}\n"
        f"🎯 الفلتر: {'الجميع' if filter_type == 'all' else 'النشطون فقط'}\n\n"
        f"يمكنك إلغاء البث المجدول بأمر /cancel_broadcast",
        parse_mode=ParseMode.HTML
    )

    # JobQueue آمن هنا (وظيفة واحدة لا تتسلسل)
    job_data = {
        'message_id': message_id,
        'chat_id': chat_id,
        'filter_type': filter_type,
        'scheduled_by': update.effective_user.id,
    }
    job_name = f"scheduled_broadcast_{datetime.now().timestamp()}"
    context.job_queue.run_once(
        _execute_scheduled_broadcast, delay_seconds, data=job_data, name=job_name)

    dm = get_data_manager()
    await dm.set_active_broadcast_job(job_name)

    context.user_data.clear()
    return ConversationHandler.END


async def _execute_scheduled_broadcast(context: CallbackContext):
    data = context.job.data
    try:
        await context.bot.send_message(
            chat_id=MAIN_ADMIN_ID,
            text="⏰ <b>بدء البث المجدول الآن!</b>",
            parse_mode=ParseMode.HTML)
    except Exception:
        pass

    await broadcast_execute(
        context, datetime.now(LOCAL_TZ),
        filter_type=data.get('filter_type', 'all'),
        message_id=data.get('message_id'),
        chat_id=data.get('chat_id'))


# --- تنفيذ البث ---

async def broadcast_execute(
    context: CallbackContext, schedule_time: datetime, filter_type: str = 'all',
    message_id: int = None, chat_id: int = None
):
    """بدء البث كمهمة خلفية واحدة متصلة"""
    global _active_broadcast_task

    if not message_id or not chat_id:
        logger.error("broadcast_execute: missing message_id or chat_id")
        return

    dm = get_data_manager()
    banned_users = await dm.get_banned_users()

    if filter_type == 'active':
        all_orders = await dm.get_all_orders()
        active_user_ids = {order.get('user_id') for _, order in all_orders}
        all_users = list(active_user_ids - {MAIN_ADMIN_ID} - banned_users)
    else:
        all_users = [uid for uid in await dm.get_all_user_ids()
                     if uid != MAIN_ADMIN_ID and uid not in banned_users]

    if not all_users:
        await context.bot.send_message(
            chat_id=MAIN_ADMIN_ID,
            text="❌ لا يوجد مستخدمون لإرسال الرسالة إليهم")
        return

    data = {
        'message_id': message_id,
        'chat_id': chat_id,
        'users': all_users,
        'success': 0,
        'failed': 0,
        'fail_reasons': {},
        'batch_index': 0,
        'report_message_id': None,
        'start_time': datetime.now(),
        'total_users': len(all_users),
        'filter_type': filter_type,
        'broadcast_db_id': None,
        'cancelled': False,
    }

    data['broadcast_db_id'] = await dm.log_broadcast_start(
        admin_id=MAIN_ADMIN_ID, total_users=len(all_users))

    filter_text = 'الجميع' if filter_type == 'all' else 'النشطون فقط'
    try:
        report_message = await context.bot.send_message(
            chat_id=MAIN_ADMIN_ID,
            text=(
                f"🚀 <b>بدء عملية البث</b>\n\n"
                f"👥 عدد المستخدمين: {len(all_users)}\n"
                f"📦 حجم الدفعة: {BROADCAST_BATCH_SIZE}\n"
                f"⏱ التأخير بين الدفعات: {BROADCAST_BATCH_DELAY} ثانية\n"
                f"🎯 الفلتر: {filter_text}\n\n"
                f"{_build_progress_bar(0)}\n\n"
                f"⏳ جاري الإرسال..."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 إلغاء البث", callback_data="broadcast_cancel_active")]
            ]),
            parse_mode=ParseMode.HTML)
        data['report_message_id'] = report_message.message_id
    except Exception as e:
        logger.error(f"Failed to send initial broadcast report: {e}")
        return

    _set_active_broadcast_data(data)
    await dm.set_active_broadcast_job('active_async_task')

    # ✅ الاحتفاظ بمرجع المهمة لمنع garbage collector
    _active_broadcast_task = asyncio.create_task(_broadcast_run_loop(context, data))


async def _broadcast_run_loop(context: CallbackContext, data: dict):
    """حلقة البث الرئيسية - مهمة واحدة متصلة عبر جميع الدفعات"""
    users = data['users']
    total_users = len(users)
    bot = context.bot

    try:
        while True:
            if data.get('cancelled'):
                logger.info(f"Broadcast cancelled at batch {data['batch_index']}")
                break

            batch_index = data['batch_index']
            batch_start = batch_index * BROADCAST_BATCH_SIZE
            batch_end = batch_start + BROADCAST_BATCH_SIZE
            batch_users = users[batch_start:batch_end]

            if not batch_users:
                break

            for user_id in batch_users:
                if data.get('cancelled'):
                    break
                try:
                    await bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=data['chat_id'],
                        message_id=data['message_id'])
                    data['success'] += 1
                    await asyncio.sleep(0.05)
                except Forbidden:
                    data['failed'] += 1
                    reason = "⛔ المستخدم حظر البوت"
                    data['fail_reasons'][reason] = data['fail_reasons'].get(reason, 0) + 1
                    await get_data_manager().remove_user(user_id)
                except BadRequest as e:
                    data['failed'] += 1
                    err = str(e).lower()
                    dm = get_data_manager()
                    if "chat not found" in err:
                        reason = "👤 المستخدم غادر المحادثة"
                        await dm.remove_user(user_id)
                    elif "user is deactivated" in err:
                        reason = "🗑 حساب المستخدم محذوف"
                        await dm.remove_user(user_id)
                    elif "message to copy not found" in err:
                        reason = "❌ الرسالة الأصلية محذوفة"
                    else:
                        reason = "⚠️ خطأ في الطلب"
                    data['fail_reasons'][reason] = data['fail_reasons'].get(reason, 0) + 1
                except Exception as e:
                    data['failed'] += 1
                    reason = "⚠️ خطأ غير متوقع"
                    data['fail_reasons'][reason] = data['fail_reasons'].get(reason, 0) + 1
                    logger.error(f"Broadcast failed to {user_id}: {e}")

            if data.get('cancelled'):
                break

            total_processed = min(batch_end, total_users)
            if data['report_message_id'] and (
                    batch_index % 3 == 0 or len(batch_users) < BROADCAST_BATCH_SIZE):
                try:
                    await _broadcast_update_progress(context, data, total_processed, total_users)
                except Exception as e:
                    logger.error(f"Progress update failed (non-fatal): {e}")

            data['batch_index'] += 1
            if batch_end >= total_users:
                break

            try:
                await asyncio.sleep(BROADCAST_BATCH_DELAY)
            except asyncio.CancelledError:
                break

        await _broadcast_finalize(context, data)

    except asyncio.CancelledError:
        logger.info("Broadcast run loop cancelled")
        data['cancelled'] = True
        await _broadcast_finalize(context, data)
    except Exception as e:
        logger.error(f"Broadcast run loop error: {e}", exc_info=True)
        try:
            await _broadcast_finalize(context, data)
        except Exception as finalize_err:
            logger.error(f"Failed to finalize broadcast: {finalize_err}")


async def broadcast_cancel_active(update: Update, context: CallbackContext) -> None:
    """إلغاء البث النشط من زر الإلغاء"""
    query = update.callback_query
    await query.answer("🚫 جاري إلغاء البث...")

    data = _get_active_broadcast_data()
    if data:
        data['cancelled'] = True
        logger.info(f"Broadcast cancellation requested by {query.from_user.id}")

    dm = get_data_manager()
    await dm.clear_active_broadcast_job()

    try:
        await query.edit_message_text(
            "🚫 تم إلغاء البث. سيتم إيقاف الإرسال بعد الدفعة الحالية.",
            reply_markup=build_back_to_admin_menu_keyboard())
    except Exception:
        pass


async def broadcast_cancel_command(update: Update, context: CallbackContext) -> None:
    """أمر /cancel_broadcast"""
    if not await is_admin_in_group(update.effective_user.id, context):
        return

    data = _get_active_broadcast_data()
    if data and not data.get('cancelled'):
        data['cancelled'] = True
        dm = get_data_manager()
        await dm.clear_active_broadcast_job()
        _clear_active_broadcast_data()
        await update.message.reply_text(
            "🚫 تم إيقاف عملية البث. سيتم الإيقاف بعد الدفعة الحالية.")
    else:
        dm = get_data_manager()
        job_name = await dm.clear_active_broadcast_job()
        if job_name and job_name.startswith('scheduled_broadcast_'):
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
            await update.message.reply_text("🚫 تم إلغاء البث المجدول.")
        else:
            await update.message.reply_text("ℹ️ لا توجد عملية بث جارية أو مجدولة.")


# --- دوال مساعدة ---

async def _broadcast_finalize(context: CallbackContext, data: dict):
    """إنهاء البث وإرسال التقرير النهائي"""
    total = data['success'] + data['failed']
    percentage = (data['success'] / total * 100) if total > 0 else 0
    duration = datetime.now() - data['start_time']

    report_text = (
        f"✅ <b>انتهت عملية البث!</b>\n\n"
        f"{_build_progress_bar(100)}\n\n"
        f"⏱ المدة: {duration.seconds // 60} دقيقة و {duration.seconds % 60} ثانية\n"
        f"📊 <b>الإحصائيات النهائية:</b>\n"
        f"- الإجمالي: {total}\n"
        f"- نجح: {data['success']} ✅ ({percentage:.1f}%)\n"
        f"- فشل: {data['failed']} ❌\n"
    )

    if data['fail_reasons']:
        report_text += "\n📋 <b>أسباب الفشل:</b>\n"
        for reason, count in sorted(data['fail_reasons'].items(),
                                    key=lambda x: x[1], reverse=True):
            report_text += f"- {reason}: {count}\n"

    if data.get('cancelled'):
        report_text = "🚫 <b>تم إلغاء البث!</b>\n\n" + report_text

    try:
        await context.bot.edit_message_text(
            chat_id=MAIN_ADMIN_ID,
            message_id=data['report_message_id'],
            text=report_text,
            reply_markup=build_back_to_admin_menu_keyboard(),
            parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send final broadcast report: {e}")
        try:
            await context.bot.send_message(
                chat_id=MAIN_ADMIN_ID, text=report_text,
                reply_markup=build_back_to_admin_menu_keyboard(),
                parse_mode=ParseMode.HTML)
        except Exception as e2:
            logger.error(f"Failed fallback report: {e2}")

    if data.get('broadcast_db_id'):
        dm = get_data_manager()
        await dm.log_broadcast_complete(
            data['broadcast_db_id'], data['success'], data['failed'])

    dm = get_data_manager()
    await dm.clear_active_broadcast_job()
    _clear_active_broadcast_data()


async def _broadcast_update_progress(context: CallbackContext, data: dict,
                                     total_processed: int, total_users: int):
    remaining = total_users - total_processed
    percentage = (total_processed / total_users * 100) if total_users > 0 else 0
    elapsed = (datetime.now() - data['start_time']).total_seconds()

    if total_processed > 0 and remaining > 0:
        rate = elapsed / total_processed
        eta_seconds = int(rate * remaining)
        eta_text = f"{eta_seconds // 60} دقيقة و {eta_seconds % 60} ثانية"
    else:
        eta_text = "جاري الحساب..."

    filter_text = 'الجميع' if data.get('filter_type') == 'all' else 'النشطون فقط'
    status_text = "🚫 <b>جاري إلغاء البث...</b>" if data.get('cancelled') \
        else "📢 <b>جاري إجراء البث...</b>"

    report_text = (
        f"{status_text} ({filter_text})\n\n"
        f"{_build_progress_bar(percentage)}\n\n"
        f"📊 التقدم: {total_processed}/{total_users}\n"
        f"✅ نجح: {data['success']}\n"
        f"❌ فشل: {data['failed']}\n"
        f"⏳ متبقي: {remaining} مستخدم\n"
        f"🕐 الوقت المتبقي التقريبي: {eta_text}\n\n"
        f"⏱ السرعة: {BROADCAST_BATCH_SIZE} رسالة كل {BROADCAST_BATCH_DELAY} ثانية"
    )

    await context.bot.edit_message_text(
        chat_id=MAIN_ADMIN_ID,
        message_id=data['report_message_id'],
        text=report_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 إلغاء البث", callback_data="broadcast_cancel_active")]
        ]),
        parse_mode=ParseMode.HTML)
