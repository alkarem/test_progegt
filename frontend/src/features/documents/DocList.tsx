import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useYear } from "@/app/year";
import { PageHeader, Pager, Table, type Col } from "@/components/ui";
import { DOC_STATUS } from "@/lib/labels";

export function DocList<T extends Record<string, any>>({ title, endpoint, cols, basePath, newLabel, canCreate, extraFilters, subtitle }: {
  title: string; endpoint: string; cols: Col<T>[]; basePath: string; newLabel?: string; canCreate?: boolean;
  extraFilters?: Record<string, string>; subtitle?: ReactNode;
}) {
  const { year } = useYear();
  const nav = useNavigate();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const params = { fiscal_year_id: year!.id, status, page, page_size: 50, ...extraFilters };
  const { data } = useQuery({ queryKey: [endpoint, params], queryFn: () => api<{ items: T[]; total: number }>(endpoint, { params }) });
  return (
    <div>
      <PageHeader title={title} subtitle={subtitle ?? `السنة المالية ${year!.year}`}
        actions={canCreate && newLabel && <Link className="btn-primary" to={`${basePath}/new`}>+ {newLabel}</Link>} />
      <div className="card">
        <div className="flex gap-2 border-b border-line p-3">
          <select className="input w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="الحالة">
            <option value="">كل الحالات</option>
            {Object.entries(DOC_STATUS).filter(([k]) => k !== "APPROVED").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <Table cols={cols} rows={data?.items} onRow={(r) => nav(`${basePath}/${r.id}`)} />
        {data && <Pager page={page} total={data.total} pageSize={50} onPage={setPage} />}
      </div>
    </div>
  );
}
