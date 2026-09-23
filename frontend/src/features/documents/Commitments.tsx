import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, Field, Modal, Money, PageHeader, Spinner, Table } from "@/components/ui";
import { COMMITMENT_STATUS, COMMITMENT_TYPE, SOURCE_LABEL, TXN_TYPE, fmtDate } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { useEntities } from "@/lib/hooks";
import { Attachments, DocLayout, Facts, ItemSelect, MoneyInput, StatusBadge, SupplierPicker, WorkflowPanel, docLink } from "./common";
import { DocList } from "./DocList";

export function CommitmentList() {
  const { can } = useAuth();
  return <DocList title="الارتباطات وطلبات الشراء" endpoint="/commitments" basePath="/commitments" newLabel="ارتباط جديد" canCreate={can("commitments.create")} cols={[
    { key: "commitment_date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.commitment_date)}</span> },
    { key: "commitment_no", title: "الرقم" },
    { key: "commitment_type", title: "النوع", render: (r: any) => COMMITMENT_TYPE[r.commitment_type] },
    { key: "item_code", title: "البند" },
    { key: "supplier_name", title: "المورد" },
    { key: "amount", title: "القيمة", num: true, render: (r: any) => <Money v={r.amount} /> },
    { key: "outstanding", title: "القائم", num: true, render: (r: any) => <Money v={r.outstanding} /> },
    { key: "commitment_status", title: "حالة الارتباط", render: (r: any) => COMMITMENT_STATUS[r.commitment_status] },
    { key: "status", title: "حالة المستند", render: (r: any) => <StatusBadge s={r.status} /> },
  ]} />;
}

export function CommitmentForm() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: ents = [] } = useEntities();
  const [f, setF] = useState<any>({ commitment_type: "PURCHASE_ORDER", commitment_date: new Date().toISOString().slice(0, 10), item_id: "", amount: "", description: "", reference: "", expected_completion: "" });
  const [err, setErr] = useState<unknown>(null);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const entity = f.entity_id ?? ents[0]?.id;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api("/commitments", { body: {
        fiscal_year_id: year!.id, entity_id: entity, commitment_type: f.commitment_type, commitment_date: f.commitment_date,
        item_id: f.item_id, amount: normalizeMoneyInput(f.amount), description: f.description, supplier_id: f.supplier_id ?? null,
        reference: f.reference || null, expected_completion: f.expected_completion || null,
      } });
      qc.invalidateQueries({ queryKey: ["/commitments"] });
      nav(`/commitments/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <div>
      <PageHeader title="ارتباط جديد" subtitle="طلب الشراء يُحجز مبدئيًا؛ أمر الشراء والعقد والالتزام تُرحَّل كارتباط يخفض المتاح حتى يُسيَّل بالصرف." />
      <form onSubmit={submit} className="card grid gap-4 p-4 md:grid-cols-3">
        <Field label="النوع"><select className="input" value={f.commitment_type} onChange={(e) => set("commitment_type", e.target.value)}>{Object.entries(COMMITMENT_TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="التاريخ"><input type="date" className="input" value={f.commitment_date} onChange={(e) => set("commitment_date", e.target.value)} required /></Field>
        <Field label="الجهة"><select className="input" value={entity ?? ""} onChange={(e) => set("entity_id", e.target.value)}>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="البند"><ItemSelect value={f.item_id} onChange={(v) => set("item_id", v)} required /></Field>
        <Field label="القيمة"><MoneyInput value={f.amount} onChange={(v) => set("amount", v)} required /></Field>
        <Field label="تاريخ الإنجاز المتوقع"><input type="date" className="input" value={f.expected_completion} onChange={(e) => set("expected_completion", e.target.value)} /></Field>
        <Field label="المورد" className="md:col-span-2"><SupplierPicker value={f.supplier_id ?? null} onChange={(id) => set("supplier_id", id)} /></Field>
        <Field label="المرجع"><input className="input" value={f.reference} onChange={(e) => set("reference", e.target.value)} /></Field>
        <Field label="البيان" className="md:col-span-3"><input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} required minLength={3} /></Field>
        <div className="md:col-span-3"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ كمسودة</button></div>
      </form>
    </div>
  );
}

function ConvertModal({ c, open, onClose }: { c: any; open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const [f, setF] = useState<any>({ commitment_type: "PURCHASE_ORDER", commitment_date: new Date().toISOString().slice(0, 10), amount: c.amount, description: c.description, supplier_id: c.supplier_id, reference: "" });
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api(`/commitments/${c.id}/convert`, { body: { ...f, amount: normalizeMoneyInput(f.amount), reference: f.reference || null } });
      onClose(); nav(`/commitments/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <Modal open={open} onClose={onClose} title="تحويل طلب الشراء إلى ارتباط">
      <form onSubmit={submit} className="grid gap-3">
        <p className="text-sm text-ink-soft">يُنشأ ارتباط جديد (مسودة). عند ترحيله يُحرَّر الحجز المبدئي ويُسجَّل الارتباط بدلًا منه.</p>
        <Field label="النوع"><select className="input" value={f.commitment_type} onChange={(e) => setF({ ...f, commitment_type: e.target.value })}>
          {Object.entries(COMMITMENT_TYPE).filter(([k]) => k !== "PURCHASE_REQUEST").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="التاريخ"><input type="date" className="input" value={f.commitment_date} onChange={(e) => setF({ ...f, commitment_date: e.target.value })} required /></Field>
        <Field label="القيمة"><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} required /></Field>
        <Field label="المورد"><SupplierPicker value={f.supplier_id} onChange={(id) => setF({ ...f, supplier_id: id })} /></Field>
        <Field label="البيان"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} required /></Field>
        <Field label="المرجع"><input className="input" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
        <ErrorBox error={err} />
        <button className="btn-primary">تحويل</button>
      </form>
    </Modal>
  );
}

