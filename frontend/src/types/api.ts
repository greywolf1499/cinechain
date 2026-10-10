// Hand-mirrored types for the CineChain FastAPI backend. Keep field names and
// nullability in lockstep with the pydantic schemas they mirror.

// --- auth (schemas/auth.py) ---

export interface User {
	id: string;
	username: string;
	display_name: string;
	is_admin: boolean;
	created_at: string;
	/** Golden Veto tokens left (one every 30 days). */
	veto_tokens?: number;
	last_veto_reset_at?: string | null;
}

export interface UserSummary {
	id: string;
	username: string;
	display_name: string;
	veto_tokens?: number;
}

export interface WatchlistStatus {
	letterboxd_username: string | null;
	synced_at: string | null;
	total_items: number;
	last_error: string | null;
}

// --- runs (schemas/runs.py) ---

export type RunStatus = "active" | "completed" | "forfeited" | "failed";
export type StepStatus = "watched" | "planned";
export type RepeatPolicy = "strict" | "penalty" | "allowed";
export type RulesPreset = string;

/** Opt-in Engine V2 conditions; a win/fail fires once the metric reaches / exceeds `count`. */
export interface RunCondition {
	type: string;
	count: number;
}

export type ChronoDirection = "climb" | "descent";
export type RuntimeStaircase = "ascending" | "descending";

export interface RulesConfig {
	modifiers?: { key: string; params: Record<string, ModifierParamValue> }[];
	table_mode?: boolean;
	preset: RulesPreset;
	allow_repeats: RepeatPolicy;
	no_consecutive_actor: boolean;
	max_cast_order: number;
	min_runtime: number;
	wildcards_budget: number; // -1 = unlimited
	/** The Rabbit Hole: forced (rule-breaking) steps cost a life instead of a wildcard. */
	lives_remaining?: number;
	max_lives?: number;
	allow_reroll?: boolean;
	rh_rules_version?: number;
	rh_seed?: number;
	tier_deck?: RabbitDeckTier[];
	fog?: "off" | "fog" | "abyss";
	periscope_charges?: number;
	revealed_depths?: number[];
	grid?: {
		size?: number;
		layout?: string;
		cells?: Array<{
			id: string;
			label?: string;
			emoji?: string;
			hidden?: boolean;
			difficulty?: number;
			query?: Record<string, unknown>;
		}>;
	};
	grid_seed?: number;
	grid_revealed?: string[];
	link?: string;
	size?: number;
	layout?: string;
	victory?: string;
	universe?: string;
	waypoints?: number[];
	waypoint_movie_ids?: number[];
	legs?: Array<{
		from: number;
		to: number;
		par?: number | null;
		distance?: number | null;
		reached_at_step?: number | null;
		path?: number[];
	}>;
	current_leg?: number;
	infiltration_par?: number;
	hop_limit?: number;
	target_list_id?: string;
	daily?: boolean;
	curses?: boolean;
	relics?: { skip_curse: number };
	reroll_tokens?: number;
	curse_skip?: number;
	/** The Rabbit Hole: optional depth at which escaping completes the run. */
	escape_depth?: number | null;
	/** Server-owned one-depth Rabbit Hole tier override. */
	tier_override?: { depth: number; tier?: number; predicate?: RabbitPredicate };
	/** Canon-Only Island: the CuratedList every film must belong to. */
	allowed_curated_list_id?: string;
	/** Decade Sieve: the decade start (e.g. 1970) every film must fall in. */
	target_decade?: number;
	/** Standalone modes: also require a shared actor/director between hops (hybrid play). */
	require_cast_link?: boolean;
	/** Chrono Climb / Descent: which way time must move. */
	direction?: "climb" | "descent";
	/** Engine V3 composable modifiers, usable on any graph engine (null/absent = off). */
	chrono_direction?: ChronoDirection | null;
	runtime_staircase?: RuntimeStaircase | null;
	/** May not pick a country visited within the last N steps (World Passport defaults to 3). */
	country_cooldown?: number | null;
	/** Blind Fork workflow: offer 3 films, the partner vetoes 1 and picks from the rest. */
	blind_fork?: boolean;
	/** Server-owned: the offer waiting for the partner's answer. */
	pending_fork?: PendingFork | null;
	/** Tug of War plane/traversal selected for new version-4 runs. */
	tug_plane?: { id: string; params: Record<string, unknown> };
	tug_traversal?: string;
	tug_plane_snapshot?: {
		id?: string;
		label?: string;
		blurb?: string;
		traversal?: string;
		poles?: Record<string, { label?: string }>;
	};
	tug_deal?: { movie_id: number; territory: "team_a" | "team_b" | "neutral" }[];
	tug_portals?: { remaining: number };
	/** Legacy Tug dimension for saved version-1–3 runs. */
	dimension?: "era" | "geography";
	target_lead?: number;
	tug_rules_version?: number;
	steal_enabled?: boolean;
	momentum_cap?: number;
	sudden_death_after?: number;
	sudden_death_every?: number;
	sudden_death_enabled?: boolean;
	/** Era dimension cut-offs: Team A = before `era_a_before`, Team B = after `era_b_after`. */
	era_a_before?: number;
	era_b_after?: number;
	/** Genre Pendulum: the genres the target swings through, and steps spent on each. */
	genre_cycle?: string[];
	swing_frequency?: number;
	/** Server-owned Tug of War state: points per team and who plays each side. */
	tug_scores?: { team_a: number; team_b: number };
	tug_players?: { team_a: string | null; team_b: string | null };
	/** Server-owned v2 Tug state, recomputed from watched steps. */
	tug_momentum?: {
		streak_team: "team_a" | "team_b" | null;
		streak: number;
		anchor: "team_a" | "team_b" | null;
		effective_target: number;
		sudden_death: boolean;
		next_team: "team_a" | "team_b";
		rope?: number;
		streaks?: { team_a: number; team_b: number };
		banks?: { team_a: boolean; team_b: boolean };
		rounds?: number;
		round_complete?: boolean;
		pulls: {
			step_id: string;
			puller: "team_a" | "team_b";
			territory: "team_a" | "team_b" | null;
			kind: "home" | "invasion" | "neutral" | "sudden_neutral";
			points: number;
			streak: number;
			multiplier: number;
		}[];
	};
	win_condition?: RunCondition | RunCondition[];
	fail_condition?: RunCondition | RunCondition[];
	/** Watchlist March Madness: the 16 seeds (or `seed_from_watchlist`) when creating; the server
	 * builds `bracket` + `bracket_films`. */
	bracket_movie_ids?: number[];
	seed_from_watchlist?: boolean;
	bracket?: Bracket;
	bracket_films?: Record<string, BracketFilm>;
	bracket_tape?: Record<string, BracketTape>;
	/** The Method Actor Marathon: the picked actor's TMDB id when creating; the server builds the rest. */
	actor_id?: number;
	actor?: { id: number; name: string };
	filmography?: (CareerFilm | AuteurFilm)[];
	career_eras?: CareerEra[];
	max_skip?: number | null;
	track_length?: "milestones" | "short" | "feature" | "full" | "endless";
	order?: "strict" | "relaxed" | "free";
	/** The Auteur Marathon: the picked director's TMDB id when creating; the server builds
	 * `director` + the chronological `filmography`. */
	director_id?: number;
	director?: { id: number; name: string };
	/** The Regional Deep Dive: the slice to create (list + country and/or decade); the server
	 * builds `expedition`. */
	curated_list_id?: string;
	target_country?: string;
	expedition?: Expedition;
	/** Bounty Board (any mode but the bracket / Rabbit Hole): wildcards are earned by completing
	 * bounties instead of being budgeted. The server draws `active_bounties`. */
	bounty_board?: boolean;
	active_bounties?: BountyId[];
	completed_bounties?: BountyId[];
	bounty_stars?: number;
	tunnel_hints_remaining?: number;
	bounty_discards_left?: number;
	bounty_roll_note?: string | null;
	/** AI bounty definitions by id (server-owned). */
	custom_bounties?: Record<BountyId, CustomBounty>;
	/** AI "Tale of the Tape" lines by matchup id (server-owned). */
	bracket_commentary?: Record<string, string>;
	vibe_state?: { state: "steady" | "fatigued" | "recovering"; load?: number; rolling_load?: number | null; chaser_recommended?: boolean; setpoint?: number; reason?: string };
	/** The Rotten Tomatoes Split: first team to this many points wins; scores are server-owned. */
	target_points?: number;
	split_scores?: { team_a: number; team_b: number };
	split_players?: { team_a: string | null; team_b: string | null };
	/** The Chaos Button: a handicap for the next film only (null/absent = none). */
	active_chaos?: ActiveChaos | null;
}

