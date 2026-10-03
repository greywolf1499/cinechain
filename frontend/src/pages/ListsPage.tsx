import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { X } from "lucide-react";
import Breadcrumbs from "../components/Breadcrumbs";
import ListBrowser from "../components/ListBrowser";
import PageHeading from "../components/PageHeading";
import Toast, { type ToastState } from "../components/Toast";
import { useAuthStore } from "../store/authStore";

export default function ListsPage() {
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const [searchParams] = useSearchParams();
  const curator = searchParams.get("curator") ?? undefined;
  const [toast, setToast] = useState<ToastState | null>(null);

  return (
    <div>
      <Breadcrumbs items={[{ label: "Lists", to: curator ? "/lists" : undefined }, ...(curator ? [{ label: curator }] : [])]} />
      <PageHeading
        title="Curated Lists"
        subtitle="Every canon list across your curators - search, sort and enable the ones you want badges for."
      />
      {curator && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-accent/40 bg-accent/10 px-4 py-2.5 text-sm text-accent">
          Showing lists from <strong>{curator}</strong>
          <Link to={`/curators/${curator}`} className="underline">View profile</Link>
          <Link to="/lists" className="ml-auto flex items-center gap-1 text-xs hover:underline">
            <X className="h-3.5 w-3.5" /> Clear
          </Link>
        </div>
      )}
      <ListBrowser key={curator ?? "all"} account={curator} isAdmin={isAdmin} onToast={setToast} />
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