export function CommitmentView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { can } = useAuth();
  const [conv, setConv] = useState(false);
  const q = useQuery({ queryKey: ["commitment", id], queryFn: () => api(`/commitments/${id}`) });
  const mv = useQuery({ queryKey: ["commitment-mv", id, q.data?.status], queryFn: () => api<any[]>(`/commitments/${id}/movements`), enabled: q.data?.status === "POSTED" });
  if (!q.data) return <Spinner />;
  const c = q.data;
  const isPR = c.commitment_type === "PURCHASE_REQUEST";
  return (
    <div>
      <PageHeader title={`${COMMITMENT_TYPE[c.commitment_type]} ${c.commitment_no}`}
        subtitle={<span className="flex gap-2"><StatusBadge s={c.status} /><Badge tone="brand">{COMMITMENT_STATUS[c.commitment_status]}</Badge></span>}
        actions={<>
          {isPR && c.status === "POSTED" && c.commitment_status === "APPROVED" && can("commitments.create") && <button className="btn-primary" onClick={() => setConv(true)}>تحويل إلى ارتباط</button>}
          {!isPR && c.status === "POSTED" && ["APPROVED", "PARTIALLY_PAID"].includes(c.commitment_status) && can("adjustments.create") &&
            <Link className="btn-outline" to={`/adjustments/new?commitment=${c.id}`}>إلغاء الرصيد القائم</Link>}
        </>} />
      <DocLayout
        main={<>
          <section className="card p-4">
            <Facts items={[["التاريخ", fmtDate(c.commitment_date)], ["البند", `${c.item_code} ${c.item_name}`], ["المورد", c.supplier_name], ["القيمة", <Money v={c.amount} strong />],
              ["المسدد", <Money v={c.paid} />], ["الملغى", <Money v={c.cancelled} />], ["القائم", <Money v={c.outstanding} strong />],
              ["الإنجاز المتوقع", fmtDate(c.expected_completion)], ["المرجع", c.reference], ["البيان", c.description],
              ["الأصل", c.parent_id ? <Link className="text-brand underline" to={`/commitments/${c.parent_id}`}>طلب الشراء الأصلي</Link> : null]]} />
          </section>
          {mv.data && <section className="card mt-4">
            <h2 className="border-b border-line p-3 font-semibold">حركات الارتباط</h2>
            <Table cols={[
              { key: "entry_no", title: "رقم القيد" },
              { key: "entry_date", title: "التاريخ", render: (r: any) => fmtDate(r.entry_date) },
              { key: "txn_type", title: "الحركة", render: (r: any) => TXN_TYPE[r.txn_type] ?? r.txn_type },
              { key: "src", title: "المستند", render: (r: any) => <Link className="text-brand underline" to={docLink(r.source_type, r.source_id)}>{SOURCE_LABEL[r.source_type]} {r.document_no}</Link> },
              { key: "signed_amount", title: "المبلغ", num: true, render: (r: any) => <Money v={r.signed_amount} /> },
            ]} rows={mv.data} />
          </section>}
        </>}
        side={<><WorkflowPanel type="commitment" doc={c} onChange={() => qc.invalidateQueries({ queryKey: ["commitment", id] })} />
          <Attachments type="commitment" id={c.id} locked={c.status === "POSTED"} /></>}
      />
      {conv && <ConvertModal c={c} open={conv} onClose={() => setConv(false)} />}
    </div>
  );
}
