import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { useYear } from "@/app/year";
import { Field, Money, PageHeader, Spinner, Table } from "@/components/ui";
import { SOURCE_LABEL, fmtDate } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { StatusBadge, docLink, MoneyInput } from "@/features/documents/common";

export function Search() {
  const [sp, setSp] = useSearchParams();
  const { year } = useYear();
  const q = sp.get("q") ?? "";
  const [text, setText] = useState(q);
  const [f, setF] = useState({ types: [] as string[], amount_min: "", amount_max: "", date_from: "", date_to: "", allYears: false });
  const params = { q: q || undefined, type: f.types.length ? f.types : undefined, fiscal_year_id: f.allYears ? undefined : year!.id,
    amount_min: f.amount_min ? normalizeMoneyInput(f.amount_min) : undefined, amount_max: f.amount_max ? normalizeMoneyInput(f.amount_max) : undefined,
    date_from: f.date_from || undefined, date_to: f.date_to || undefined, limit: 100 };
  const active = !!(q || f.amount_min || f.amount_max || f.date_from || f.date_to);
  const { data, isFetching } = useQuery({ queryKey: ["search", params], queryFn: () => api<any>("/search", { params }), enabled: active });
  return (
    <div>
      <PageHeader title="البحث الشامل" subtitle="ابحث برقم المستند أو المبلغ أو رقم البند (مثل 2/16) أو اسم المورد أو البيان أو اسم المستخدم. البحث يتجاهل الفروق في الهمزات والتاء المربوطة والأرقام الهندية." />
      <form className="card mb-4 grid gap-3 p-3 md:grid-cols-6" onSubmit={(e) => { e.preventDefault(); setSp(text ? { q: text } : {}); }}>
        <Field label="نص البحث" className="md:col-span-3"><input className="input" value={text} onChange={(e) => setText(e.target.value)} autoFocus /></Field>
        <Field label="من مبلغ"><MoneyInput value={f.amount_min} onChange={(v) => setF({ ...f, amount_min: v })} /></Field>
        <Field label="إلى مبلغ"><MoneyInput value={f.amount_max} onChange={(v) => setF({ ...f, amount_max: v })} /></Field>
        <div className="flex items-end"><button className="btn-primary w-full">بحث</button></div>
        <Field label="من تاريخ"><input type="date" className="input" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label="إلى تاريخ"><input type="date" className="input" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
        <div className="flex flex-wrap items-end gap-3 md:col-span-4 text-sm">
          {Object.entries(SOURCE_LABEL).map(([k, v]) => <label key={k} className="flex items-center gap-1"><input type="checkbox" checked={f.types.includes(k)}
            onChange={(e) => setF({ ...f, types: e.target.checked ? [...f.types, k] : f.types.filter((x) => x !== k) })} />{v}</label>)}
          <label className="flex items-center gap-1"><input type="checkbox" checked={f.allYears} onChange={(e) => setF({ ...f, allYears: e.target.checked })} />كل السنوات</label>
        </div>
      </form>
      {isFetching && <Spinner />}
      {data && <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <section className="card"><h2 className="border-b border-line p-3 font-bold">المستندات ({data.documents.length})</h2>
          <Table rows={data.documents} empty="لا نتائج." cols={[
            { key: "type", title: "النوع", render: (r: any) => SOURCE_LABEL[r.type] },
            { key: "doc_no", title: "الرقم", render: (r: any) => <Link className="text-brand underline" to={docLink(r.type, r.id)}>{r.doc_no}</Link> },
            { key: "date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.date)}</span> },
            { key: "description", title: "البيان" },
            { key: "amount", title: "المبلغ", num: true, render: (r: any) => <Money v={r.amount} /> },
            { key: "status", title: "الحالة", render: (r: any) => <StatusBadge s={r.status} /> },
          ]} /></section>
        <div className="space-y-4">
          <section className="card"><h2 className="border-b border-line p-3 font-bold">البنود</h2>
            {data.items.length ? data.items.map((i: any) => <Link key={i.id} to={`/budget?item=${encodeURIComponent(i.code)}`} className="block p-2 text-sm hover:bg-surface"><b>{i.code}</b> {i.name}</Link>) : <div className="p-3 text-sm text-ink-mute">—</div>}</section>
          <section className="card"><h2 className="border-b border-line p-3 font-bold">الموردون والمستفيدون</h2>
            {data.suppliers.length ? data.suppliers.map((s: any) => <div key={s.id} className="p-2 text-sm">{s.name}</div>) : <div className="p-3 text-sm text-ink-mute">—</div>}</section>
        </div>
      </div>}
    </div>
  );
}
