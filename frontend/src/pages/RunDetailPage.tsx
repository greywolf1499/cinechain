import { useParams } from "react-router-dom";
import { Clapperboard } from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <div>
      <PageHeading title="Run detail" subtitle={`Run ${id}`} />
      <EmptyState
        icon={Clapperboard}
        title="Timeline coming in Phase 9"
        description="The poster filmstrip, step logging, and Fork in the Road modal land in Phase 9."
      />
    </div>
  );
}
