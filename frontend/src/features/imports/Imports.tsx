import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadGet } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, Field, Money, PageHeader, Spinner, Table, useToast } from "@/components/ui";
import { fmtDateTime } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { useEntities } from "@/lib/hooks";
import { MoneyInput } from "@/features/documents/common";

const B_STATUS: Record<string, { l: string; t: string }> = {
  UPLOADED: { l: "مرفوع", t: "mute" }, ANALYZED: { l: "محلل", t: "brand" }, VALIDATED: { l: "تم التحقق", t: "brand" },
  IMPORTED: { l: "مستورد", t: "ok" }, DISCARDED: { l: "مستبعد", t: "mute" },
};
const SEV: Record<string, string> = { CRITICAL: "bad", HIGH: "bad", WARNING: "warn", INFO: "brand" };
const STEPS = ["رفع الملف", "التحليل", "القرارات", "التحقق والمعاينة", "الاستيراد"];

export function ImportList() {
  const { year } = useYear();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: ents = [] } = useEntities();
  const file = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const { data } = useQuery({ queryKey: ["imports"], queryFn: () => api<any[]>("/imports") });
  const upload = async () => {
    const f = file.current?.files?.[0];
    if (!f) return;
    setBusy(true); setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", f); fd.append("fiscal_year_id", year!.id);
      if (ents[0]) fd.append("entity_id", ents[0].id);
      const b = await api<any>("/imports", { method: "POST", form: fd });
      await api(`/imports/${b.id}/analyze`, { method: "POST" });
      qc.invalidateQueries({ queryKey: ["imports"] });
      nav(`/settings/imports/${b.id}`);
    } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  return (
    <div>
      <PageHeader title="استيراد سجل Excel" subtitle={`يُقرأ سجل مراقبة الاعتماد (نموذج 23/6) صفًا صفًا ويُحوَّل إلى عمليات تمر عبر محرك الترحيل نفسه. لا تُنقل أرصدة Excel؛ تُستخدم للمقارنة فقط. السنة: ${year!.year}`} />
      <section className="card mb-4 flex flex-wrap items-end gap-3 p-4">
        <Field label="ملف Excel (.xlsx)"><input ref={file} type="file" accept=".xlsx" className="input" /></Field>
        <button className="btn-primary" onClick={upload} disabled={busy}>{busy ? "جارٍ الرفع والتحليل…" : "رفع وتحليل"}</button>
        <ErrorBox error={err} />
      </section>
      <div className="card"><Table rows={data} onRow={(r) => nav(`/settings/imports/${r.id}`)} empty="لا توجد دفعات." cols={[
        { key: "filename", title: "الملف" },
        { key: "created_at", title: "الرفع", render: (r: any) => <span className="num">{fmtDateTime(r.created_at)}</span> },
        { key: "status", title: "الحالة", render: (r: any) => <Badge tone={B_STATUS[r.status]?.t}>{B_STATUS[r.status]?.l ?? r.status}</Badge> },
        { key: "stats", title: "المشاكل", render: (r: any) => r.stats?.issues ? <span className="flex gap-1">{Object.entries(r.stats.issues).map(([k, v]) => <Badge key={k} tone={SEV[k]}>{k} {String(v)}</Badge>)}{r.stats.blocking && <Badge tone="bad">حاجبة</Badge>}</span> : "—" },
        { key: "imported_at", title: "الاستيراد", render: (r: any) => <span className="num">{fmtDateTime(r.imported_at)}</span> },
      ]} /></div>
    </div>
  );
}

