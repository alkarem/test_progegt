from app.modules.authorizations import service
from app.modules.ledger import engine
from app.modules.workflow.registry import DocHandler, register

register(DocHandler(
    source_type=service.SOURCE_TYPE,
    perm_prefix="authorizations",
    label="تفويض",
    get=service.get,
    amount=lambda s, a: a.amount,
    line_ids=lambda s, a: [x.budget_line_id for x in service.allocations_of(s, a.id)],
    simulate=lambda s, a: engine.simulate(s, service.build_posting(s, a, a.created_by, None)),
    post=lambda s, a, actor, override: service.post(s, a, actor, override),
    definition_code=lambda a: "authorization",
    doc_no=lambda a: a.auth_no,
))
