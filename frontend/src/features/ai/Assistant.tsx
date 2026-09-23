import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { Badge, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { fmtDateTime } from "@/lib/labels";

type Source = { tool: string; source: string | null; fiscal_year: number | null; as_of: string | null; link: string | null };
type Answer = { id: string; status: string; answer: string; grounding: any; sources: Source[]; raw: any[] | null };

const EXAMPLES = ["كم المتبقي من بند الصيانة؟", "ما إجمالي المصروفات في الربع الأول؟", "ما أكبر المناقلات؟",
  "ما العمليات التي تنتظر الاعتماد؟", "ما البنود التي تجاوز تنفيذها 80%؟", "هل يوجد بند تجاوز الاعتماد؟"];
const STATUS: Record<string, { l: string; t: string }> = {
  OK: { l: "الأرقام متحقق منها", t: "ok" }, UNGROUNDED: { l: "تعذر التحقق من الأرقام", t: "warn" },
  REFUSED: { l: "رُفض السؤال", t: "bad" }, ERROR: { l: "خطأ", t: "bad" },
};

/** عرض نتائج الأدوات جدولًا خامًا عند فشل التحقق من الأرقام (12-ai §2). */
function RawResult({ r }: { r: any }) {
  const lists = Object.entries(r).filter(([, v]) => Array.isArray(v) && (v as any[]).length && typeof (v as any[])[0] === "object") as [string, any[]][];
  const scalars = Object.entries(r).filter(([k, v]) => !Array.isArray(v) && typeof v !== "object" && !["link"].includes(k));
  const objs = Object.entries(r).filter(([, v]) => v && typeof v === "object" && !Array.isArray(v)) as [string, Record<string, any>][];
  return (
    <div className="space-y-2 text-sm" dir="ltr">
      <div className="flex flex-wrap gap-3 text-xs text-ink-soft">{scalars.map(([k, v]) => <span key={k}>{k}: <b className="num">{String(v)}</b></span>)}</div>
      {objs.map(([k, o]) => <table key={k} className="w-full"><tbody>{Object.entries(o).map(([a, b]) => <tr key={a}><td className="td font-mono text-xs">{a}</td><td className="td num">{String(b ?? "—")}</td></tr>)}</tbody></table>)}
      {lists.map(([k, rows]) => (
        <table key={k} className="w-full"><thead><tr>{Object.keys(rows[0]).map((c) => <th key={c} className="th font-mono">{c}</th>)}</tr></thead>
          <tbody>{rows.map((row, i) => <tr key={i}>{Object.keys(rows[0]).map((c) => <td key={c} className="td num">{String(row[c] ?? "—")}</td>)}</tr>)}</tbody></table>
      ))}
    </div>
  );
}

export function Assistant() {
  const { year } = useYear();
  const { can } = useAuth();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api<any>("/ai/status") });
  const history = useQuery({ queryKey: ["ai-history"], queryFn: () => api<any[]>("/ai/history"), enabled: !!status.data?.enabled });
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [answers, setAnswers] = useState<(Answer & { question: string })[]>([]);
  const [summary, setSummary] = useState<any>(null);

  const submit = async (e?: FormEvent, text?: string) => {
    e?.preventDefault();
    const question = (text ?? q).trim();
    if (question.length < 3) return;
    setBusy(true); setErr(null);
    try {
      const a = await api<Answer>("/ai/ask", { body: { question, fiscal_year_id: year!.id } });
      setAnswers((xs) => [{ ...a, question }, ...xs]); setQ("");
      qc.invalidateQueries({ queryKey: ["ai-history"] });
    } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  const draft = async () => {
    setBusy(true); setErr(null);
    try { setSummary(await api("/ai/summary", { body: { fiscal_year_id: year!.id } })); } catch (x) { setErr(x); } finally { setBusy(false); }
  };

  if (!status.data) return <Spinner />;
  if (!status.data.enabled) return (
    <div>
      <PageHeader title="المساعد الذكي" />
      <div className="card p-6 text-sm text-ink-soft">المساعد الذكي غير مفعّل في هذا النظام. يعمل النظام المالي بالكامل دونه؛ ويفعّله مسؤول النظام بضبط <span dir="ltr" className="font-mono">GBCFMS_AI_ENABLED=true</span> ومفتاح الخدمة.</div>
    </div>
  );
  return (
    <div>
      <PageHeader title="المساعد الذكي" subtitle={`يقرأ ويشرح فقط: لا يعتمد ولا يرحّل ولا يعدّل. كل رقم في الإجابة يُتحقق من وجوده في بيانات النظام. السنة: ${year!.year}`}
        actions={can("reports.view") && <button className="btn-outline" onClick={draft} disabled={busy}>مسودة ملخص إداري</button>} />
      <form onSubmit={submit} className="card mb-4 p-4">
        <textarea className="input" rows={2} value={q} onChange={(e) => setQ(e.target.value)} placeholder="اسأل عن الموقف المالي…" maxLength={1000}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button className="btn-primary" disabled={busy || q.trim().length < 3}>{busy ? "جارٍ…" : "اسأل"}</button>
          {EXAMPLES.map((x) => <button type="button" key={x} className="rounded-full bg-surface px-3 py-1 text-xs hover:bg-brand-50" onClick={() => submit(undefined, x)} disabled={busy}>{x}</button>)}
        </div>
        <ErrorBox error={err} />
        {status.data.masking_personal_data && <p className="mt-2 text-xs text-ink-mute">تُرسل إلى الخدمة أرقام ورموز مجمعة فقط؛ أسماء الأشخاص والنصوص الحرة لا تغادر النظام (D-14).</p>}
      </form>

      {summary && <section className="card mb-4 p-4">
        <div className="mb-2 flex items-center gap-2"><h2 className="font-bold">مسودة الملخص الإداري</h2><Badge tone={STATUS[summary.status]?.t}>{STATUS[summary.status]?.l}</Badge><Badge>مسودة — تُراجع قبل الاستخدام</Badge></div>
        {summary.draft ? <p className="whitespace-pre-line leading-7">{summary.draft}</p> : <p className="text-sm text-ink-soft">تعذرت صياغة مسودة بأرقام متحقق منها؛ البيانات المصدرية أدناه.</p>}
        <details className="mt-3"><summary className="cursor-pointer text-sm text-brand">الأرقام المصدرية</summary><RawResult r={summary.data} /></details>
      </section>}

      <div className="space-y-4">
        {answers.map((a) => (
          <section key={a.id} className="card p-4">
            <div className="mb-2 text-sm text-ink-soft">س: {a.question}</div>
            <p className="whitespace-pre-line leading-7">{a.answer}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <Badge tone={STATUS[a.status]?.t}>{STATUS[a.status]?.l}</Badge>
              {a.sources.map((s, i) => (
                <span key={i} className="rounded border border-line px-2 py-0.5">المصدر: {s.source}{s.fiscal_year && ` · ${s.fiscal_year}`}{s.as_of && ` · حتى ${s.as_of}`}
                  {s.link && <Link className="ms-1 text-brand underline" to={s.link}>فتح</Link>}</span>
              ))}
            </div>
            {a.raw && <div className="mt-3 space-y-3">{a.raw.map((r, i) => <RawResult key={i} r={r} />)}</div>}
          </section>
        ))}
      </div>

      {history.data && history.data.length > 0 && <section className="card mt-6">
        <h2 className="border-b border-line p-3 font-bold">أسئلتي السابقة</h2>
        {history.data.map((h) => (
          <div key={h.id} className="border-b border-line p-3 text-sm last:border-0">
            <div className="flex justify-between"><span className="font-bold">{h.question}</span><span className="num text-xs text-ink-mute">{fmtDateTime(h.created_at)}</span></div>
            {h.answer && <div className="mt-1 text-ink-soft">{h.answer}</div>}
          </div>
        ))}
      </section>}
    </div>
  );
}
