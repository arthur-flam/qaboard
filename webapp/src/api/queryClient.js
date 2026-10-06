import { QueryClient } from "@tanstack/react-query";
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { get, set, del } from "idb-keyval";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Failing requests are mostly 404s for commits/batches that don't exist: retrying won't help
      retry: (count, error) => count < 2 && !(error?.response?.status >= 400 && error?.response?.status < 500),
      refetchOnWindowFocus: false,
      // Keep data around while users go back and forth between pages
      gcTime: 10 * 60 * 1000,
    },
  },
});

// To show the projects list, the user and the site configuration right away when the app starts,
// we keep them in IndexedDB. They are refreshed in the background.
const persisted_queries = new Set(['config', 'me', 'projects']);

export const persistOptions = {
  persister: createAsyncStoragePersister({
    storage: { getItem: get, setItem: set, removeItem: del },
    key: 'qaboard-query-cache',
  }),
  maxAge: 7 * 24 * 3600 * 1000,
  // bump to discard what's persisted when the shape of the data changes
  buster: '2',
  dehydrateOptions: {
    shouldDehydrateQuery: query => persisted_queries.has(query.queryKey[0]) && query.state.status === 'success',
  },
};