export function ImportWizard() {
  const { id } = useParams();
  const { me, can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const batch = useQuery({ queryKey: ["import", id], queryFn: () => api<any>(`/imports/${id}`) });
  const issues = useQuery({ queryKey: ["import-issues", id, batch.data?.status], queryFn: () => api<any[]>(`/imports/${id}/issues`), enabled: !!batch.data && batch.data.status !== "UPLOADED" });
  const preview = useQuery({ queryKey: ["import-preview", id, batch.data?.status, batch.data?.decisions], queryFn: () => api<any>(`/imports/${id}/preview`), enabled: !!batch.data && ["ANALYZED", "VALIDATED", "IMPORTED"].includes(batch.data.status) });
  const [dec, setDec] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const b = batch.data;
  const d = dec ?? b?.decisions ?? {};
  const refresh = () => { qc.invalidateQueries({ queryKey: ["import", id] }); qc.invalidateQueries({ queryKey: ["imports"] }); };
  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true); setErr(null);
    try { await fn(); toast(ok); refresh(); } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  if (!b) return <Spinner />;
  const step = b.status === "UPLOADED" ? 1 : b.status === "ANALYZED" ? 2 : b.status === "VALIDATED" ? 3 : 4;
  const done = ["IMPORTED", "DISCARDED"].includes(b.status);
  const authIssues = (issues.data ?? []).filter((i) => i.decision === "AUTH_AMOUNT");
  const needsException = (issues.data ?? []).some((i) => i.decision === "D-02");
  const blocking = (issues.data ?? []).filter((i) => i.blocking);
  const pv = preview.data;
  return (
    <div>
      <PageHeader title={`دفعة استيراد: ${b.filename}`} subtitle={<span className="flex items-center gap-2"><Badge tone={B_STATUS[b.status]?.t}>{B_STATUS[b.status]?.l}</Badge><span dir="ltr" className="font-mono text-xs">sha256 {b.sha256.slice(0, 16)}…</span></span>}
        actions={<>
          <button className="btn-outline" onClick={() => downloadGet(`/imports/${id}/report`, { format: "pdf" })}>تقرير جودة البيانات PDF</button>
          <button className="btn-outline" onClick={() => downloadGet(`/imports/${id}/report`, { format: "xlsx" })}>Excel</button>
          <Link className="btn-ghost" to="/settings/imports">كل الدفعات</Link>
        </>} />
      <ol className="mb-4 flex flex-wrap gap-2 text-sm">{STEPS.map((s, i) => <li key={s} className={`rounded-full px-3 py-1 ${i <= step ? "bg-brand text-white" : "bg-surface text-ink-mute"}`}>{i + 1}. {s}</li>)}</ol>
      <ErrorBox error={err} />
      {!done && <section className="card mb-4 p-4">
        <h2 className="mb-3 font-bold">القرارات</h2>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="التاريخ الافتراضي للصفوف بلا تاريخ (D-04)" hint="تُوسَم «تقديري»؛ الافتراضي 31/12"><input type="date" className="input" value={d.default_date ?? ""} onChange={(e) => setDec({ ...d, default_date: e.target.value || undefined })} /></Field>
          {needsException && <Field label="الاستثناء التاريخي (D-02)" className="md:col-span-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!d.accept_historical_exceptions} onChange={(e) => setDec({ ...d, accept_historical_exceptions: e.target.checked })} />
            أقبل استيراد المصروفات التي تجاوزت الرصيد تاريخيًا كما حدثت، موسومة «استثناء تاريخي»</label></Field>}
          {authIssues.map((i) => (
            <Field key={i.id} label={`قيمة التفويض ${i.details?.key?.split(":")[1]} (مجموع التوزيع ${i.details?.sum})`}>
              <MoneyInput value={d.authorization_amounts?.[i.details?.key] ?? ""} onChange={(v) => setDec({ ...d, authorization_amounts: { ...(d.authorization_amounts ?? {}), [i.details.key]: v } })} />
            </Field>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-outline" disabled={busy} onClick={() => run(async () => {
            const clean = { ...d, authorization_amounts: Object.fromEntries(Object.entries(d.authorization_amounts ?? {}).filter(([, v]) => v).map(([k, v]) => [k, normalizeMoneyInput(String(v))])) };
            await api(`/imports/${id}/decisions`, { method: "PUT", body: clean }); setDec(null);
          }, "حُفظت القرارات؛ أعد التحقق")}>حفظ القرارات</button>
          <button className="btn-primary" disabled={busy} onClick={() => run(() => api(`/imports/${id}/validate`, { method: "POST" }), "اكتمل التحقق")}>التحقق</button>
          {b.status === "VALIDATED" && can("imports.commit") && (
            b.created_by === me?.id ? <span className="self-center text-sm text-warn">ينفذ الاستيراد مستخدم آخر غير الذي حضّر الدفعة (فصل المهام).</span>
              : <button className="btn-ok" disabled={busy || blocking.length > 0} onClick={() => { if (window.confirm("تنفيذ الاستيراد؟ ستُنشأ المستندات وتُرحَّل في معاملة واحدة.")) run(() => api(`/imports/${id}/commit`, { method: "POST" }), "اكتمل الاستيراد"); }}>تنفيذ الاستيراد</button>)}
          <button className="btn-ghost text-bad" disabled={busy} onClick={() => run(() => api(`/imports/${id}/discard`, { method: "POST" }), "استُبعدت الدفعة")}>استبعاد</button>
        </div>
      </section>}
      {pv && <section className="card mb-4">
        <h2 className="border-b border-line p-3 font-bold">المعاينة: الموقف المعاد حسابه مقابل Excel</h2>
        <div className="flex flex-wrap gap-4 p-3 text-sm">
          <span>تفويضات: <b className="num">{pv.authorizations.length}</b></span><span>مناقلات: <b className="num">{pv.transfers.length}</b></span>
          <span>مصروفات: <b className="num">{pv.expenditures.length}</b></span><span>ارتباطات: <b className="num">{pv.commitments.length}</b></span>
          <span>تواريخ تقديرية: <b className="num">{pv.estimated_dates}</b></span>
        </div>
        <Table rows={pv.positions} cols={[
          { key: "item_code", title: "البند" },
          { key: "allocation", title: "المفوَّض", num: true, render: (r: any) => <Money v={r.allocation} /> },
          { key: "transfer_in", title: "وارد", num: true, render: (r: any) => <Money v={r.transfer_in} /> },
          { key: "transfer_out", title: "صادر", num: true, render: (r: any) => <Money v={r.transfer_out} /> },
          { key: "actual", title: "الفعلي", num: true, render: (r: any) => <Money v={r.actual} /> },
          { key: "book_balance", title: "الرصيد المحسوب", num: true, render: (r: any) => <Money v={r.book_balance} strong /> },
          { key: "excel_balance", title: "رصيد Excel", num: true, render: (r: any) => <Money v={r.excel_balance} /> },
          { key: "difference", title: "الفرق", num: true, render: (r: any) => r.difference && Number(r.difference) !== 0 ? <span className="text-bad"><Money v={r.difference} /></span> : "—" },
        ]} footer={<tr className="bg-surface font-bold"><td className="td">الإجمالي</td>
          {["allocation", "transfer_in", "transfer_out", "actual", "book_balance"].map((k) => <td key={k} className="td"><Money v={pv.totals[k]} /></td>)}<td className="td" /><td className="td" /></tr>} />
      </section>}
      {issues.data && <section className="card">
        <h2 className="border-b border-line p-3 font-bold">تقرير جودة البيانات ({issues.data.length} ملاحظة{blocking.length > 0 && `، ${blocking.length} حاجبة`})</h2>
        <Table rows={issues.data} empty="لا توجد ملاحظات." cols={[
          { key: "severity", title: "الخطورة", render: (r: any) => <Badge tone={SEV[r.severity]}>{r.severity}</Badge> },
          { key: "code", title: "القاعدة", render: (r: any) => <span className="num">{r.code}</span> },
          { key: "location", title: "الموقع", render: (r: any) => <span dir="ltr" className="font-mono text-xs">{r.location}</span> },
          { key: "message", title: "الوصف" },
          { key: "decision", title: "القرار", render: (r: any) => r.decision ? <Badge tone="brand">{r.decision}</Badge> : null },
          { key: "blocking", title: "", render: (r: any) => r.blocking && <Badge tone="bad">حاجبة</Badge> },
        ]} />
      </section>}
    </div>
  );
}
