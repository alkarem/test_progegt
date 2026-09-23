import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, setAccessToken } from "@/api/client";
import { useAuth } from "@/app/auth";
import { ErrorBox, Field } from "@/components/ui";

export function Login() {
  const { me, login } = useAuth();
  const nav = useNavigate();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (me) return <Navigate to={me.must_change_password ? "/account/password" : "/"} replace />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await login(u, p); nav("/"); } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-brand-900 p-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <h1 className="text-lg font-bold text-brand">نظام مراقبة الاعتمادات والمصروفات الحكومية</h1>
        <p className="mb-5 text-sm text-ink-mute">تسجيل الدخول</p>
        <div className="space-y-3">
          <Field label="اسم المستخدم"><input className="input" autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} required dir="ltr" /></Field>
          <Field label="كلمة المرور"><input className="input" type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required dir="ltr" /></Field>
          <ErrorBox error={err} />
          <button className="btn-primary w-full py-2" disabled={busy}>{busy ? "…" : "دخول"}</button>
        </div>
      </form>
    </div>
  );
}

export function ChangePassword() {
  const { reload, me } = useAuth();
  const nav = useNavigate();
  const [cur, setCur] = useState("");
  const [n1, setN1] = useState("");
  const [n2, setN2] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (n1 !== n2) { setErr({ message: "كلمتا المرور غير متطابقتين." }); return; }
    try {
      const t = await api<{ access_token: string }>("/auth/change-password", { body: { current_password: cur, new_password: n1 } });
      setAccessToken(t.access_token);
      await reload();
      nav("/");
    } catch (x) { setErr(x); }
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-3 p-6">
        <h1 className="text-lg font-bold">تغيير كلمة المرور</h1>
        {me?.must_change_password && <p className="text-sm text-warn">يجب تغيير كلمة المرور عند أول دخول.</p>}
        <Field label="كلمة المرور الحالية"><input className="input" type="password" value={cur} onChange={(e) => setCur(e.target.value)} required dir="ltr" /></Field>
        <Field label="كلمة المرور الجديدة" hint="12 حرفًا على الأقل، حروف وأرقام، ولا تحتوي اسم المستخدم."><input className="input" type="password" value={n1} onChange={(e) => setN1(e.target.value)} required dir="ltr" /></Field>
        <Field label="تأكيد كلمة المرور"><input className="input" type="password" value={n2} onChange={(e) => setN2(e.target.value)} required dir="ltr" /></Field>
        <ErrorBox error={err} />
        <button className="btn-primary w-full py-2">حفظ</button>
      </form>
    </div>
  );
}
