from app.modules.commitments import service
from app.modules.ledger import engine
from app.modules.workflow.registry import DocHandler, register


def _cancel(session, c):
    service.on_workflow_change(session, c)


register(DocHandler(
    source_type=service.SOURCE_TYPE,
    perm_prefix="commitments",
    label="ارتباط",
    get=service.get,
    amount=lambda s, c: c.amount,
    line_ids=lambda s, c: [c.budget_line_id],
    simulate=lambda s, c: engine.simulate(s, service.build_posting(s, c, c.created_by, None)),
    post=lambda s, c, actor, override: service.post(s, c, actor, override),
    definition_code=lambda c: "purchase_request" if c.commitment_type == service.PR else "commitment",
    doc_no=lambda c: c.commitment_no,
    on_cancel=_cancel,
    on_status=service.on_workflow_change,
))
