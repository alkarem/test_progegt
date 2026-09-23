/** السنة المالية المختارة تطبق على كل الشاشات (07-ui §2). تُحفظ محليًا كتفضيل للعرض فقط. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/api/client";

export type FiscalYear = { id: string; year: number; status: string; control_basis: string; start_date: string; end_date: string; count_reservations: boolean; carry_forward_item_id: string | null };
type YearCtx = { years: FiscalYear[]; year: FiscalYear | null; setYearId: (id: string) => void; reload: () => void };
const Ctx = createContext<YearCtx>({ years: [], year: null, setYearId: () => {}, reload: () => {} });
export const useYear = () => useContext(Ctx);

const KEY = "gbcfms.year";
const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };

export function YearProvider({ children }: { children: ReactNode }) {
  const { data: years = [] } = useQuery({ queryKey: ["fiscal-years"], queryFn: () => api<FiscalYear[]>("/fiscal-years") });
  const qc = useQueryClient();
  const reload = () => { qc.invalidateQueries({ queryKey: ["fiscal-years"] }); };
  const [id, setId] = useState<string | null>(read());
  useEffect(() => {
    if (years.length && !years.find((y) => y.id === id)) {
      setId((years.find((y) => y.status === "OPEN") ?? years[0]).id);
    }
  }, [years, id]);
  const setYearId = (v: string) => { setId(v); try { localStorage.setItem(KEY, v); } catch { /* تفضيل اختياري */ } };
  return <Ctx.Provider value={{ years, year: years.find((y) => y.id === id) ?? null, setYearId, reload }}>{children}</Ctx.Provider>;
}