export interface ActiveChaos {
	id: string;
	/** "Time Machine: Pre-1970 only" */
	label: string;
	skipped?: string[];
}

/** A static bounty id ("short_king"...) or an AI bounty's ("ai_3f9a2c"). */
export type BountyId = string;

/** An AI-written bounty; its programmatic `rule` is evaluated server-side. */
export interface CustomBounty {
	id: BountyId;
	title: string;
	icon: string;
	description: string;
}

export interface SplitCandidate {
	movie_id: number;
	title: string;
	year: number | null;
	poster_path: string | null;
	critic_score: number;
	audience_score: number;
	divergence: number;
	favours: "critics" | "audience";
}

export interface SplitPool {
	omdb_enabled: boolean;
	min_divergence: number;
	scanned: number;
	candidates: SplitCandidate[];
}

export type BracketRound = "round_of_16" | "quarterfinals" | "semifinals" | "finals";

export interface BracketMatchup {
	id: string;
	a: number | null;
	b: number | null;
	winner: number | null;
	/** Partner votes: user id -> movie id. */
	votes: Record<string, number>;
}

export interface Bracket {
	round_of_16: BracketMatchup[];
	quarterfinals: BracketMatchup[];
	semifinals: BracketMatchup[];
	finals: BracketMatchup[];
	champion: number | null;
}

/** Card data snapshotted when the bracket was created. */
export interface BracketFilm {
	title: string;
	release_year: number | null;
	poster_path: string | null;
	runtime: number | null;
	overview: string;
	tagline: string;
}

export interface BracketTape {
	axes: { name: string; left: string | null; right: string | null; contrast: boolean }[];
	headline: string;
	source: "ai" | "template";
	version: number;
}

export type CareerMilestone = "debut" | "breakout" | "prestige_peak" | "modern_resurgence"
	| "first_theatrical" | "first_lead" | "genre_pivot" | "against_type" | "comeback" | "language_crossover";

export interface CareerEra {
	start_movie_id: number;
	end_movie_id: number;
	label: string;
}

export interface CareerContext {
	milestones?: CareerMilestone[];
	evidence?: Partial<Record<CareerMilestone, string>>;
	era_index?: number;
	era_label?: string | null;
	suggestions?: string[];
	suggestion_evidence?: string;
}

