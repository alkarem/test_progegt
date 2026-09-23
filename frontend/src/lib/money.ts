/** المبالغ نصوص من الخادم وتُعالج بـ decimal.js فقط؛ لا تتحول إلى number أبدًا (08-api §3). */
import Decimal from "decimal.js";

export type Money = string;
export const D = (v: Money | number | null | undefined) => new Decimal(v ?? 0);

export function fmt(v: Money | null | undefined, opts: { parens?: boolean } = {}): string {
  if (v === null || v === undefined || v === "") return "—";
  const d = D(v);
  const [int, frac] = d.abs().toFixed(3).split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const s = `${grouped}.${frac}`;
  if (d.isNeg()) return opts.parens === false ? `-${s}` : `(${s})`;
  return s;
}

export const isNeg = (v: Money | null | undefined) => v != null && D(v).isNeg();

/** تحقق إدخال المبلغ: أرقام بثلاث خانات عشرية كحد أقصى (الدينار = 1000 درهم). */
export const MONEY_RE = /^\d{1,15}(\.\d{1,3})?$/;
export const normalizeMoneyInput = (s: string) =>
  s.replace(/[٠-٩]/g, (c) => String("٠١٢٣٤٥٦٧٨٩".indexOf(c))).replace(/[,٬\s]/g, "").replace("٫", ".");

export const pct = (v: string | null | undefined) => (v == null ? "—" : `${D(v).toFixed(2)}%`);
