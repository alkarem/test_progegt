from app.modules.adjustments import service
from app.modules.ledger import engine
from app.modules.workflow.registry import DocHandler, register

register(DocHandler(
    source_type=service.SOURCE_TYPE,
    perm_prefix="adjustments",
    label="تسوية / قيد عكسي",
    get=service.get,
    amount=service.amount,
    line_ids=service.line_ids,
    simulate=lambda s, a: engine.simulate(s, service.build_posting(s, a, a.created_by, None)),
    post=lambda s, a, actor, override: service.post(s, a, actor, override),
    definition_code=lambda a: "adjustment",
    doc_no=lambda a: a.adjustment_no,
))
