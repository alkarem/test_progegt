import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { Badge, ErrorBox, Field, Modal, PageHeader, Spinner, Table, useToast } from "@/components/ui";
import { fmtDateTime } from "@/lib/labels";

const size = (b: number | null) => b == null ? "—" : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${(b / 1e3).toFixed(0)} KB`;

export function Backup() {
  const qc = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: ["backup-settings"], queryFn: () => api<any>("/backup/settings") });
  const list = useQuery({ queryKey: ["backups"], queryFn: () => api<any[]>("/backups") });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<any>("/health/db") });
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [restore, setRestore] = useState<any>(null);
  const [confirm, setConfirm] = useState("");
  useEffect(() => { if (settings.data) setS(settings.data); }, [settings.data]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["backups"] }); qc.invalidateQueries({ queryKey: ["health"] }); };
  const wrap = (fn: () => Promise<unknown>, ok: string) => async () => {
    setBusy(true); setErr(null);
    try { await fn(); toast(ok); refresh(); } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  const h = health.data;
  return (
    <div>
      <PageHeader title="النسخ الاحتياطي وصحة النظام" actions={<button className="btn-primary" disabled={busy} onClick={wrap(() => api("/backup/run", { method: "POST" }), "اكتملت النسخة الاحتياطية")}>{busy ? "جارٍ…" : "نسخة احتياطية الآن"}</button>} />
      <ErrorBox error={err} />
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card p-4">
          <h2 className="mb-3 font-bold">الإعدادات</h2>
          {!s ? <Spinner /> : <div className="grid gap-3 md:grid-cols-2">
            <Field label="المسار" className="md:col-span-2"><input className="input" dir="ltr" value={s.location} onChange={(e) => setS({ ...s, location: e.target.value })} /></Field>
            <Field label="وقت النسخ اليومي"><input type="time" className="input" value={s.daily_time} onChange={(e) => setS({ ...s, daily_time: e.target.value })} /></Field>
            <Field label="عدد النسخ المحتفظ بها"><input type="number" className="input num" min={1} max={365} value={s.retention_count} onChange={(e) => setS({ ...s, retention_count: Number(e.target.value) })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} />النسخ اليومي مفعّل</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.include_attachments} onChange={(e) => setS({ ...s, include_attachments: e.target.checked })} />تضمين المرفقات</label>
            <div className="md:col-span-2"><button className="btn-outline" onClick={wrap(() => api("/backup/settings", { method: "PUT", body: s }), "حُفظت الإعدادات")}>حفظ الإعدادات</button></div>
          </div>}
        </section>
        <section className="card p-4">
          <h2 className="mb-3 font-bold">صحة قاعدة البيانات</h2>
          {!h ? <Spinner /> : <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-ink-mute">حجم القاعدة</dt><dd className="num">{h.database_size}</dd>
            <dt className="text-ink-mute">الاتصالات</dt><dd className="num">{h.connections}</dd>
            <dt className="text-ink-mute">آخر نسخة ناجحة</dt><dd className="num">{h.last_backup ? fmtDateTime(h.last_backup.at) : <Badge tone="bad">لا توجد</Badge>}</dd>
            <dt className="text-ink-mute">مطابقة الأرصدة للقيود</dt><dd>{h.balances_reconciled ? <Badge tone="ok">مطابقة</Badge> : <Badge tone="bad">غير مطابقة</Badge>}</dd>
            <dt className="text-ink-mute">تشفير النسخ</dt><dd>{h.encryption_configured ? <Badge tone="ok">مفعّل</Badge> : <Badge tone="warn">غير مهيأ</Badge>}</dd>
          </dl>}
        </section>
      </div>
      <section className="card mt-4">
        <h2 className="border-b border-line p-3 font-bold">سجل النسخ</h2>
        <Table rows={list.data} empty="لا توجد نسخ." cols={[
          { key: "started_at", title: "الوقت", render: (r: any) => <span className="num">{fmtDateTime(r.started_at)}</span> },
          { key: "kind", title: "النوع", render: (r: any) => r.kind === "MANUAL" ? "يدوية" : r.kind === "SCHEDULED" ? "مجدولة" : r.kind === "PRE_RESTORE" ? "قبل الاستعادة" : r.kind },
          { key: "status", title: "الحالة", render: (r: any) => <Badge tone={r.status === "SUCCEEDED" ? "ok" : r.status === "FAILED" ? "bad" : "brand"}>{r.status}</Badge> },
          { key: "file_name", title: "الملف", render: (r: any) => <span dir="ltr" className="font-mono text-xs">{r.file_name}</span> },
          { key: "size", title: "الحجم", render: (r: any) => <span className="num">{size(r.size_bytes)}</span> },
          { key: "enc", title: "مشفرة", render: (r: any) => r.encrypted ? "نعم" : "لا" },
          { key: "verified", title: "التحقق", render: (r: any) => r.verified_at ? <Badge tone={r.verification?.ok ? "ok" : "bad"}>{r.verification?.ok ? "سليمة" : "تالفة"}</Badge> : "—" },
          { key: "a", title: "", render: (r: any) => r.status === "SUCCEEDED" && <span className="flex gap-1">
            <button className="btn-ghost px-2 text-xs" onClick={wrap(() => api(`/backups/${r.id}/verify`, { method: "POST" }), "اكتمل التحقق")}>تحقق</button>
            <button className="btn-ghost px-2 text-xs text-bad" onClick={() => { setRestore(r); setConfirm(""); }}>استعادة</button></span> },
        ]} />
      </section>
      <Modal open={!!restore} onClose={() => setRestore(null)} title="استعادة نسخة احتياطية">
        <div className="space-y-3 text-sm">
          <p className="text-bad">الاستعادة تستبدل قاعدة البيانات الحالية بالكامل بمحتوى النسخة <span dir="ltr" className="font-mono">{restore?.file_name}</span>. تُؤخذ نسخة أمان تلقائيًا قبل الاستعادة، وتُنهى جلسات جميع المستخدمين.</p>
          <Field label={`للتأكيد اكتب اسم قاعدة البيانات: ${h?.database_name ?? ""}`}><input className="input" dir="ltr" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
          <button className="btn-danger" disabled={!confirm || confirm !== h?.database_name || busy} onClick={wrap(async () => { await api(`/backups/${restore.id}/restore`, { body: { confirm } }); setRestore(null); }, "اكتملت الاستعادة")}>استعادة</button>
        </div>
      </Modal>
    </div>
  );
}