export interface CareerFilm extends CareerContext {
	movie_id: number;
	title: string;
	release_date: string;
	year: number;
	poster_path: string | null;
	character: string | null;
	order: number;
	vote_average: number;
	vote_count: number;
	age: number | null;
	milestones: CareerMilestone[];
}

export interface AuteurFilm extends CareerContext {
	movie_id: number;
	title: string;
	release_date: string;
	year: number;
	poster_path: string | null;
	runtime: number | null;
}

export interface ExpeditionFilm {
	movie_id: number;
	title: string;
	year: number | null;
	poster_path: string | null;
	runtime: number | null;
	badge_label: string;
	rank: number | null;
}

export interface Expedition {
	list_id: string;
	list_title: string;
	badge_prefix: string;
	badge_color: string;
	/** ISO 3166-1 alpha-2 code, or null for a decade-only slice. */
	country: string | null;
	country_name: string | null;
	decade: number | null;
	movie_ids: number[];
	films: ExpeditionFilm[];
}

export interface PersonSummary {
	person_id: number;
	name: string;
	profile_path: string | null;
	known_for_department: string | null;
	known_for: string[];
}

export interface TugTraversalMeta {
	id: string;
	label: string;
	graph: boolean;
}

export interface TugPlaneMeta {
	id: string;
	label: string;
	blurb: string;
	params_schema: { [key: string]: { type: string; default?: unknown; minimum?: number; maximum?: number; enum?: string[] } };
	defaults: Record<string, unknown>;
	poles: Record<string, { label: string; query: Record<string, unknown> }>;
	allowed_traversals: TugTraversalMeta[];
	default_traversal: string;
	frozen: boolean;
}

export interface TugBalance {
	eligible: number;
	known: number;
	unknown: number;
	team_a: number;
	team_b: number;
	neutral: number;
	team_a_rate: number;
	team_b_rate: number;
	neutral_rate: number;
	pole_ratio: number | null;
	balanced: boolean;
	issues: string[];
	traversal: string;
	traversal_valid: boolean;
	bridge_density?: number | null;
	explanation?: string | null;
}

export interface PendingFork {
	offered_by_id: string;
	/** Films still in play: 3 when offered, 2 once the partner has vetoed one. */
	movie_ids: number[];
	offered_at: string;
	/** Link metadata (actor, characters) per film id, logged with the accepted film. */
	links?: Record<string, Record<string, unknown>>;
	vetoed_movie_id?: number;
	vetoed_by_id?: string;
}

export interface GoldenVetoResult {
	target: "fork" | "step";
	veto_tokens: number;
	run: RunDetail;
}

/** A hand-written "Super-Unlock" payload: any JSON object, sent to the backend as-is. */
export type RawRulesConfig = Record<string, unknown>;

export interface RunParticipant {
	user_id: string;
	role: "owner" | "member";
	joined_at: string;
}

export interface RunStep {
	id: string;
	run_id: string;
	movie_id: number;
	movie_title: string;
	movie_poster_path: string | null;
	movie_release_year: number | null;
	movie_origin_country: string | null;
	movie_origin_countries?: string[];
	/** Poster colour ("#rrggbb"), when the movie cache has computed it (Aesthetic Gradient). */
	movie_dominant_color?: string | null;
	/** Historical Time-Travel: the year the film is set in (negative = BCE) and its era label. */
	movie_narrative_year?: number | null;
	movie_narrative_era_label?: string | null;
	transition_metadata: Record<string, unknown> | null;
	user_notes: string | null;
	status: StepStatus;
	watched_at: string | null;
	logged_by_user_id: string | null;
	logged_at: string;
}

export interface Run {
	id: string;
	name: string;
	game_type: string;
	status: RunStatus;
	engine_version: number; // 1 = legacy run, 2 = Challenge Engine V2
	status_reason: string | null;
	rules_config: RulesConfig;
	created_at: string;
	completed_at: string | null;
}

export interface RunDetail extends Run {
	steps: RunStep[];
	participants: RunParticipant[];
}

// --- movies (not yet exposed by the backend - Phase 9 will add /movies/*) ---

export interface MovieSummary {
	tmdb_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	origin_country: string | null;
	origin_countries?: string[];
	/** TMDB popularity (search results only). */
	popularity?: number | null;
}

export interface MovieDetail extends MovieSummary {
	overview: string | null;
	tagline: string | null;
	runtime: number | null;
	original_language: string | null;
	genre_ids: number[];
	ratings: MovieRatings | null;
	/** LLM-extracted kebab-case tropes; null = not extracted yet. */
	extracted_tropes?: string[] | null;
}

export interface TropeExtraction {
	tmdb_id: number;
	tropes: string[];
	cached: boolean;
	/** False when the LLM is off: an empty list then means "not extracted". */
	enabled: boolean;
}

export interface TropeEvidence {
	slug: string;
	sources: string[];
	tvtropes_url: string | null;
}

export interface MovieTropeEvidence {
	tmdb_id: number;
	evidence: TropeEvidence[];
}

export interface MovieRatings {
	imdb_rating: string | null;
	rotten_tomatoes: string | null;
	metacritic: string | null;
}

export interface CastMember {
	actor_id: number;
	name: string;
	profile_path: string | null;
	character_name: string | null;
	cast_order: number | null;
}

export interface GenreOut {
	id: number;
	name: string;
}

// --- engine (schemas/engine.py) ---

