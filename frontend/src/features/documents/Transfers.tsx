import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { ErrorBox, Field, Money, PageHeader, Spinner, Table } from "@/components/ui";
import { fmtDate } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { useEntities } from "@/lib/hooks";
import { Attachments, DocLayout, Facts, ItemSelect, MoneyInput, StatusBadge, WorkflowPanel } from "./common";
import { DocList } from "./DocList";

type TLine = { from_item_id: string; to_item_id: string; amount: string };

export function TransferList() {
  const { can } = useAuth();
  return <DocList title="المناقلات" endpoint="/transfers" basePath="/transfers" newLabel="مناقلة جديدة" canCreate={can("transfers.create")} cols={[
    { key: "transfer_date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.transfer_date)}</span> },
    { key: "transfer_no", title: "الرقم" },
    { key: "reason", title: "السبب" },
    { key: "approval_no", title: "رقم الموافقة" },
    { key: "total", title: "الإجمالي", num: true, render: (r: any) => <Money v={r.total} /> },
    { key: "status", title: "الحالة", render: (r: any) => <StatusBadge s={r.status} /> },
  ]} />;
}

export function TransferForm() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: ents = [] } = useEntities();
  const [f, setF] = useState({ transfer_date: new Date().toISOString().slice(0, 10), reason: "", approval_no: "", entity_id: "" });
  const [lines, setLines] = useState<TLine[]>([{ from_item_id: "", to_item_id: "", amount: "" }]);
  const [err, setErr] = useState<unknown>(null);
  const entity = f.entity_id || ents[0]?.id;
  const upd = (i: number, p: Partial<TLine>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...p } : l)));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api("/transfers", { body: {
        fiscal_year_id: year!.id, entity_id: entity, transfer_date: f.transfer_date, reason: f.reason, approval_no: f.approval_no || null,
        lines: lines.map((l) => ({ ...l, amount: normalizeMoneyInput(l.amount) })),
      } });
      qc.invalidateQueries({ queryKey: ["/transfers"] });
      nav(`/transfers/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <div>
      <PageHeader title="مناقلة جديدة" subtitle="كل سطر يولّد قيدين متوازنين (صادر من بند ووارد إلى آخر). لا مناقلة من بند إلى نفسه، ولا مناقلة تتجاوز المتاح دون صلاحية." />
      <form onSubmit={submit} className="card grid gap-4 p-4 md:grid-cols-3">
        <Field label="التاريخ"><input type="date" className="input" value={f.transfer_date} onChange={(e) => setF({ ...f, transfer_date: e.target.value })} required /></Field>
        <Field label="رقم الموافقة"><input className="input" value={f.approval_no} onChange={(e) => setF({ ...f, approval_no: e.target.value })} /></Field>
        <Field label="الجهة"><select className="input" value={entity ?? ""} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="السبب" className="md:col-span-3"><input className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} required minLength={3} /></Field>
        <div className="md:col-span-3">
          <div className="label">الأسطر</div>
          <table className="w-full"><thead><tr><th className="th">من البند</th><th className="th">إلى البند</th><th className="th">المبلغ</th><th /></tr></thead><tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td className="py-1 pe-2"><ItemSelect value={l.from_item_id} onChange={(v) => upd(i, { from_item_id: v })} required /></td>
                <td className="py-1 pe-2"><ItemSelect value={l.to_item_id} onChange={(v) => upd(i, { to_item_id: v })} required /></td>
                <td className="w-44 py-1 pe-2"><MoneyInput value={l.amount} onChange={(v) => upd(i, { amount: v })} required /></td>
                <td className="w-10"><button type="button" className="btn-ghost px-2" aria-label="حذف السطر" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button></td>
              </tr>
            ))}
          </tbody></table>
          <button type="button" className="btn-ghost mt-1" onClick={() => setLines([...lines, { from_item_id: "", to_item_id: "", amount: "" }])}>+ سطر</button>
        </div>
        <div className="md:col-span-3"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ كمسودة</button></div>
      </form>
    </div>
  );
}

export function TransferView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["transfer", id], queryFn: () => api(`/transfers/${id}`) });
  if (!q.data) return <Spinner />;
  const t = q.data;
  return (
    <div>
      <PageHeader title={`مناقلة ${t.transfer_no}`} subtitle={<StatusBadge s={t.status} />} />
      <DocLayout
        main={<section className="card">
          <div className="p-4"><Facts items={[["التاريخ", fmtDate(t.transfer_date)], ["رقم الموافقة", t.approval_no], ["السبب", t.reason], ["الإجمالي", <Money v={t.total} strong />]]} /></div>
          <Table cols={[
            { key: "from", title: "من البند", render: (r: any) => `${r.source.item_code} ${r.source.item_name}` },
            { key: "from_av", title: "متاح المصدر الآن", num: true, render: (r: any) => <Money v={r.source.available_now} /> },
            { key: "to", title: "إلى البند", render: (r: any) => `${r.target.item_code} ${r.target.item_name}` },
            { key: "to_av", title: "متاح المستفيد الآن", num: true, render: (r: any) => <Money v={r.target.available_now} /> },
            { key: "amount", title: "المبلغ", num: true, render: (r: any) => <Money v={r.amount} strong /> },
          ]} rows={t.lines} />
        </section>}
        side={<><WorkflowPanel type="transfer" doc={t} onChange={() => qc.invalidateQueries({ queryKey: ["transfer", id] })} />
          <Attachments type="transfer" id={t.id} locked={t.status === "POSTED"} /></>}
      />
    </div>
  );
}
