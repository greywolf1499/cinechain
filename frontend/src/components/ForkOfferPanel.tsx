import { useState } from "react";
import { Loader2, Star, Undo2 } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import ExpandableText from "./ui/ExpandableText";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import {
  useAcceptForkMovie,
  useGoldenVeto,
  useLlmStatus,
  useMovieDetail,
  useVetoForkMovie,
  useWithdrawFork,
} from "../lib/queries";
import { useAuthStore } from "../store/authStore";
import { useTableSeat } from "../lib/tableMode";
import type { PendingFork, PitchResult, RunDetail, RunStep, UserSummary } from "../types/api";

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong.";
}

/** Blind Fork, step 2. The offering player sees a status banner (and can withdraw); the partner
 * gets the "Partner's Offer" overlay: veto one of three films, then accept one of the two left. */
export default function ForkOfferPanel({
  run,
  users,
  frontier,
}: {
  run: RunDetail;
  users: UserSummary[] | undefined;
  /** The film the offered films would follow. */
  frontier: RunStep | undefined;
}) {
  const fork = run.rules_config.pending_fork;
  const meId = useAuthStore((s) => s.user?.id);
  const seat = useTableSeat(run.id);
  const actorId = run.rules_config.table_mode ? seat?.id : meId;
  if (seat?.pending) return null;
  if (!fork || run.status !== "active") return null;
  const offerer = users?.find((u) => u.id === fork.offered_by_id)?.display_name ?? "Your partner";
  if (fork.offered_by_id === actorId) return <OfferWaitingBanner runId={run.id} fork={fork} />;
  return (
    <PartnerOffer key={`${fork.offered_at}:${actorId}`} runId={run.id} fork={fork} offerer={offerer} frontier={frontier}
      tableTokens={run.rules_config.table_mode ? users?.find((u) => u.id === actorId)?.veto_tokens ?? 0 : undefined} />
  );
}

function OfferWaitingBanner({ runId, fork }: { runId: string; fork: PendingFork }) {
  const withdraw = useWithdrawFork(runId);
  const vetoed = fork.vetoed_movie_id !== undefined;
  return (
    <div
      role="status"
      className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-fuchsia-400/30 bg-fuchsia-500/5 px-4 py-3"
    >
      <span aria-hidden className="text-xl">🎭</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-fuchsia-200">Blind Fork offered</p>
        <p className="text-xs text-zinc-400">
          {vetoed
            ? "Your partner struck one film and is choosing between the other two."
            : "Waiting for your partner to veto one of your three films and pick from the rest."}{" "}
          Logging is paused until they answer.
        </p>
        {withdraw.isError && <p className="mt-1 text-xs text-red-400">{errorMessage(withdraw.error)}</p>}
      </div>
      <button
        type="button"
        disabled={withdraw.isPending}
        onClick={() => withdraw.mutate()}
        className="flex items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
      >
        {withdraw.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Undo2 className="h-3.5 w-3.5" />}
        Withdraw offer
      </button>
    </div>
  );
}

