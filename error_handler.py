"""
معالج الأخطاء المركزي - النسخة المحسّنة الكاملة
يتضمن: تصفية شاملة للأخطاء العابرة (حتى لا يُغرق الأدمن بإشعارات)

التحسين عن بوت الحقوق (كان يصفّي نوعين فقط):
- Forbidden (حظر البوت) → soft-delete تلقائي + لا إشعار
- TimedOut / NetworkError / RetryAfter → لا إشعار
- أخطاء httpx (انقطاع السيرفر/مهلة الاتصال) → لا إشعار
- أخطاء المحتوى (message not modified / query too old /
  no text to edit / message to edit not found) → لا إشعار
"""
import traceback

from telegram import Update
from telegram.ext import CallbackContext
from telegram.error import Forbidden, TimedOut, NetworkError, RetryAfter

from config import logger, MAIN_ADMIN_ID
from data_manager import get_data_manager

# رسائل خطأ شائعة غير جديرة بإشعار الأدمن
_BENIGN_STRINGS = (
    "message is not modified",
    "query is too old",
    "there is no text in the message to edit",
    "message to edit not found",
    "message to delete not found",
    "message can't be deleted",
    "canceled by new editmessagetext",
    "chat not found",
    "button data invalid",
    "file must be non-empty",
)


def _is_benign(error: Exception) -> bool:
    """فحص شامل: هل الخطأ عابر ولا يستحق إشعار الأدمن؟"""
    # 1) فحص سلسلة الاستثناء بالكامل (يشمل httpx الداخلية)
    chain = []
    e = error
    while e is not None and len(chain) < 5:
        chain.append(e)
        e = e.__cause__ or e.__context__
    for exc in chain:
        if isinstance(exc, (Forbidden, TimedOut, NetworkError, RetryAfter)):
            return True
        mod = type(exc).__module__ or ""
        if mod.startswith("httpx"):
            return True
        err_lower = str(exc).lower()
        if any(s in err_lower for s in _BENIGN_STRINGS):
            return True
        if "server disconnected" in err_lower or "remoteprotocolerror" in err_lower:
            return True
        if "connecttimeout" in err_lower or "readtimeout" in err_lower:
            return True
    return False


async def global_error_handler(update: object, context: CallbackContext) -> None:
    """معالج الأخطاء العام - يصفّي العابر ويبلّغ عن الجوهري فقط"""
    error = context.error
    error_str = str(error).lower() if error else ""

    if _is_benign(error or Exception("")):
        logger.info(f"Benign error ignored: {str(error)[:120]}")

        # حظر البوت من مستخدم → تعطيله تلقائياً (soft-delete) لإيقاف بثه مستقبلاً
        if isinstance(error, Forbidden):
            try:
                if isinstance(update, Update) and update.effective_user:
                    dm = get_data_manager()
                    await dm.remove_user(update.effective_user.id)
            except Exception:
                pass
        return

    # خطأ حقيقي: تسجيل + إشعار الأدمن + رسالة للمستخدم
    logger.error(f"Exception while handling an update: {error}", exc_info=error)
    if error:
        traceback.print_exception(type(error), error, error.__traceback__)

    error_text = str(error)[:800] if error else "Unknown error"
    try:
        await context.bot.send_message(
            chat_id=MAIN_ADMIN_ID,
            text=f"⚠️ <b>خطأ في البوت</b>\n\n<code>{error_text}</code>",
            parse_mode='HTML')
    except Exception as notify_error:
        logger.error(f"Failed to notify admin about error: {notify_error}")

    if isinstance(update, Update) and update.effective_chat:
        try:
            await update.effective_chat.send_message(
                "❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى لاحقاً.")
        except Exception:
            pass


async def safe_answer_callback(query, text: str = "", show_alert: bool = False) -> bool:
    """الإجابة على زر callback بشكل آمن"""
    try:
        await query.answer(text=text, show_alert=show_alert)
        return True
    except Exception as e:
        logger.warning(f"Failed to answer callback query: {e}")
        return False


async def safe_edit_message(query, text: str, reply_markup=None, parse_mode=None) -> bool:
    """تعديل رسالة بشكل آمن - يرسل رسالة جديدة إذا كانت الأصلية غير قابلة للتعديل"""
    try:
        await query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return True
        if "message to edit not found" in err:
            try:
                await query.bot.send_message(
                    chat_id=query.message.chat_id if query.message else query.from_user.id,
                    text=text, reply_markup=reply_markup, parse_mode=parse_mode)
                return True
            except Exception:
                return False
        logger.error(f"Failed to edit message: {e}")
        return False
