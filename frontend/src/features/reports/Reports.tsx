import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, download } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { ErrorBox, Field, Money, PageHeader, Spinner } from "@/components/ui";
import { fmtDate, TXN_TYPE } from "@/lib/labels";
import { pct } from "@/lib/money";
import { useEntities } from "@/lib/hooks";

type Info = { code: string; title: string; description: string; params: string[] };
type Col = { key: string; title: string; kind: string; total: boolean };
type Result = { code: string; title: string; subtitle: string | null; notes: string[]; columns: Col[]; rows: Record<string, any>[]; totals: Record<string, any>; fingerprint: string };

const GROUPS: Record<string, string> = { item: "البند", month: "الشهر", txn_type: "نوع الحركة", component: "المكوّن", source_type: "المصدر" };
const COMPONENTS: Record<string, string> = { APPROPRIATION: "الاعتماد", ALLOCATION: "المفوَّض", TRANSFER_IN: "وارد", TRANSFER_OUT: "صادر", RESERVATION: "حجز", COMMITMENT: "ارتباط", ACTUAL: "فعلي" };

function Cell({ c, v }: { c: Col; v: any }) {
  if (v == null || v === "") return <>—</>;
  if (c.kind === "money") return <Money v={String(v)} />;
  if (c.kind === "percent") return <span className="num">{pct(String(v))}</span>;
  if (c.kind === "date") return <span className="num">{fmtDate(String(v))}</span>;
  if (c.kind === "int") return <span className="num">{v}</span>;
  return <>{String(v)}</>;
}

function ParamInputs({ info, params, set }: { info: Info; params: Record<string, any>; set: (k: string, v: any) => void }) {
  const { data: ents = [] } = useEntities();
  const { year } = useYear();
  const lines = useQuery({ queryKey: ["position", year!.id], queryFn: () => api<any[]>("/budget-position", { params: { fiscal_year_id: year!.id } }), enabled: info.params.includes("budget_line_id") });
  const suppliers = useQuery({ queryKey: ["suppliers", ""], queryFn: () => api<any>("/suppliers", { params: { page_size: 200 } }), enabled: info.params.includes("supplier_id") });
  return (
    <div className="grid gap-3 md:grid-cols-4">
      {info.params.map((k) => {
        switch (k) {
          case "fiscal_year_id": return null;
          case "entity_id": return <Field key={k} label="الجهة"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)}><option value="">الكل</option>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>;
          case "as_of": return <Field key={k} label="الموقف في تاريخ"><input type="date" className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)} /></Field>;
          case "date_from": return <Field key={k} label="من تاريخ"><input type="date" className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)} /></Field>;
          case "date_to": return <Field key={k} label="إلى تاريخ"><input type="date" className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)} /></Field>;
          case "month": return <Field key={k} label="الشهر"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value ? Number(e.target.value) : undefined)} required><option value="">—</option>{Array.from({ length: 12 }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}</select></Field>;
          case "quarter": return <Field key={k} label="الربع"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value ? Number(e.target.value) : undefined)} required><option value="">—</option>{[1, 2, 3, 4].map((q) => <option key={q} value={q}>الربع {q}</option>)}</select></Field>;
          case "open_only": return <Field key={k} label="القائمة فقط"><input type="checkbox" checked={!!params[k]} onChange={(e) => set(k, e.target.checked || undefined)} /></Field>;
          case "status": return <Field key={k} label="الحالة"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)}><option value="">الكل</option><option value="POSTED">مرحّل</option><option value="REVERSED">معكوس</option></select></Field>;
          case "budget_line_id": return <Field key={k} label="البند" className="md:col-span-2"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)} required><option value="">— اختر —</option>{(lines.data ?? []).filter((r) => r.budget_line_id).map((r) => <option key={r.budget_line_id} value={r.budget_line_id}>{r.item_code} {r.item_name}</option>)}</select></Field>;
          case "supplier_id": return <Field key={k} label="المستفيد"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)}><option value="">الكل</option>{(suppliers.data?.items ?? []).map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>;
          case "txn_type": return <Field key={k} label="نوع الحركة"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)}><option value="">الكل</option>{Object.entries(TXN_TYPE).map(([a, b]) => <option key={a} value={a}>{b}</option>)}</select></Field>;
          case "component": return <Field key={k} label="المكوّن"><select className="input" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)}><option value="">الكل</option>{Object.entries(COMPONENTS).map(([a, b]) => <option key={a} value={a}>{b}</option>)}</select></Field>;
          case "table_name": return <Field key={k} label="الجدول"><input className="input" dir="ltr" value={params[k] ?? ""} onChange={(e) => set(k, e.target.value || undefined)} /></Field>;
          case "group_by": return <Field key={k} label="التجميع حسب" className="md:col-span-2"><div className="flex flex-wrap gap-3 pt-1">{Object.entries(GROUPS).map(([g, l]) => {
            const cur: string[] = params[k] ?? ["item"];
            return <label key={g} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={cur.includes(g)} onChange={(e) => set(k, e.target.checked ? [...cur, g] : cur.filter((x) => x !== g))} />{l}</label>;
          })}</div></Field>;
          default: return null;
        }
      })}
    </div>
  );
}

