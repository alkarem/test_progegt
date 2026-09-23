import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./index.css";
import { ApiError } from "@/api/client";
import { AuthProvider, useAuth } from "@/app/auth";
import { ChangePassword, Login } from "@/app/Login";
import { Shell } from "@/app/Shell";
import { YearProvider } from "@/app/year";
import { Spinner, ToastProvider } from "@/components/ui";
import { Assistant } from "@/features/ai/Assistant";
import { Alerts, Notifications } from "@/features/alerts/Alerts";
import { Inbox } from "@/features/approvals/Inbox";
import { Audit } from "@/features/audit/Audit";
import { BudgetPosition } from "@/features/budget/BudgetPosition";
import { LineDetail } from "@/features/budget/LineDetail";
import { Dashboard } from "@/features/dashboard/Dashboard";
import { AdjustmentForm, AdjustmentList, AdjustmentView } from "@/features/documents/Adjustments";
import { AuthorizationForm, AuthorizationList, AuthorizationView } from "@/features/documents/Authorizations";
import { BudgetDocForm, BudgetDocList, BudgetDocView } from "@/features/documents/BudgetDocuments";
import { CommitmentForm, CommitmentList, CommitmentView } from "@/features/documents/Commitments";
import { DocumentsHub } from "@/features/documents/DocumentsHub";
import { ExpenditureForm, ExpenditureList, ExpenditureView } from "@/features/documents/Expenditures";
import { TransferForm, TransferList, TransferView } from "@/features/documents/Transfers";
import { ImportList, ImportWizard } from "@/features/imports/Imports";
import { Reports } from "@/features/reports/Reports";
import { Search } from "@/features/search/Search";
import { Account } from "@/features/settings/Account";
import { Backup } from "@/features/settings/Backup";
import { FiscalYears } from "@/features/settings/FiscalYears";
import { Grants } from "@/features/settings/Grants";
import { Items } from "@/features/settings/Items";
import { SettingsHub } from "@/features/settings/SettingsHub";
import { Users } from "@/features/settings/Users";
import { WorkflowSettings } from "@/features/settings/Workflow";

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
    },
  },
});

function Guard({ perm, children }: { perm?: string; children: ReactNode }) {
  const { can } = useAuth();
  if (perm && !can(perm)) return <div className="card p-6 text-ink-mute">لا تملك صلاحية عرض هذه الصفحة.</div>;
  return <>{children}</>;
}

function Protected() {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="p-10"><Spinner /></div>;
  if (!me) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  if (me.must_change_password && loc.pathname !== "/account/password") return <Navigate to="/account/password" replace />;
  return <YearProvider><Shell /></YearProvider>;
}

// [المسار، الصلاحية، العنصر]
const ROUTES: [string, string | undefined, ReactNode][] = [
  ["/", "budget.view", <Dashboard />],
  ["/budget", "budget.view", <BudgetPosition />],
  ["/budget/lines/:id", "budget.view", <LineDetail />],
  ["/budget-documents", "budget_documents.view", <BudgetDocList />],
  ["/budget-documents/new", "budget_documents.create", <BudgetDocForm />],
  ["/budget-documents/:id", "budget_documents.view", <BudgetDocView />],
  ["/authorizations", "authorizations.view", <AuthorizationList />],
  ["/authorizations/new", "authorizations.create", <AuthorizationForm />],
  ["/authorizations/:id", "authorizations.view", <AuthorizationView />],
  ["/transfers", "transfers.view", <TransferList />],
  ["/transfers/new", "transfers.create", <TransferForm />],
  ["/transfers/:id", "transfers.view", <TransferView />],
  ["/commitments", "commitments.view", <CommitmentList />],
  ["/commitments/new", "commitments.create", <CommitmentForm />],
  ["/commitments/:id", "commitments.view", <CommitmentView />],
  ["/expenditures", "expenditures.view", <ExpenditureList />],
  ["/expenditures/new", "expenditures.create", <ExpenditureForm />],
  ["/expenditures/:id", "expenditures.view", <ExpenditureView />],
  ["/adjustments", "adjustments.view", <AdjustmentList />],
  ["/adjustments/new", "adjustments.create", <AdjustmentForm />],
  ["/adjustments/:id", "adjustments.view", <AdjustmentView />],
  ["/documents", undefined, <DocumentsHub />],
  ["/items", "catalog.view", <Items />],
  ["/approvals", "workflow.inbox", <Inbox />],
  ["/reports", "reports.view", <Reports />],
  ["/alerts", "alerts.view", <Alerts />],
  ["/notifications", undefined, <Notifications />],
  ["/audit", "audit.view", <Audit />],
  ["/search", undefined, <Search />],
  ["/assistant", "ai.use", <Assistant />],
  ["/account", undefined, <Account />],
  ["/settings", undefined, <SettingsHub />],
  ["/settings/users", "users.view", <Users />],
  ["/settings/fiscal-years", "fiscal.view", <FiscalYears />],
  ["/settings/workflow", "workflow.inbox", <WorkflowSettings />],
  ["/settings/grants", "budget.view", <Grants />],
  ["/settings/backup", "backup.manage", <Backup />],
  ["/settings/imports", "imports.prepare", <ImportList />],
  ["/settings/imports/:id", "imports.prepare", <ImportWizard />],
];

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected />}>
        <Route path="/account/password" element={<ChangePassword />} />
        {ROUTES.map(([path, perm, el]) => <Route key={path} path={path} element={<Guard perm={perm}>{el}</Guard>} />)}
        <Route path="*" element={<div className="card p-6">الصفحة غير موجودة.</div>} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BrowserRouter>
          <AuthProvider><App /></AuthProvider>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