export interface EngineMeta {
	modifiers: ModifierMeta[];
	game_type: string;
	display_name: string;
	description: string;
	capabilities: string[];
	requires?: string[];
	unavailable_reason?: string | null;
	seed_policy: "none" | "free" | "derived" | "pair";
	queue_policy: "frontier" | "slot" | "none";
	tagline?: string;
	tags?: string[];
	rulebook?: RuleSection;
	glossary?: Record<string, string>;
	discovery_filters: FilterSpec[];
	rule_fields: RuleField[];
	presets: Preset[];
	default_preset: string;
	bounty_reward: "wildcard" | "life" | "hint" | "star";
	rabbit_tiers?: { number: number; name: string; rule: string; start_depth: number; emoji: string }[] | null;
	warning_window?: number | null;
	tug_planes?: TugPlaneMeta[] | null;
	vibe?: { setpoints: Record<"gentle" | "balanced" | "brave", number>; chaser_load_margin: number; chaser_length: string } | null;
}

export type ModifierParamValue = string | number | boolean | string[];
export interface ModifierParamSchema {
	title?: string;
	type: "string" | "integer" | "boolean" | "array";
	default?: ModifierParamValue;
	enum?: string[];
	minimum?: number;
	maximum?: number;
	items?: { enum?: string[] };
}
export interface ModifierMeta {
	key: string;
	label: string;
	emoji: string;
	blurb: string;
	scope: "film" | "pair" | "sequence";
	params_schema: { properties: Record<string, ModifierParamSchema> };
	compatible: boolean;
	incompatible_reason: string | null;
	default_params: Record<string, ModifierParamValue> | null;
}

export type RuleValue = boolean | string | number | null;

export interface RuleField {
	key: keyof RulesConfig;
	kind: "int" | "bool" | "enum" | "segmented";
	label: string;
	help: string;
	min: number | null;
	max: number | null;
	options: string[];
	default: RuleValue;
	group: "core" | "advanced";
}

export interface Preset {
	id: string;
	label: string;
	emoji: string;
	blurb: string;
	values: Partial<Record<keyof RulesConfig, RuleValue>>;
}

export interface FilterSpec {
	key: string;
	kind: "select" | "toggle" | "range";
	label: string;
	source: "origin_country" | "release_year" | "narrative_year" | "runtime" | "genre_ids" | "tug_effect" | "tier_compliant" | "new_country" | "facet";
	facet?: string | null;
	default: boolean | string | number | null;
	server_param: "include_off_tier" | null;
	help: string;
}

export interface RuleSection {
	goal: string;
	turn: string[];
	scoring: string[];
	lose: string[];
	tips: string[];
	glossary: string[];
}

export interface RunRulebook {
	game_type: string;
	display_name: string;
	rulebook: RuleSection;
	overlays: { key: string; title: string; rulebook: RuleSection }[];
	glossary: Record<string, string>;
	settings: Record<string, string>;
}

export interface SeedOptions {
	seed_policy: EngineMeta["seed_policy"];
	allowed_ids: number[] | null;
	reason: string | null;
}

export interface SeedSuggestionResponse {
	suggestion: SeedSuggestion | null;
	reason: string | null;
}

export interface CuratedSlices {
	hydrated: number;
	total: number;
	countries: Record<string, number>;
	decades: Record<string, number>;
	pairs: Record<string, number>;
	indexing: boolean;
	indexing_error: string | null;
}

/** How two films are linked. `kind: "director"` reuses the shape: actor_id/actor_name hold the director. */
export type ConnectionKind = "actor" | "director" | "craft" | "character";

/** What a person was on a film (Crew & Craft Trail). */
export type CraftRole = "actor" | "composer" | "cinematographer" | "writer" | "director";

export interface SharedActorConnection {
	kind?: ConnectionKind;
	/** For `kind: "craft"`: the shared person; `role_in_*` is what they were on each film. */
	actor_id: number;
	actor_name: string;
	profile_path: string | null;
	character_in_from: string | null;
	character_in_to: string | null;
	role_in_from?: CraftRole | null;
	role_in_to?: CraftRole | null;
}

/** A key craft credit on a film (`GET /movies/{id}/crew`). */
export interface CrewMember {
	person_id: number;
	name: string;
	job: string;
	department: string;
	role: CraftRole;
	profile_path: string | null;
}

/** The rule shaping a run's next hop (backend `ConstraintInfo`). */
export interface ConstraintInfo {
	overlay_progress?: { key: string; label: string; next: string | null; available?: boolean; can_skip?: boolean }[];
	kind: "director" | "actor" | "free" | "year" | "country" | "color" | "semantic" | (string & {});
	title: string;
	detail: string | null;
	rule_query?: FacetQuery | null;
	/** ISO codes locked out by `country_cooldown`, most recently visited first. */
	cooldown_countries?: string[];
	/** One-line notes for other active modifiers (chrono direction, runtime staircase). */
	modifier_notes?: string[];
	/** The Rabbit Hole: the active tier, lives and the tier boundary ahead. */
	rabbit_hole?: RabbitHoleState | null;
}

export interface RabbitHoleState {
	depth: number;
	tier: number;
	tier_name: string;
	tier_rule: string;
	lives_remaining: number;
	max_lives: number;
	next_tier: number | null;
	next_tier_name: string | null;
	next_tier_rule: string | null;
	steps_until_next: number | null;
	upcoming_tier_warning: string | null;
	dead_end: boolean;
	tier_override: number | null;
	curses: RabbitPredicate[];
	curse_skipped: boolean;
	relics: { skip_curse?: number };
	reroll_tokens: number;
	daily: boolean;
	fog: "off" | "fog" | "abyss";
	periscope_charges: number;
	revealed_depths: number[];
}

