"""
نظام تحديد معدل الطلبات (Rate Limiter) - مضاد السبام
ملاحظة: في بوت الحقوق كان هذا النظام موجوداً لكن غير موصول بأي معالج (كود ميت).
في هذا البوت تم توصيله فعلياً بتدفق الطالب (الأزرار/الطلبات/البحث).
"""
import time
from collections import defaultdict
from typing import Optional, Tuple

from config import logger, ADMIN_IDS

RATE_LIMITS = {
    "message": {"max_requests": 20, "window_seconds": 60, "warning_threshold": 0.8},
    "order": {"max_requests": 3, "window_seconds": 300, "warning_threshold": 1.0},
    "search": {"max_requests": 10, "window_seconds": 30, "warning_threshold": 0.9},
    "callback": {"max_requests": 30, "window_seconds": 60, "warning_threshold": 0.8},
}

MUTE_THRESHOLDS = {"first_warning": 3, "second_warning": 6, "hard_mute": 10}
MUTE_DURATIONS = {"first_warning": 30, "second_warning": 300, "hard_mute": 900}


class RateLimiter:
    """تحديد المعدل بنافذة منزلقة + كتم تدريجي"""

    def __init__(self):
        self._requests: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._muted: dict[int, dict] = {}
        self._violation_count: dict[int, list[float]] = defaultdict(list)
        self._last_warning_time: dict[int, float] = {}

    def check_rate(self, user_id: int, action_type: str = "message") -> Tuple[bool, Optional[str]]:
        if user_id in ADMIN_IDS:
            return True, None

        now = time.time()

        mute_info = self._muted.get(user_id)
        if mute_info:
            if now < mute_info["until"]:
                remaining = int(mute_info["until"] - now)
                return False, f"🔇 تم كتمك لمدة {remaining} ثانية بسبب الإرسال المفرط."
            else:
                del self._muted[user_id]

        limits = RATE_LIMITS.get(action_type)
        if not limits:
            return True, None

        max_requests = limits["max_requests"]
        window = limits["window_seconds"]

        timestamps = self._requests[user_id][action_type]
        self._requests[user_id][action_type] = [t for t in timestamps if now - t < window]
        timestamps = self._requests[user_id][action_type]

        if len(timestamps) >= max_requests:
            self._record_violation(user_id, now)
            return False, self._get_rejection_message(action_type, len(timestamps), max_requests, window)

        timestamps.append(now)
        return True, None

    def _record_violation(self, user_id: int, now: float):
        self._violation_count[user_id].append(now)
        self._violation_count[user_id] = [t for t in self._violation_count[user_id] if now - t < 300]
        violations = len(self._violation_count[user_id])

        if violations >= MUTE_THRESHOLDS["hard_mute"]:
            self._mute_user(user_id, "hard_mute", now)
        elif violations >= MUTE_THRESHOLDS["second_warning"]:
            self._mute_user(user_id, "second_warning", now)
        elif violations >= MUTE_THRESHOLDS["first_warning"]:
            self._mute_user(user_id, "first_warning", now)

    def _mute_user(self, user_id: int, level: str, now: float):
        duration = MUTE_DURATIONS[level]
        self._muted[user_id] = {"until": now + duration, "reason": level, "level": level}
        logger.warning(f"User {user_id} muted for {duration}s ({level}) - rate limit violations")

    def _get_rejection_message(self, action_type: str, current: int,
                               max_req: int, window: int) -> str:
        window_text = self._format_duration(window)
        if action_type == "message":
            return f"⚠️ أنت ترسل بسرعة كبيرة. الحد {max_req} رسالة في {window_text}. يرجى التريث."
        if action_type == "order":
            return f"⚠️ أنت تقدم طلبات بسرعة كبيرة. الحد {max_req} طلبات في {window_text}."
        if action_type == "search":
            return f"⚠️ أنت تبحث بسرعة كبيرة. الحد {max_req} بحثاً في {window_text}."
        if action_type == "callback":
            return "⚠️ أنت تضغط الأزرار بسرعة كبيرة. يرجى التريث قليلاً."
        return "⚠️ يرجى إبطاء سرعة الإرسال."

    def is_muted(self, user_id: int) -> bool:
        mute_info = self._muted.get(user_id)
        if not mute_info:
            return False
        if time.time() >= mute_info["until"]:
            del self._muted[user_id]
            return False
        return True

    def cleanup(self):
        """تنظيف البيانات القديمة (يُستدعى دورياً كل 5 دقائق)"""
        now = time.time()
        for user_id in list(self._requests.keys()):
            for action_type in list(self._requests[user_id].keys()):
                window = RATE_LIMITS.get(action_type, {}).get("window_seconds", 60)
                self._requests[user_id][action_type] = [
                    t for t in self._requests[user_id][action_type] if now - t < window]
                if not self._requests[user_id][action_type]:
                    del self._requests[user_id][action_type]
            if not self._requests[user_id]:
                del self._requests[user_id]
        for user_id in list(self._violation_count.keys()):
            self._violation_count[user_id] = [
                t for t in self._violation_count[user_id] if now - t < 300]
            if not self._violation_count[user_id]:
                del self._violation_count[user_id]
        for user_id in list(self._muted.keys()):
            if now >= self._muted[user_id]["until"]:
                del self._muted[user_id]

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} ثانية"
        if seconds < 3600:
            return f"{seconds // 60} دقيقة"
        return f"{seconds // 3600} ساعة"


# --- Singleton ---
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
