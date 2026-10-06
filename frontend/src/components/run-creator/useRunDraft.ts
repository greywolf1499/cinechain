import { useEffect, useMemo, useReducer } from "react";
import { AUTEUR_MARATHON } from "../../lib/auteurTrack";
import { BRACKET_SIZE, MARCH_MADNESS } from "../../lib/bracket";
import { NO_BOUNTY_MODES } from "../../lib/bounties";
import { METHOD_ACTOR } from "../../lib/careerTrack";
import { REGIONAL_DEEP_DIVE } from "../../lib/expedition";
import { usesCastLinks } from "../../lib/gameModes";
import { DEFAULT_GENRE_CYCLE, DEFAULT_SWING_FREQUENCY, GENRE_PENDULUM } from "../../lib/pendulum";
import { clearModifiers, modifierPayload } from "../../lib/modifiers";
import { DEFAULT_TARGET_POINTS, RT_SPLIT } from "../../lib/splitScore";
import { TUG_OF_WAR } from "../../lib/tugOfWar";
import { RABBIT_HOLE } from "../../lib/rabbitHole";
import { engineDefaultRules } from "../RulesetFields";
import { parseRawRules, RAW_RULES_EXAMPLE } from "../RawRulesEditor";
import { useCuratedSlices, useSeedOptions } from "../../lib/queries";
import type { CuratedListSummary, EngineMeta, MovieSummary, PersonSummary, RawRulesConfig, RulesConfig, TugDimension } from "../../types/api";

export interface RunDraft {
  name: string;
  gameType: string;
  participantIds: string[];
  seedMovie: MovieSummary | null;
  tailSeedMovie: MovieSummary | null;
  rules: RulesConfig;
  canonListId: string;
  targetDecade: number;
  tugDimension: TugDimension;
  genreCycle: string[];
  swingFrequency: number;
  bracketFilms: MovieSummary[];
  actor: PersonSummary | null;
  director: PersonSummary | null;
  diveListId: string;
  diveCountry: string;
  diveDecade: string;
  bountyBoard: boolean;
  splitTarget: number;
  escapeDepth: number | null;
  rawMode: boolean;
  rawText: string;
}

type DraftAction = { type: "patch"; changes: Partial<RunDraft> } | { type: "reset" };

export function initialDraft(): RunDraft {
  return {
    name: "",
    gameType: "cinechain",
    participantIds: [],
    seedMovie: null,
    tailSeedMovie: null,
    rules: { preset: "loading", allow_repeats: "strict", no_consecutive_actor: false,
      max_cast_order: 15, min_runtime: 0, wildcards_budget: 0 },
    canonListId: "",
    targetDecade: 1970,
    tugDimension: "era",
    genreCycle: [...DEFAULT_GENRE_CYCLE],
    swingFrequency: DEFAULT_SWING_FREQUENCY,
    bracketFilms: [],
    actor: null,
    director: null,
    diveListId: "",
    diveCountry: "",
    diveDecade: "",
    bountyBoard: false,
    splitTarget: DEFAULT_TARGET_POINTS,
    escapeDepth: null,
    rawMode: false,
    rawText: "",
  };
}

function reducer(state: RunDraft, action: DraftAction): RunDraft {
  if (action.type === "reset") return initialDraft();
  if (action.changes.gameType && action.changes.gameType !== state.gameType) {
    return {
      ...state,
      ...action.changes,
      rules: initialDraft().rules,
      seedMovie: null,
      tailSeedMovie: null,
    };
  }
  if (["canonListId", "targetDecade", "diveListId", "diveCountry", "diveDecade", "rawText", "rawMode"].some(
    (key) => key in action.changes,
  )) {
    return { ...state, ...action.changes, seedMovie: null, tailSeedMovie: null };
  }
  return { ...state, ...action.changes };
}

export interface CreateRunPayload {
  name: string;
  game_type: string;
  participant_user_ids: string[];
  seed_movie_id?: number | null;
  tail_seed_movie_id: number | null;
  rules_config: RulesConfig | RawRulesConfig;
}

