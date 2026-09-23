import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export type Item = { id: string; code: string; name: string; chapter_id: string; chapter_code: string; parent_id: string | null; is_postable: boolean; is_active: boolean; display_order: number; budget_type: string };
export type Entity = { id: string; code: string; name: string; parent_id: string | null; is_active: boolean };
export type Supplier = { id: string; name: string; kind: string };

export const useItems = () => useQuery({ queryKey: ["items"], queryFn: () => api<Item[]>("/items"), staleTime: 300_000 });
export const useEntities = () => useQuery({ queryKey: ["entities"], queryFn: () => api<Entity[]>("/entities"), staleTime: 300_000 });
export const useSuppliers = (q: string) =>
  useQuery({ queryKey: ["suppliers", q], queryFn: () => api<{ items: Supplier[] }>("/suppliers", { params: { q, page_size: 20 } }) });
