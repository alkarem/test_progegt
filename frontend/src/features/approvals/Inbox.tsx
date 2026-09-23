import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { Badge, Money, PageHeader, Table } from "@/components/ui";
import { fmtDateTime } from "@/lib/labels";
import { docLink } from "@/features/documents/common";

export function Inbox() {
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["inbox"], queryFn: () => api<any[]>("/inbox"), refetchInterval: 60_000 });
  return (
    <div>
      <PageHeader title="الموافقات" subtitle="المستندات التي تنتظر إجراءك في دورة الموافقة. لا تظهر هنا المستندات التي أنشأتها بنفسك (فصل المهام)." />
      <div className="card">
        <Table rows={data} onRow={(r) => nav(docLink(r.source_type, r.source_id))} empty="لا توجد مستندات بانتظارك." cols={[
          { key: "type_label", title: "النوع" },
          { key: "doc_no", title: "الرقم" },
          { key: "amount", title: "المبلغ", num: true, render: (r) => <Money v={r.amount} /> },
          { key: "step_name", title: "المرحلة" },
          { key: "waiting_since", title: "منذ", render: (r) => <span className="num">{fmtDateTime(r.waiting_since)}</span> },
          { key: "flags", title: "", render: (r) => <span className="flex gap-1">{r.overdue && <Badge tone="bad">متأخر</Badge>}{r.on_behalf_of && <Badge tone="brand">بالتفويض</Badge>}</span> },
        ]} />
      </div>
    </div>
  );
}
