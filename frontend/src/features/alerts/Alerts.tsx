import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, Field, Modal, PageHeader, Pager, Table, useToast } from "@/components/ui";
import { SEVERITY, fmtDateTime } from "@/lib/labels";
import { docLink } from "@/features/documents/common";

const ALERT_STATUS: Record<string, string> = { OPEN: "مفتوح", ACKNOWLEDGED: "تم الاطلاع", RESOLVED: "محلول" };

export function Alerts() {
  const { can } = useAuth();
  const { year } = useYear();
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("OPEN");
  const [severity, setSeverity] = useState("");
  const [page, setPage] = useState(1);
  const [resolving, setResolving] = useState<any>(null);
  const [resolution, setResolution] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const params = { status: status || undefined, severity: severity || undefined, fiscal_year_id: year!.id, page, page_size: 50 };
  const { data } = useQuery({ queryKey: ["alerts", params], queryFn: () => api<any>("/alerts", { params }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const ack = useMutation({ mutationFn: (id: string) => api(`/alerts/${id}/ack`, { method: "POST" }), onSuccess: refresh, onError: setErr });
  const evaluate = useMutation({ mutationFn: () => api<any>("/alerts/evaluate", { method: "POST" }), onSuccess: (r) => { toast(`اكتمل التقييم: ${Object.values(r.raised ?? {}).reduce((a: number, b: any) => a + Number(b), 0)} تنبيه جديد`); refresh(); }, onError: setErr });
  const resolve = async () => {
    try { await api(`/alerts/${resolving.id}/resolve`, { body: { resolution } }); setResolving(null); setResolution(""); refresh(); } catch (e) { setErr(e); }
  };
  return (
    <div>
      <PageHeader title="التنبيهات" subtitle="تنبيهات قواعد الرقابة: الاقتراب من الاستنفاد، التجاوز، الارتباطات القديمة، التكرار المحتمل…"
        actions={can("alerts.manage") && <button className="btn-outline" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>تقييم الآن</button>} />
      <ErrorBox error={err} />
      <div className="card">
        <div className="flex gap-2 border-b border-line p-3">
          <select className="input w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="الحالة"><option value="">كل الحالات</option>{Object.entries(ALERT_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select className="input w-auto" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }} aria-label="الخطورة"><option value="">كل المستويات</option>{Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
        </div>
        <Table rows={data?.items} empty="لا توجد تنبيهات." cols={[
          { key: "severity", title: "الخطورة", render: (r: any) => <Badge tone={SEVERITY[r.severity]?.tone}>{SEVERITY[r.severity]?.label}</Badge> },
          { key: "message", title: "التنبيه" },
          { key: "link", title: "", render: (r: any) => r.source_id ? <Link className="text-brand underline" to={docLink(r.source_type, r.source_id)}>المستند</Link>
            : r.budget_line_id ? <Link className="text-brand underline" to={`/budget/lines/${r.budget_line_id}`}>البند</Link> : null },
          { key: "created_at", title: "التاريخ", render: (r: any) => <span className="num">{fmtDateTime(r.created_at)}</span> },
          { key: "status", title: "الحالة", render: (r: any) => ALERT_STATUS[r.status] + (r.resolution ? ` — ${r.resolution}` : "") },
          { key: "actions", title: "", render: (r: any) => r.status !== "RESOLVED" && <span className="flex gap-1">
            {r.status === "OPEN" && <button className="btn-ghost px-2 text-xs" onClick={() => ack.mutate(r.id)}>اطلعت</button>}
            {can("alerts.manage") && <button className="btn-ghost px-2 text-xs" onClick={() => setResolving(r)}>حل</button>}</span> },
        ]} />
        {data && <Pager page={page} total={data.total} pageSize={50} onPage={setPage} />}
      </div>
      <Modal open={!!resolving} onClose={() => setResolving(null)} title="حل التنبيه">
        <p className="mb-2 text-sm">{resolving?.message}</p>
        <Field label="الإجراء المتخذ / سبب الإغلاق"><textarea className="input" rows={3} value={resolution} onChange={(e) => setResolution(e.target.value)} /></Field>
        <button className="btn-primary mt-3" disabled={resolution.trim().length < 3} onClick={resolve}>حفظ</button>
      </Modal>
    </div>
  );
}

export function Notifications() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [page, setPage] = useState(1);
  const { data } = useQuery({ queryKey: ["notifications", page], queryFn: () => api<any>("/notifications", { params: { page, page_size: 50 } }) });
  const markAll = async () => { await api("/notifications/read", { body: {} }); qc.invalidateQueries({ queryKey: ["notifications"] }); qc.invalidateQueries({ queryKey: ["unread"] }); };
  const open = async (n: any) => {
    if (!n.read_at) { await api("/notifications/read", { body: { ids: [n.id] } }); qc.invalidateQueries({ queryKey: ["unread"] }); qc.invalidateQueries({ queryKey: ["notifications"] }); }
    if (n.link) nav(n.link);
  };
  return (
    <div>
      <PageHeader title="الإشعارات" actions={<button className="btn-outline" onClick={markAll}>تعليم الكل كمقروء</button>} />
      <div className="card divide-y divide-line">
        {data?.items.length === 0 && <div className="p-6 text-center text-ink-mute">لا توجد إشعارات.</div>}
        {data?.items.map((n: any) => (
          <button key={n.id} onClick={() => open(n)} className={`block w-full p-3 text-start hover:bg-surface ${n.read_at ? "" : "bg-brand-50"}`}>
            <div className="flex justify-between"><span className="font-bold">{n.title}</span><span className="num text-xs text-ink-mute">{fmtDateTime(n.created_at)}</span></div>
            {n.body && <div className="text-sm text-ink-soft">{n.body}</div>}
          </button>
        ))}
        {data && <Pager page={page} total={data.total} pageSize={50} onPage={setPage} />}
      </div>
    </div>
  );
}
