import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, Field, Money, PageHeader, Spinner, Table } from "@/components/ui";
import { AUTH_TYPE, fmtDate } from "@/lib/labels";
import { D, fmt, isNeg, normalizeMoneyInput } from "@/lib/money";
import { useEntities } from "@/lib/hooks";
import { Attachments, DocLayout, Facts, LinesEditor, MoneyInput, StatusBadge, WorkflowPanel, type Line } from "./common";
import { DocList } from "./DocList";

export function AuthorizationList() {
  const { can } = useAuth();
  return <DocList title="التفويضات المالية" endpoint="/authorizations" basePath="/authorizations" newLabel="تفويض جديد"
    canCreate={can("authorizations.create")} cols={[
      { key: "auth_date", title: "التاريخ", render: (r: any) => <span className="num">{fmtDate(r.auth_date)}</span> },
      { key: "auth_no", title: "رقم التفويض" },
      { key: "auth_type", title: "النوع", render: (r: any) => AUTH_TYPE[r.auth_type] },
      { key: "period", title: "الفترة", render: (r: any) => r.period_from ? <span className="num">{fmtDate(r.period_from)} — {fmtDate(r.period_to)}</span> : "—" },
      { key: "amount", title: "قيمة التفويض", num: true, render: (r: any) => <Money v={r.amount} /> },
      { key: "status", title: "الحالة", render: (r: any) => <StatusBadge s={r.status} /> },
    ]} />;
}

export function AuthorizationForm() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: ents = [] } = useEntities();
  const [f, setF] = useState<any>({ auth_type: "FINANCIAL", auth_date: new Date().toISOString().slice(0, 10), auth_no: "", amount: "", purpose: "", period_from: "", period_to: "", over_allocation_reason: "" });
  const [lines, setLines] = useState<Line[]>([{ item_id: "", amount: "" }]);
  const [err, setErr] = useState<unknown>(null);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const entity = f.entity_id ?? ents[0]?.id;
  const safe = (s: string) => { try { return D(normalizeMoneyInput(s) || 0); } catch { return D(0); } };
  const allocated = lines.reduce((s, l) => s.plus(safe(l.amount)), D(0));
  const diff = safe(f.amount).minus(allocated);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api("/authorizations", { body: {
        fiscal_year_id: year!.id, entity_id: entity, auth_no: f.auth_no, auth_type: f.auth_type, auth_date: f.auth_date,
        period_from: f.period_from || null, period_to: f.period_to || null, amount: normalizeMoneyInput(f.amount), purpose: f.purpose,
        over_allocation_reason: f.over_allocation_reason || null,
        allocations: lines.filter((l) => l.item_id).map((l) => ({ item_id: l.item_id, amount: normalizeMoneyInput(l.amount) })),
      } });
      qc.invalidateQueries({ queryKey: ["/authorizations"] });
      nav(`/authorizations/${r.id}`);
    } catch (x) { setErr(x); }
  };
  return (
    <div>
      <PageHeader title="تفويض جديد" subtitle="التفويض يوزَّع على البنود؛ كل توزيع يرفع «المخصص» للبند عند الترحيل." />
      <form onSubmit={submit} className="card grid gap-4 p-4 md:grid-cols-3">
        <Field label="رقم التفويض"><input className="input" value={f.auth_no} onChange={(e) => set("auth_no", e.target.value)} required /></Field>
        <Field label="النوع"><select className="input" value={f.auth_type} onChange={(e) => set("auth_type", e.target.value)}>{Object.entries(AUTH_TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="التاريخ"><input type="date" className="input" value={f.auth_date} onChange={(e) => set("auth_date", e.target.value)} required /></Field>
        <Field label="الجهة"><select className="input" value={entity ?? ""} onChange={(e) => set("entity_id", e.target.value)}>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="من"><input type="date" className="input" value={f.period_from} onChange={(e) => set("period_from", e.target.value)} /></Field>
        <Field label="إلى"><input type="date" className="input" value={f.period_to} onChange={(e) => set("period_to", e.target.value)} /></Field>
        <Field label="قيمة التفويض"><MoneyInput value={f.amount} onChange={(v) => set("amount", v)} required /></Field>
        <Field label="الغرض" className="md:col-span-2"><input className="input" value={f.purpose} onChange={(e) => set("purpose", e.target.value)} required minLength={3} /></Field>
        <div className="md:col-span-3"><LinesEditor lines={lines} setLines={setLines} title="التوزيع على البنود" />
          <div className="mt-2 flex gap-4 text-sm">
            <span>الموزع: <b className="num">{fmt(allocated.toFixed(3))}</b></span>
            <span>غير الموزع: <b className={`num ${isNeg(diff.toFixed(3)) ? "text-bad" : ""}`}>{fmt(diff.toFixed(3), { parens: true })}</b></span>
          </div></div>
        {diff.isNeg() && <Field label="سبب تجاوز التوزيع لقيمة التفويض" className="md:col-span-3" hint="مطلوب عند تجاوز التوزيع لقيمة التفويض">
          <input className="input" value={f.over_allocation_reason} onChange={(e) => set("over_allocation_reason", e.target.value)} required /></Field>}
        <div className="md:col-span-3"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ كمسودة</button></div>
      </form>
    </div>
  );
}

export function AuthorizationView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["authorization", id], queryFn: () => api(`/authorizations/${id}`) });
  if (!q.data) return <Spinner />;
  const a = q.data;
  return (
    <div>
      <PageHeader title={`تفويض ${AUTH_TYPE[a.auth_type]} رقم ${a.auth_no}`} subtitle={<StatusBadge s={a.status} />} />
      <DocLayout
        main={<section className="card">
          <div className="p-4"><Facts items={[["التاريخ", fmtDate(a.auth_date)], ["الفترة", a.period_from ? `${fmtDate(a.period_from)} — ${fmtDate(a.period_to)}` : "—"],
            ["قيمة التفويض", <Money v={a.amount} strong />], ["الموزع", <Money v={a.allocated} />],
            ["غير الموزع", <><Money v={a.unallocated} />{isNeg(a.unallocated) && <Badge tone="bad">توزيع زائد</Badge>}</>],
            ["الغرض", a.purpose], ["سبب التجاوز", a.over_allocation_reason]]} /></div>
          <Table cols={[{ key: "item_code", title: "البند" }, { key: "item_name", title: "الاسم" }, { key: "amount", title: "المبلغ الموزع", num: true, render: (r: any) => <Money v={r.amount} /> }]} rows={a.allocations} />
        </section>}
        side={<><WorkflowPanel type="authorization" doc={a} onChange={() => qc.invalidateQueries({ queryKey: ["authorization", id] })} />
          <Attachments type="authorization" id={a.id} locked={a.status === "POSTED"} /></>}
      />
    </div>
  );
}
