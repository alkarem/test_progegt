"""تسجيل مستندات الميزانية في محرك الموافقات."""
from app.modules.budget import service
from app.modules.ledger import engine
from app.modules.workflow.registry import DocHandler, register

HANDLER = DocHandler(
    source_type=service.SOURCE_TYPE,
    perm_prefix="budget_documents",
    label="مستند ميزانية",
    get=service.get,
    amount=service.total,
    line_ids=lambda s, d: [ln.budget_line_id for ln in service.lines_of(s, d.id)],
    simulate=lambda s, d: engine.simulate(s, service.build_posting(s, d, d.created_by, None)),
    post=lambda s, d, actor, override: service.post(s, d, actor, approved_by=actor, override_grant_id=override),
    definition_code=lambda d: "budget_document",
    doc_no=lambda d: d.doc_no,
)
register(HANDLER)
