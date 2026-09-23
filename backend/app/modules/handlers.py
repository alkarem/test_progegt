"""استيراد كل معالجات المستندات لتسجيلها في محرك الموافقات."""
from app.modules.adjustments import handler as _adjustments  # noqa: F401
from app.modules.authorizations import handler as _authorizations  # noqa: F401
from app.modules.budget import handler as _budget  # noqa: F401
from app.modules.commitments import handler as _commitments  # noqa: F401
from app.modules.expenditures import handler as _expenditures  # noqa: F401
from app.modules.transfers import handler as _transfers  # noqa: F401