export function useRunDraft(
  engines: EngineMeta[] | undefined,
  isAdmin: boolean,
  curatedLists: CuratedListSummary[] | undefined,
) {
  const [draft, dispatch] = useReducer(reducer, undefined, initialDraft);
  const engine = engines?.find((item) => item.game_type === draft.gameType);
  useEffect(() => {
    if (engine && draft.rules.preset === "loading") {
      dispatch({ type: "patch", changes: { rules: engineDefaultRules(engine) } });
    }
  }, [engine, draft.rules.preset]);
  const engineSupportsRaw =
    engine?.capabilities.includes("json_rules") ?? draft.gameType === "cinechain";
  const rawEnabled = draft.rawMode && isAdmin && engineSupportsRaw;
  const rawParse = parseRawRules(draft.rawText);
  const isTracker = !!engine && !engine.capabilities.includes("discover_candidates");
  const isTunnel = engine?.seed_policy === "pair";
  const needsCanonList = draft.gameType === "canon_island";
  const needsDecade = draft.gameType === "decade_sieve";
  const isBracket = draft.gameType === MARCH_MADNESS;
  const isMethodActor = draft.gameType === METHOD_ACTOR;
  const isAuteur = draft.gameType === AUTEUR_MARATHON;
  const isDive = draft.gameType === REGIONAL_DEEP_DIVE;
  const isSplit = draft.gameType === RT_SPLIT;
  const canBounty = !NO_BOUNTY_MODES.has(draft.gameType);
  const islandLists = (curatedLists ?? []).filter((list) => list.is_enabled && list.total_items > 0);
  const castLinked = usesCastLinks(draft.gameType, draft.rules);
  const formRules: RulesConfig = {
    ...(isBracket ? { bracket_movie_ids: draft.bracketFilms.map((film) => film.tmdb_id) } : {}),
    ...(isMethodActor && draft.actor ? { actor_id: draft.actor.person_id } : {}),
    ...(isAuteur && draft.director ? { director_id: draft.director.person_id } : {}),
    ...(isSplit ? { target_points: draft.splitTarget } : {}),
    ...(draft.gameType === RABBIT_HOLE && draft.escapeDepth !== null
      ? { escape_depth: draft.escapeDepth }
      : {}),
    ...(draft.bountyBoard && canBounty ? { bounty_board: true } : {}),
    ...(isDive
      ? {
          curated_list_id: draft.diveListId,
          ...(draft.diveCountry ? { target_country: draft.diveCountry } : {}),
          ...(draft.diveDecade ? { target_decade: Number(draft.diveDecade) } : {}),
        }
      : {}),
    ...clearModifiers(draft.rules),
    ...modifierPayload(draft.gameType, draft.rules, engine?.capabilities),
    ...(needsCanonList ? { allowed_curated_list_id: draft.canonListId } : {}),
    ...(needsDecade ? { target_decade: draft.targetDecade } : {}),
    ...(draft.gameType === TUG_OF_WAR
      ? { dimension: draft.tugDimension }
      : {}),
    ...(draft.gameType === GENRE_PENDULUM
      ? {
          genre_cycle: draft.genreCycle.length > 0 ? draft.genreCycle : DEFAULT_GENRE_CYCLE,
          swing_frequency: draft.swingFrequency,
        }
      : {}),
  };
  const effectiveRules = rawEnabled && rawParse.value ? rawParse.value : formRules;
  const slices = useCuratedSlices(isDive ? draft.diveListId : undefined);
  const seedSettingsReady = (
    !needsCanonList || !!effectiveRules.allowed_curated_list_id
  ) && (
    !isDive || (!!effectiveRules.curated_list_id && (
      !!effectiveRules.target_country || effectiveRules.target_decade !== undefined
    ))
  ) && (!rawEnabled || !rawParse.error);
  const seedOptions = useSeedOptions(
    draft.gameType,
    effectiveRules,
    engine?.seed_policy === "derived" && seedSettingsReady,
    !!slices.data?.indexing,
  );
  const seedsReady = engine?.seed_policy !== "derived" || (
    seedSettingsReady && !!seedOptions.data && !seedOptions.isError
  );
  const sameSeeds = isTunnel && !!draft.seedMovie && draft.seedMovie.tmdb_id === draft.tailSeedMovie?.tmdb_id;
  const missingMode =
    (needsCanonList && !draft.canonListId) ||
    (isTunnel && (!draft.seedMovie || !draft.tailSeedMovie)) ||
    (isBracket && draft.bracketFilms.length !== BRACKET_SIZE) ||
    (isMethodActor && !draft.actor) ||
    (isAuteur && !draft.director) ||
    (isDive && (!draft.diveListId || (!draft.diveCountry && !draft.diveDecade)));

  const blockers = useMemo(() => {
    const messages: string[] = [];
    if (!engine) messages.push("Wait for game modes to load.");
    if (!draft.name.trim()) messages.push("Enter a run name.");
    if (needsCanonList && !draft.canonListId) messages.push("Choose a canon list.");
    if (isTunnel && !draft.seedMovie) messages.push("Choose Partner A's starting film.");
    if (isTunnel && !draft.tailSeedMovie) messages.push("Choose Partner B's starting film.");
    if (sameSeeds) messages.push("Partners need different starting films.");
    if (isBracket && draft.bracketFilms.length !== BRACKET_SIZE) {
      messages.push(`Pick ${BRACKET_SIZE} bracket films (${draft.bracketFilms.length}/${BRACKET_SIZE}).`);
    }
    if (isMethodActor && !draft.actor) messages.push("Choose an actor.");
    if (isAuteur && !draft.director) messages.push("Choose a director.");
    if (isDive && !draft.diveListId) messages.push("Choose a canon list to slice.");
    if (isDive && !draft.diveCountry && !draft.diveDecade) {
      messages.push("Choose a country, a decade, or both for the slice.");
    }
    if (
      draft.gameType === RABBIT_HOLE &&
      draft.escapeDepth !== null &&
      (!Number.isInteger(draft.escapeDepth) || draft.escapeDepth < 25 || draft.escapeDepth > 60)
    ) {
      messages.push("Set the Rabbit Hole escape depth between 25 and 60.");
    }
    if (rawEnabled && rawParse.error) messages.push(`Fix the raw rules JSON: ${rawParse.error}`);
    if (engine?.seed_policy === "derived" && seedSettingsReady) {
      if (seedOptions.isError) messages.push(`Check seed settings: ${seedOptions.error.message}`);
      else if (!seedOptions.data) messages.push("Checking eligible seed films...");
      else if (draft.seedMovie && !seedOptions.data.allowed_ids?.includes(draft.seedMovie.tmdb_id)) {
        messages.push("Choose a seed from your eligible slice.");
      }
    }
    if (isDive && seedSettingsReady) {
      if (slices.isError) messages.push(`Check slice availability: ${slices.error.message}`);
      else if (!slices.data) messages.push("Checking slice availability...");
      else {
        const country = typeof effectiveRules.target_country === "string" ? effectiveRules.target_country.toUpperCase() : undefined;
        const decade = typeof effectiveRules.target_decade === "number" ? effectiveRules.target_decade : undefined;
        const count = country && decade !== undefined
          ? slices.data.pairs[`${country}:${decade}`]
          : country ? slices.data.countries[country] : slices.data.decades[String(decade)];
        if (!count) messages.push("Choose a non-empty indexed slice.");
      }
    }
    return messages;
  }, [draft, engine, isAuteur, isBracket, isDive, isMethodActor, isTunnel, needsCanonList, rawEnabled, rawParse.error, sameSeeds, seedSettingsReady, seedOptions.data, seedOptions.isError, seedOptions.error, slices.data, slices.isError, slices.error, effectiveRules.target_country, effectiveRules.target_decade]);

  function buildPayload(): CreateRunPayload {
    const rules = rawEnabled && rawParse.value ? rawParse.value : formRules;
    return {
      name: draft.name.trim(),
      game_type: draft.gameType,
      participant_user_ids: draft.participantIds,
      ...(engine?.seed_policy !== "none" ? { seed_movie_id: draft.seedMovie?.tmdb_id ?? null } : {}),
      tail_seed_movie_id: isTunnel ? (draft.tailSeedMovie?.tmdb_id ?? null) : null,
      rules_config: rules,
    };
  }

  function toggleRawMode() {
    if (!draft.rawMode && !draft.rawText.trim()) {
      dispatch({ type: "patch", changes: { rawText: JSON.stringify({ ...RAW_RULES_EXAMPLE, ...formRules }, null, 2) } });
    }
    dispatch({ type: "patch", changes: { rawMode: !draft.rawMode } });
  }

  return {
    draft,
    dispatch,
    update: (changes: Partial<RunDraft>) => dispatch({ type: "patch", changes }),
    reset: () => dispatch({ type: "reset" }),
    engine,
    engineSupportsRaw,
    rawEnabled,
    rawParse,
    isTracker,
    isTunnel,
    canBounty,
    islandLists,
    castLinked,
    formRules,
    effectiveRules,
    seedOptions,
    seedsReady,
    blockers,
    missingMode,
    sameSeeds,
    buildPayload,
    toggleRawMode,
  };
}
