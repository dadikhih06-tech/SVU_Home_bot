"""
إدارة فترة الانتظار والمهلة
محسّن: نص رسالة المهلة يستخدم القيمة من config (15 دقيقة) بدل نص ثابت
"""
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import CallbackContext

from config import (
    logger, COOLDOWN_UPDATE_INTERVAL, COOLDOWN_MINUTES,
    ORDER_TIMEOUT_SECONDS, ORDER_TIMEOUT_MINUTES
)
from data_manager import get_data_manager


async def check_order_cooldown(user_id: int, context) -> bool:
    """هل يمكن للمستخدم تقديم طلب جديد (انتهت فترة الانتظار)؟"""
    dm = get_data_manager()
    last_order_time = await dm.get_last_order_time(user_id)
    if last_order_time and datetime.now() - last_order_time < timedelta(minutes=COOLDOWN_MINUTES):
        return False
    return True


def schedule_cooldown_reminder(context: CallbackContext, user_id: int):
    """جدولة تذكير بعد انتهاء فترة الانتظار"""
    context.job_queue.run_once(
        callback=cooldown_reminder,
        when=COOLDOWN_MINUTES * 60,
        data={'user_id': user_id},
        name=f"cooldown_reminder_{user_id}",
        job_kwargs={'replace_existing': True}
    )


async def cooldown_reminder(context: CallbackContext):
    data = context.job.data
    await context.bot.send_message(
        chat_id=data['user_id'],
        text="✅ يمكنك الآن تقديم طلب جديد بعد انتهاء فترة الانتظار."
    )


async def start_cooldown_timer(update: Update, context: CallbackContext,
                               user_id: int, message_id: int = None):
    """مؤقت العد التنازلي المرئي"""
    dm = get_data_manager()
    last_order_time = await dm.get_last_order_time(user_id)
    if not last_order_time:
        return

    remaining_time = int(
        (last_order_time + timedelta(minutes=COOLDOWN_MINUTES) - datetime.now()).total_seconds())
    if remaining_time <= 0:
        return

    data = {
        'user_id': user_id,
        'remaining_time': remaining_time,
        'message_id': message_id,
        'chat_id': update.effective_chat.id
    }
    await update_cooldown_message(context, data)
    context.job_queue.run_once(
        update_cooldown_timer, COOLDOWN_UPDATE_INTERVAL,
        data=data, name=f"cooldown_timer_{user_id}"
    )


async def update_cooldown_timer(context: CallbackContext):
    data = context.job.data
    data['remaining_time'] -= COOLDOWN_UPDATE_INTERVAL
    if data['remaining_time'] > 0:
        await update_cooldown_message(context, data)
        context.job_queue.run_once(
            update_cooldown_timer, COOLDOWN_UPDATE_INTERVAL,
            data=data, name=context.job.name
        )
    else:
        try:
            await context.bot.delete_message(
                chat_id=data['chat_id'], message_id=data['message_id'])
        except Exception:
            pass


async def update_cooldown_message(context: CallbackContext, data: dict):
    minutes, seconds = divmod(data['remaining_time'], 60)
    text = f"⏳ يرجى الانتظار {minutes:02d}:{seconds:02d} قبل تقديم طلب جديد."
    try:
        if data['message_id']:
            await context.bot.edit_message_text(
                chat_id=data['chat_id'], message_id=data['message_id'], text=text)
        else:
            message = await context.bot.send_message(chat_id=data['chat_id'], text=text)
            data['message_id'] = message.message_id
    except Exception as e:
        logger.error(f"Failed to update cooldown message: {e}")


def schedule_order_timeout(context: CallbackContext, user_id: int):
    """جدولة إلغاء تلقائي للطلب بعد انتهاء المهلة (15 دقيقة)"""
    context.job_queue.run_once(
        callback=timeout_order,
        when=ORDER_TIMEOUT_SECONDS,
        data=user_id,
        name=f"order_timeout_{user_id}",
        job_kwargs={'replace_existing': True}
    )


async def timeout_order(context: CallbackContext):
    user_id = context.job.data
    user_data = context.application.user_data.get(user_id, {})
    if user_data:
        logger.info(f"Timeout: Canceling incomplete order for user {user_id}")
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"⚠️ تم إلغاء طلبك السابق تلقائياً بسبب عدم إكماله "
                f"في الوقت المحدد ({ORDER_TIMEOUT_MINUTES} دقيقة).\n"
                f"اضغط /start لتقديم طلب جديد."
            )
        )
        user_data.clear()

    for job_name in [f"cooldown_timer_{user_id}", f"cooldown_reminder_{user_id}"]:
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()


def cancel_user_jobs(context: CallbackContext, user_id: int):
    """إلغاء جميع المهام المجدولة لمستخدم"""
    for job_name in [
        f"order_timeout_{user_id}",
        f"cooldown_timer_{user_id}",
        f"cooldown_reminder_{user_id}"
    ]:
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
