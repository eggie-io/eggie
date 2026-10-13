import { createContext, useContext } from "react";

// undefined until /api/health answers; null is the usual VM, a string a site.
export const SiteContext = createContext<string | null | undefined>(undefined);

export const useSite = () => useContext(SiteContext);
