import { BookUser } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";

export default function PassportPage() {
  return (
    <div>
      <PageHeading title="Passport" subtitle="Cultural breadth, decades traversed, keystone actors" />
      <EmptyState
        icon={BookUser}
        title="Passport stats coming in Phase 10"
        description="Countries visited, decades spanned, and your most-used keystone actors land in Phase 10."
      />
    </div>
  );
}
