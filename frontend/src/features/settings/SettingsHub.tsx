import { Link } from "react-router-dom";
import { useAuth } from "@/app/auth";
import { PageHeader } from "@/components/ui";

const CARDS = [
  { to: "/settings/fiscal-years", t: "السنوات المالية", d: "فتح السنة، أساس الرقابة، إقفال الفترات والإقفال السنوي", p: "fiscal.view" },
  { to: "/settings/users", t: "المستخدمون والصلاحيات", d: "الأدوار ونطاق الصلاحيات وإعادة تعيين كلمات المرور", p: "users.view" },
  { to: "/items", t: "البنود", d: "شجرة بنود الباب الثاني", p: "catalog.view" },
  { to: "/settings/workflow", t: "مسارات الموافقة", d: "شرائح المبالغ ومهل المراحل", p: "workflow.inbox" },
  { to: "/settings/grants", t: "منح الاستثناء", d: "السماح المحدود باعتماد عملية تتجاوز المتاح", p: "budget.view" },
  { to: "/settings/imports", t: "استيراد Excel", d: "استيراد سجل الباب الثاني مع تقرير جودة البيانات", p: "imports.prepare" },
  { to: "/settings/backup", t: "النسخ الاحتياطي", d: "النسخ اليومي والتحقق والاستعادة وصحة القاعدة", p: "backup.manage" },
  { to: "/account", t: "حسابي", d: "الجلسات والتفويض المؤقت وكلمة المرور" },
];

export function SettingsHub() {
  const { can } = useAuth();
  return (
    <div>
      <PageHeader title="الإعدادات" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {CARDS.filter((c) => !c.p || can(c.p)).map((c) => (
          <Link key={c.to} to={c.to} className="card block p-4 hover:border-brand"><div className="font-bold text-brand">{c.t}</div><div className="mt-1 text-sm text-ink-soft">{c.d}</div></Link>
        ))}
      </div>
    </div>
  );
}
