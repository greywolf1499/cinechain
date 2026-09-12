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
	logged_by_user_id: string | null;
	logged_at: string;
}

export interface Run {
	id: string;
	name: string;
	game_type: string;
	status: RunStatus;
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
}

export interface CastMember {
	actor_id: number;
	name: string;
	profile_path: string | null;
	character_name: string | null;
	cast_order: number | null;
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
}

export interface BridgeResult {
	path: BridgeNode[];
	hops: number;
	connections: SharedActorConnection[];
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
