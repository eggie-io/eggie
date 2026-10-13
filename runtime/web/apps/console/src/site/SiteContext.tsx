import { createContext, useContext } from "react";

export const SiteContext = createContext<string | null>(null);

export const useSite = () => useContext(SiteContext);
