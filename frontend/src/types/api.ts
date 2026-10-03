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

export type RunStatus = "active" | "completed" | "abandoned";
export type StepStatus = "watched" | "planned";
export type RepeatPolicy = "strict" | "penalty" | "allowed";
export type RulesPreset = "standard" | "purist" | "casual" | "custom";

export interface RulesConfig {
	preset: RulesPreset;
	allow_repeats: RepeatPolicy;
	no_consecutive_actor: boolean;
	max_cast_order: number;
	min_runtime: number;
	wildcards_budget: number; // -1 = unlimited
}

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

export interface SharedActorConnection {
	actor_id: number;
	actor_name: string;
	profile_path: string | null;
	character_in_from: string | null;
	character_in_to: string | null;
}

export interface ValidationResult {
	valid: boolean;
	reason: string | null;
	connections: SharedActorConnection[];
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
}

export interface BridgeAlternatePath {
	label: string;
	path: BridgeNode[];
	hops: number;
	connections: SharedActorConnection[];
}

export interface BridgeResult {
	path: BridgeNode[];
	hops: number;
	connections: SharedActorConnection[];
	label?: string;
	alternate_paths?: BridgeAlternatePath[];
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
