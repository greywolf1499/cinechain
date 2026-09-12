import { Film } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";

export default function RunsPage() {
  return (
    <div>
      <PageHeading title="Runs" subtitle="Your active and past movie challenges" />
      <EmptyState
        icon={Film}
        title="No runs yet"
        description="Run creation, participant picking, and the chain timeline land in Phase 9."
      />
    </div>
  );
}
