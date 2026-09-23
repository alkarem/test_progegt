"""الاستيراد على ملف «الباب الثاني.xlsx» الحقيقي (لا يُرفع للمستودع لاحتوائه أسماء أشخاص).

يُشغَّل محليًا: GBCFMS_REAL_EXCEL=/path/to/file.xlsx pytest tests/imports/test_real_file.py
"""
import os
from decimal import Decimal

import pytest

from tests.import_helpers import run_import

REAL = os.environ.get("GBCFMS_REAL_EXCEL")
pytestmark = pytest.mark.skipif(not REAL or not os.path.exists(REAL or ""), reason="الملف الحقيقي غير متوفر")


def test_real_file_numbers_match_blueprint_analysis(client, team, user_factory, settings_tmp_storage):
    res = run_import(client, team, user_factory, open(REAL, "rb").read())
    codes = res["issue_codes"]
    for code in ("IMP-05", "IMP-06", "IMP-07", "IMP-09", "IMP-10", "IMP-11", "IMP-12", "IMP-13", "IMP-14", "IMP-15",
                 "IMP-16", "IMP-17", "DQ-04", "IMP-01", "IMP-08"):
        assert code in codes, code
    assert res["created"] == {"authorizations": 5, "transfers": 16, "expenditures": 24}
    pos = res["positions"]
    assert pos["2/16"]["transfer_in"] == Decimal("80130") and pos["2/16"]["available"] == Decimal("-18370")
    assert pos["2/17"]["available"] == Decimal("-1255")
    assert pos["2/18"]["available"] == Decimal("-24969.420")
    assert sum(p["actual"] for p in pos.values()) == Decimal("498674.420")
    assert sum(p["allocation"] for p in pos.values()) == Decimal("346034")
