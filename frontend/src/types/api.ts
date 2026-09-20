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
}

export interface ConnectivityTestResult {
	reachable: boolean;
	version: string | null;
	detail: string | null;
}
