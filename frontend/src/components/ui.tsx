import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "@/api/client";
import { fmt, isNeg, type Money as M } from "@/lib/money";

const TONES: Record<string, string> = {
  ok: "bg-ok-50 text-ok", warn: "bg-warn-50 text-warn", bad: "bg-bad-50 text-bad",
  brand: "bg-brand-50 text-brand", mute: "bg-surface text-ink-mute border border-line",
};

export function Badge({ tone = "mute", children }: { tone?: string; children: ReactNode }) {
  return <span className={`badge ${TONES[tone] ?? TONES.mute}`}>{children}</span>;
}

export function Money({ v, strong, className = "" }: { v: M | null | undefined; strong?: boolean; className?: string }) {
  return <span className={`num ${isNeg(v) ? "text-bad" : ""} ${strong ? "font-bold" : ""} ${className}`}>{fmt(v)}</span>;
}

export function Spinner({ label = "جارٍ التحميل…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-ink-mute">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand border-t-transparent" />
      {label}
    </div>
  );
}

export function Empty({ children = "لا توجد بيانات." }: { children?: ReactNode }) {
  return <div className="p-8 text-center text-ink-mute">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div role="alert" className="rounded-md border border-bad/30 bg-bad-50 p-3 text-sm text-bad">
      <div className="font-bold">{e.message ?? "حدث خطأ"}</div>
      {e.code === "INSUFFICIENT_BUDGET" && e.details && (
        <table className="mt-2 text-ink">
          <tbody>
            <tr><td className="pe-4">البند</td><td>{e.details.item_code} {e.details.item_name}</td></tr>
            <tr><td className="pe-4">المتاح</td><td className="num">{e.details.available}</td></tr>
            <tr><td className="pe-4">المطلوب</td><td className="num">{e.details.requested}</td></tr>
            <tr><td className="pe-4 font-bold">العجز</td><td className="num font-bold text-bad">{e.details.shortfall}</td></tr>
          </tbody>
        </table>
      )}
      {e.details?.errors && (
        <ul className="mt-1 list-disc ps-5 text-xs">{e.details.errors.map((x: any, i: number) => <li key={i}>{x.loc?.slice(1).join(".")}: {x.msg}</li>)}</ul>
      )}
      {e.details?.items && Array.isArray(e.details.items) && (
        <ul className="mt-1 list-disc ps-5 text-xs">{e.details.items.map((x: any, i: number) => <li key={i}>{x.item_code}: المطلوب {x.required} — المتاح {x.available}</li>)}</ul>
      )}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold text-ink">{title}</h1>
        {subtitle && <div className="mt-0.5 text-sm text-ink-mute">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2 noprint">{actions}</div>}
    </div>
  );
}

export function Field({ label, children, hint, className = "" }: { label: string; children: ReactNode; hint?: string; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-xs text-ink-mute">{hint}</span>}
    </label>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus();
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-16" onMouseDown={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title}
           className={`card w-full ${wide ? "max-w-4xl" : "max-w-lg"}`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-bold">{title}</h2>
          <button className="btn-ghost px-2" onClick={onClose} aria-label="إغلاق">✕</button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

type Toast = { id: number; text: string; tone: "ok" | "bad" };
const ToastCtx = createContext<(text: string, tone?: "ok" | "bad") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "bad" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 start-4 z-[60] flex flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`card px-4 py-2 text-sm font-bold ${t.tone === "ok" ? "border-ok text-ok" : "border-bad text-bad"}`}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export type Col<T> = { key: string; title: string; render?: (r: T) => ReactNode; num?: boolean; className?: string };

export function Table<T extends Record<string, any>>({ cols, rows, onRow, footer, empty }: {
  cols: Col<T>[]; rows: T[] | undefined; onRow?: (r: T) => void; footer?: ReactNode; empty?: ReactNode;
}) {
  if (!rows) return <Spinner />;
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead><tr>{cols.map((c) => <th key={c.key} className={`th ${c.num ? "text-end" : ""}`}>{c.title}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? i} onClick={onRow ? () => onRow(r) : undefined}
                className={onRow ? "cursor-pointer hover:bg-brand-50" : "hover:bg-surface"}>
              {cols.map((c) => (
                <td key={c.key} className={`td ${c.num ? "text-end" : ""} ${c.className ?? ""}`}>
                  {c.render ? c.render(r) : r[c.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot className="bg-brand-50 font-bold">{footer}</tfoot>}
      </table>
    </div>
  );
}

export function Pager({ page, total, pageSize, onPage }: { page: number; total: number; pageSize: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between border-t border-line px-3 py-2 text-sm text-ink-mute">
      <span>{total} سجل</span>
      <div className="flex items-center gap-2">
        <button className="btn-outline px-2 py-1" disabled={page <= 1} onClick={() => onPage(page - 1)}>السابق</button>
        <span className="num">{page} / {pages}</span>
        <button className="btn-outline px-2 py-1" disabled={page >= pages} onClick={() => onPage(page + 1)}>التالي</button>
      </div>
    </div>
  );
}

export function Kpi({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: string; hint?: ReactNode }) {
  return (
    <div className="card p-3">
      <div className="text-xs font-bold text-ink-mute">{label}</div>
      <div className={`mt-1 text-lg font-bold ${tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-ink"}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-mute">{hint}</div>}
    </div>
  );
}
