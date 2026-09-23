import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { Badge, ErrorBox, Field, Modal, PageHeader, Spinner } from "@/components/ui";

type Node = { id: string; code: string; name: string; chapter_id: string; parent_id: string | null; is_postable: boolean; is_active: boolean; display_order: number; budget_type: string; children: Node[] };

function ItemForm({ item, parent, onClose }: { item: Node | null; parent: Node | null; onClose: () => void }) {
  const qc = useQueryClient();
  const chapters = useQuery({ queryKey: ["chapters"], queryFn: () => api<any[]>("/chapters") });
  const [f, setF] = useState({ code: parent ? `${parent.code}/` : "", name: item?.name ?? "", chapter_id: item?.chapter_id ?? parent?.chapter_id ?? "", display_order: item?.display_order ?? 0, is_active: item?.is_active ?? true });
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      if (item) await api(`/items/${item.id}`, { method: "PATCH", body: { name: f.name, display_order: f.display_order, is_active: f.is_active } });
      else await api("/items", { body: { chapter_id: f.chapter_id || chapters.data?.[0]?.id, code: f.code, name: f.name, parent_id: parent?.id ?? null, display_order: f.display_order } });
      qc.invalidateQueries({ queryKey: ["items-tree"] }); qc.invalidateQueries({ queryKey: ["items"] }); onClose();
    } catch (x) { setErr(x); }
  };
  return (
    <Modal open onClose={onClose} title={item ? `تعديل البند ${item.code}` : parent ? `بند فرعي تحت ${parent.code}` : "بند جديد"}>
      <form onSubmit={submit} className="grid gap-3">
        {!item && <Field label="الباب"><select className="input" value={f.chapter_id} onChange={(e) => setF({ ...f, chapter_id: e.target.value })} disabled={!!parent}>{chapters.data?.map((c) => <option key={c.id} value={c.id}>{c.code} {c.name}</option>)}</select></Field>}
        {!item && <Field label="رقم البند" hint="مثل 2/30 أو 2/16/1"><input className="input num" dir="ltr" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} required /></Field>}
        <Field label="اسم البند"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
        <Field label="ترتيب العرض"><input type="number" className="input num" value={f.display_order} onChange={(e) => setF({ ...f, display_order: Number(e.target.value) })} /></Field>
        {item && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />نشط (البند غير النشط لا يقبل حركات جديدة)</label>}
        <ErrorBox error={err} /><button className="btn-primary">حفظ</button>
      </form>
    </Modal>
  );
}

function Tree({ nodes, depth, onEdit, onAdd, manage }: { nodes: Node[]; depth: number; onEdit: (n: Node) => void; onAdd: (n: Node) => void; manage: boolean }) {
  return <>{nodes.map((n) => (
    <div key={n.id}>
      <div className="flex items-center gap-2 border-b border-line py-2 pe-3 text-sm hover:bg-surface" style={{ paddingInlineStart: `${12 + depth * 20}px` }}>
        <span className="num w-16 font-bold">{n.code}</span>
        <Link className="flex-1 hover:underline" to={`/budget?item=${encodeURIComponent(n.code)}`}>{n.name}</Link>
        {!n.is_postable && <Badge tone="brand">تجميعي</Badge>}
        {!n.is_active && <Badge>غير نشط</Badge>}
        {manage && <><button className="btn-ghost px-2 text-xs" onClick={() => onAdd(n)}>+ فرعي</button><button className="btn-ghost px-2 text-xs" onClick={() => onEdit(n)}>تعديل</button></>}
      </div>
      {n.children.length > 0 && <Tree nodes={n.children} depth={depth + 1} onEdit={onEdit} onAdd={onAdd} manage={manage} />}
    </div>
  ))}</>;
}

export function Items() {
  const { can } = useAuth();
  const { data } = useQuery({ queryKey: ["items-tree"], queryFn: () => api<Node[]>("/items/tree") });
  const [form, setForm] = useState<{ item: Node | null; parent: Node | null } | null>(null);
  return (
    <div>
      <PageHeader title="البنود" subtitle="شجرة بنود الباب الثاني. البند الذي له فروع يصبح تجميعيًا ولا يُرحَّل عليه مباشرة."
        actions={can("catalog.manage") && <button className="btn-primary" onClick={() => setForm({ item: null, parent: null })}>+ بند</button>} />
      <div className="card">{!data ? <Spinner /> : <Tree nodes={data} depth={0} manage={can("catalog.manage")} onEdit={(n) => setForm({ item: n, parent: null })} onAdd={(n) => setForm({ item: null, parent: n })} />}</div>
      {form && <ItemForm {...form} onClose={() => setForm(null)} />}
    </div>
  );
}
