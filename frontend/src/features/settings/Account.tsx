import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { Badge, ErrorBox, Field, PageHeader, Table } from "@/components/ui";
import { ROLE, fmtDateTime } from "@/lib/labels";

function Delegations() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["delegations"], queryFn: () => api<any[]>("/delegations") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<any>("/users", { params: { page_size: 200 } }), enabled: can("users.view") });
  const [f, setF] = useState({ delegate_id: "", valid_from: new Date().toISOString().slice(0, 16), valid_to: "", reason: "" });
  const [err, setErr] = useState<unknown>(null);
  const name = (id: string) => users.data?.items.find((u: any) => u.id === id)?.full_name ?? id.slice(0, 8);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api("/delegations", { body: { ...f, valid_from: new Date(f.valid_from).toISOString(), valid_to: new Date(f.valid_to).toISOString() } });
      qc.invalidateQueries({ queryKey: ["delegations"] }); setErr(null);
    } catch (x) { setErr(x); }
  };
  const revoke = async (id: string) => { try { await api(`/delegations/${id}/revoke`, { method: "POST" }); qc.invalidateQueries({ queryKey: ["delegations"] }); } catch (x) { setErr(x); } };
  return (
    <section className="card mt-4">
      <h2 className="border-b border-line p-3 font-bold">التفويض المؤقت لصلاحيات الموافقة</h2>
      {users.data && <form onSubmit={submit} className="grid gap-3 border-b border-line p-3 md:grid-cols-5">
        <Field label="إلى"><select className="input" value={f.delegate_id} onChange={(e) => setF({ ...f, delegate_id: e.target.value })} required><option value="">—</option>{users.data.items.filter((u: any) => u.is_active).map((u: any) => <option key={u.id} value={u.id}>{u.full_name}</option>)}</select></Field>
        <Field label="من"><input type="datetime-local" className="input" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} required /></Field>
        <Field label="إلى"><input type="datetime-local" className="input" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} required /></Field>
        <Field label="السبب"><input className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} required minLength={5} /></Field>
        <div className="flex items-end"><button className="btn-primary">تفويض</button></div>
      </form>}
      <ErrorBox error={err} />
      <Table rows={list.data} empty="لا توجد تفويضات." cols={[
        { key: "delegator_id", title: "المفوِّض", render: (r: any) => name(r.delegator_id) },
        { key: "delegate_id", title: "المفوَّض إليه", render: (r: any) => name(r.delegate_id) },
        { key: "valid", title: "المدة", render: (r: any) => <span className="num text-xs">{fmtDateTime(r.valid_from)} — {fmtDateTime(r.valid_to)}</span> },
        { key: "reason", title: "السبب" },
        { key: "st", title: "", render: (r: any) => r.revoked_at ? <Badge>ملغى</Badge> : <button className="btn-ghost px-2 text-xs" onClick={() => revoke(r.id)}>إلغاء</button> },
      ]} />
    </section>
  );
}

export function Account() {
  const { me, can } = useAuth();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<any[]>("/auth/sessions") });
  const end = async (id: string) => { await api(`/auth/sessions/${id}`, { method: "DELETE" }); qc.invalidateQueries({ queryKey: ["sessions"] }); };
  return (
    <div>
      <PageHeader title="حسابي" actions={<Link className="btn-outline" to="/account/password">تغيير كلمة المرور</Link>} />
      <section className="card p-4 text-sm">
        <div className="font-bold">{me?.full_name} <span dir="ltr" className="text-ink-mute">({me?.username})</span></div>
        <div className="mt-1">الأدوار: {me?.roles.map((r) => ROLE[r] ?? r).join("، ")}</div>
        {me && Object.keys(me.scopes).length > 0 && <div className="mt-1"><Badge tone="warn">صلاحيات مقيدة بنطاق</Badge></div>}
      </section>
      <section className="card mt-4">
        <h2 className="border-b border-line p-3 font-bold">الجلسات النشطة</h2>
        <Table rows={sessions.data} cols={[
          { key: "issued_at", title: "بدأت", render: (r: any) => <span className="num">{fmtDateTime(r.issued_at)}</span> },
          { key: "last_used_at", title: "آخر استخدام", render: (r: any) => <span className="num">{fmtDateTime(r.last_used_at)}</span> },
          { key: "ip", title: "IP", render: (r: any) => <span className="num">{r.ip}</span> },
          { key: "user_agent", title: "المتصفح", render: (r: any) => <span dir="ltr" className="text-xs">{r.user_agent?.slice(0, 60)}</span> },
          { key: "a", title: "", render: (r: any) => r.current ? <Badge tone="ok">الحالية</Badge> : <button className="btn-ghost px-2 text-xs" onClick={() => end(r.id)}>إنهاء</button> },
        ]} />
      </section>
      {can("workflow.inbox") && <Delegations />}
    </div>
  );
}
