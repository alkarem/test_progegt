import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { Badge, ErrorBox, Field, Modal, PageHeader, Table, useToast } from "@/components/ui";
import { ROLE, fmtDateTime } from "@/lib/labels";
import { useItems } from "@/lib/hooks";
import { useYear } from "@/app/year";

type U = { id: string; username: string; full_name: string; email: string | null; is_active: boolean; must_change_password: boolean; locked_until: string | null; last_login_at: string | null; roles: string[]; scopes: Record<string, string[]> };

function UserEditor({ u, onClose }: { u: U | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { years } = useYear();
  const { data: items = [] } = useItems();
  const isNew = !u;
  const [f, setF] = useState({ username: "", full_name: u?.full_name ?? "", email: u?.email ?? "", password: "", is_active: u?.is_active ?? true });
  const [roles, setRoles] = useState<string[]>(u?.roles ?? []);
  const [fyScope, setFyScope] = useState<string[]>(u?.scopes.FISCAL_YEAR ?? []);
  const [itemScope, setItemScope] = useState<string[]>(u?.scopes.ITEM ?? []);
  const [newPass, setNewPass] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const toggle = (arr: string[], set: (x: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      let id = u?.id;
      if (isNew) {
        id = (await api<U>("/users", { body: { username: f.username, full_name: f.full_name, email: f.email || null, password: f.password, roles } })).id;
      } else {
        await api(`/users/${id}`, { method: "PATCH", body: { full_name: f.full_name, email: f.email || null, is_active: f.is_active } });
        await api(`/users/${id}/roles`, { method: "PUT", body: { roles } });
      }
      const scopes: Record<string, string[]> = {};
      if (fyScope.length) scopes.FISCAL_YEAR = fyScope;
      if (itemScope.length) scopes.ITEM = itemScope;
      await api(`/users/${id}/scopes`, { method: "PUT", body: { scopes } });
      qc.invalidateQueries({ queryKey: ["users"] });
      toast("حُفظ المستخدم");
      onClose();
    } catch (x) { setErr(x); }
  };
  const reset = async () => {
    try { await api(`/users/${u!.id}/reset-password`, { body: { new_password: newPass } }); toast("أُعيد تعيين كلمة المرور؛ سيُطلب تغييرها عند الدخول"); setNewPass(""); } catch (x) { setErr(x); }
  };
  return (
    <Modal open onClose={onClose} title={isNew ? "مستخدم جديد" : `تعديل ${u!.username}`} wide>
      <form onSubmit={save} className="grid gap-3 md:grid-cols-2">
        {isNew && <Field label="اسم الدخول"><input className="input" dir="ltr" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required minLength={3} pattern="[A-Za-z0-9_.\-]+" /></Field>}
        <Field label="الاسم الكامل"><input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} required /></Field>
        <Field label="البريد"><input className="input" dir="ltr" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        {isNew && <Field label="كلمة مرور مؤقتة" hint="12 حرفًا على الأقل؛ يُلزم المستخدم بتغييرها"><input className="input" dir="ltr" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required /></Field>}
        {!isNew && <Field label="الحالة"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />نشط</label></Field>}
        <Field label="الأدوار" className="md:col-span-2"><div className="flex flex-wrap gap-3">{Object.entries(ROLE).map(([k, v]) =>
          <label key={k} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={roles.includes(k)} onChange={() => toggle(roles, setRoles, k)} />{v}</label>)}</div></Field>
        <Field label="نطاق السنوات (فارغ = الكل)"><div className="flex flex-wrap gap-3">{years.map((y) =>
          <label key={y.id} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={fyScope.includes(y.id)} onChange={() => toggle(fyScope, setFyScope, y.id)} />{y.year}</label>)}</div></Field>
        <Field label="نطاق البنود (فارغ = الكل)"><select multiple className="input h-28" value={itemScope} onChange={(e) => setItemScope(Array.from(e.target.selectedOptions, (o) => o.value))}>
          {items.map((i) => <option key={i.id} value={i.id}>{i.code} {i.name}</option>)}</select></Field>
        <div className="md:col-span-2"><ErrorBox error={err} /><button className="btn-primary mt-2">حفظ</button></div>
      </form>
      {!isNew && <div className="mt-4 flex items-end gap-2 border-t border-line pt-3">
        <Field label="إعادة تعيين كلمة المرور"><input className="input" dir="ltr" type="password" value={newPass} onChange={(e) => setNewPass(e.target.value)} /></Field>
        <button className="btn-outline" disabled={newPass.length < 12} onClick={reset}>تعيين</button>
      </div>}
    </Modal>
  );
}

export function Users() {
  const { can } = useAuth();
  const { data } = useQuery({ queryKey: ["users"], queryFn: () => api<{ items: U[] }>("/users", { params: { page_size: 200 } }) });
  const [edit, setEdit] = useState<U | null | "new">(null);
  return (
    <div>
      <PageHeader title="المستخدمون والصلاحيات" actions={can("users.manage") && <button className="btn-primary" onClick={() => setEdit("new")}>+ مستخدم</button>} />
      <div className="card">
        <Table rows={data?.items} onRow={can("users.manage") ? (r) => setEdit(r) : undefined} cols={[
          { key: "username", title: "اسم الدخول", render: (r) => <span dir="ltr">{r.username}</span> },
          { key: "full_name", title: "الاسم" },
          { key: "roles", title: "الأدوار", render: (r) => r.roles.map((x) => ROLE[x] ?? x).join("، ") },
          { key: "scopes", title: "النطاق", render: (r) => Object.keys(r.scopes).length ? <Badge tone="warn">مقيد</Badge> : "كامل" },
          { key: "last_login_at", title: "آخر دخول", render: (r) => <span className="num">{fmtDateTime(r.last_login_at)}</span> },
          { key: "status", title: "الحالة", render: (r) => <span className="flex gap-1">{r.is_active ? <Badge tone="ok">نشط</Badge> : <Badge>معطل</Badge>}
            {r.locked_until && new Date(r.locked_until) > new Date() && <Badge tone="bad">مقفل</Badge>}{r.must_change_password && <Badge tone="warn">يلزم تغيير كلمة المرور</Badge>}</span> },
        ]} />
      </div>
      {edit && <UserEditor u={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  );
}
