import { useEffect, useRef, useState } from "react";
import { Loader2, Play, Trophy, Vote } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import ExpandableText from "./ui/ExpandableText";
import AcquisitionControl from "./AcquisitionControl";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { cn } from "../lib/cn";
import { ApiError } from "../lib/api";
import { BRACKET_ROUNDS, isActiveMatchup } from "../lib/bracket";
import {
  useAdvanceBracket,
  useBracketVote,
  useJellyfinLookup,
  useMatchupCommentary,
} from "../lib/queries";
import type { Bracket, BracketFilm, BracketMatchup, BracketTape, JellyfinItemSummary, RunDetail } from "../types/api";

type Films = Record<string, BracketFilm>;

function filmOf(films: Films, id: number | null): BracketFilm | null {
  return id === null ? null : (films[String(id)] ?? { title: `Film ${id}`, release_year: null, poster_path: null, runtime: null, overview: "", tagline: "" });
}

/** The tournament: Round of 16 -> Quarterfinals -> Semifinals -> Finals -> Champion podium. */
export default function BracketView({
  run,
  users,
  currentUserId,
}: {
  run: RunDetail;
  users: { id: string; display_name: string }[] | undefined;
  currentUserId: string | undefined;
}) {
  const bracket = run.rules_config.bracket as Bracket;
  const films = (run.rules_config.bracket_films ?? {}) as Films;
  const tmdbIds = Object.keys(films).map(Number);
  const { data: server } = useJellyfinLookup(tmdbIds);
  const locked = run.status !== "active";
  const [openId, setOpenId] = useState<string | null>(null);
  const matchups = BRACKET_ROUNDS.flatMap((round) => bracket[round.key]);
  const open = matchups.find((m) => m.id === openId) ?? null;

  return (
    <div className="flex flex-col gap-3">
      {bracket.champion !== null && (
        <ChampionBanner
          movieId={bracket.champion}
          film={filmOf(films, bracket.champion)}
          availability={server?.[String(bracket.champion)]}
        />
      )}
      <div
        aria-label="Tournament bracket"
        className="flex min-h-[34rem] items-stretch gap-4 overflow-x-auto rounded-xl border border-app-border bg-app-surface/40 p-4"
      >
        {BRACKET_ROUNDS.map((round) => (
          <section key={round.key} aria-label={round.label} className="flex w-48 shrink-0 flex-col">
            <h3 className="mb-2 text-center text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              {round.label}
            </h3>
            <div className="flex flex-1 flex-col justify-around gap-2">
              {bracket[round.key].map((matchup) => (
                <MatchupBox
                  key={matchup.id}
                  matchup={matchup}
                  films={films}
                  server={server}
                  interactive={!locked && isActiveMatchup(matchup)}
                  onOpen={() => setOpenId(matchup.id)}
                />
              ))}
            </div>
          </section>
        ))}
        <Podium champion={bracket.champion} films={films} server={server} />
      </div>

      {open && (
        <MatchupCard
          runId={run.id}
          matchup={open}
          films={films}
          server={server}
          users={users}
          participantIds={run.participants.map((p) => p.user_id)}
          currentUserId={currentUserId}
          tableMode={!!run.rules_config.table_mode}
          cachedTape={run.rules_config.bracket_tape?.[open.id] ?? null}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}

function ChampionBanner({
  movieId,
  film,
  availability,
}: {
  movieId: number;
  film: BracketFilm | null;
  availability: JellyfinItemSummary | undefined;
}) {
  if (!film) return null;

  return (
    <section
      aria-label="Tournament champion"
      className="flex flex-col gap-4 rounded-xl border border-amber-300/60 bg-amber-400/10 p-4 shadow-[0_0_30px_-8px_rgba(251,191,36,0.35)] sm:flex-row"
    >
      <MoviePoster movieId={movieId} path={film.poster_path} title={film.title} className="mx-auto w-28 shrink-0 sm:mx-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-amber-300">
          <Trophy className="h-4 w-4" /> March Madness Champion
          <OnServerBadge onServer={availability?.on_server} />
        </p>
        <h3 className="text-lg font-bold text-amber-100">
          {film.title}
          {film.release_year ? <span className="ml-2 text-sm font-normal text-amber-200/70">({film.release_year})</span> : null}
        </h3>
        <ExpandableText
          text={film.overview}
          fallback="No logline available."
          lines={3}
          className="text-sm leading-relaxed text-zinc-300"
        />
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          {availability?.on_server && availability.play_url ? (
            <a
              href={availability.play_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-9 items-center gap-2 rounded-md bg-emerald-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-500"
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              Play on Jellyfin
            </a>
          ) : (
            <AcquisitionControl
              tmdbId={movieId}
              title={film.title}
              onServer={availability?.on_server}
            />
          )}
        </div>
      </div>
    </section>
  );
}

function MatchupBox({
  matchup,
  films,
  server,
  interactive,
  onOpen,
}: {
  matchup: BracketMatchup;
  films: Films;
  server: Record<string, JellyfinItemSummary> | undefined;
  interactive: boolean;
  onOpen: () => void;
}) {
  const rows = [matchup.a, matchup.b];
  return (
    <button
      type="button"
      disabled={!interactive}
      onClick={onOpen}
      aria-label={`Matchup ${matchup.id}`}
      className={cn(
        "flex flex-col overflow-hidden rounded-lg border text-left text-xs transition-colors",
        interactive
          ? "border-accent/60 bg-app-surface hover:bg-app-surface-hover"
          : "cursor-default border-app-border bg-app-bg/60",
      )}
    >
      {rows.map((id, index) => {
        const film = filmOf(films, id);
        const won = id !== null && matchup.winner === id;
        const lost = id !== null && matchup.winner !== null && !won;
        return (
          <span
            key={index}
            className={cn(
              "flex items-center gap-1.5 px-2 py-1.5",
              index === 0 && "border-b border-app-border/70",
              won && "bg-emerald-500/10 font-semibold text-emerald-300",
              lost && "text-zinc-600 line-through",
              id === null && "italic text-zinc-600",
            )}
          >
            <span className="min-w-0 flex-1 truncate">{film ? film.title : "TBD"}</span>
            {id !== null && server?.[String(id)]?.on_server === true && (
              <span
                title="On Jellyfin"
                aria-label="On Jellyfin"
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400"
              />
            )}
            {film?.release_year && <span className="shrink-0 text-[10px] text-zinc-500">{film.release_year}</span>}
            {won && <span aria-hidden>✓</span>}
          </span>
        );
      })}
      {interactive && (
        <span className="bg-accent/10 px-2 py-0.5 text-center text-[10px] font-semibold text-accent">
          Decide matchup
        </span>
      )}
    </button>
  );
}

function Podium({
  champion,
  films,
  server,
}: {
  champion: number | null;
  films: Films;
  server: Record<string, JellyfinItemSummary> | undefined;
}) {
  const film = filmOf(films, champion);
  return (
    <section aria-label="Champion podium" className="flex w-44 shrink-0 flex-col items-center justify-center gap-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-amber-300">Champion</h3>
      <div
        className={cn(
          "flex w-full flex-col items-center gap-2 rounded-xl border px-3 py-4 text-center",
          film
            ? "border-amber-300/60 bg-amber-400/10 shadow-[0_0_30px_-8px_rgba(251,191,36,0.6)]"
            : "border-dashed border-app-border",
        )}
      >
        <Trophy className={cn("h-8 w-8", film ? "text-amber-300" : "text-zinc-700")} />
        {film ? (
          <>
            <MoviePoster movieId={champion ?? undefined} path={film.poster_path} title={film.title} className="w-24" />
            <p className="text-sm font-semibold text-amber-100">{film.title}</p>
            <p className="text-[11px] text-amber-200/70">{film.release_year ?? ""}</p>
            <OnServerBadge onServer={champion === null ? undefined : server?.[String(champion)]?.on_server} />
          </>
        ) : (
          <p className="text-xs text-zinc-600">To be crowned</p>
        )}
      </div>
    </section>
  );
}

/** Both films side by side: poster, runtime, logline - and the buttons that settle the matchup. */
function MatchupCard({
  runId,
  matchup,
  films,
  server,
  users,
  participantIds,
  currentUserId,
  tableMode,
  cachedTape,
  onClose,
}: {
  runId: string;
  matchup: BracketMatchup;
  films: Films;
  server: Record<string, JellyfinItemSummary> | undefined;
  users: { id: string; display_name: string }[] | undefined;
  participantIds: string[];
  currentUserId: string | undefined;
  tableMode: boolean;
  cachedTape: BracketTape | null;
  onClose: () => void;
}) {
  const advance = useAdvanceBracket(runId);
  const tape = useMatchupCommentary(runId);
  const selectedTape =
    cachedTape || (tape.data?.matchup_id === matchup.id ? tape.data.tape : null);
  const requestedMatchups = useRef(new Set<string>());
  const castVote = useBracketVote(runId);
  const [error, setError] = useState<string | null>(null);
  const partners = participantIds.length > 1;
  const pending = advance.isPending || castVote.isPending;

  useEffect(() => {
    if (
      matchup.a !== null &&
      matchup.b !== null &&
      !selectedTape &&
      !tape.isPending &&
      !requestedMatchups.current.has(matchup.id)
    ) {
      requestedMatchups.current.add(matchup.id);
      tape.mutate(matchup.id);
    }
  }, [matchup.id, matchup.a, matchup.b, selectedTape, tape.isPending, tape.mutate]);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  }

  const sides = [matchup.a, matchup.b] as const;
  const nameOf = (userId: string) => users?.find((u) => u.id === userId)?.display_name ?? "Someone";

  return (
    <Modal open onClose={onClose} title="Matchup" widthClassName="max-w-3xl">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_auto_1fr]">
        {sides.map((id, index) => {
          if (id === null) return null;
          const film = filmOf(films, id) as BracketFilm;
          const availability = server?.[String(id)];
          const votes = Object.entries(matchup.votes).filter(([, movieId]) => movieId === id);
          const myVote = currentUserId ? matchup.votes[currentUserId] === id : false;
          return (
            <div key={id} className="contents">
              {index === 1 && (
                <div className="flex flex-col items-center justify-center gap-3 sm:w-44">
                  <span className="text-lg font-black text-zinc-600">VS</span>
                  {selectedTape ? (
                    <div aria-label="Tale of the Tape" className="w-full rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/5 p-3">
                      <blockquote className="text-center font-serif text-xs italic leading-relaxed text-fuchsia-100">
                        “{selectedTape.headline}”
                      </blockquote>
                      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
                        {selectedTape.axes.map((axis) => (
                          <div key={axis.name} className="contents">
                            <dt className={cn("text-right", axis.contrast ? "text-fuchsia-200" : "text-emerald-300")}>{axis.name}</dt>
                            <dd className="truncate text-zinc-400">{axis.left ?? "—"} / {axis.right ?? "—"}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  ) : tape.isPending ? (
                    <Loader2 aria-label="Preparing matchup tape" className="h-4 w-4 animate-spin text-fuchsia-300" />
                  ) : tape.isError ? (
                    <p role="alert" className="text-center text-[10px] text-amber-400">
                      {tape.error instanceof ApiError ? tape.error.message : "The matchup tape could not be prepared."}
                    </p>
                  ) : null}
                </div>
              )}
              <article
                className={cn(
                  "flex flex-col gap-3 rounded-xl border p-3",
                  onServerCardClass(availability?.on_server),
                )}
              >
                <MoviePoster movieId={id} path={film.poster_path} title={film.title} className="mx-auto w-36" />
                <div className="text-center">
                  <h4 className="text-sm font-semibold text-zinc-100">{film.title}</h4>
                  <p className="text-[11px] text-zinc-500">
                    {film.release_year ?? "—"}
                    {film.runtime ? ` · ${film.runtime} min` : ""}
                  </p>
                </div>
                <ExpandableText
                  text={film.overview}
                  fallback="No logline available."
                  lines={4}
                  expandMode="dialog"
                  dialogTitle={`${film.title} overview`}
                  lead={film.tagline ? <em className="text-zinc-300">&ldquo;{film.tagline}&rdquo;</em> : undefined}
                  className="text-xs leading-relaxed text-zinc-400"
                />
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <OnServerBadge onServer={availability?.on_server} />
                  <AcquisitionControl tmdbId={id} title={film.title} onServer={availability?.on_server} />
                </div>
                {partners && (
                  <p className="text-center text-[11px] text-zinc-500" aria-label="Votes">
                    🗳️ {votes.length} vote{votes.length === 1 ? "" : "s"}
                    {votes.length > 0 && ` (${votes.map(([userId]) => nameOf(userId)).join(", ")})`}
                  </p>
                )}
                <div className="mt-auto flex flex-col gap-1.5">
                  {partners && !tableMode && (
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() =>
                        void run(() => castVote.mutateAsync({ matchup_id: matchup.id, movie_id: id }))
                      }
                      className={cn(
                        "flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-60",
                        myVote
                          ? "border-accent bg-accent/15 text-accent"
                          : "border-app-border text-zinc-300 hover:bg-app-surface-hover",
                      )}
                    >
                      <Vote className="h-3.5 w-3.5" /> {myVote ? "Your vote" : "Vote"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() =>
                      void run(() =>
                        advance.mutateAsync({ matchup_id: matchup.id, winning_movie_id: id }),
                      )
                    }
                    className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                  >
                    {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                    🏆 Advance Winner
                  </button>
                </div>
              </article>
            </div>
          );
        })}
      </div>
      {tableMode && partners && <div className="mt-4 flex flex-col gap-2" aria-label="Table votes">
        {participantIds.map((participantId) => <div key={participantId} className="flex flex-wrap items-center gap-2">
          <span className="min-w-20 text-sm text-zinc-300">{nameOf(participantId)}:</span>
          {sides.map((movieId, index) => movieId !== null && <button key={movieId} type="button"
            aria-pressed={matchup.votes[participantId] === movieId} disabled={pending || !isActiveMatchup(matchup)}
            className={cn("rounded border px-4 py-2 text-sm", matchup.votes[participantId] === movieId ? "border-accent text-accent" : "border-app-border")}
            onClick={async () => {
              setError(null);
              try {
                const updated = await castVote.mutateAsync({ matchup_id: matchup.id, movie_id: movieId, acting_participant_id: participantId });
                const resolved = BRACKET_ROUNDS.flatMap((round) => updated.rules_config.bracket?.[round.key] ?? [])
                  .find((item) => item.id === matchup.id)?.winner;
                if (resolved) onClose();
              } catch (cause) { setError(cause instanceof Error ? cause.message : "Vote failed."); }
            }}>{index === 0 ? "A" : "B"} · {filmOf(films, movieId)?.title}</button>)}
        </div>)}
      </div>}
      {partners && (
        <p className="mt-3 text-center text-[11px] text-zinc-500">
          A majority of the table's votes decides the matchup; on a tie anyone can advance the winner.
        </p>
      )}
      {error && <p className="mt-3 text-center text-xs text-red-300">{error}</p>}
    </Modal>
  );
}
