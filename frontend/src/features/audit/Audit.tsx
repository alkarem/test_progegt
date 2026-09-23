import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { Badge, ErrorBox, Field, Modal, PageHeader } from "@/components/ui";
import { fmtDateTime } from "@/lib/labels";

type Row = { id: number; occurred_at: string; user_name: string | null; ip: string | null; user_agent: string | null; action: string; table_name: string | null; record_id: string | null; changed_fields: string[] | null; old_values: Record<string, any> | null; new_values: Record<string, any> | null; reason: string | null };
const ACTION: Record<string, string> = { INSERT: "إضافة", UPDATE: "تعديل", DELETE: "حذف", LOGIN: "دخول", LOGIN_FAILED: "دخول فاشل", LOGOUT: "خروج", EXPORT: "تصدير", RESTORE: "استعادة" };

function Diff({ r }: { r: Row }) {
  const keys = r.changed_fields ?? Object.keys({ ...(r.old_values ?? {}), ...(r.new_values ?? {}) });
  return (
    <table className="w-full text-sm" dir="ltr">
      <thead><tr><th className="th">field</th><th className="th">old</th><th className="th">new</th></tr></thead>
      <tbody>{keys.map((k) => <tr key={k}><td className="td font-mono">{k}</td>
        <td className="td font-mono text-bad">{JSON.stringify(r.old_values?.[k] ?? null)}</td>
        <td className="td font-mono text-ok">{JSON.stringify(r.new_values?.[k] ?? null)}</td></tr>)}</tbody>
    </table>
  );
}

export function Audit() {
  const { can } = useAuth();
  const [f, setF] = useState({ table_name: "", record_id: "", action: "", date_from: "", date_to: "" });
  const [applied, setApplied] = useState(f);
  const [open, setOpen] = useState<Row | null>(null);
  const clean = Object.fromEntries(Object.entries(applied).filter(([, v]) => v));
  const q = useInfiniteQuery({
    queryKey: ["audit", clean], initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => api<{ items: Row[]; next_before_id: number | null }>("/audit-log", { params: { ...clean, before_id: pageParam, limit: 100 } }),
    getNextPageParam: (p) => p.next_before_id ?? undefined,
  });
  const verify = useMutation({ mutationFn: () => api<any>("/audit-log/verify-chain", { method: "POST" }) });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div>
      <PageHeader title="سجل التدقيق" subtitle="سجل غير قابل للتعديل أو الحذف، مسلسل بسلسلة Hash تكشف أي عبث."
        actions={can("audit.verify") && <button className="btn-outline" onClick={() => verify.mutate()} disabled={verify.isPending}>التحقق من سلامة السلسلة</button>} />
      {verify.data && <div className={`card mb-4 p-3 text-sm ${verify.data.ok ? "border-ok" : "border-bad"}`}>
        {verify.data.ok ? <Badge tone="ok">السلسلة سليمة</Badge> : <Badge tone="bad">انكسار عند السجل {verify.data.broken_at_id}</Badge>}
        <span className="ms-2">فُحص <span className="num">{verify.data.checked}</span> سجلًا · آخر Hash <span className="num font-mono text-xs" dir="ltr">{verify.data.last_hash.slice(0, 16)}…</span></span>
      </div>}
      <ErrorBox error={verify.error ?? q.error} />
      <form className="card mb-4 grid gap-3 p-3 md:grid-cols-6" onSubmit={(e) => { e.preventDefault(); setApplied(f); }}>
        <Field label="الجدول"><input className="input" dir="ltr" value={f.table_name} onChange={(e) => setF({ ...f, table_name: e.target.value })} /></Field>
        <Field label="معرّف السجل"><input className="input" dir="ltr" value={f.record_id} onChange={(e) => setF({ ...f, record_id: e.target.value })} /></Field>
        <Field label="الإجراء"><select className="input" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}><option value="">الكل</option>{Object.entries(ACTION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="من"><input type="date" className="input" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label="إلى"><input type="date" className="input" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
        <div className="flex items-end"><button className="btn-primary">تطبيق</button></div>
      </form>
      <div className="card overflow-auto">
        <table className="w-full text-sm">
          <thead><tr>{["#", "الوقت", "المستخدم", "الإجراء", "الجدول", "السجل", "الحقول", "السبب", "IP"].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="cursor-pointer hover:bg-surface" onClick={() => setOpen(r)}>
              <td className="td num">{r.id}</td><td className="td num">{fmtDateTime(r.occurred_at)}</td><td className="td">{r.user_name ?? "—"}</td>
              <td className="td">{ACTION[r.action] ?? r.action}</td><td className="td font-mono text-xs" dir="ltr">{r.table_name}</td>
              <td className="td font-mono text-xs" dir="ltr">{r.record_id?.slice(0, 8)}</td>
              <td className="td text-xs" dir="ltr">{r.changed_fields?.slice(0, 4).join(", ")}{(r.changed_fields?.length ?? 0) > 4 && "…"}</td>
              <td className="td">{r.reason}</td><td className="td num text-xs">{r.ip}</td>
            </tr>))}</tbody>
        </table>
        {q.hasNextPage && <div className="p-3 text-center"><button className="btn-ghost" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>المزيد</button></div>}
      </div>
      <Modal open={!!open} onClose={() => setOpen(null)} title={`سجل تدقيق #${open?.id}`} wide>
        {open && <><div className="mb-2 text-sm">{fmtDateTime(open.occurred_at)} — {open.user_name} — {ACTION[open.action] ?? open.action} <span dir="ltr" className="font-mono">{open.table_name}/{open.record_id}</span></div>
          {open.user_agent && <div className="mb-2 text-xs text-ink-mute" dir="ltr">{open.user_agent}</div>}
          <Diff r={open} /></>}
      </Modal>
    </div>
  );
}