export function Reports() {
  const { can } = useAuth();
  const { year } = useYear();
  const catalog = useQuery({ queryKey: ["reports"], queryFn: () => api<Info[]>("/reports") });
  const [sel, setSel] = useState<Info | null>(null);
  const [params, setParams] = useState<Record<string, any>>({});
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const body = () => ({ ...params, ...(sel?.params.includes("fiscal_year_id") ? { fiscal_year_id: year!.id } : {}) });
  const run = async () => {
    if (!sel) return;
    setBusy(true); setErr(null);
    try { setRes(await api<Result>(`/reports/${sel.code}/run`, { body: body() })); } catch (e) { setErr(e); setRes(null); } finally { setBusy(false); }
  };
  const exp = async (format: string) => { try { await download(`/reports/${sel!.code}/export`, body(), { format }); } catch (e) { setErr(e); } };
  const print = async () => {
    try {
      const r = await api<Response>(`/reports/${sel!.code}/export`, { method: "POST", body: body(), params: { format: "html" }, raw: true });
      const w = window.open(URL.createObjectURL(new Blob([await r.text()], { type: "text/html" })));
      w?.addEventListener("load", () => w.print());
    } catch (e) { setErr(e); }
  };
  if (!catalog.data) return <Spinner />;
  return (
    <div>
      <PageHeader title="التقارير" subtitle="كل رقم محسوب من القيود المرحّلة. يحمل كل تقرير بصمة تحقق تطابق النسخة المصدرة." />
      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <nav className="card max-h-[75vh] overflow-auto p-2" aria-label="قائمة التقارير">
          {catalog.data.map((r) => (
            <button key={r.code} onClick={() => { setSel(r); setParams({}); setRes(null); setErr(null); }}
              className={`block w-full rounded-md px-3 py-2 text-start text-sm ${sel?.code === r.code ? "bg-brand text-white" : "hover:bg-brand-50"}`}>
              <div className="font-bold">{r.title}</div><div className={`text-xs ${sel?.code === r.code ? "text-white/80" : "text-ink-mute"}`}>{r.description}</div>
            </button>
          ))}
        </nav>
        <div>
          {!sel ? <div className="card p-8 text-center text-ink-mute">اختر تقريرًا من القائمة.</div> : (
            <>
              <section className="card p-4">
                <h2 className="mb-3 font-bold">{sel.code} — {sel.title}</h2>
                <ParamInputs info={sel} params={params} set={(k, v) => setParams((p) => { const n = { ...p, [k]: v }; if (v === undefined) delete n[k]; return n; })} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn-primary" onClick={run} disabled={busy}>{busy ? "جارٍ التشغيل…" : "عرض"}</button>
                  {can("reports.export") && res && <>
                    <button className="btn-outline" onClick={() => exp("pdf")}>PDF</button>
                    <button className="btn-outline" onClick={() => exp("xlsx")}>Excel</button>
                    <button className="btn-outline" onClick={print}>طباعة</button>
                  </>}
                </div>
                <ErrorBox error={err} />
              </section>
              {res && (
                <section className="card mt-4 overflow-auto">
                  <div className="border-b border-line p-3">
                    <div className="font-bold">{res.title}</div>
                    {res.subtitle && <div className="text-sm text-ink-soft">{res.subtitle}</div>}
                    {res.notes.map((n, i) => <div key={i} className="mt-1 text-xs text-warn">• {n}</div>)}
                  </div>
                  <table className="w-full text-sm">
                    <thead><tr>{res.columns.map((c) => <th key={c.key} className="th">{c.title}</th>)}</tr></thead>
                    <tbody>{res.rows.length === 0 ? <tr><td className="td text-center text-ink-mute" colSpan={res.columns.length}>لا توجد بيانات.</td></tr> :
                      res.rows.map((r, i) => <tr key={i} className="hover:bg-surface">{res.columns.map((c) => <td key={c.key} className="td"><Cell c={c} v={r[c.key]} /></td>)}</tr>)}</tbody>
                    {Object.keys(res.totals).length > 0 && <tfoot><tr className="bg-surface font-bold">{res.columns.map((c, i) => <td key={c.key} className="td">{i === 0 ? "الإجمالي" : c.key in res.totals ? <Cell c={c} v={res.totals[c.key]} /> : ""}</td>)}</tr></tfoot>}
                  </table>
                  <div className="p-3 text-xs text-ink-mute" dir="ltr">fingerprint: <span className="num">{res.fingerprint}</span> · {res.rows.length} rows</div>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
