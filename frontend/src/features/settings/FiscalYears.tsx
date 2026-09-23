import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear, type FiscalYear } from "@/app/year";
import { Badge, ErrorBox, Field, Modal, Money, PageHeader, Spinner, Table, useToast } from "@/components/ui";
import { fmtDate, fmtDateTime } from "@/lib/labels";
import { useItems } from "@/lib/hooks";

const BASIS: Record<string, string> = { APPROPRIATION: "على الاعتماد", AUTHORIZATION: "على التفويض", TWO_LEVEL: "مستويان (اعتماد ثم تفويض)" };
const FY_STATUS: Record<string, { l: string; t: string }> = { DRAFT: { l: "مسودة", t: "mute" }, OPEN: { l: "مفتوحة", t: "ok" }, CLOSING: { l: "قيد الإقفال", t: "warn" }, CLOSED: { l: "مقفلة", t: "bad" } };

function YearForm({ fy, onClose }: { fy: FiscalYear | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { reload } = useYear();
  const { data: items = [] } = useItems();
  const [f, setF] = useState({ year: fy?.year ?? new Date().getFullYear() + 1, control_basis: fy?.control_basis ?? "TWO_LEVEL", count_reservations: fy?.count_reservations ?? true, carry_forward_item_id: fy?.carry_forward_item_id ?? "" });
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const body = { control_basis: f.control_basis, count_reservations: f.count_reservations, carry_forward_item_id: f.carry_forward_item_id || null };
      if (fy) await api(`/fiscal-years/${fy.id}`, { method: "PATCH", body });
      else await api("/fiscal-years", { body: { year: f.year, ...body } });
      qc.invalidateQueries({ queryKey: ["fiscal-years"] }); reload(); onClose();
    } catch (x) { setErr(x); }
  };
  return (
    <Modal open onClose={onClose} title={fy ? `إعدادات سنة ${fy.year}` : "سنة مالية جديدة"}>
      <form onSubmit={submit} className="grid gap-3">
        {!fy && <Field label="السنة"><input type="number" className="input num" value={f.year} onChange={(e) => setF({ ...f, year: Number(e.target.value) })} min={2000} max={2100} /></Field>}
        <Field label="أساس الرقابة" hint="يحدد ما يُقارن به المصروف لحساب المتاح (D-01)"><select className="input" value={f.control_basis} onChange={(e) => setF({ ...f, control_basis: e.target.value })}>{Object.entries(BASIS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.count_reservations} onChange={(e) => setF({ ...f, count_reservations: e.target.checked })} />تُخصم الحجوزات المبدئية (طلبات الشراء) من المتاح (D-12)</label>
        <Field label="بند ترحيل الارتباطات للسنة التالية (D-08)"><select className="input" value={f.carry_forward_item_id} onChange={(e) => setF({ ...f, carry_forward_item_id: e.target.value })}><option value="">— نفس البند —</option>{items.map((i) => <option key={i.id} value={i.id}>{i.code} {i.name}</option>)}</select></Field>
        <ErrorBox error={err} /><button className="btn-primary">حفظ</button>
      </form>
    </Modal>
  );
}

function CloseYear({ fy, onClose }: { fy: FiscalYear; onClose: () => void }) {
  const { reload } = useYear();
  const toast = useToast();
  const pv = useQuery({ queryKey: ["closing-preview", fy.id], queryFn: () => api<any>(`/fiscal-years/${fy.id}/closing-preview`) });
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const close = async () => {
    try { const r = await api<any>(`/fiscal-years/${fy.id}/close`, { body: { reason } }); toast(`أُقفلت سنة ${r.fiscal_year}`); reload(); onClose(); } catch (x) { setErr(x); }
  };
  const d = pv.data;
  const unfunded = (d?.carry_forward_funding ?? []).filter((x: any) => Number(x.shortfall) > 0);
  return (
    <Modal open onClose={onClose} title={`الإقفال السنوي — ${fy.year}`} wide>
      {!d ? <Spinner /> : <div className="space-y-3 text-sm">
        <p>الإقفال معاملة واحدة لا يُتراجع عنها: تُحرَّر الحجوزات المبدئية، وتُرحَّل الارتباطات القائمة إلى السنة التالية، وتُمنع أي حركة على السنة بعد ذلك.</p>
        <div className="grid gap-3 md:grid-cols-3">
          <div className="card p-3"><div className="text-xs text-ink-mute">مستندات معلقة</div><div className="num text-xl font-bold">{d.pending_documents.reduce((a: number, x: any) => a + x.count, 0)}</div></div>
          <div className="card p-3"><div className="text-xs text-ink-mute">حجوزات ستُحرَّر</div><div className="num text-xl font-bold">{d.reservations_to_release.length}</div></div>
          <div className="card p-3"><div className="text-xs text-ink-mute">ارتباطات ستُرحَّل إلى {d.next_year ?? "—"}</div><div className="num text-xl font-bold">{d.commitments_to_carry.length}</div></div>
        </div>
        {d.pending_documents.length > 0 && <div className="text-bad">لا يُقفل عام فيه مستندات غير منتهية: {d.pending_documents.map((x: any) => `${x.table} (${x.count})`).join("، ")}</div>}
        {!d.next_year && d.commitments_to_carry.length > 0 && <div className="text-bad">لا توجد سنة تالية لترحيل الارتباطات إليها؛ أنشئها وافتحها أولًا.</div>}
        {unfunded.length > 0 && <div className="text-bad">الارتباطات المرحّلة تحتاج اعتمادًا في السنة التالية: {unfunded.map((x: any) => <span key={x.item_code} className="ms-2">{x.item_code} (<Money v={x.shortfall} />)</span>)}</div>}
        <Field label="سبب الإقفال / رقم القرار"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <Field label={`للتأكيد اكتب ${fy.year}`}><input className="input num" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
        <ErrorBox error={err} />
        <button className="btn-danger" disabled={confirm !== String(fy.year) || reason.trim().length < 3} onClick={close}>إقفال السنة نهائيًا</button>
      </div>}
    </Modal>
  );
}

