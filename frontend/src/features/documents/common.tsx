import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, downloadGet } from "@/api/client";
import { useAuth } from "@/app/auth";
import { Badge, ErrorBox, Field, Modal, Money, Spinner, useToast } from "@/components/ui";
import { DOC_STATUS, DOC_STATUS_TONE, SOURCE_PATH, fmtDate, fmtDateTime } from "@/lib/labels";
import { MONEY_RE, normalizeMoneyInput } from "@/lib/money";
import { useItems, useSuppliers } from "@/lib/hooks";

export const StatusBadge = ({ s }: { s: string }) => <Badge tone={DOC_STATUS_TONE[s]}>{DOC_STATUS[s] ?? s}</Badge>;

export function MoneyInput({ value, onChange, required, id }: { value: string; onChange: (v: string) => void; required?: boolean; id?: string }) {
  const bad = value !== "" && !MONEY_RE.test(normalizeMoneyInput(value));
  return (
    <input id={id} className={`input num ${bad ? "border-bad" : ""}`} dir="ltr" inputMode="decimal" placeholder="0.000" value={value}
           required={required} aria-invalid={bad} onChange={(e) => onChange(e.target.value)}
           onBlur={() => { const v = normalizeMoneyInput(value); if (MONEY_RE.test(v)) onChange(v); }} />
  );
}

export function ItemSelect({ value, onChange, required }: { value: string; onChange: (v: string) => void; required?: boolean }) {
  const { data = [] } = useItems();
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)} required={required}>
      <option value="">— اختر البند —</option>
      {data.filter((i) => i.is_postable && i.is_active).map((i) => <option key={i.id} value={i.id}>{i.code} {i.name}</option>)}
    </select>
  );
}

