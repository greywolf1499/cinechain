/** TMDB tagline: muted + italic, rendered only when the movie actually has one. */
export default function MovieTagline({ tagline }: { tagline: string | null | undefined }) {
  const text = tagline?.trim();
  if (!text) return null;
  return <p className="mt-2 text-xs italic text-zinc-400">&ldquo;{text}&rdquo;</p>;
}
