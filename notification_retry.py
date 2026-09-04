"""
نظام إعادة المحاولة التلقائية للإشعارات الفاشلة
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from telegram.ext import CallbackContext
from telegram.error import NetworkError, TimedOut, BadRequest, Forbidden, RetryAfter

from config import logger, MAIN_ADMIN_ID
from data_manager import get_data_manager

MAX_RETRY_ATTEMPTS = 8
INITIAL_RETRY_DELAY = 120
MAX_RETRY_DELAY = 3600
RETRY_JOB_INTERVAL = 60


class NotificationRetryManager:
    """إرسال آمن مع تسجيل الفشل وإعادة محاولة تلقائية بـ Backoff"""

    @staticmethod
    async def safe_send_message(
        bot, chat_id: int, text: str, notification_type: str = "general",
        reference_id: int = None, parse_mode: str = "HTML",
        reply_markup=None, **kwargs
    ) -> bool:
        try:
            await bot.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode,
                reply_markup=reply_markup, **kwargs)
            return True
        except RetryAfter as e:
            retry_after = e.retry_after if hasattr(e, 'retry_after') else 30
            logger.warning(f"Rate limited. Retry after {retry_after}s. Saving notification.")
            await NotificationRetryManager._save_failed_notification(
                chat_id=chat_id, text=text, notification_type=notification_type,
                reference_id=reference_id, parse_mode=parse_mode,
                error_type="rate_limited", error_message=str(e),
                custom_retry_delay=retry_after)
            return False
        except (NetworkError, TimedOut) as e:
            logger.warning(f"Network error sending to {chat_id}: {e}")
            await NotificationRetryManager._save_failed_notification(
                chat_id=chat_id, text=text, notification_type=notification_type,
                reference_id=reference_id, parse_mode=parse_mode,
                error_type="network", error_message=str(e))
            return False
        except Forbidden as e:
            logger.warning(f"Bot blocked by user {chat_id}: {e}")
            return False
        except BadRequest as e:
            err = str(e).lower()
            if "chat not found" in err:
                logger.warning(f"Chat not found: {chat_id}")
                return False
            logger.error(f"Bad request sending notification: {e}")
            await NotificationRetryManager._save_failed_notification(
                chat_id=chat_id, text=text, notification_type=notification_type,
                reference_id=reference_id, parse_mode=parse_mode,
                error_type="bad_request", error_message=str(e))
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending to {chat_id}: {e}")
            await NotificationRetryManager._save_failed_notification(
                chat_id=chat_id, text=text, notification_type=notification_type,
                reference_id=reference_id, parse_mode=parse_mode,
                error_type="unknown", error_message=str(e))
            return False

    @staticmethod
    async def _save_failed_notification(
        chat_id: int, text: str, notification_type: str, reference_id: int = None,
        parse_mode: str = "HTML", reply_markup=None, error_type: str = "unknown",
        error_message: str = "", custom_retry_delay: int = None, **kwargs
    ):
        dm = get_data_manager()
        if custom_retry_delay:
            next_retry = datetime.now() + timedelta(seconds=custom_retry_delay)
        else:
            next_retry = datetime.now() + timedelta(seconds=INITIAL_RETRY_DELAY)

        reply_markup_json = None
        if reply_markup:
            try:
                from telegram import InlineKeyboardMarkup
                if isinstance(reply_markup, InlineKeyboardMarkup):
                    reply_markup_json = json.dumps(reply_markup.to_dict())
            except Exception:
                pass

        await dm.save_failed_notification(
            chat_id=chat_id, message_text=text, parse_mode=parse_mode,
            reply_markup_json=reply_markup_json, notification_type=notification_type,
            reference_id=reference_id, attempt_count=1,
            max_attempts=MAX_RETRY_ATTEMPTS,
            next_retry_at=next_retry.isoformat(),
            error_type=error_type, error_message=error_message[:500])

    @staticmethod
    async def process_retry_queue(context: CallbackContext):
        """معالجة طابور الإعادة (كل دقيقة)"""
        dm = get_data_manager()
        pending = await dm.get_pending_retry_notifications(limit=10)
        if not pending:
            return

        for n in pending:
            notif_id = n['id']
            chat_id = n['chat_id']
            text = n['message_text']
            attempt = n['attempt_count']
            max_attempts = n['max_attempts']

            if n.get('error_type', '') in ('blocked', 'chat_not_found'):
                await dm.delete_failed_notification(notif_id)
                continue

            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=n.get('parse_mode', 'HTML'))
                await dm.delete_failed_notification(notif_id)
                logger.info(f"Retried notification {notif_id} to {chat_id} OK (attempt {attempt})")
            except RetryAfter as e:
                retry_after = e.retry_after if hasattr(e, 'retry_after') else 60
                next_retry = datetime.now() + timedelta(seconds=retry_after)
                await dm.update_failed_notification_retry(
                    notif_id, attempt + 1, next_retry.isoformat(), "rate_limited")
            except (NetworkError, TimedOut):
                new_attempt = attempt + 1
                if new_attempt >= max_attempts:
                    await dm.delete_failed_notification(notif_id)
                    await NotificationRetryManager._notify_admin_permanent_failure(
                        context, notif_id, chat_id, text, new_attempt)
                else:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** (new_attempt - 1)), MAX_RETRY_DELAY)
                    next_retry = datetime.now() + timedelta(seconds=delay)
                    await dm.update_failed_notification_retry(
                        notif_id, new_attempt, next_retry.isoformat(), "network")
            except (Forbidden, BadRequest) as e:
                await dm.delete_failed_notification(notif_id)
                logger.warning(f"Permanent failure for notification {notif_id}: {e}")
            except Exception as e:
                new_attempt = attempt + 1
                if new_attempt >= max_attempts:
                    await dm.delete_failed_notification(notif_id)
                    await NotificationRetryManager._notify_admin_permanent_failure(
                        context, notif_id, chat_id, text, new_attempt)
                else:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** (new_attempt - 1)), MAX_RETRY_DELAY)
                    next_retry = datetime.now() + timedelta(seconds=delay)
                    await dm.update_failed_notification_retry(
                        notif_id, new_attempt, next_retry.isoformat(), type(e).__name__)

    @staticmethod
    async def _notify_admin_permanent_failure(context, notif_id, chat_id, text, attempts):
        try:
            await context.bot.send_message(
                chat_id=MAIN_ADMIN_ID,
                text=(
                    f"⚠️ <b>فشل دائم في إرسال إشعار</b>\n\n"
                    f"📋 معرف الإشعار: {notif_id}\n"
                    f"👤 المستلم: <code>{chat_id}</code>\n"
                    f"🔄 عدد المحاولات: {attempts}\n"
                    f"📝 نص الرسالة:\n<code>{text[:200]}</code>"
                ),
                parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")


def get_retry_manager() -> NotificationRetryManager:
    return NotificationRetryManager()
