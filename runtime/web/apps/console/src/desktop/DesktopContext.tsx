import { createContext, useContext } from "react";

// The desktop app's own screens, when this page is showing inside its window.
export const DesktopHome = createContext<string | null>(null);

export const useDesktopHome = () => useContext(DesktopHome);

// The agents that window can connect by itself; empty outside it.
export const DesktopConnect = createContext<readonly string[]>([]);

export const useDesktopConnect = () => useContext(DesktopConnect);
