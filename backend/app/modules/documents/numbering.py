"""ترقيم تلقائي للمستندات لكل (سنة، نوع)، بقفل صف العداد لمنع التكرار عند التزامن."""
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

PREFIXES = {
    "ORIGINAL_BUDGET": "BUD", "BUDGET_INCREASE": "INC", "BUDGET_DECREASE": "DEC",
    "authorization": "AUT", "transfer": "TRF", "PURCHASE_REQUEST": "PR", "PURCHASE_ORDER": "PO",
    "CONTRACT": "CON", "OBLIGATION": "OBL", "OTHER": "CMT", "expenditure": "EXP", "ADJUSTMENT": "ADJ",
    "REVERSAL": "REV",
}


def next_doc_no(session: Session, fiscal_year_id: uuid.UUID, year: int, doc_type: str) -> str:
    n = session.execute(text(
        "INSERT INTO document_sequences (fiscal_year_id, doc_type, last_no) VALUES (:fy, :t, 1)"
        " ON CONFLICT (fiscal_year_id, doc_type) DO UPDATE SET last_no = document_sequences.last_no + 1"
        " RETURNING last_no"), {"fy": fiscal_year_id, "t": doc_type}).scalar_one()
    return f"{PREFIXES.get(doc_type, doc_type[:3].upper())}-{year}-{n:05d}"
