"""تطبيع النص العربي للبحث وكشف التكرار (FR-SR-02)."""
import re

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})
_HONORIFICS = re.compile(r"^\s*(السيد|السيده|الساده|السادة|السيدة)\s*/?\s*")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    t = _DIACRITICS.sub("", text).replace(_TATWEEL, "").translate(_MAP)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def normalize_party_name(text: str | None) -> str:
    """اسم المورد/المستفيد: تطبيع + إزالة الألقاب («السيد /»)."""
    return normalize(_HONORIFICS.sub("", text or ""))
