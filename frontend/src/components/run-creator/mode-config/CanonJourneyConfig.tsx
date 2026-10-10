import HeroSeedPreview from "../HeroSeedPreview";
import { CanonListSelect } from "./CanonListConfig";
import type { ModeConfigProps } from "./types";

export function ConnectCanonConfig({ draft, update }: ModeConfigProps) {
  const waypoints = draft.waypointMovies;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-zinc-500">Choose three distinct canon waypoints. The server calculates each leg&apos;s par from the cached graph.</p>
      {waypoints.map((waypoint, index) => (
        <HeroSeedPreview
          key={index}
          value={waypoint}
          onChange={(movie) => {
            const next: typeof waypoints = [
              index === 0 ? movie : waypoints[0],
              index === 1 ? movie : waypoints[1],
              index === 2 ? movie : waypoints[2],
            ];
            update({ waypointMovies: next });
          }}
          gameType="connect_canon"
          label={`Waypoint ${index + 1}`}
          rules={draft.rules}
          excludeIds={waypoints.flatMap((item, itemIndex) => item && itemIndex !== index ? [item.tmdb_id] : [])}
        />
      ))}
    </div>
  );
}

export function CanonInfiltrationConfig(props: ModeConfigProps) {
  return (
    <div className="flex flex-col gap-3">
      <CanonListSelect
        value={props.draft.canonListId}
        onChange={(canonListId) => props.update({ canonListId })}
        curatedLists={props.curatedLists}
        onGoLists={props.onGoLists}
        label="Target canon list"
      />
      <p className="text-xs text-zinc-500">
        Pick a seed at least two cached cast-links away from the target list.
      </p>
    </div>
  );
}
