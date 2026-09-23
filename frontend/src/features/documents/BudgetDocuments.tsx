import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { ErrorBox, Field, Money, PageHeader, Spinner, Table } from "@/components/ui";
import { fmtDate } from "@/lib/labels";
import { D, fmt, normalizeMoneyInput } from "@/lib/money";
import { useEntities } from "@/lib/hooks";
import { Attachments, DocLayout, Facts, LinesEditor, StatusBadge, WorkflowPanel, type Line } from "./common";
import { DocList } from "./DocList";

export const BUDGET_KIND: Record<string, string> = { ORIGINAL_BUDGET: "اعتماد أصلي", BUDGET_INCREASE: "تعزيز (زيادة)", BUDGET_DECREASE: "تخفيض" };

export function BudgetDocList() {
  const { can } = useAuth();
  return <DocList title="الاعتماد الأصلي والتعديلات" endpoint="/budget-documents" basePath="/budget-documents" newLabel="مستند ميزانية"
    canCreate={can("budget_documents.create")} cols={[
      { key: "doc_date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.doc_date)}</span> },
      { key: "doc_no", title: "الرقم" },
      { key: "kind", title: "النوع", render: (r: any) => BUDGET_KIND[r.kind] },
      { key: "description", title: "البيان" },
      { key: "reference", title: "المرجع" },
      { key: "status", title: "الحالة", render: (r: any) => <StatusBadge s={r.status} /> },
    ]} />;
}

export function BudgetDocForm() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: ents = [] } = useEntities();
  const [f, setF] = useState({ kind: "ORIGINAL_BUDGET", doc_date: new Date().toISOString().slice(0, 10), description: "", reference: "", entity_id: "" });
  const [lines, setLines] = useState<Line[]>([{ item_id: "", amount: "" }]);
  const [err, setErr] = useState<unknown>(null);
  const entity = f.entity_id || ents[0]?.id;
  const total = lines.reduce((s, l) => { try { return s.plus(D(normalizeMoneyInput(l.amount) || 0)); } catch { return s; } }, D(0));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api("/budget-documents", { body: {
        fiscal_year_id: year!.id, kind: f.kind, doc_date: f.doc_date, description: f.description, reference: f.reference || null,
        lines: lines.map((l) => ({ entity_id: entity, item_id: l.item_id, amount: normalizeMoneyInput(l.amount) })),
      } });
      qc.invalidateQueries({ queryKey: ["/budget-documents"] });
      nav(`/budget-documents/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <div>
      <PageHeader title="مستند ميزانية جديد" subtitle="الاعتماد الأصلي أو التعزيز أو التخفيض — كل سطر يصبح قيدًا في دفتر الاعتمادات عند الترحيل." />
      <form onSubmit={submit} className="card grid gap-4 p-4 md:grid-cols-3">
        <Field label="النوع"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(BUDGET_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="التاريخ"><input type="date" className="input" value={f.doc_date} onChange={(e) => setF({ ...f, doc_date: e.target.value })} required /></Field>
        <Field label="الجهة"><select className="input" value={entity ?? ""} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="البيان" className="md:col-span-2"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} required minLength={3} /></Field>
        <Field label="المرجع (قرار/كتاب)"><input className="input" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
        <div className="md:col-span-3"><LinesEditor lines={lines} setLines={setLines} />
          <div className="mt-2 text-sm">الإجمالي: <span className="num font-semibold">{fmt(total.toFixed(3))}</span></div></div>
        <div className="md:col-span-3"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ كمسودة</button></div>
      </form>
    </div>
  );
}

export function BudgetDocView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["budget-doc", id], queryFn: () => api(`/budget-documents/${id}`) });
  if (!q.data) return <Spinner />;
  const d = q.data;
  return (
    <div>
      <PageHeader title={`${BUDGET_KIND[d.kind]} ${d.doc_no}`} subtitle={<StatusBadge s={d.status} />} />
      <DocLayout
        main={<section className="card">
          <div className="p-4"><Facts items={[["التاريخ", fmtDate(d.doc_date)], ["البيان", d.description], ["المرجع", d.reference], ["الإجمالي", <Money v={d.total} strong />]]} /></div>
          <Table cols={[{ key: "item_code", title: "البند" }, { key: "item_name", title: "الاسم" }, { key: "amount", title: "المبلغ", num: true, render: (r: any) => <Money v={r.amount} /> }]} rows={d.lines} />
        </section>}
        side={<><WorkflowPanel type="budget_document" doc={d} onChange={() => qc.invalidateQueries({ queryKey: ["budget-doc", id] })} />
          <Attachments type="budget_document" id={d.id} locked={d.status === "POSTED"} /></>}
      />
    </div>
  );
}
