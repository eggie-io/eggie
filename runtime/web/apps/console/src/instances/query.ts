import { queryOptions } from "@tanstack/react-query";
import { createApi } from "../api/client";
import type { Instance } from "./instances";

const api = createApi((input, init) => fetch(input, init));

// The shell's back arrow and the switcher read one cache entry, so they share one fetch.
export const instancesQuery = queryOptions({
  queryKey: ["instances"],
  queryFn: () => api.get<{ instances: Instance[] }>("/api/instances"),
  retry: false,
});
