from app.modules.ledger import engine
from app.modules.transfers import service
from app.modules.workflow.registry import DocHandler, register

register(DocHandler(
    source_type=service.SOURCE_TYPE,
    perm_prefix="transfers",
    label="مناقلة",
    get=service.get,
    amount=service.total,
    line_ids=lambda s, t: [x for ln in service.lines_of(s, t.id) for x in (ln.from_line_id, ln.to_line_id)],
    simulate=lambda s, t: engine.simulate(s, service.build_posting(s, t, t.created_by, None)),
    post=lambda s, t, actor, override: service.post(s, t, actor, override),
    definition_code=lambda t: "transfer",
    doc_no=lambda t: t.transfer_no,
))
