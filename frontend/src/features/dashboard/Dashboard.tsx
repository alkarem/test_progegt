import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/api/client";
import { useYear } from "@/app/year";
import { Kpi, Money, PageHeader, Spinner } from "@/components/ui";
import { fmt, pct } from "@/lib/money";

const C = { budget: "#15507A", actual: "#1E7F5C", commitment: "#B7791F", available: "#7A8594", bad: "#B42318" };
const n = (s: string) => Number.parseFloat(s);   // للرسم فقط؛ الأرقام المعروضة نصية دقيقة
const MONTHS = ["ينا", "فبر", "مار", "أبر", "ماي", "يون", "يول", "أغس", "سبت", "أكت", "نوف", "ديس"];

export function Dashboard() {
  const { year } = useYear();
  const nav = useNavigate();
  const params = { fiscal_year_id: year!.id };
  const kpi = useQuery({ queryKey: ["kpis", year!.id], queryFn: () => api("/dashboard/kpis", { params }) });
  const chart = (name: string) => useQuery({ queryKey: ["chart", name, year!.id], queryFn: () => api<any>(`/dashboard/charts/${name}`, { params }) });
  const bva = chart("budget_vs_actual");
  const monthly = chart("monthly_expenditure");
  const top = chart("top_spending");
  const low = chart("low_balance");
  const over = chart("over_budget");
  const transfers = chart("transfer_analysis");
  const yoy = chart("year_over_year");
  const k = kpi.data;
  const tip = (v: any, name: any) => [fmt(String(v)), name];

  return (
    <div>
      <PageHeader title="لوحة القيادة" subtitle={`السنة المالية ${year!.year} — كل الأرقام محسوبة من الحركات المرحّلة`} />
      {!k ? <Spinner /> : (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Kpi label="إجمالي الاعتماد" value={<Money v={k.total_budget} />} />
          <Kpi label="إجمالي المصروف" value={<Money v={k.total_actual} />} />
          <Kpi label="إجمالي الارتباطات" value={<Money v={k.total_commitments} />} />
          <Kpi label="الرصيد المتاح" value={<Money v={k.total_available} />} tone={k.total_available.startsWith("-") ? "bad" : "ok"} />
          <Kpi label="نسبة التنفيذ" value={pct(k.execution_rate)} hint={`الاستخدام (مع الارتباطات): ${pct(k.utilization_rate)}`} />
          <Kpi label="عدد المناقلات" value={k.transfers_count} />
          <Kpi label="العمليات المعلقة" value={k.pending_count} tone={k.pending_count ? "warn" : undefined} />
          <Kpi label="البنود المتجاوزة" value={k.exceeded_count} tone={k.exceeded_count ? "bad" : "ok"} />
          <Kpi label="التنبيهات المفتوحة" value={k.open_alerts} tone={k.open_alerts ? "warn" : undefined} />
          <Kpi label="بانتظار اعتمادي" value={k.awaiting_my_action} tone={k.awaiting_my_action ? "warn" : undefined} />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card p-4 lg:col-span-2">
          <h2 className="mb-2 font-bold">الاعتماد مقابل الفعلي والارتباطات لكل بند</h2>
          {!bva.data ? <Spinner /> : (
            <ResponsiveContainer width="100%" height={Math.max(220, bva.data.length * 30)}>
              <BarChart data={bva.data.map((r: any) => ({ ...r, b: n(r.budget), a: n(r.actual), c: n(r.commitment) }))} layout="vertical" margin={{ left: 10, right: 10 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tickFormatter={(v) => (v / 1000).toLocaleString("en") + "k"} reversed />
                <YAxis type="category" dataKey="item_code" width={50} orientation="right" interval={0} />
                <Tooltip formatter={tip} labelFormatter={(l, p) => `${l} ${p?.[0]?.payload?.item_name ?? ""}`} />
                <Legend />
                <Bar dataKey="b" name="الاعتماد" fill={C.budget} barSize={8} onClick={(d: any) => d?.item_code && nav(`/budget?item=${d.item_code}`)} />
                <Bar dataKey="a" name="الفعلي" fill={C.actual} barSize={8} />
                <Bar dataKey="c" name="الارتباطات" fill={C.commitment} barSize={8} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>
        <section className="card p-4">
          <h2 className="mb-2 font-bold">المصروف الشهري والتراكمي</h2>
          {!monthly.data ? <Spinner /> : (
            <>
              <ResponsiveContainer width="100%" height={240}>
                <ComposedChart data={monthly.data.months.map((m: any, _i: number, all: any[]) => {
                  // لا يُمد الخط التراكمي إلى أشهر لم تقع فيها حركة بعد
                  const last = Math.max(0, ...all.filter((x) => n(x.actual) !== 0).map((x) => x.month));
                  return { m: MONTHS[m.month - 1], a: n(m.actual), c: m.month <= last ? n(m.cumulative) : null };
                })}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="m" reversed /><YAxis orientation="right" tickFormatter={(v) => (v / 1000).toLocaleString("en") + "k"} />
                  <Tooltip formatter={tip} /><Legend />
                  <Bar dataKey="a" name="مصروف الشهر" fill={C.actual} />
                  <Line dataKey="c" name="التراكمي" stroke={C.budget} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
              {monthly.data.estimated_dates_total !== "0" && monthly.data.estimated_dates_total !== "0.000" && (
                <p className="mt-1 text-xs text-warn">مصروفات بتاريخ تقديري (مستوردة بلا تاريخ): <Money v={monthly.data.estimated_dates_total} /></p>
              )}
            </>
          )}
        </section>
        <section className="card p-4">
          <h2 className="mb-2 font-bold">أعلى البنود صرفًا</h2>
          {!top.data ? <Spinner /> : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={top.data.map((r: any) => ({ ...r, a: n(r.actual) }))} layout="vertical">
                <XAxis type="number" hide reversed /><YAxis type="category" dataKey="item_code" width={50} orientation="right" />
                <Tooltip formatter={tip} labelFormatter={(l, p) => `${l} ${p?.[0]?.payload?.item_name ?? ""}`} />
                <Bar dataKey="a" name="الفعلي" fill={C.actual} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>
        <ListCard title="بنود منخفضة الرصيد (≥ 80%)" rows={low.data} tone="warn" nav={nav}
                  value={(r) => <><span className="num">{pct(r.utilization_rate)}</span> · متاح <Money v={r.available} /></>} />
        <ListCard title="بنود متجاوزة" rows={over.data} tone="bad" nav={nav} value={(r) => <>تجاوز <Money v={r.excess} strong /></>} />
        <section className="card p-4">
          <h2 className="mb-2 font-bold">تحليل المناقلات</h2>
          {!transfers.data ? <Spinner /> : !transfers.data.length ? <p className="text-ink-mute">لا توجد مناقلات مرحّلة.</p> : (
            <table className="w-full text-sm"><thead><tr><th className="th">من</th><th className="th">إلى</th><th className="th text-end">المبلغ</th><th className="th text-end">عدد</th></tr></thead>
              <tbody>{transfers.data.map((t: any, i: number) => (
                <tr key={i}><td className="td">{t.from_code}</td><td className="td">{t.to_code}</td><td className="td text-end"><Money v={t.amount} /></td><td className="td text-end num">{t.n}</td></tr>
              ))}</tbody></table>
          )}
        </section>
        <section className="card p-4">
          <h2 className="mb-2 font-bold">مقارنة السنوات</h2>
          {!yoy.data ? <Spinner /> : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={yoy.data.map((r: any) => ({ y: String(r.year), b: n(r.budget), a: n(r.actual) }))}>
                <XAxis dataKey="y" reversed /><YAxis orientation="right" tickFormatter={(v) => (v / 1000).toLocaleString("en") + "k"} />
                <Tooltip formatter={tip} /><Legend />
                <Bar dataKey="b" name="الاعتماد" fill={C.budget} /><Bar dataKey="a" name="الفعلي" fill={C.actual} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>
      </div>
    </div>
  );
}

function ListCard({ title, rows, value, tone, nav }: { title: string; rows: any[] | undefined; value: (r: any) => React.ReactNode; tone: string; nav: (p: string) => void }) {
  return (
    <section className="card p-4">
      <h2 className="mb-2 font-bold">{title}</h2>
      {!rows ? <Spinner /> : !rows.length ? <p className="text-ink-mute">لا يوجد.</p> : (
        <ul className="divide-y divide-line">
          {rows.map((r) => (
            <li key={r.item_code} className="flex cursor-pointer items-center justify-between py-1.5 hover:bg-surface" onClick={() => nav(`/budget?item=${r.item_code}`)}>
              <span><span className={`font-bold ${tone === "bad" ? "text-bad" : "text-warn"}`}>{r.item_code}</span> {r.item_name}</span>
              <span className="text-sm">{value(r)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