function PartnerOffer({
  runId,
  fork,
  offerer,
  frontier,
  tableTokens,
}: {
  runId: string;
  fork: PendingFork;
  offerer: string;
  frontier: RunStep | undefined;
  tableTokens?: number;
}) {
  const [open, setOpen] = useState(true);
  const [advice, setAdvice] = useState<Record<number, string>>({});
  const [adviceState, setAdviceState] = useState<"idle" | "loading" | "error">("idle");
  const [adviceError, setAdviceError] = useState<string | null>(null);
  const { data: llm } = useLlmStatus();
  const veto = useVetoForkMovie(runId);
  const accept = useAcceptForkMovie(runId);
  const goldenVeto = useGoldenVeto(runId);
  const ownTokens = useAuthStore((s) => s.user?.veto_tokens ?? 0);
  const tokens = tableTokens ?? ownTokens;
  const vetoPhase = fork.movie_ids.length > 2;
  const busy = veto.isPending || accept.isPending || goldenVeto.isPending;
  const error = [veto, accept, goldenVeto].find((m) => m.isError)?.error;

  async function askCritic() {
    if (!frontier) return;
    setAdviceState("loading");
    setAdviceError(null);
    try {
      const results = await Promise.all(
        fork.movie_ids.map(async (movieId) => {
          const result = await api.post<PitchResult>("/engine/pitch", {
            previous_movie_id: frontier.movie_id,
            candidate_movie_id: movieId,
            link_label: (fork.links?.[String(movieId)]?.actor_name as string | undefined) ?? null,
            style: "critic",
          });
          return [movieId, result.pitch] as const;
        }),
      );
      setAdvice(Object.fromEntries(results));
      setAdviceState("idle");
    } catch (err) {
      setAdviceState("error");
      setAdviceError(errorMessage(err));
    }
  }

  return (
    <>
      <div
        role="status"
        className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/10 px-4 py-3"
      >
        <span aria-hidden className="text-xl">🎭</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-fuchsia-200">{offerer} made you an offer</p>
          <p className="text-xs text-zinc-400">
            {vetoPhase ? "Veto one of three films, then pick your favourite." : "Pick your favourite of the last two."}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-md bg-fuchsia-400 px-3 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-fuchsia-300"
        >
          Open offer
        </button>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="Partner's Offer" widthClassName="max-w-4xl">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-zinc-400">
            {vetoPhase ? (
              <>
                <strong className="text-zinc-200">{offerer}</strong> picked three films that each connect to
                the chain. Strike one with <strong className="text-red-300">🚫 Veto</strong>, then choose.
              </>
            ) : (
              <>One film is gone. Commit your favourite of the remaining two.</>
            )}
          </p>

          <div className="flex flex-wrap items-center gap-2">
            {llm?.enabled && (
              <button
                type="button"
                disabled={adviceState === "loading" || !frontier}
                onClick={askCritic}
                className="flex items-center gap-1.5 rounded-full border border-fuchsia-400/30 bg-fuchsia-500/10 px-3 py-1 text-xs font-medium text-fuchsia-300 transition-colors hover:bg-fuchsia-500/20 disabled:opacity-60"
              >
                {adviceState === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <span aria-hidden>✨</span>}
                AI Critic Veto Advice
              </button>
            )}
            {adviceState === "error" && <span className="text-xs text-amber-300">{adviceError}</span>}
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {fork.movie_ids.map((movieId) => (
              <OfferCard
                key={movieId}
                movieId={movieId}
                advice={advice[movieId]}
                actorName={fork.links?.[String(movieId)]?.actor_name as string | undefined}
              >
                {vetoPhase ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => veto.mutate(movieId)}
                    className="flex items-center justify-center gap-1.5 rounded-md border border-red-900 bg-red-950/60 px-3 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-900/60 disabled:opacity-60"
                  >
                    🚫 Veto
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => accept.mutate(movieId, { onSuccess: () => setOpen(false) })}
                    className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                  >
                    {accept.isPending && accept.variables === movieId ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <span aria-hidden>🍿</span>
                    )}
                    Accept &amp; Watch
                  </button>
                )}
              </OfferCard>
            ))}
          </div>

          {fork.vetoed_movie_id !== undefined && <VetoedNote movieId={fork.vetoed_movie_id} />}

          {error && <p role="alert" className="text-xs text-red-400">{errorMessage(error)}</p>}

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-app-border pt-3">
            <p className="text-[11px] text-zinc-500">
              Hate the whole offer? Spend a Golden Veto to tear it up and make them pick again.
            </p>
            <button
              type="button"
              disabled={busy || tokens < 1}
              onClick={() => goldenVeto.mutate("fork", { onSuccess: () => setOpen(false) })}
              title={tokens < 1 ? "No Golden Veto tokens left - you get one every 30 days" : undefined}
              className="flex items-center gap-1.5 rounded-md border border-amber-700/60 bg-amber-950/40 px-3 py-1.5 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-900/40 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Star className="h-3.5 w-3.5" />
              Golden Veto ({tokens} left)
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}

function OfferCard({
  movieId,
  advice,
  actorName,
  children,
}: {
  movieId: number;
  advice: string | undefined;
  actorName: string | undefined;
  children: React.ReactNode;
}) {
  const { movie, isHydrating } = useMovieDetail(movieId);
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-app-border bg-app-bg p-3">
      <MoviePoster path={movie?.poster_path ?? null} title={movie?.title ?? "Loading"} className="w-full" />
      <div>
        <p className="text-sm font-semibold text-zinc-100">{movie?.title ?? "Loading..."}</p>
        <p className="text-[11px] text-zinc-500">
          {movie?.release_year ?? "—"}
          {actorName && <> &middot; via {actorName}</>}
        </p>
      </div>
      <ExpandableText
        text={movie?.overview}
        fallback={isHydrating ? "Fetching the plot..." : "No overview available."}
        lines={4}
        className={cn("text-xs leading-relaxed text-zinc-400", isHydrating && "animate-pulse")}
      />
      {advice && (
        <p className="rounded-md border border-fuchsia-400/30 bg-fuchsia-500/10 px-2.5 py-2 text-xs italic text-fuchsia-200">
          &ldquo;{advice}&rdquo;
        </p>
      )}
      <div className="mt-auto flex flex-col pt-1">{children}</div>
    </div>
  );
}

function VetoedNote({ movieId }: { movieId: number }) {
  const { movie } = useMovieDetail(movieId);
  return (
    <p className="text-xs text-zinc-500">
      Vetoed: <s>{movie?.title ?? `film ${movieId}`}</s>
    </p>
  );
}
