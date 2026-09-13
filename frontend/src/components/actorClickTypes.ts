export interface ActorClickPayload {
	actorId: number;
	actorName: string;
	profilePath: string | null;
	/** Character this actor played in the movie the click originated from, if known. */
	characterName?: string | null;
	/** The tmdb_id of the movie whose cast strip this click came from - lets
	 * callers tell whether the actor is guaranteed connected to the frontier
	 * (clicked from the frontier's own cast) or not (an older step's cast). */
	sourceMovieId: number;
}
