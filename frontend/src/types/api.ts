// Hand-mirrored types for the CineChain FastAPI backend. Keep field names and
// nullability in lockstep with the pydantic schemas they mirror.

// --- auth (schemas/auth.py) ---

export interface User {
	id: string;
	username: string;
	display_name: string;
	is_admin: boolean;
	created_at: string;
}

export interface UserSummary {
	id: string;
	username: string;
	display_name: string;
}

// --- runs (schemas/runs.py) ---

export type RunStatus = "active" | "completed" | "forfeited" | "failed";
export type StepStatus = "watched" | "planned";
export type RepeatPolicy = "strict" | "penalty" | "allowed";
export type RulesPreset = "standard" | "purist" | "casual" | "custom";

/** Opt-in Engine V2 conditions; a win/fail fires once the metric reaches / exceeds `count`. */
export interface RunCondition {
	type: string;
	count: number;
}

export interface RulesConfig {
	preset: RulesPreset;
	allow_repeats: RepeatPolicy;
	no_consecutive_actor: boolean;
	max_cast_order: number;
	min_runtime: number;
	wildcards_budget: number; // -1 = unlimited
	/** Canon-Only Island: the CuratedList every film must belong to. */
	allowed_curated_list_id?: string;
	/** Decade Sieve: the decade start (e.g. 1970) every film must fall in. */
	target_decade?: number;
	/** Standalone modes: also require a shared actor/director between hops (hybrid play). */
	require_cast_link?: boolean;
	/** Chrono Climb / Descent: which way time must move. */
	direction?: "climb" | "descent";
	win_condition?: RunCondition | RunCondition[];
	fail_condition?: RunCondition | RunCondition[];
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
	/** Poster colour ("#rrggbb"), when the movie cache has computed it (Aesthetic Gradient). */
	movie_dominant_color?: string | null;
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
}

export interface MovieDetail extends MovieSummary {
	overview: string | null;
	tagline: string | null;
	runtime: number | null;
	original_language: string | null;
	genre_ids: number[];
	ratings: MovieRatings | null;
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
	game_type: string;
	display_name: string;
	description: string;
	capabilities: string[];
}

/** How two films are linked. `kind: "director"` reuses the shape: actor_id/actor_name hold the director. */
export type ConnectionKind = "actor" | "director";

export interface SharedActorConnection {
	kind?: ConnectionKind;
	actor_id: number;
	actor_name: string;
	profile_path: string | null;
	character_in_from: string | null;
	character_in_to: string | null;
}

/** The rule shaping a run's next hop (backend `ConstraintInfo`). */
export interface ConstraintInfo {
	kind: "director" | "actor" | "free" | "year" | "country" | "color" | "semantic" | (string & {});
	title: string;
	detail: string | null;
}

export interface ValidationResult {
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
}

export interface RouletteMovie {
	tmdb_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	origin_country: string | null;
	runtime: number | null;
	overview: string | null;
	tagline: string | null;
	genre_ids: number[];
	imdb_rating: string | null;
}

export interface RouletteSpinResult {
	movie: RouletteMovie;
	pool_size: number;
}

export interface Suggestion {
	movie_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	origin_country: string | null;
	connecting_actor_id: number;
	connecting_actor_name: string;
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
}

export interface EngineMeta {
	game_type: string;
	display_name: string;
	description: string;
	capabilities: string[];
}

// --- discovery (schemas/discovery.py, Phase 13) ---

export interface DiscoveryConnection {
	kind?: ConnectionKind;
	actor_id: number;
	actor_name: string;
	profile_path: string | null;
	character_in_frontier: string | null;
	character_in_candidate: string | null;
}

export interface DiscoveryCandidate {
	movie_id: number;
	title: string;
	poster_path: string | null;
	release_year: number | null;
	origin_country: string | null;
	genre_ids: number[];
	popularity: number | null;
	connections: DiscoveryConnection[];
	already_in_run: boolean;
	existing_step_number: number | null;
	/** The run's constraint (e.g. country) couldn't be checked yet; logging re-checks it. */
	constraint_unverified?: boolean;
	/** Aesthetic Gradient: the poster's dominant colour ("#rrggbb"). */
	dominant_color?: string | null;
	/** Semantic Trope Web: plot similarity to the frontier film, 0..1. */
	semantic_score?: number | null;
	/** Chrono modes: release year minus the frontier film's (negative on a descent). */
	year_delta?: number | null;
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
