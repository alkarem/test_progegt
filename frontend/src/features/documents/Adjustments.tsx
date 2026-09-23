import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { ErrorBox, Field, Money, PageHeader, Spinner } from "@/components/ui";
import { SOURCE_LABEL, fmtDate } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { useEntities } from "@/lib/hooks";
import { Attachments, DocLayout, Facts, ItemSelect, MoneyInput, StatusBadge, WorkflowPanel, docLink } from "./common";
import { DocList } from "./DocList";

const KIND: Record<string, string> = { ADJUSTMENT: "تسوية", COMMITMENT_CANCELLATION: "إلغاء ارتباط", REVERSAL: "قيد عكسي" };
const COMPONENT: Record<string, string> = { APPROPRIATION: "الاعتماد", ALLOCATION: "المخصص", ACTUAL: "المصروف الفعلي" };
type ALine = { item_id: string; component: string; direction: number; amount: string };

export function AdjustmentList() {
  const { can } = useAuth();
  return <DocList title="التسويات والقيود العكسية" endpoint="/adjustments" basePath="/adjustments" newLabel="تسوية" canCreate={can("adjustments.create")} cols={[
    { key: "adjustment_date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.adjustment_date)}</span> },
    { key: "adjustment_no", title: "الرقم" },
    { key: "kind", title: "النوع", render: (r: any) => KIND[r.kind] ?? r.kind },
    { key: "reason", title: "السبب" },
    { key: "amount", title: "المبلغ", num: true, render: (r: any) => <Money v={r.amount} /> },
    { key: "status", title: "الحالة", render: (r: any) => <StatusBadge s={r.status} /> },
  ]} />;
}

export function AdjustmentForm() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [sp] = useSearchParams();
  const commitmentId = sp.get("commitment");
  const { data: ents = [] } = useEntities();
  const cm = useQuery({ queryKey: ["commitment", commitmentId], queryFn: () => api(`/commitments/${commitmentId}`), enabled: !!commitmentId });
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [reason, setReason] = useState("");
  const [cancelAmount, setCancelAmount] = useState("");
  const [lines, setLines] = useState<ALine[]>([{ item_id: "", component: "ACTUAL", direction: -1, amount: "" }]);
  const [err, setErr] = useState<unknown>(null);
  const entity = ents[0]?.id;
  const upd = (i: number, p: Partial<ALine>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...p } : l)));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const body = commitmentId
        ? { fiscal_year_id: year!.id, kind: "COMMITMENT_CANCELLATION", adjustment_date: date, reason, commitment_id: commitmentId, cancel_amount: normalizeMoneyInput(cancelAmount) }
        : { fiscal_year_id: year!.id, kind: "ADJUSTMENT", adjustment_date: date, reason,
            lines: lines.map((l) => ({ entity_id: entity, item_id: l.item_id, component: l.component, direction: l.direction, amount: normalizeMoneyInput(l.amount) })) };
      const r = await api("/adjustments", { body });
      qc.invalidateQueries({ queryKey: ["/adjustments"] });
      nav(`/adjustments/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <div>
      <PageHeader title={commitmentId ? "إلغاء رصيد ارتباط قائم" : "تسوية جديدة"}
        subtitle="التسوية تُنشئ قيودًا جديدة ولا تعدّل أي قيد سابق. لعكس مستند مرحّل بالكامل استخدم زر «قيد عكسي» من صفحة المستند." />
      <form onSubmit={submit} className="card grid gap-4 p-4 md:grid-cols-3">
        <Field label="التاريخ"><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} required /></Field>
        <Field label="السبب" className="md:col-span-2"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} required minLength={5} /></Field>
        {commitmentId ? (
          cm.data ? <>
            <div className="md:col-span-2"><Facts items={[["الارتباط", cm.data.commitment_no], ["البند", `${cm.data.item_code} ${cm.data.item_name}`], ["القائم", <Money v={cm.data.outstanding} strong />]]} /></div>
            <Field label="المبلغ الملغى"><MoneyInput value={cancelAmount} onChange={setCancelAmount} required /></Field>
          </> : <Spinner />
        ) : (
          <div className="md:col-span-3">
            <div className="label">أسطر التسوية</div>
            <table className="w-full"><thead><tr><th className="th">البند</th><th className="th">المكوّن</th><th className="th">الاتجاه</th><th className="th">المبلغ</th><th /></tr></thead><tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td className="py-1 pe-2"><ItemSelect value={l.item_id} onChange={(v) => upd(i, { item_id: v })} required /></td>
                  <td className="py-1 pe-2"><select className="input" value={l.component} onChange={(e) => upd(i, { component: e.target.value })}>{Object.entries(COMPONENT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></td>
                  <td className="py-1 pe-2"><select className="input" value={l.direction} onChange={(e) => upd(i, { direction: Number(e.target.value) })}><option value={1}>زيادة (+)</option><option value={-1}>نقص (−)</option></select></td>
                  <td className="w-40 py-1 pe-2"><MoneyInput value={l.amount} onChange={(v) => upd(i, { amount: v })} required /></td>
                  <td className="w-10"><button type="button" className="btn-ghost px-2" aria-label="حذف السطر" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button></td>
                </tr>
              ))}
            </tbody></table>
            <button type="button" className="btn-ghost mt-1" onClick={() => setLines([...lines, { item_id: "", component: "ACTUAL", direction: -1, amount: "" }])}>+ سطر</button>
          </div>
        )}
        <div className="md:col-span-3"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ كمسودة</button></div>
      </form>
    </div>
  );
}

export function AdjustmentView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["adjustment", id], queryFn: () => api(`/adjustments/${id}`) });
  if (!q.data) return <Spinner />;
  const a = q.data;
  return (
    <div>
      <PageHeader title={`${KIND[a.kind] ?? a.kind} ${a.adjustment_no}`} subtitle={<StatusBadge s={a.status} />} />
      <DocLayout
        main={<section className="card p-4">
          <Facts items={[["التاريخ", fmtDate(a.adjustment_date)], ["المبلغ", <Money v={a.amount} strong />], ["السبب", a.reason],
            ["يعكس", a.reverses_source_id ? <Link className="text-brand underline" to={docLink(a.reverses_source_type, a.reverses_source_id)}>{SOURCE_LABEL[a.reverses_source_type]}</Link> : null],
            ["الارتباط", a.commitment_id ? <Link className="text-brand underline" to={`/commitments/${a.commitment_id}`}>عرض الارتباط</Link> : null]]} />
        </section>}
        side={<><WorkflowPanel type="adjustment" doc={a} onChange={() => qc.invalidateQueries({ queryKey: ["adjustment", id] })} />
          <Attachments type="adjustment" id={a.id} locked={a.status === "POSTED"} /></>}
      />
    </div>
  );
}