export interface RabbitPredicate {
	predicate_id: string;
	params: Record<string, number>;
	name: string;
	rule: string;
	difficulty: number;
}

export interface RabbitDeckTier {
	number: number;
	name?: string;
	rule?: string;
	start_depth: number;
	hidden?: boolean;
	emoji?: string;
	predicate_id?: string;
	params?: Record<string, number>;
	difficulty?: number;
	curses?: RabbitPredicate[];
}

export interface FacetQuery {
	all?: FacetQuery[] | null;
	any?: FacetQuery[] | null;
	not?: FacetQuery | null;
	facet?: string | null;
	op?: "eq" | "ne" | "lt" | "le" | "gt" | "ge" | "contains" | "has_any" | "has_all" | null;
	value?: string | number | boolean | string[] | number[] | null;
}

export interface ValidationResult {
	overlay_skippable?: string[];
	connection_type?: ConnectionKind | null;
	valid: boolean;
	reason: string | null;
	connections: SharedActorConnection[];
	/** A hard rule violation (canon list / decade) - a wildcard can't override it. */
	blocked?: boolean;
	/** Semantic Trope Web: cosine similarity of the two plots (-1..1). */
	similarity?: number | null;
	/** Aesthetic Gradient: RGB distance between the two posters' colours. */
	color_distance?: number | null;
	/** Rule evidence for the hop (Chrono year delta, Passport countries). */
	mechanic?: Record<string, unknown> | null;
	/** Meet in the Middle: this film would also connect the opposite end - the chains collide. */
	collision?: boolean;
}

/** Meet in the Middle: which end of the tunnel a step extends. */
export type TunnelSide = "head" | "tail";

/** Both frontiers of a Meet in the Middle run and the quick-BFS distance between them. */
export interface TunnelState {
	head_frontier_movie_id: number | null;
	tail_frontier_movie_id: number | null;
	head_steps: number;
	tail_steps: number;
	collided: boolean;
	/** Movie-hops between the frontiers: 0 = collided, null = none found within the limits. */
	distance_hops: number | null;
	searched_depth: number;
	message: string | null;
	hints_remaining: number;
}

export interface TunnelHintResponse {
	level: "actor" | "film";
	actor: { actor_id: number; actor_name: string } | null;
	film: { movie_id: number; title: string } | null;
	tokens_remaining: number;
}

export interface LlmStatus {
	enabled: boolean;
	provider: string;
}

export interface PitchResult {
	pitch: string;
}

export interface TeaserResult {
	teasers: Record<string, string>;
}

export type LlmProvider = "off" | "local_gguf" | "ollama" | "openai";

/** The fixed local Qwen GGUF on disk plus any running download (`GET /settings/integrations/llm/download-status`). */
export interface LlmDownloadStatus {
	downloaded: boolean;
	size_bytes: number;
	path: string;
	downloading: boolean;
	bytes_downloaded: number;
	total_bytes: number;
	percent: number;
	error: string | null;
}

export interface LlmTestResult {
	ok: boolean;
	latency_ms: number | null;
	output: string | null;
	provider: string;
	model: string;
	detail: string | null;
}

export interface RouletteMovie {
	tmdb_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	origin_country: string | null;
	origin_countries?: string[];
	runtime: number | null;
	vibe_load?: number | null;
	overview: string | null;
	tagline: string | null;
	genre_ids: number[];
	imdb_rating: string | null;
}

export interface RouletteSpinResult {
	movie: RouletteMovie;
	pool_size: number;
	/** Every distinct pick, `movie` first (3 for a Blind Draft). */
	movies: RouletteMovie[];
}

export interface Suggestion extends DiscoveryCandidate {
	connecting_actor_id: number | null;
	connecting_actor_name: string | null;
}

export interface KeystoneActor {
	actor_id: number;
	actor_name: string;
	appearances: number;
}

export interface BridgeNode {
	movie_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	popularity: number | null;
	original_language?: string | null;
	rating?: number | null;
	runtime: number | null; // null = detail not fetched yet
	origin_countries: string[];
}

/** Dynamic highlight chip derived from analysing a whole path (backend `PathTag`). */
export interface PathTag {
	key: "canon_heavy" | "multi_country" | "epic_runtimes" | (string & {});
	label: string;
	emoji: string;
	detail: string;
}

export interface BridgeAlternatePath {
	label: string;
	path: BridgeNode[];
	hops: number;
	connections: SharedActorConnection[];
	tags?: PathTag[];
}

export interface BridgeResult {
	path: BridgeNode[];
	hops: number;
	connections: SharedActorConnection[];
	label?: string;
	tags?: PathTag[];
	alternate_paths?: BridgeAlternatePath[];
}

/** One displayable route: the primary result or an alternate/deeper one (edited in place by swaps). */
export interface BridgeRoute {
	label: string;
	path: BridgeNode[];
	hops: number;
	connections: SharedActorConnection[];
	tags: PathTag[];
}

export interface SwapCandidate {
	node: BridgeNode;
	connection_in: SharedActorConnection;
	connection_out: SharedActorConnection;
}

/** "same": the exact same two connecting actors; "broad": any actor shared with each neighbour. */
export type SwapMode = "same" | "broad";

export interface SwapNodeResult {
	candidates: SwapCandidate[];
	total: number;
}

export interface PathTagsResult {
	tags: PathTag[];
	nodes: BridgeNode[];
}

