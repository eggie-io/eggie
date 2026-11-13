import { useQueries, useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { fileUrl, listingUrl } from "../uploads/paths";
import type { Listing } from "../uploads/queries";
import { DOCS_DIR, pageNames } from "./pages";

// An agent may write or rewrite a page while it is open, so pages refresh on their own.
const REFRESH_MS = 15_000;

const sourceQuery = (id: string, page: string | undefined) => ({
  queryKey: ["docs", id, page ?? ""] as const,
  queryFn: () => api.text(fileUrl(id, `${DOCS_DIR}/${page}.md`)),
  enabled: page !== undefined,
  refetchInterval: REFRESH_MS,
  refetchOnWindowFocus: true,
});

export function useDocPages(id: string, enabled = true) {
  return useQuery({
    queryKey: ["docs", id],
    enabled,
    queryFn: async () => {
      try {
        return pageNames((await api.get<Listing>(listingUrl(id, DOCS_DIR))).entries);
      } catch (error) {
        if (error instanceof ApiError && error.code === "folder_not_found") return [];
        throw error;
      }
    },
    refetchInterval: REFRESH_MS,
    refetchOnWindowFocus: true,
  });
}

export function useDocSource(id: string, page: string | undefined) {
  return useQuery(sourceQuery(id, page));
}

export function useDocSources(id: string, pages: string[]) {
  return useQueries({ queries: pages.map((page) => sourceQuery(id, page)) });
}
