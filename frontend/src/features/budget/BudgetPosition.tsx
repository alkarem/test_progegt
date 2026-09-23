import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, download } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, Money, PageHeader, Spinner } from "@/components/ui";
import { LEVEL } from "@/lib/labels";
import { D, pct } from "@/lib/money";

type Row = { budget_line_id: string | null; item_code: string; item_name: string; level: string; position: Record<string, string> };

export function BudgetPosition() {
  const { year } = useYear();
  const { can } = useAuth();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const { data } = useQuery({ queryKey: ["position", year!.id], queryFn: () => api<Row[]>("/budget-position", { params: { fiscal_year_id: year!.id } }) });
  useEffect(() => {
    const code = sp.get("item");
    const row = code && data?.find((r) => r.item_code === code && r.budget_line_id);
    if (row) nav(`/budget/lines/${row.budget_line_id}`, { replace: true });
  }, [sp, data, nav]);
  const sum = (k: string, f?: (p: Record<string, string>) => string) =>
    (data ?? []).reduce((a, r) => a.plus(D(f ? f(r.position) : r.position[k])), D(0)).toFixed(3);
  const transfers = (p: Record<string, string>) => D(p.transfer_in).minus(D(p.transfer_out)).toFixed(3);
  const commitments = (p: Record<string, string>) => D(p.commitment).plus(D(p.reservation)).toFixed(3);

  return (
    <div>
      <PageHeader title="تقرير تنفيذ الباب الثاني" subtitle={`السنة المالية ${year!.year} — أساس الرقابة: ${({ AUTHORIZATION: "التفويض", APPROPRIATION: "الاعتماد", TWO_LEVEL: "مستويان (الاعتماد ثم التفويض)" } as any)[year!.control_basis]}`}
        actions={can("reports.export") && <>
          <button className="btn-outline" onClick={() => download("/reports/RPT-02/export", { fiscal_year_id: year!.id }, { format: "pdf" })}>PDF</button>
          <button className="btn-outline" onClick={() => download("/reports/RPT-02/export", { fiscal_year_id: year!.id }, { format: "xlsx" })}>Excel</button>
          <button className="btn-outline" onClick={() => window.print()}>طباعة</button>
        </>} />
      <div className="card overflow-x-auto">
        {!data ? <Spinner /> : (
          <table className="w-full">
            <thead className="sticky top-0"><tr>
              {["البند", "الاعتماد", "المناقلات", "الارتباطات", "المصروف", "الرصيد", "نسبة التنفيذ", "الحالة"].map((h, i) =>
                <th key={h} className={`th ${i > 0 && i < 7 ? "text-end" : ""}`}>{h}</th>)}
            </tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.item_code + (r.budget_line_id ?? "")} className={r.budget_line_id ? "cursor-pointer hover:bg-brand-50" : "text-ink-mute"}
                    onClick={() => r.budget_line_id && nav(`/budget/lines/${r.budget_line_id}`)}>
                  <td className="td"><span className="font-bold">{r.item_code}</span> {r.item_name}</td>
                  <td className="td text-end"><Money v={r.position.control_base} /></td>
                  <td className="td text-end"><Money v={transfers(r.position)} /></td>
                  <td className="td text-end"><Money v={commitments(r.position)} /></td>
                  <td className="td text-end"><Money v={r.position.actual} /></td>
                  <td className="td text-end"><Money v={r.position.available} strong /></td>
                  <td className="td text-end">
                    {r.position.actual_rate == null ? "—" : (
                      <div className="flex items-center justify-end gap-2">
                        <span className="num text-xs">{pct(r.position.actual_rate)}</span>
                        <span className="h-1.5 w-16 overflow-hidden rounded bg-line">
                          <span className={`block h-full ${D(r.position.actual_rate).gt(100) ? "bg-bad" : D(r.position.actual_rate).gte(80) ? "bg-warn" : "bg-ok"}`}
                                style={{ width: `${Math.min(100, Number(r.position.actual_rate))}%` }} />
                        </span>
                      </div>
                    )}
                  </td>
                  <td className="td"><Badge tone={LEVEL[r.level]?.tone}>{LEVEL[r.level]?.label ?? r.level}</Badge></td>
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-brand-50 font-bold"><tr>
              <td className="td">إجمالي الباب</td>
              <td className="td text-end"><Money v={sum("control_base")} /></td>
              <td className="td text-end"><Money v={sum("", transfers)} /></td>
              <td className="td text-end"><Money v={sum("", commitments)} /></td>
              <td className="td text-end"><Money v={sum("actual")} /></td>
              <td className="td text-end"><Money v={sum("available")} /></td>
              <td className="td" colSpan={2} />
            </tr></tfoot>
          </table>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-mute">الاعتماد = أساس الرقابة. الرصيد = الاعتماد + المناقلات − المصروف − الارتباطات والحجوزات. انقر أي بند لعرض مكوناته وتسلسل حركاته. <Link className="underline" to="/reports">كل التقارير</Link></p>
    </div>
  );
}
