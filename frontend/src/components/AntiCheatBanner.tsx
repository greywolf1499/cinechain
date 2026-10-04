import { Lock } from "lucide-react";
import { Link } from "react-router-dom";

/** Shown when the Bridge Solver refuses today's Daily Puzzle pair (HTTP 403 anti_cheat_locked). */
export default function AntiCheatBanner() {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-2.5 rounded-xl border border-amber-700/60 bg-amber-950/30 px-5 py-4 text-sm font-medium text-amber-200"
    >
      <Lock className="h-4 w-4 shrink-0" />
      <span>
        🔒 Playing today&apos;s Daily Puzzle? Solve it or forfeit at{" "}
        <Link to="/tools/daily" className="font-semibold underline hover:text-amber-100">
          /tools/daily
        </Link>{" "}
        first!
      </span>
    </div>
  );
}
