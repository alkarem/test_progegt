"""أدوات المبالغ: الدينار الليبي بثلاث خانات عشرية (D-11). لا float في أي مسار مالي."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, PlainSerializer

SCALE = Decimal("0.001")
MAX_AMOUNT = Decimal("999999999999999.999")  # NUMERIC(18,3)
ZERO = Decimal("0.000")


def to_money(value) -> Decimal:
    if isinstance(value, float):
        raise ValueError("المبالغ لا تُقبل كأرقام عشرية عائمة (float)؛ أرسلها كنص.")
    if isinstance(value, Decimal):
        d = value
    else:
        text = str(value).strip().replace(",", "").replace("٬", "")
        try:
            d = Decimal(text)
        except InvalidOperation as e:
            raise ValueError("المبلغ غير صالح.") from e
    if not d.is_finite():
        raise ValueError("المبلغ غير صالح.")
    if d.as_tuple().exponent < -3:
        raise ValueError("المبلغ يقبل ثلاث خانات عشرية كحد أقصى (درهم).")
    if abs(d) > MAX_AMOUNT:
        raise ValueError("المبلغ يتجاوز الحد الأقصى.")
    return d.quantize(SCALE, rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    return f"{value:,.3f}"


# نوع Pydantic: يقبل نصًا أو عددًا صحيحًا أو Decimal، ويُسلسل كنص "1234.500"
Money = Annotated[Decimal, BeforeValidator(to_money), PlainSerializer(lambda v: f"{v:.3f}", return_type=str, when_used="json")]


def _positive(v: Decimal) -> Decimal:
    if v <= 0:
        raise ValueError("المبلغ يجب أن يكون أكبر من صفر.")
    return v


PositiveMoney = Annotated[
    Decimal, BeforeValidator(to_money), AfterValidator(_positive),
    PlainSerializer(lambda v: f"{v:.3f}", return_type=str, when_used="json"),
]