export interface RunStats {
	total_hops: number;
	countries: string[];
	decades: number[];
	keystone_actors: KeystoneActor[];
	par_score?: number | null;
}

// --- discovery (schemas/discovery.py, Phase 13) ---

export interface DiscoveryConnection {
	kind?: ConnectionKind;
	actor_id: number;
	actor_name: string;
	profile_path: string | null;
	character_in_frontier: string | null;
	character_in_candidate: string | null;
	role_in_frontier?: CraftRole | null;
	role_in_candidate?: CraftRole | null;
}

export interface DiscoveryCandidate {
	facet_values?: Record<string, string | number | boolean | string[] | number[] | null>;
	overlay_ok?: Record<string, boolean | null>;
	movie_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	origin_country: string | null;
	origin_countries?: string[];
	genre_ids: number[];
	popularity: number | null;
	original_language?: string | null;
	rating?: number | null;
	/** Minutes, when the film's detail is cached. */
	runtime?: number | null;
	/** Culture-neutral load score when Vibe Control or Chaser is active. */
	vibe_load?: number | null;
	connections: DiscoveryConnection[];
	already_in_run: boolean;
	existing_step_number: number | null;
	/** The run's constraint (e.g. country) couldn't be checked yet; logging re-checks it. */
	constraint_unverified?: boolean;
	/** Aesthetic Gradient: the poster's dominant colour ("#rrggbb"). */
	dominant_color?: string | null;
	/** Semantic Trope Web: plot similarity to the frontier film, 0..1. */
	semantic_score?: number | null;
	/** Semantic Trope Web: the candidate's LLM-extracted tropes. */
	tropes?: string[];
	trope_sources?: Record<string, string[]>;
	trope_urls?: Record<string, string>;
	/** The Rabbit Hole: true = verified to satisfy the active tier's rule (null = couldn't be checked). */
	tier_compliant?: boolean | null;
	upcoming_tier_warning?: string | null;
	/** Chrono modes: release year minus the frontier film's (negative on a descent). */
	year_delta?: number | null;
	/** Historical Time-Travel: the setting year (negative = BCE), era label and leap from the frontier. */
	narrative_year?: number | null;
	narrative_era_label?: string | null;
	narrative_delta?: number | null;
	/** Tug of War v2/v3: effect and projected net rope movement for the next pull. */
	tug_effect?: "home" | "invasion" | "neutral" | "sudden_neutral" | null;
	tug_breaks_streak?: boolean;
	tug_points?: number | null;
	tug_territory?: "team_a" | "team_b" | "neutral" | null;
	tug_territory_evidence?: Record<string, boolean | null>;
	tug_link?: Record<string, unknown> | null;
	grid_cells?: string[];
	grid_jump_cells?: string[];
	target_distance?: number | null;
}

/** POST /movies/{id}/narrative-era: a film's setting year. */
export interface NarrativeEra {
	tmdb_id: number;
	narrative_year: number;
	narrative_era_label: string;
	source: "resolved" | "manual" | "default";
}

// --- integrations (schemas/integrations.py) ---

export interface JellyfinItemSummary {
	on_server: boolean | null;
	item_id: string | null;
	play_url: string | null;
}

export interface RequestClientStatus {
	enabled: boolean;
	implemented: boolean;
	reachable: boolean | null;
	version: string | null;
}

export interface IntegrationStatus {
	jellyfin: {
		enabled: boolean;
		reachable: boolean;
		version: string | null;
	};
	radarr: RequestClientStatus;
	seerr: RequestClientStatus;
}

// --- system (routes_system.py) ---

export interface CacheFlushResult {
	max_age_days: number;
	ratings_removed: number;
	movies_cast_reset: number;
	cast_edges_removed: number;
	actors_marked_stale: number;
	actors_removed: number;
	vacuumed: boolean;
	db_size_before: number | null;
	db_size_after: number | null;
}

/** Data Spa treatments (`POST /system/spa/{treatment}`, `POST /runs/{id}/prepare`). */
export type SpaTreatment = "details" | "people" | "ratings" | "embeddings" | "facets" | "fix_all";

export interface CoverageCount {
	known: number;
	total: number;
}

export interface ProviderBudget {
	provider: string;
	day: string;
	used: number;
	limit: number;
	remaining: number | null;
	exhausted: boolean;
}

/** `GET /system/cache/health`: SQL coverage counts per data family. */
export interface CacheHealth {
	total_movies: number;
	coverage: Record<Exclude<SpaTreatment, "fix_all">, CoverageCount>;
	families: Record<string, CoverageCount>;
	budgets: ProviderBudget[];
}

/** `GET /runs/{id}/discover?envelope=1` funnel counts. */
export interface DiscoveryDiagnostics {
	engine_pool: number;
	after_modifiers: number;
	after_filters: number;
	widened: boolean;
	reason: string | null;
}

export interface DiscoveryEnvelope {
	candidates: DiscoveryCandidate[];
	diagnostics: DiscoveryDiagnostics;
}

export interface CacheStats {
	cached_movies: number;
	cached_actors: number;
	cached_cast_edges: number;
	db_size_bytes: number | null;
}

// --- settings (routes_settings.py, Phase 10.1) ---

