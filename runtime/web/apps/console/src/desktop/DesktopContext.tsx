import { createContext, useContext } from "react";

// The desktop app's own screens, when this page is showing inside its window.
export const DesktopHome = createContext<string | null>(null);

export const useDesktopHome = () => useContext(DesktopHome);
