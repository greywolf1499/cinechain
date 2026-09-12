import { GitBranch } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";

export default function BridgePage() {
  return (
    <div>
      <PageHeading title="Bridge Solver" subtitle="Find a path between any two films" />
      <EmptyState
        icon={GitBranch}
        title="Bridge solver UI coming in Phase 10"
        description="Target search, live depth progress via SSE, and the resulting bridge path land in Phase 10."
      />
    </div>
  );
}
