import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

interface ChaserCatalogue {
  named_variants: {
    chaser_trigger: { query: { any: [{ facet: "runtime"; op: "ge"; value: number }, { facet: "genre"; op: "contains"; value: number }] } };
  };
}

export function useNeedsChaser(runtime: number | null | undefined, genreIds: number[] | null | undefined) {
  const catalogue = useQuery({
    queryKey: ["facets"],
    queryFn: () => api.get<ChaserCatalogue>("/facets"),
    staleTime: 5 * 60 * 1000,
  });
  const leaves = catalogue.data?.named_variants.chaser_trigger.query.any;
  return {
    heavy: !!leaves && ((runtime != null && runtime >= leaves[0].value) || !!genreIds?.includes(leaves[1].value)),
    error: catalogue.error,
  };
}
