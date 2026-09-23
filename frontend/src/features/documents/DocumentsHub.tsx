import { Link } from "react-router-dom";
import { useAuth } from "@/app/auth";
import { PageHeader } from "@/components/ui";

const CARDS = [
  { to: "/budget-documents", t: "الاعتماد الأصلي والتعديلات", d: "الاعتماد الأصلي والتعزيز والتخفيض", p: "budget_documents.view" },
  { to: "/authorizations", t: "التفويضات", d: "التفويضات المالية والمصلحية وتوزيعها", p: "authorizations.view" },
  { to: "/transfers", t: "المناقلات", d: "النقل بين البنود", p: "transfers.view" },
  { to: "/commitments", t: "الارتباطات", d: "طلبات الشراء وأوامر الشراء والعقود والالتزامات", p: "commitments.view" },
  { to: "/expenditures", t: "المصروفات", d: "أذونات الصرف", p: "expenditures.view" },
  { to: "/adjustments", t: "التسويات والقيود العكسية", d: "التصحيح دون تعديل أي قيد سابق", p: "adjustments.view" },
  { to: "/search", t: "البحث الشامل", d: "البحث في كل المستندات" },
];

export function DocumentsHub() {
  const { can } = useAuth();
  return (
    <div>
      <PageHeader title="المستندات" subtitle="كل عملية مالية مستند له دورة موافقة ومرفقات، ولا يؤثر في الأرصدة إلا بعد ترحيله." />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {CARDS.filter((c) => !c.p || can(c.p)).map((c) => (
          <Link key={c.to} to={c.to} className="card block p-4 hover:border-brand"><div className="font-bold text-brand">{c.t}</div><div className="mt-1 text-sm text-ink-soft">{c.d}</div></Link>
        ))}
      </div>
    </div>
  );
}
