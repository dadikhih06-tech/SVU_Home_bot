"""
نظام التحقق من صحة المدخلات
متصل فعلياً بتدفق الطالب (رقم العملية) وإدارة الأدمن (أسماء الخدمات/الأقسام/المواد/الأسعار)
"""
import re
from typing import Optional, Tuple

from config import logger

# --- أنماط الخطورة (SQL Injection / XSS) ---
DANGEROUS_PATTERNS = [
    r"(?i)(DROP\s+TABLE|DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET)",
    r"(?i)(UNION\s+SELECT|OR\s+1\s*=\s*1|AND\s+1\s*=\s*1)",
    r"<script[^>]*>|</script>",
    r"(?i)(javascript:|on\w+\s*=)",
]

VALIDATION_RULES = {
    "transaction_id": {
        "min_length": 1, "max_length": 100,
        "pattern": r"^[^<>{}\\]+$",
        "pattern_error": "رقم العملية يحتوي رموز غير مسموحة.",
        "max_error": "رقم العملية طويل جداً. الحد الأقصى 100 حرف.",
        "empty_error": "يرجى إدخال رقم العملية أو إرسال صورة/ملف الإيصال.",
    },
    "service_name": {
        "min_length": 2, "max_length": 50,
        "pattern": r"^[\u0600-\u06FFa-zA-Z0-9\s\-()️🧮📝📚⭐🧾]+",
        "pattern_error": "اسم الخدمة يحتوي رموز غير مسموحة.",
        "min_error": "اسم الخدمة قصير جداً.",
        "max_error": "اسم الخدمة طويل جدأ. الحد الأقصى 50 حرف.",
        "empty_error": "يرجى إدخال اسم الخدمة.",
    },
    "section_name": {
        "min_length": 2, "max_length": 50,
        "pattern": r"^[\u0600-\u06FFa-zA-Z0-9\s\-()]+$",
        "pattern_error": "اسم القسم يحتوي رموز غير مسموحة.",
        "min_error": "اسم القسم قصير جداً.",
        "max_error": "اسم القسم طويل جداً. الحد الأقصى 50 حرف.",
        "empty_error": "يرجى إدخال اسم القسم.",
    },
    "course_name": {
        "min_length": 2, "max_length": 80,
        "pattern": r"^[\u0600-\u06FFa-zA-Z0-9\s\-()'()]+$",
        "pattern_error": "اسم المادة يحتوي رموز غير مسموحة.",
        "min_error": "اسم المادة قصير جداً.",
        "max_error": "اسم المادة طويل جداً. الحد الأقصى 80 حرف.",
        "empty_error": "يرجى إدخال اسم المادة.",
    },
    "payment_name": {
        "min_length": 2, "max_length": 30,
        "pattern": r"^[\u0600-\u06FFa-zA-Z0-9\s\-()]+$",
        "pattern_error": "اسم طريقة الدفع يحتوي رموز غير مسموحة.",
        "min_error": "اسم طريقة الدفع قصير جداً.",
        "max_error": "اسم طريقة الدفع طويل جداً.",
        "empty_error": "يرجى إدخال اسم طريقة الدفع.",
    },
    "price": {
        "min_value": 100, "max_value": 10000000, "type": "numeric",
        "type_error": "يرجى إدخال رقم صحيح للسعر.",
        "min_error": "السعر أقل من الحد الأدنى (100 ل.س).",
        "max_error": "السعر أعلى من الحد الأقصى (10,000,000 ل.س).",
        "empty_error": "يرجى إدخال السعر.",
    },
}


class InputValidator:
    """تحقق متقدم: نمط + طول + مدى عددي + تطهير + كشف حقن"""

    @staticmethod
    def validate(text: str, field_type: str) -> Tuple[bool, Optional[str]]:
        if not text or not text.strip():
            return False, VALIDATION_RULES.get(
                field_type, {}).get("empty_error", "يرجى إدخال قيمة.")

        text = text.strip()
        rules = VALIDATION_RULES.get(field_type)
        if not rules:
            return True, None

        if InputValidator.detect_injection(text):
            logger.warning(f"Injection attempt in '{field_type}': {text[:50]}")
            return False, "❌ المدخل يحتوي محتوى غير مسموح به."

        if rules.get("type") == "numeric":
            return InputValidator._validate_numeric(text, rules)

        min_len = rules.get("min_length", 0)
        max_len = rules.get("max_length", float("inf"))
        if len(text) < min_len:
            return False, rules.get("min_error", "المدخل قصير جداً.")
        if len(text) > max_len:
            return False, rules.get("max_error", "المدخل طويل جداً.")

        pattern = rules.get("pattern")
        if pattern and not re.match(pattern, text):
            return False, rules.get("pattern_error", "المدخل يحتوي رموز غير مسموحة.")

        return True, None

    @staticmethod
    def _validate_numeric(text: str, rules: dict) -> Tuple[bool, Optional[str]]:
        normalized = InputValidator.normalize_numbers(text)
        normalized = re.sub(r"[.,٬\s]", "", normalized)
        try:
            value = int(normalized)
        except ValueError:
            return False, rules.get("type_error", "يرجى إدخال رقم صحيح.")
        if value < rules.get("min_value", float("-inf")):
            return False, rules.get("min_error", "القيمة أقل من الحد الأدنى.")
        if value > rules.get("max_value", float("inf")):
            return False, rules.get("max_error", "القيمة أعلى من الحد الأقصى.")
        return True, None

    @staticmethod
    def sanitize(text: str) -> str:
        if not text:
            return text
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        return text.strip()

    @staticmethod
    def detect_injection(text: str) -> bool:
        if not text:
            return False
        return any(re.search(p, text) for p in DANGEROUS_PATTERNS)

    @staticmethod
    def normalize_numbers(text: str) -> str:
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        persian_digits = "۰۱۲۳۴۵۶۷۸۹"
        result = []
        for char in text:
            if char in arabic_digits:
                result.append(str(arabic_digits.index(char)))
            elif char in persian_digits:
                result.append(str(persian_digits.index(char)))
            else:
                result.append(char)
        return "".join(result)

    @staticmethod
    def validate_search_query(text: str) -> Tuple[bool, Optional[str]]:
        if not text or not text.strip():
            return False, "يرجى إدخال نص للبحث."
        text = text.strip()
        if InputValidator.detect_injection(text):
            return False, "❌ نص البحث يحتوي محتوى غير مسموح."
        if len(text) > 100:
            return False, "نص البحث طويل جداً. الحد الأقصى 100 حرف."
        return True, None