export function SupplierPicker({ value, onChange }: { value: string | null; onChange: (id: string | null, name?: string) => void }) {
  const [q, setQ] = useState("");
  const { data } = useSuppliers(q);
  const toast = useToast();
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const create = async () => {
    try {
      const s = await api<{ id: string; name: string }>("/suppliers", { body: { name: q } });
      onChange(s.id, s.name); toast(`أُضيف المورد «${s.name}»`); qc.invalidateQueries({ queryKey: ["suppliers"] }); setErr(null);
    } catch (e) { setErr(e); }
  };
  return (
    <div>
      <div className="flex gap-2">
        <input className="input" placeholder="ابحث باسم المورد/المستفيد" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">— بلا —</option>
          {data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      {q.length > 2 && !data?.items.length && <button type="button" className="btn-ghost mt-1 px-1 text-xs" onClick={create}>+ إضافة «{q}» موردًا جديدًا</button>}
      <ErrorBox error={err} />
    </div>
  );
}

export type Line = { item_id: string; amount: string };

export function LinesEditor({ lines, setLines, title = "البنود" }: { lines: Line[]; setLines: (l: Line[]) => void; title?: string }) {
  const upd = (i: number, patch: Partial<Line>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div>
      <div className="label">{title}</div>
      <table className="w-full"><tbody>
        {lines.map((l, i) => (
          <tr key={i}>
            <td className="py-1 pe-2"><ItemSelect value={l.item_id} onChange={(v) => upd(i, { item_id: v })} required /></td>
            <td className="w-44 py-1 pe-2"><MoneyInput value={l.amount} onChange={(v) => upd(i, { amount: v })} required /></td>
            <td className="w-10 py-1"><button type="button" className="btn-ghost px-2" aria-label="حذف السطر" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button></td>
          </tr>
        ))}
      </tbody></table>
      <button type="button" className="btn-ghost mt-1" onClick={() => setLines([...lines, { item_id: "", amount: "" }])}>+ سطر</button>
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = { SUBMIT: "تقديم", APPROVE: "اعتماد", REJECT: "رفض", RETURN: "إرجاع", CANCEL: "إلغاء", POST: "اعتماد نهائي وترحيل", SKIP: "تخطي" };
const STEP_PERM: Record<string, string> = { FINANCIAL_REVIEW: "review", BUDGET_CONTROL: "control", SUPERVISOR_APPROVAL: "supervise", FINAL_APPROVAL: "approve" };
const PREFIX: Record<string, string> = { budget_document: "budget_documents", authorization: "authorizations", transfer: "transfers", commitment: "commitments", expenditure: "expenditures", adjustment: "adjustments" };

export function WorkflowPanel({ type, doc, onChange }: { type: string; doc: { id: string; status: string; created_by: string }; onChange: () => void }) {
  const { me, can } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const wf = useQuery({ queryKey: ["wf", type, doc.id, doc.status], queryFn: () => api(`/documents/${type}/${doc.id}/workflow`) });
  const hist = useQuery({ queryKey: ["hist", type, doc.id, doc.status], queryFn: () => api<any[]>(`/documents/${type}/${doc.id}/history`) });
  const grants = useQuery({ queryKey: ["grants"], queryFn: () => api<any[]>("/override-grants"), enabled: can(`${PREFIX[type]}.approve`) });
  const [comment, setComment] = useState("");
  const [ack, setAck] = useState(false);
  const [grant, setGrant] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [revOpen, setRevOpen] = useState(false);
  const act = useMutation({
    mutationFn: (action: string) => api(`/documents/${type}/${doc.id}/${action}`, {
      body: { comment: comment || null, acknowledge_shortfall: ack, override_grant_id: grant || null, expected_step: action === "approve" ? wf.data?.current_step : null },
    }),
    onSuccess: (_, action) => { toast(`تم: ${ACTION_LABEL[action.toUpperCase()] ?? action}`); setComment(""); setErr(null); onChange(); },
    onError: (e) => setErr(e),
  });
  const step = wf.data?.current_step as string | undefined;
  const mine = me?.id === doc.created_by;
  const canStep = step && can(`${PREFIX[type]}.${STEP_PERM[step]}`) && !mine;
  const editable = ["DRAFT", "RETURNED"].includes(doc.status);
  const activeGrants = (grants.data ?? []).filter((g) => g.user_id === me?.id && !g.revoked_at && new Date(g.valid_to) > new Date());

  return (
    <section className="card p-4">
      <h2 className="mb-2 font-bold">دورة الموافقة</h2>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge s={doc.status} />
        {step && <span>المرحلة الحالية: <b>{wf.data?.current_step_name}</b></span>}
        {wf.data?.round > 1 && <Badge tone="warn">الجولة {wf.data.round}</Badge>}
      </div>
      {(editable || step) && (
        <div className="space-y-2">
          <Field label="تعليق (إلزامي للرفض والإرجاع والإلغاء)"><textarea className="input" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
          {step === "BUDGET_CONTROL" && canStep && (
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              تمرير رغم العجز لطلب منحة استثناء من المعتمد النهائي (يتطلب تعليقًا)</label>
          )}
          {step === "FINAL_APPROVAL" && canStep && activeGrants.length > 0 && (
            <Field label="منحة استثناء (عند العجز فقط)">
              <select className="input" value={grant} onChange={(e) => setGrant(e.target.value)}>
                <option value="">— بدون —</option>
                {activeGrants.map((g) => <option key={g.id} value={g.id}>متبقٍ {g.max_amount} − {g.used_amount} حتى {fmtDate(g.valid_to)}</option>)}
              </select>
            </Field>
          )}
          <ErrorBox error={err} />
          <div className="flex flex-wrap gap-2">
            {editable && mine && can(`${PREFIX[type]}.submit`) && <button className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("submit")}>تقديم للاعتماد</button>}
            {canStep && <button className="btn-ok" disabled={act.isPending} onClick={() => act.mutate("approve")}>{step === "FINAL_APPROVAL" ? "اعتماد نهائي وترحيل" : "اعتماد المرحلة"}</button>}
            {canStep && <button className="btn-outline" disabled={act.isPending} onClick={() => act.mutate("return")}>إرجاع للتعديل</button>}
            {canStep && <button className="btn-danger" disabled={act.isPending} onClick={() => act.mutate("reject")}>رفض</button>}
            {((editable && mine) || (step && can(`${PREFIX[type]}.cancel`))) && <button className="btn-ghost" disabled={act.isPending} onClick={() => act.mutate("cancel")}>إلغاء المستند</button>}
          </div>
          {mine && step && <p className="text-xs text-ink-mute">لا يعتمد المستند منشئه (فصل المهام).</p>}
        </div>
      )}
      {doc.status === "POSTED" && can("adjustments.create") && type !== "adjustment" && (
        <button className="btn-outline mt-2" onClick={() => setRevOpen(true)}>إنشاء قيد عكسي…</button>
      )}
      <ReversalModal open={revOpen} onClose={() => setRevOpen(false)} type={type} id={doc.id} onDone={(adjId) => nav(`/adjustments/${adjId}`)} />
      <h3 className="mb-1 mt-4 text-sm font-bold">السجل</h3>
      {!hist.data ? <Spinner /> : !hist.data.length ? <p className="text-xs text-ink-mute">لم يُقدَّم بعد.</p> : (
        <ol className="space-y-1.5 text-xs">
          {hist.data.map((h) => (
            <li key={h.id} className="rounded bg-surface p-2">
              <b>{ACTION_LABEL[h.action] ?? h.action}</b> {h.step && `— ${h.step}`} · {h.actor_name} {h.on_behalf_of && "(نيابة)"} · <span className="num">{fmtDateTime(h.acted_at)}</span>
              {h.comment && <div className="mt-0.5 text-ink-soft">«{h.comment}»</div>}
              {h.budget_check && h.budget_check.ok === false && <div className="text-bad">فحص الرصيد: {h.budget_check.message}</div>}
              {h.budget_check?.override_used && h.budget_check.override_used !== "0.000" && <div className="text-bad">استُخدمت منحة استثناء: <Money v={h.budget_check.override_used} /></div>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function ReversalModal({ open, onClose, type, id, onDone }: { open: boolean; onClose: () => void; type: string; id: string; onDone: (id: string) => void }) {
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const submit = async () => {
    try { const a = await api("/reversals", { body: { source_type: type, source_id: id, adjustment_date: date, reason } }); onDone(a.id); }
    catch (e) { setErr(e); }
  };
  return (
    <Modal open={open} onClose={onClose} title="قيد عكسي كامل للمستند">
      <p className="mb-3 text-sm text-ink-soft">لا يُحذف المستند المرحّل ولا يُعدَّل. القيد العكسي مستند مستقل يمر بدورة الموافقة، ويعكس كل قيود المستند.</p>
      <div className="space-y-3">
        <Field label="تاريخ القيد العكسي"><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <Field label="السبب"><textarea className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <ErrorBox error={err} />
        <button className="btn-primary" onClick={submit} disabled={reason.trim().length < 5}>إنشاء مسودة القيد العكسي</button>
      </div>
    </Modal>
  );
}

export function Attachments({ type, id, locked }: { type: string; id: string; locked?: boolean }) {
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const ref = useRef<HTMLInputElement>(null);
  const [cat, setCat] = useState("INVOICE");
  const [err, setErr] = useState<unknown>(null);
  const { data } = useQuery({ queryKey: ["att", type, id], queryFn: () => api<any[]>("/attachments", { params: { source_type: type, source_id: id } }) });
  const upload = async (f: File) => {
    const form = new FormData();
    form.append("file", f); form.append("category", cat); form.append("source_type", type); form.append("source_id", id);
    try { await api("/attachments", { form }); toast("رُفع المرفق"); setErr(null); qc.invalidateQueries({ queryKey: ["att", type, id] }); }
    catch (e) { setErr(e); }
  };
  return (
    <section className="card p-4">
      <h2 className="mb-2 font-bold">المستندات المؤيدة</h2>
      {!data ? <Spinner /> : !data.length ? <p className="mb-2 text-xs text-warn">لا توجد مرفقات.</p> : (
        <ul className="mb-2 space-y-1 text-sm">
          {data.map((a) => (
            <li key={a.id} className="flex items-center justify-between gap-2">
              <button className="truncate text-brand underline" onClick={() => downloadGet(`/attachments/${a.id}/download`)}>{a.original_filename}</button>
              <span className="text-xs text-ink-mute">{(a.size_bytes / 1024).toFixed(0)} ك.ب {a.is_locked && "🔒"}</span>
            </li>
          ))}
        </ul>
      )}
      {can("documents.upload") && (
        <div className="flex gap-2">
          <select className="input w-auto text-xs" value={cat} onChange={(e) => setCat(e.target.value)}>
            {Object.entries({ INVOICE: "فاتورة", PAYMENT_ORDER: "أمر صرف", DECISION: "قرار", CONTRACT: "عقد", AUTHORIZATION_LETTER: "كتاب تفويض", CHEQUE: "شيك", RECEIPT: "إيصال", OTHER: "أخرى" })
              .map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <input ref={ref} type="file" hidden accept=".pdf,.png,.jpg,.jpeg,.xlsx,.docx" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button className="btn-outline text-xs" onClick={() => ref.current?.click()}>+ إرفاق ملف</button>
        </div>
      )}
      {locked && <p className="mt-1 text-xs text-ink-mute">المرفقات مقفلة بعد الترحيل؛ يمكن إضافة مرفق جديد فقط.</p>}
      <ErrorBox error={err} />
    </section>
  );
}

export function DocLayout({ main, side }: { main: ReactNode; side: ReactNode }) {
  return <div className="grid gap-4 lg:grid-cols-3"><div className="space-y-4 lg:col-span-2">{main}</div><div className="space-y-4">{side}</div></div>;
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3">
      {items.map(([k, v]) => <div key={k}><dt className="text-xs text-ink-mute">{k}</dt><dd className="font-bold">{v ?? "—"}</dd></div>)}
    </dl>
  );
}

export const docLink = (type: string, id: string) => `/${SOURCE_PATH[type]}/${id}`;
