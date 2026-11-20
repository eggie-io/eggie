import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, createApi } from "../api/client";
import { projectPath } from "../projects/queries";
import { pollMs, type AddressList } from "./domains";

// Under "projects", so invalidating every project query also refreshes the addresses.
const ADDRESSES = (id: string) => ["projects", "addresses", id] as const;

const addressesPath = (id: string) => `${projectPath(id)}/addresses`;
const addressPath = (id: string, hostname: string) => `${addressesPath(id)}/${encodeURIComponent(hostname)}`;

export function useAddresses(id: string, enabled = true) {
  return useQuery({
    queryKey: ADDRESSES(id),
    queryFn: () => api.get<AddressList>(addressesPath(id)),
    enabled,
    refetchInterval: (query) => pollMs(query.state.data),
  });
}

// The service may take 10 s to check a domain and the API waits 20 s for it; the shared
// client's 10 s would show a check that went through as a failure.
const actions = createApi((input, init) => fetch(input, init), { timeoutMs: 30_000 });

function useAddressAction<T>(id: string, send: (arg: T) => Promise<AddressList>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: (list) => client.setQueryData<AddressList>(ADDRESSES(id), { addresses: list.addresses }),
    onSettled: () => client.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export const useAddDomain = (id: string) =>
  useAddressAction(id, (hostname: string) => actions.post<AddressList>(addressesPath(id), { hostname }));

export const useRemoveDomain = (id: string) =>
  useAddressAction(id, (hostname: string) => actions.del<AddressList>(addressPath(id, hostname)));

export const useMakePrimary = (id: string) =>
  useAddressAction(id, (hostname: string) => actions.post<AddressList>(`${addressPath(id, hostname)}/primary`));

export const useCheckDomain = (id: string) =>
  useAddressAction(id, (hostname: string) => actions.post<AddressList>(`${addressPath(id, hostname)}/check`));