export interface IntegrationConfig {
	tmdb_configured: boolean;
	tmdb_api_key_masked: string | null;
	jellyfin_url: string;
	jellyfin_configured: boolean;
	jellyfin_api_key_masked: string | null;
	omdb_configured: boolean;
	omdb_api_key_masked: string | null;
	omdb_soft_cap: number;
	radarr_url: string;
	radarr_configured: boolean;
	radarr_api_key_masked: string | null;
	radarr_default_quality_profile_id: number | null;
	radarr_default_root_folder_path: string | null;
	seerr_url: string;
	seerr_configured: boolean;
	seerr_api_key_masked: string | null;
	seerr_request_mode: RequestMode;
	seerr_user_id: number | null;
	embedding_provider: EmbeddingProvider;
	embedding_base_url: string;
	embedding_model: string;
	embedding_api_key_masked: string | null;
	embedding_local_preset: LocalPresetKey;
	embedding_local_presets: LocalPresetInfo[];
	llm_provider: LlmProvider;
	llm_base_url: string;
	llm_model: string;
	llm_api_key_masked: string | null;
	llm_keep_alive_seconds: number;
	llm_local_available: boolean;
	tvtropes_enabled: boolean;
}

export type EmbeddingProvider = "local_onnx" | "ollama" | "openai";

export type LocalPresetKey = "arctic-embed-xs" | "multilingual-e5-small" | "all-minilm-l6-v2";

/** One on-device ONNX embedding model the admin can pick (backend `LocalPresetInfo`). */
export interface LocalPresetInfo {
	key: LocalPresetKey;
	label: string;
	/** Short strength badge, e.g. "[~24MB] High-Precision Retrieval". */
	badge: string;
	description: string;
	size_mb: number;
	hf_repo: string;
	recommended: boolean;
	/** The model files are already in config_dir/models. */
	downloaded: boolean;
}

/** Result of "Test Connection" for an embedding provider (backend `EmbeddingTestResult`). */
export interface EmbeddingTestResult {
	ok: boolean;
	latency_ms: number | null;
	dimension: number | null;
	provider: string;
	model: string;
	detail: string | null;
}

// --- Radarr / Seerr request flow (routes_integrations.py, Phase 16) ---

export type RequestMode = "auto" | "prompt";

export interface QualityProfile {
	id: number;
	name: string;
}

export interface RootFolder {
	id: number | null;
	path: string;
	free_space: number | null;
}

export interface RadarrOptions {
	enabled: boolean;
	profiles: QualityProfile[];
	root_folders: RootFolder[];
	default_quality_profile_id: number | null;
	default_root_folder_path: string | null;
}

export interface SeerrUser {
	id: number;
	display_name: string;
	email: string | null;
}

export interface SeerrServer {
	id: number;
	name: string;
	is_default: boolean;
	is_4k: boolean;
	active_profile_id: number | null;
	active_directory: string | null;
	profiles: QualityProfile[];
	root_folders: RootFolder[];
}

export interface SeerrOptions {
	enabled: boolean;
	request_mode: RequestMode;
	default_user_id: number | null;
	users: SeerrUser[];
	servers: SeerrServer[];
}

export interface RequestConfig {
	service: "seerr" | "radarr" | null;
	request_mode: RequestMode;
}

export interface AcquisitionStatus {
	state: "available" | "downloading" | "requested" | "missing";
	source: "jellyfin" | "radarr" | "seerr" | null;
}

export interface RequestResult {
	success: boolean;
	service: "seerr" | "radarr";
	mode: "auto" | "advanced";
	state: "requested" | "downloading";
}

export interface ConnectivityTestResult {
	reachable: boolean;
	version: string | null;
	detail: string | null;
}

export interface SolverConfig {
	bridge_max_duration_seconds: number;
}

export interface JellyfinTestLookupResult {
	query_type: string;
	enabled: boolean;
	matches: Array<{
		item_id: string | null;
		name: string | null;
		production_year: number | null;
		provider_ids: Record<string, string>;
	}>;
	error?: string | null;
}

// --- curated canons (schemas/curated.py, Phase 15.5) ---

export interface CuratedListSummary {
	id: string;
	preset_key: string | null;
	title: string;
	url: string;
	badge_prefix: string;
	badge_color: string;
	is_ranked: boolean;
	total_items: number;
	is_enabled: boolean;
	film_count: number;
	description: string | null;
	preview_posters: string[];
	source_account_id: string | null;
	last_synced_at: string | null;
	last_sync_error: string | null;
	slug: string | null;
	badge_emoji: string | null;
	image_url: string | null;
	has_custom_image: boolean;
	account_username: string | null;
	account_display_name: string | null;
	watched_count: number;
	matched: number;
	unmatched: number;
	tv_titles: number;
	ambiguous: number;
}

export interface CuratedListEntry {
	list_id: string;
	position: number;
	slug: string;
	title: string;
	year: number | null;
	imdb_id: string | null;
	tmdb_id: number | null;
	match_tier: string | null;
	status: "matched" | "unmatched" | "tv_title" | "ambiguous";
	reason: string | null;
	attempted_at: string;
}

export interface Page<T> {
	items: T[];
	total: number;
	page: number;
	page_size: number;
	pages: number;
}

export type ListSort = "name" | "film_count" | "popularity" | "synced";
export type ListState = "all" | "enabled" | "disabled";
export type AccountSort = "name" | "lists" | "enabled";
export type AccountKind = "all" | "hq" | "other";

export interface CuratedAccount {
	username: string;
	display_name: string | null;
	avatar_url: string | null;
	bio: string | null;
	is_hq: boolean;
	account_tier: string | null;
	total_public_lists: number;
	last_inspected_at: string | null;
	lists_discovered_at: string | null;
	discovered_lists: number;
	enabled_lists: number;
}

export interface CuratedAccountLists {
	account: CuratedAccount;
	discovered: boolean;
	partial: boolean;
	error: string | null;
	lists: CuratedListSummary[];
}