function Periods({ fy }: { fy: FiscalYear }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const { data } = useQuery({ queryKey: ["periods", fy.id], queryFn: () => api<any[]>(`/fiscal-years/${fy.id}/periods`) });
  const act = async (id: string, what: "close" | "reopen") => {
    try {
      const reason = what === "reopen" ? window.prompt("سبب إعادة الفتح") : null;
      if (what === "reopen" && !reason) return;
      await api(`/periods/${id}/${what}`, { body: what === "reopen" ? { reason } : {} });
      qc.invalidateQueries({ queryKey: ["periods", fy.id] }); setErr(null);
    } catch (x) { setErr(x); }
  };
  return (
    <div><ErrorBox error={err} />
      <Table rows={data} cols={[
        { key: "period_no", title: "الشهر" },
        { key: "range", title: "الفترة", render: (r: any) => <span className="num">{fmtDate(r.start_date)} — {fmtDate(r.end_date)}</span> },
        { key: "status", title: "الحالة", render: (r: any) => r.status === "OPEN" ? <Badge tone="ok">مفتوحة</Badge> : <Badge tone="bad">مقفلة {fmtDateTime(r.closed_at)}</Badge> },
        { key: "a", title: "", render: (r: any) => can("fiscal.close_period") && fy.status === "OPEN" && (r.status === "OPEN"
          ? <button className="btn-ghost px-2 text-xs" onClick={() => act(r.id, "close")}>إقفال</button>
          : <button className="btn-ghost px-2 text-xs" onClick={() => act(r.id, "reopen")}>إعادة فتح</button>) },
      ]} />
    </div>
  );
}

export function FiscalYears() {
  const { can } = useAuth();
  const { years, reload } = useYear();
  const toast = useToast();
  const [form, setForm] = useState<FiscalYear | null | "new">(null);
  const [closing, setClosing] = useState<FiscalYear | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const openYear = async (fy: FiscalYear) => { try { await api(`/fiscal-years/${fy.id}/open`, { method: "POST" }); toast(`فُتحت سنة ${fy.year}`); reload(); } catch (x) { setErr(x); } };
  return (
    <div>
      <PageHeader title="السنوات المالية" actions={can("fiscal.manage") && <button className="btn-primary" onClick={() => setForm("new")}>+ سنة مالية</button>} />
      <ErrorBox error={err} />
      <div className="space-y-3">
        {years.map((fy) => (
          <section key={fy.id} className="card">
            <div className="flex flex-wrap items-center gap-3 p-4">
              <div className="num text-2xl font-bold">{fy.year}</div>
              <Badge tone={FY_STATUS[fy.status]?.t}>{FY_STATUS[fy.status]?.l ?? fy.status}</Badge>
              <span className="text-sm text-ink-soft">{BASIS[fy.control_basis]} · {fy.count_reservations ? "الحجوزات تُخصم" : "الحجوزات لا تُخصم"}</span>
              <div className="ms-auto flex gap-2">
                <button className="btn-ghost" onClick={() => setOpen(open === fy.id ? null : fy.id)}>الفترات</button>
                {can("fiscal.manage") && fy.status !== "CLOSED" && <button className="btn-outline" onClick={() => setForm(fy)}>الإعدادات</button>}
                {can("fiscal.manage") && fy.status === "DRAFT" && <button className="btn-ok" onClick={() => openYear(fy)}>فتح السنة</button>}
                {can("fiscal.close_year") && ["OPEN", "CLOSING"].includes(fy.status) && <button className="btn-danger" onClick={() => setClosing(fy)}>الإقفال السنوي</button>}
                {can("imports.prepare") && fy.status === "OPEN" && <Link className="btn-ghost" to="/settings/imports">استيراد Excel</Link>}
              </div>
            </div>
            {open === fy.id && <Periods fy={fy} />}
          </section>
        ))}
      </div>
      {form && <YearForm fy={form === "new" ? null : form} onClose={() => setForm(null)} />}
      {closing && <CloseYear fy={closing} onClose={() => setClosing(null)} />}
    </div>
  );
}
