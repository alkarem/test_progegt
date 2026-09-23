import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/app/auth";
import { useYear } from "@/app/year";
import { ROLE } from "@/lib/labels";

// القائمة الرئيسية (البند 32 من المتطلبات)
const MENU: { to: string; label: string; icon: string; perm?: string }[] = [
  { to: "/", label: "لوحة القيادة", icon: "▦", perm: "budget.view" },
  { to: "/budget", label: "الميزانية", icon: "◧", perm: "budget.view" },
  { to: "/authorizations", label: "التفويضات", icon: "✎", perm: "authorizations.view" },
  { to: "/items", label: "البنود", icon: "☰", perm: "catalog.view" },
  { to: "/transfers", label: "المناقلات", icon: "⇄", perm: "transfers.view" },
  { to: "/commitments", label: "الارتباطات", icon: "⧉", perm: "commitments.view" },
  { to: "/expenditures", label: "المصروفات", icon: "↧", perm: "expenditures.view" },
  { to: "/approvals", label: "الموافقات", icon: "✓", perm: "workflow.inbox" },
  { to: "/reports", label: "التقارير", icon: "▤", perm: "reports.view" },
  { to: "/documents", label: "المستندات", icon: "▢", perm: "adjustments.view" },
  { to: "/alerts", label: "التنبيهات", icon: "⚠", perm: "alerts.view" },
  { to: "/audit", label: "التدقيق", icon: "⌕", perm: "audit.view" },
  { to: "/settings", label: "الإعدادات", icon: "⚙" },
];

export function Shell() {
  const { me, can, logout } = useAuth();
  const { years, year, setYearId } = useYear();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const { data: unread } = useQuery({
    queryKey: ["unread"], queryFn: () => api<{ count: number }>("/notifications/unread-count"), refetchInterval: 60_000,
  });
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("global-search")?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const search = (e: FormEvent) => { e.preventDefault(); if (q.trim()) nav(`/search?q=${encodeURIComponent(q.trim())}`); };

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col bg-brand-900 text-white">
        <div className="border-b border-white/10 px-4 py-4">
          <div className="text-sm font-bold leading-tight">نظام مراقبة الاعتمادات<br />والمصروفات الحكومية</div>
          <div className="mt-1 text-[11px] text-white/60">GBCFMS</div>
        </div>
        <nav className="flex-1 overflow-y-auto py-2" aria-label="القائمة الرئيسية">
          {MENU.filter((m) => !m.perm || can(m.perm)).map((m) => (
            <NavLink key={m.to} to={m.to} end={m.to === "/"}
              className={({ isActive }) => `mx-2 my-0.5 flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${isActive ? "bg-white/15 font-bold" : "text-white/80 hover:bg-white/10"}`}>
              <span aria-hidden className="w-4 text-center">{m.icon}</span>{m.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 p-3 text-xs">
          <div className="font-bold">{me?.full_name}</div>
          <div className="text-white/60">{me?.roles.map((r) => ROLE[r] ?? r).join("، ")}</div>
          <div className="mt-2 flex gap-2">
            <NavLink to="/account" className="text-white/80 underline">حسابي</NavLink>
            <button className="text-white/80 underline" onClick={async () => { await logout(); nav("/login"); }}>خروج</button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-white px-5 py-2.5">
          <form onSubmit={search} className="flex-1" role="search">
            <input id="global-search" className="input max-w-md" placeholder="بحث شامل: رقم مستند، تفويض، مورد، بند، قيمة…  (Ctrl+K)"
                   value={q} onChange={(e) => setQ(e.target.value)} aria-label="بحث شامل" />
          </form>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-ink-mute">السنة المالية</span>
            <select className="input w-auto" value={year?.id ?? ""} onChange={(e) => setYearId(e.target.value)} aria-label="السنة المالية">
              {years.map((y) => <option key={y.id} value={y.id}>{y.year}{y.status !== "OPEN" ? ` (${y.status === "CLOSED" ? "مقفلة" : y.status === "PLANNING" ? "تخطيط" : "قيد الإقفال"})` : ""}</option>)}
            </select>
          </label>
          <NavLink to="/notifications" className="relative rounded-md p-2 hover:bg-surface" aria-label="الإشعارات">
            <span aria-hidden>🔔</span>
            {!!unread?.count && <span className="num absolute -top-0.5 -start-0.5 rounded-full bg-bad px-1.5 text-[10px] font-bold text-white">{unread.count}</span>}
          </NavLink>
        </header>
        <main className="flex-1 p-5">
          {!year ? <div className="card p-6 text-ink-mute">لا توجد سنة مالية بعد. {can("fiscal.manage") && <NavLink className="text-brand underline" to="/settings/fiscal-years">أنشئ سنة مالية</NavLink>}</div> : <Outlet />}
        </main>
      </div>
    </div>
  );
}
