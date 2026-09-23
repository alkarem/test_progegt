"""كلمات المرور (Argon2id)، ورموز JWT، ورموز التحديث (13-security)."""
import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import Unauthorized, ValidationFailed

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

# قائمة محلية مختصرة لكلمات المرور الشائعة (تُوسَّع من ملف في الإنتاج)
_COMMON = {"password", "123456789012", "passwordpassword", "qwertyuiopas", "admin1234567", "111111111111",
           "aaaaaaaaaaaa", "000000000000", "123123123123", "password1234"}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def validate_password_policy(password: str, username: str | None = None) -> None:
    problems = []
    if len(password) < 12:
        problems.append("12 حرفًا على الأقل")
    if len(password) > 128:
        problems.append("128 حرفًا كحد أقصى")
    if password.lower() in _COMMON or len(set(password)) < 4:
        problems.append("كلمة مرور شائعة أو متكررة الأحرف")
    if username and username.lower() in password.lower():
        problems.append("لا تحتوي اسم المستخدم")
    if not re.search(r"\d", password) or not re.search(r"[^\W\d_]", password):
        problems.append("تحتوي حروفًا وأرقامًا")
    if problems:
        raise ValidationFailed("كلمة المرور لا تستوفي السياسة: " + "، ".join(problems) + ".",
                               code="WEAK_PASSWORD", details={"rules": problems})


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID, auth_version: int) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=s.access_token_minutes)
    payload = {"sub": str(user_id), "sid": str(session_id), "av": auth_version, "iat": now, "exp": exp,
               "typ": "access", "jti": uuid.uuid4().hex}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), s.access_token_minutes * 60


def decode_access_token(token: str) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm],
                             options={"require": ["sub", "exp", "iat", "sid", "av"]})
    except jwt.ExpiredSignatureError as e:
        raise Unauthorized("انتهت صلاحية الجلسة، يرجى تحديث الرمز.", code="TOKEN_EXPIRED") from e
    except jwt.PyJWTError as e:
        raise Unauthorized("رمز الدخول غير صالح.", code="INVALID_TOKEN") from e
    if payload.get("typ") != "access":
        raise Unauthorized("رمز الدخول غير صالح.", code="INVALID_TOKEN")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """يعيد (الرمز الخام للعميل، بصمته للتخزين). لا يُخزَّن الرمز الخام أبدًا."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
