import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, Field, Money, PageHeader, Spinner } from "@/components/ui";
import { PAYMENT_METHOD, fmtDate } from "@/lib/labels";
import { fmt, normalizeMoneyInput } from "@/lib/money";
import { useEntities, useItems } from "@/lib/hooks";
import { Attachments, DocLayout, Facts, ItemSelect, MoneyInput, StatusBadge, SupplierPicker, WorkflowPanel } from "./common";
import { DocList } from "./DocList";

export function ExpenditureList() {
  const { can } = useAuth();
  return <DocList title="المصروفات" endpoint="/expenditures" basePath="/expenditures" newLabel="مصروف جديد" canCreate={can("expenditures.create")} cols={[
    { key: "expenditure_date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.expenditure_date)}</span> },
    { key: "document_no", title: "رقم المستند" },
    { key: "item_code", title: "البند" },
    { key: "supplier_name", title: "المستفيد" },
    { key: "payment_method", title: "طريقة الدفع", render: (r: any) => PAYMENT_METHOD[r.payment_method] },
    { key: "amount", title: "المبلغ", num: true, render: (r: any) => <Money v={r.amount} /> },
    { key: "status", title: "الحالة", render: (r: any) => <StatusBadge s={r.status} /> },
  ]} />;
}

export function ExpenditureForm() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: ents = [] } = useEntities();
  const [f, setF] = useState<any>({ expenditure_date: new Date().toISOString().slice(0, 10), payment_method: "CHEQUE", amount: "", item_id: "", description: "" });
  const [commitments, setCommitments] = useState<any[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const entity = f.entity_id ?? ents[0]?.id;
  const { data: items = [] } = useItems();
  const loadCommitments = async (itemId: string) => {
    set("item_id", itemId); set("commitment_id", null);
    const code = items.find((i) => i.id === itemId)?.code;
    if (!code) return setCommitments([]);
    const r = await api<any>("/commitments", { params: { fiscal_year_id: year!.id, status: "POSTED", page_size: 200 } });
    setCommitments(r.items.filter((c: any) => c.item_code === code && c.commitment_type !== "PURCHASE_REQUEST"
      && ["APPROVED", "PARTIALLY_PAID"].includes(c.commitment_status)));
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const body = { ...f, fiscal_year_id: year!.id, entity_id: entity, amount: normalizeMoneyInput(f.amount) };
      Object.keys(body).forEach((k) => body[k] === "" && delete body[k]);
      const r = await api("/expenditures", { body });
      qc.invalidateQueries({ queryKey: ["/expenditures"] });
      nav(`/expenditures/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <div>
      <PageHeader title="مصروف جديد" subtitle="يُحفظ كمسودة ثم يُقدَّم لدورة الموافقة. لا يُرحَّل قبل فحص الرصيد." />
      <form onSubmit={submit} className="card grid gap-4 p-4 md:grid-cols-3">
        <Field label="التاريخ"><input type="date" className="input" value={f.expenditure_date} onChange={(e) => set("expenditure_date", e.target.value)} required /></Field>
        <Field label="رقم المستند" hint="اتركه فارغًا للترقيم التلقائي"><input className="input" value={f.document_no ?? ""} onChange={(e) => set("document_no", e.target.value)} /></Field>
        <Field label="الجهة"><select className="input" value={entity ?? ""} onChange={(e) => set("entity_id", e.target.value)}>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="البند"><ItemSelect value={f.item_id} onChange={loadCommitments} required /></Field>
        <Field label="المبلغ"><MoneyInput value={f.amount} onChange={(v) => set("amount", v)} required /></Field>
        <Field label="طريقة الدفع"><select className="input" value={f.payment_method} onChange={(e) => set("payment_method", e.target.value)}>{Object.entries(PAYMENT_METHOD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="المورد / المستفيد" className="md:col-span-2"><SupplierPicker value={f.supplier_id ?? null} onChange={(id) => set("supplier_id", id)} /></Field>
        <Field label="الارتباط المرتبط (اختياري)" hint="الصرف على ارتباط يسيّله ولا يُخصم مرتين">
          <select className="input" value={f.commitment_id ?? ""} onChange={(e) => set("commitment_id", e.target.value || null)}>
            <option value="">— بدون —</option>
            {commitments.map((c) => <option key={c.id} value={c.id}>{c.commitment_no} ({c.item_code}) — القائم {fmt(c.outstanding)}</option>)}
          </select>
        </Field>
        <Field label="رقم أمر الصرف"><input className="input" value={f.payment_order_no ?? ""} onChange={(e) => set("payment_order_no", e.target.value)} /></Field>
        <Field label="رقم الشيك"><input className="input" value={f.cheque_no ?? ""} onChange={(e) => set("cheque_no", e.target.value)} required={f.payment_method === "CHEQUE"} /></Field>
        <Field label="نوع المصروف"><input className="input" value={f.expense_type ?? ""} onChange={(e) => set("expense_type", e.target.value)} /></Field>
        <Field label="البيان" className="md:col-span-3"><input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} required /></Field>
        <Field label="ملاحظات" className="md:col-span-3"><textarea className="input" rows={2} value={f.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></Field>
        <div className="md:col-span-3"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ كمسودة</button></div>
      </form>
    </div>
  );
}

export function ExpenditureView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["expenditure", id], queryFn: () => api(`/expenditures/${id}`) });
  if (!q.data) return <Spinner />;
  const e = q.data;
  const refresh = () => { qc.invalidateQueries({ queryKey: ["expenditure", id] }); qc.invalidateQueries({ queryKey: ["unread"] }); };
  return (
    <div>
      <PageHeader title={`مصروف ${e.document_no}`} subtitle={<StatusBadge s={e.status} />} />
      <DocLayout
        main={<section className="card p-4">
          <Facts items={[["التاريخ", <span className="num">{fmtDate(e.expenditure_date)}{e.date_is_estimated && " (تقديري)"}</span>], ["البند", `${e.item_code} ${e.item_name}`],
            ["المبلغ", <Money v={e.amount} />], ["المستفيد", e.supplier_name], ["طريقة الدفع", PAYMENT_METHOD[e.payment_method]],
            ["أمر الصرف", e.payment_order_no], ["الشيك", e.cheque_no], ["نوع المصروف", e.expense_type], ["البيان", e.description]]} />
          {e.notes && <p className="mt-3 text-sm text-ink-soft">ملاحظات: {e.notes}</p>}
          {e.possible_duplicates?.length > 0 && <div className="mt-3"><Badge tone="warn">مشابه لمستندات: {e.possible_duplicates.join("، ")}</Badge></div>}
        </section>}
        side={<><WorkflowPanel type="expenditure" doc={e} onChange={refresh} /><Attachments type="expenditure" id={e.id} locked={e.status === "POSTED"} /></>}
      />
    </div>
  );
}
