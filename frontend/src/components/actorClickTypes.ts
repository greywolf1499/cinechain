export interface ActorClickPayload {
	actorId: number;
	actorName: string;
	profilePath: string | null;
	/** Character this actor played in the movie the click originated from, if known. */
	characterName?: string | null;
}
