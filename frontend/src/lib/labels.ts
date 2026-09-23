export const DOC_STATUS: Record<string, string> = {
  DRAFT: "مسودة", SUBMITTED: "مقدم", IN_REVIEW: "قيد المراجعة", APPROVED: "معتمد", POSTED: "مرحّل",
  REJECTED: "مرفوض", RETURNED: "مُرجع للتعديل", CANCELLED: "ملغى", REVERSED: "معكوس",
};
export const DOC_STATUS_TONE: Record<string, string> = {
  DRAFT: "mute", SUBMITTED: "brand", IN_REVIEW: "brand", POSTED: "ok", REJECTED: "bad", RETURNED: "warn",
  CANCELLED: "mute", REVERSED: "warn",
};
export const COMMITMENT_TYPE: Record<string, string> = {
  PURCHASE_REQUEST: "طلب شراء", PURCHASE_ORDER: "أمر شراء", CONTRACT: "عقد", OBLIGATION: "التزام مالي", OTHER: "ارتباط آخر",
};
export const COMMITMENT_STATUS: Record<string, string> = {
  DRAFT: "مسودة", PENDING_APPROVAL: "بانتظار الاعتماد", APPROVED: "معتمد", PARTIALLY_PAID: "مسدد جزئيًا",
  FULLY_PAID: "مسدد بالكامل", CANCELLED: "ملغى", CLOSED: "مقفل",
};
export const AUTH_TYPE: Record<string, string> = { FINANCIAL: "مالي", DEPARTMENTAL: "مصلحي", OTHER: "أخرى" };
export const PAYMENT_METHOD: Record<string, string> = {
  CHEQUE: "شيك", BANK_TRANSFER: "حوالة مصرفية", CASH: "نقدًا", DEPOSIT_ACCOUNT: "حساب أمانات", OTHER: "أخرى",
};
export const TXN_TYPE: Record<string, string> = {
  ORIGINAL_BUDGET: "اعتماد أصلي", BUDGET_ALLOCATION: "توزيع تفويض", BUDGET_INCREASE: "تعزيز", BUDGET_DECREASE: "تخفيض",
  TRANSFER_IN: "مناقلة واردة", TRANSFER_OUT: "مناقلة صادرة", PRE_COMMITMENT: "حجز مبدئي",
  RESERVATION_RELEASE: "تحرير حجز", COMMITMENT: "ارتباط", COMMITMENT_LIQUIDATION: "تسييل ارتباط",
  ACTUAL_EXPENDITURE: "مصروف فعلي", ADJUSTMENT: "تسوية", REVERSAL: "قيد عكسي", CANCELLATION: "إلغاء",
  CLOSING: "إقفال", CARRY_FORWARD: "ترحيل لسنة تالية",
};
export const LEVEL: Record<string, { label: string; tone: string }> = {
  NORMAL: { label: "طبيعي", tone: "ok" }, WARNING: { label: "تحذير", tone: "warn" },
  CRITICAL: { label: "حرج", tone: "bad" }, EXHAUSTED: { label: "مستنفد", tone: "bad" },
  EXCEEDED: { label: "متجاوز", tone: "bad" }, NO_BUDGET: { label: "بلا اعتماد", tone: "mute" },
};
export const SEVERITY: Record<string, { label: string; tone: string }> = {
  INFO: { label: "معلومة", tone: "brand" }, WARNING: { label: "تحذير", tone: "warn" },
  HIGH: { label: "عالٍ", tone: "bad" }, CRITICAL: { label: "حرج", tone: "bad" },
};
export const ROLE: Record<string, string> = {
  SYSTEM_ADMIN: "مسؤول النظام", DATA_ENTRY: "مدخل بيانات", FINANCIAL_REVIEWER: "مراجع مالي",
  BUDGET_CONTROLLER: "مراقب ميزانية", SUPERVISOR: "مشرف", APPROVER: "معتمد نهائي", AUDITOR: "مدقق",
  REPORT_VIEWER: "مطلع تقارير",
};
export const SOURCE_LABEL: Record<string, string> = {
  budget_document: "مستند ميزانية", authorization: "تفويض", transfer: "مناقلة", commitment: "ارتباط",
  expenditure: "مصروف", adjustment: "تسوية / قيد عكسي",
};
export const SOURCE_PATH: Record<string, string> = {
  budget_document: "budget-documents", authorization: "authorizations", transfer: "transfers",
  commitment: "commitments", expenditure: "expenditures", adjustment: "adjustments",
};
export const fmtDate = (s?: string | null) => {
  if (!s) return "—";
  const d = s.slice(0, 10).split("-");
  return d.length === 3 ? `${d[2]}/${d[1]}/${d[0]}` : s;
};
export const fmtDateTime = (s?: string | null) => {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