export interface CanonBadge {
	badge_label: string;
	badge_color: string;
	badge_emoji?: string | null;
}

// --- passport (schemas/passport.py) ---

export interface PassportCountry {
	/** ISO 3166-1 alpha-2, uppercase. */
	code: string;
	count: number;
	/** Historical codes folded into this one (e.g. "SU" into "RU"). */
	merged_from: string[];
}

export interface PassportDirector {
	person_id: number;
	name: string;
	count: number;
}

export interface Passport {
	total_movies_watched: number;
	total_watches: number;
	decades_distribution: Record<string, number>;
	countries: PassportCountry[];
	top_directors: PassportDirector[];
	directors_coverage: { movies_with_directors: number; movies_total: number };
}

export interface DiaryImportResult {
	source: "csv" | "rss";
	run_id: string;
	total_rows: number;
	imported: number;
	skipped_duplicates: number;
	unresolved_count: number;
	unresolved: { title: string; year: number | null; reason: string }[];
	rate_limit_pauses: number;
}

export interface BackfillResult {
	looked_up: number;
	failed: number;
	total: number;
}

/** A random well-regarded cached film to start a run with (`GET /movies/seed-suggestion`). */
export interface SeedSuggestion extends MovieSummary {
	reason: string;
}

// --- tools: Watchlist Bingo (GET /tools/bingo/watchlist) ---

/** A watchlist film with whatever the local cache knows (null / empty = unknown). */
export interface BingoFilm {
	movie_id: number;
	title: string;
	year: number | null;
	poster_path: string | null;
	runtime: number | null;
	original_language: string | null;
	origin_countries: string[];
	genre_ids: number[];
	imdb_rating: number | null;
	popularity: number | null;
	canon_badges: string[];
	directed_by_woman: boolean | null;
}

export interface BingoWatchlist {
	films: BingoFilm[];
	total: number;
	/** Films still missing detail / directors / ratings; request again with `hydrate` to fill more. */
	pending: number;
}

// --- tools: Bingo squares and stamps (GET /tools/bingo/squares, POST /tools/bingo/stamp) ---

/** A server-defined Bingo square (a facet query) with the caller's watchlist films that fill it. */
export interface BingoSquare {
	id: string;
	label: string;
	/** What qualifies, shown when the square is opened. */
	hint: string;
	/** The server facet query; informational only - stamps are validated server-side. */
	query: unknown;
	/** Watchlist movie ids the server's cached facts say fill the square. */
	matches: number[];
	/** Watchlist films whose cached facts can't decide the square yet. */
	unknown: number;
}

export interface BingoSquares {
	squares: BingoSquare[];
}

export interface BingoStampResult {
	square_id: string;
	movie_id: number;
	valid: boolean;
	/** false = the film doesn't fit, null = the cache can't tell yet. */
	verdict: boolean | null;
	reason: string;
}

// --- tools: The Perfect Marathon Router (POST /tools/router/*) ---

export type WhiplashLabel = "Smooth Transition" | "Gentle Shift" | "Tonal Whiplash";

export interface RouterWeights {
	weight_genre?: number;
	weight_year?: number;
	weight_runtime?: number;
	weight_rating?: number;
}

export interface RouterFilm {
	movie_id: number;
	title: string;
	year: number | null;
	poster_path: string | null;
	overview: string | null;
	runtime: number | null;
	rating: number | null;
	genres: string[];
}

export interface RouterTransition {
	from_movie_id: number;
	to_movie_id: number;
	cost: number;
	label: WhiplashLabel;
	/** Why the hop is smooth, or where the biggest gap is ("1980s Science Fiction Harmony"). */
	summary: string;
	deltas: { genre: number; year: number; runtime: number; rating: number };
}

export interface RouterResult {
	ordered_movie_ids: number[];
	initial_whiplash_score: number;
	optimized_whiplash_score: number;
	improvement_percentage: number;
	transitions: RouterTransition[];
	/** The films in the optimized order. */
	films: RouterFilm[];
	method: "exact" | "simulated_annealing";
	calculation_ms: number;
}

// --- The Daily Bridge (schemas/puzzles.py) ---

export type DailyAttemptStatus = "not_started" | "in_progress" | "solved" | "forfeited";

export interface PuzzleHop {
	movie: MovieSummary;
	/** The link that led into this film; null for the starting film. */
	link: SharedActorConnection | null;
}

export interface DailyAttempt {
	status: DailyAttemptStatus;
	chain: PuzzleHop[];
	hops: number;
	/** One per hop once solved: "green" got closer to the target, "yellow" didn't. */
	grades: ("green" | "yellow")[] | null;
	share_text: string | null;
	run_id: string | null;
}

export interface DailyPuzzle {
	puzzle_number: number;
	date: string;
	start_movie: MovieSummary;
	target_movie: MovieSummary;
	par_hops: number;
	attempt: DailyAttempt;
	/** Only revealed once the attempt is solved or forfeited. */
	optimal_path: PuzzleHop[] | null;
}

export interface DailyHopResult {
	valid: boolean;
	reason: string | null;
	connections: SharedActorConnection[];
	recorded: boolean;
	solved: boolean;
	next_movie: MovieSummary | null;
	attempt: DailyAttempt;
}

export interface DailyForfeitResult {
	puzzle_number: number;
	par_hops: number;
	optimal_path: PuzzleHop[];
	attempt: DailyAttempt;
}

export interface DailyConvertResult {
	run_id: string;
	movies: number;
}
