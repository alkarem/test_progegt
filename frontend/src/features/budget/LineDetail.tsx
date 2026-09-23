import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { Badge, ErrorBox, Field, Money, PageHeader, Spinner } from "@/components/ui";
import { LEVEL, SOURCE_PATH, TXN_TYPE, fmtDate } from "@/lib/labels";
import { D, MONEY_RE, normalizeMoneyInput, pct } from "@/lib/money";

export function LineDetail() {
  const { id } = useParams();
  const line = useQuery({ queryKey: ["line", id], queryFn: () => api(`/budget-lines/${id}`) });
  const tl = useQuery({ queryKey: ["timeline", id], queryFn: () => api<any[]>(`/budget-lines/${id}/timeline`) });
  const [amount, setAmount] = useState("");
  const [check, setCheck] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  if (!line.data) return <Spinner />;
  const l = line.data, p = l.position;
  const runCheck = async () => {
    const v = normalizeMoneyInput(amount);
    if (!MONEY_RE.test(v)) { setErr({ message: "أدخل مبلغًا صحيحًا (ثلاث خانات عشرية كحد أقصى)." }); return; }
    setErr(null);
    try { setCheck(await api(`/budget-lines/${id}/check`, { body: { amount: v } })); } catch (e) { setErr(e); }
  };
  const steps: [string, string, string][] = [
    [p.control_basis === "APPROPRIATION" ? "الاعتماد" : "المفوَّض (التفويضات)", p.control_base, ""],
    ["+ مناقلات واردة", p.transfer_in, ""], ["− مناقلات صادرة", p.transfer_out, "neg"],
    ["= الاعتماد بعد المناقلات", p.adjusted_budget, "sum"], ["− المصروف الفعلي", p.actual, "neg"],
    ["= الرصيد الدفتري", p.book_balance, "sum"], ["− الارتباطات القائمة", p.commitment, "neg"],
    ["− الحجوزات المبدئية", p.reservation, "neg"], ["= الرصيد المتاح", p.available, "total"],
  ];
  return (
    <div>
      <PageHeader title={`${l.item_code} ${l.item_name}`} subtitle={<>السنة المالية {l.fiscal_year} · <Badge tone={LEVEL[l.level]?.tone}>{LEVEL[l.level]?.label}</Badge></>}
                  actions={<Link className="btn-outline" to="/budget">← موقف الباب</Link>} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        {[["الاعتماد الأصلي", p.appropriation], ["المفوَّض", p.allocation], ["مناقلات واردة", p.transfer_in], ["مناقلات صادرة", p.transfer_out],
          ["الارتباطات", p.commitment], ["المصروف", p.actual], ["المتاح", p.available]].map(([k, v]) => (
          <div key={k} className={`card p-3 ${k === "المتاح" ? (D(v).isNeg() ? "border-bad" : "border-ok") : ""}`}>
            <div className="text-xs font-bold text-ink-mute">{k}</div>
            <div className="mt-1 font-bold"><Money v={v} /></div>
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <section className="card p-4">
          <h2 className="mb-2 font-bold">تكوين الرصيد</h2>
          <table className="w-full text-sm"><tbody>
            {steps.map(([k, v, s]) => (
              <tr key={k} className={s === "total" ? "border-t-2 border-brand font-bold" : s === "sum" ? "border-t border-line font-bold" : ""}>
                <td className="py-1">{k}</td><td className="py-1 text-end"><Money v={v} /></td>
              </tr>
            ))}
            {p.unallocated != null && <tr className="text-ink-mute"><td className="pt-3">الاعتماد غير المفوَّض بعد</td><td className="pt-3 text-end"><Money v={p.unallocated} /></td></tr>}
            <tr className="text-ink-mute"><td className="pt-1">نسبة التنفيذ / الاستخدام</td><td className="pt-1 text-end num">{pct(p.actual_rate)} / {pct(p.utilization_rate)}</td></tr>
          </tbody></table>
          <div className="mt-4 border-t border-line pt-3">
            <Field label="فحص توفر الاعتماد لمبلغ">
              <div className="flex gap-2">
                <input className="input num" dir="ltr" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.000" />
                <button className="btn-outline" onClick={runCheck}>فحص</button>
              </div>
            </Field>
            <ErrorBox error={err} />
            {check && (
              <div className={`mt-2 rounded-md p-2 text-sm ${check.ok ? "bg-ok-50 text-ok" : "bg-bad-50 text-bad"}`}>
                {check.ok ? "الاعتماد المتاح كافٍ." : check.message}
                <div className="text-ink">المتاح <Money v={check.available} /> · المطلوب <Money v={check.requested} /> · العجز <Money v={check.shortfall} /></div>
              </div>
            )}
          </div>
        </section>
        <section className="card lg:col-span-2">
          <h2 className="border-b border-line p-4 font-bold">التسلسل الزمني لكل الحركات</h2>
          {!tl.data ? <Spinner /> : (
            <ol className="relative max-h-[560px] overflow-y-auto p-4">
              {tl.data.map((e) => (
                <li key={e.id} className="relative border-s-2 border-line ps-4 pb-3">
                  <span className={`absolute -start-[7px] top-1 h-3 w-3 rounded-full ${e.signed_amount.startsWith("-") ? "bg-warn" : "bg-brand"}`} />
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-sm">
                      <span className="num text-ink-mute">{fmtDate(e.entry_date)}</span>{" "}
                      {e.date_is_estimated && <Badge tone="warn">تاريخ تقديري</Badge>}{" "}
                      <b>{TXN_TYPE[e.txn_type] ?? e.txn_type}</b>{" "}
                      {e.document_no && (SOURCE_PATH[e.source_type]
                        ? <Link className="text-brand underline" to={`/${SOURCE_PATH[e.source_type]}/${e.source_id}`}>{e.document_no}</Link>
                        : <span>{e.document_no}</span>)}
                      {e.is_historical_exception && <Badge tone="mute">مستورد</Badge>}
                      {e.override_grant_id && <Badge tone="bad">منحة استثناء</Badge>}
                    </div>
                    <div className="text-sm"><Money v={e.signed_amount} strong /> <span className="text-ink-mute">← متاح</span> <Money v={e.available_after} /></div>
                  </div>
                  {e.description && <div className="text-xs text-ink-mute">{e.description}</div>}
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}
