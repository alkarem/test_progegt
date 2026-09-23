import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, Field, Modal, Money, PageHeader, Table } from "@/components/ui";
import { fmtDateTime } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { MoneyInput } from "@/features/documents/common";

export function Grants() {
  const { can } = useAuth();
  const { year } = useYear();
  const qc = useQueryClient();
  const grants = useQuery({ queryKey: ["grants"], queryFn: () => api<any[]>("/override-grants") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<any>("/users", { params: { page_size: 200 } }), enabled: can("users.view") });
  const lines = useQuery({ queryKey: ["position", year!.id], queryFn: () => api<any[]>("/budget-position", { params: { fiscal_year_id: year!.id } }) });
  const [open, setOpen] = useState(false);
  const now = new Date();
  const [f, setF] = useState({ user_id: "", budget_line_id: "", max_amount: "", valid_from: now.toISOString().slice(0, 16), valid_to: new Date(now.getTime() + 7 * 864e5).toISOString().slice(0, 16), reason: "" });
  const [err, setErr] = useState<unknown>(null);
  const userName = (id: string) => users.data?.items.find((u: any) => u.id === id)?.full_name ?? id.slice(0, 8);
  const lineName = (id: string | null) => !id ? "كل البنود" : (lines.data ?? []).find((r) => r.budget_line_id === id)?.item_code ?? id.slice(0, 8);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api("/override-grants", { body: { ...f, budget_line_id: f.budget_line_id || null, max_amount: normalizeMoneyInput(f.max_amount),
        valid_from: new Date(f.valid_from).toISOString(), valid_to: new Date(f.valid_to).toISOString() } });
      qc.invalidateQueries({ queryKey: ["grants"] }); setOpen(false); setErr(null);
    } catch (x) { setErr(x); }
  };
  const revoke = async (id: string) => { try { await api(`/override-grants/${id}/revoke`, { method: "POST" }); qc.invalidateQueries({ queryKey: ["grants"] }); } catch (x) { setErr(x); } };
  const approvers = (users.data?.items ?? []).filter((u: any) => u.is_active && u.roles.includes("APPROVER"));
  return (
    <div>
      <PageHeader title="منح الاستثناء من الرصيد" subtitle="منحة محددة المبلغ والمدة تتيح للمعتمد النهائي اعتماد عملية تتجاوز المتاح. كل استخدام يُسجَّل في التدقيق (D-10)."
        actions={can("budget.override_grant") && <button className="btn-primary" onClick={() => setOpen(true)}>+ منحة</button>} />
      <ErrorBox error={err} />
      <div className="card"><Table rows={grants.data} empty="لا توجد منح." cols={[
        { key: "user_id", title: "الممنوح له", render: (r: any) => userName(r.user_id) },
        { key: "line", title: "البند", render: (r: any) => lineName(r.budget_line_id) },
        { key: "max_amount", title: "الحد", num: true, render: (r: any) => <Money v={r.max_amount} /> },
        { key: "used_amount", title: "المستخدم", num: true, render: (r: any) => <Money v={r.used_amount} /> },
        { key: "valid", title: "الصلاحية", render: (r: any) => <span className="num text-xs">{fmtDateTime(r.valid_from)} — {fmtDateTime(r.valid_to)}</span> },
        { key: "reason", title: "السبب" },
        { key: "st", title: "", render: (r: any) => r.revoked_at ? <Badge>ملغاة</Badge> : new Date(r.valid_to) < now ? <Badge>منتهية</Badge>
          : can("budget.override_grant") ? <button className="btn-ghost px-2 text-xs" onClick={() => revoke(r.id)}>إلغاء</button> : <Badge tone="ok">سارية</Badge> },
      ]} /></div>
      <Modal open={open} onClose={() => setOpen(false)} title="منحة استثناء جديدة">
        <form onSubmit={submit} className="grid gap-3">
          <Field label="المعتمد النهائي"><select className="input" value={f.user_id} onChange={(e) => setF({ ...f, user_id: e.target.value })} required><option value="">—</option>{approvers.map((u: any) => <option key={u.id} value={u.id}>{u.full_name}</option>)}</select></Field>
          <Field label="البند"><select className="input" value={f.budget_line_id} onChange={(e) => setF({ ...f, budget_line_id: e.target.value })}><option value="">كل البنود</option>{(lines.data ?? []).filter((r) => r.budget_line_id).map((r) => <option key={r.budget_line_id} value={r.budget_line_id}>{r.item_code} {r.item_name}</option>)}</select></Field>
          <Field label="الحد الأقصى للتجاوز"><MoneyInput value={f.max_amount} onChange={(v) => setF({ ...f, max_amount: v })} required /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="من"><input type="datetime-local" className="input" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></Field>
            <Field label="إلى"><input type="datetime-local" className="input" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} /></Field>
          </div>
          <Field label="السبب / القرار"><input className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} required minLength={5} /></Field>
          <ErrorBox error={err} /><button className="btn-primary">منح</button>
        </form>
      </Modal>
    </div>
  );
}
