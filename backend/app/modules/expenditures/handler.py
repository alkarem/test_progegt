from app.modules.expenditures import service
from app.modules.ledger import engine
from app.modules.workflow.registry import DocHandler, register

register(DocHandler(
    source_type=service.SOURCE_TYPE,
    perm_prefix="expenditures",
    label="مصروف",
    get=service.get,
    amount=lambda s, e: e.amount,
    line_ids=lambda s, e: [e.budget_line_id],
    simulate=lambda s, e: engine.simulate(s, service.build_posting(s, e, e.created_by, None)),
    post=lambda s, e, actor, override: service.post(s, e, actor, override),
    definition_code=lambda e: "expenditure",
    doc_no=lambda e: e.document_no,
))
