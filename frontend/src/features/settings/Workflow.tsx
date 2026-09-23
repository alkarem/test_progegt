import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { Badge, ErrorBox, Money, PageHeader, Spinner } from "@/components/ui";
import { SOURCE_LABEL } from "@/lib/labels";
import { normalizeMoneyInput } from "@/lib/money";
import { MoneyInput } from "@/features/documents/common";

function StepRow({ s, manage }: { s: any; manage: boolean }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ min_amount: s.min_amount ?? "", max_amount: s.max_amount ?? "", sla_days: s.sla_days });
  const [err, setErr] = useState<unknown>(null);
  const save = async () => {
    try {
      await api(`/workflow-steps/${s.id}`, { method: "PATCH", body: { min_amount: f.min_amount ? normalizeMoneyInput(f.min_amount) : null, max_amount: f.max_amount ? normalizeMoneyInput(f.max_amount) : null, sla_days: f.sla_days } });
      qc.invalidateQueries({ queryKey: ["wf-defs"] }); setEdit(false); setErr(null);
    } catch (x) { setErr(x); }
  };
  return (
    <tr>
      <td className="td num">{s.seq}</td>
      <td className="td">{s.name} {s.runs_budget_check && <Badge tone="brand">فحص رصيد</Badge>} {s.posts && <Badge tone="ok">يرحّل</Badge>}</td>
      {edit ? <>
        <td className="td w-40"><MoneyInput value={f.min_amount} onChange={(v) => setF({ ...f, min_amount: v })} /></td>
        <td className="td w-40"><MoneyInput value={f.max_amount} onChange={(v) => setF({ ...f, max_amount: v })} /></td>
        <td className="td w-24"><input type="number" className="input num" min={1} max={90} value={f.sla_days} onChange={(e) => setF({ ...f, sla_days: Number(e.target.value) })} /></td>
        <td className="td"><button className="btn-primary px-2 text-xs" onClick={save}>حفظ</button><ErrorBox error={err} /></td>
      </> : <>
        <td className="td">{s.min_amount ? <Money v={s.min_amount} /> : "—"}</td>
        <td className="td">{s.max_amount ? <Money v={s.max_amount} /> : "—"}</td>
        <td className="td num">{s.sla_days}</td>
        <td className="td">{manage && <button className="btn-ghost px-2 text-xs" onClick={() => setEdit(true)}>تعديل</button>}</td>
      </>}
    </tr>
  );
}

export function WorkflowSettings() {
  const { can } = useAuth();
  const { data } = useQuery({ queryKey: ["wf-defs"], queryFn: () => api<any[]>("/workflow-definitions") });
  if (!data) return <Spinner />;
  return (
    <div>
      <PageHeader title="مسارات الموافقة" subtitle="المراحل وترتيبها ثابتة لضمان الرقابة؛ يمكن ضبط شرائح المبالغ (تُطبق المرحلة فقط إذا وقع المبلغ في الشريحة) ومهلة كل مرحلة بالأيام." />
      <div className="space-y-4">
        {data.map((d) => (
          <section key={d.id} className="card">
            <h2 className="border-b border-line p-3 font-bold">{d.name} <span className="text-sm font-normal text-ink-mute">({SOURCE_LABEL[d.source_type] ?? d.source_type}{d.separate_approvers && " · معتمد مختلف لكل مرحلة"})</span></h2>
            <table className="w-full text-sm"><thead><tr><th className="th">#</th><th className="th">المرحلة</th><th className="th">من مبلغ</th><th className="th">إلى مبلغ</th><th className="th">المهلة (يوم)</th><th className="th" /></tr></thead>
              <tbody>{d.steps.map((s: any) => <StepRow key={s.id} s={s} manage={can("workflow.manage")} />)}</tbody></table>
          </section>
        ))}
      </div>
    </div>
  );
}
