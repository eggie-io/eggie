const svg = { width: 16, height: 16, viewBox: "0 0 18 18", fill: "none", "aria-hidden": true } as const;
const line = { stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export const PLUS = <svg {...svg}><path d="M9 3.5v11M3.5 9h11" {...line} /></svg>;
export const ARROW = <svg {...svg}><path d="M6 12 12 6M7 5.5h5.5V11" {...line} /></svg>;
export const FOLDER = (
  <svg {...svg}><path d="M2.5 5.2A1.5 1.5 0 0 1 4 3.7h3l1.4 1.8H14A1.5 1.5 0 0 1 15.5 7v6.2A1.5 1.5 0 0 1 14 14.7H4a1.5 1.5 0 0 1-1.5-1.5V5.2Z" {...line} /></svg>
);
export const MAGNIFIER = <svg {...svg}><circle cx="8" cy="8" r="4.8" {...line} /><path d="m11.6 11.6 3.4 3.4" {...line} /></svg>;
export const GLOBE = (
  <svg {...svg}><circle cx="9" cy="9" r="6.5" {...line} /><path d="M2.5 9h13M9 2.5c2 2.2 2 10.8 0 13M9 2.5c-2 2.2-2 10.8 0 13" {...line} /></svg>
);
export const TRASH = <svg {...svg}><path d="M3.5 5h11M7.2 5V3.5h3.6V5M5 5l.7 9.5h6.6L13 5" {...line} /></svg>;
export const BACK = <svg {...svg}><path d="M11 4 6 9l5 5" {...line} /></svg>;
export const EXTERNAL = (
  <svg {...svg}><path d="M10.5 3.5h4v4M14.5 3.5 8.5 9.5M13 10.5v3A1.5 1.5 0 0 1 11.5 15h-7A1.5 1.5 0 0 1 3 13.5v-7A1.5 1.5 0 0 1 4.5 5h3" {...line} /></svg>
);
export const PROJECTS = (
  <svg {...svg}><rect x="2.5" y="3.5" width="13" height="11" rx="2.5" stroke="currentColor" strokeWidth="1.5" /><path d="M2.5 7.5h13" stroke="currentColor" strokeWidth="1.5" /></svg>
);
export const GITHUB = (
  <svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.6a7.4 7.4 0 0 0-2.3 14.4c.4.1.5-.2.5-.4v-1.3c-2 .4-2.5-.5-2.7-1-.1-.2-.5-1-.9-1.2-.3-.2-.8-.6 0-.6.6 0 1.1.6 1.3.9.7 1.2 1.9.9 2.4.7.1-.6.3-.9.6-1.1-1.8-.2-3.7-.9-3.7-4 0-.9.3-1.6.8-2.2 0-.2-.3-1 .1-2 0 0 .7-.3 2.2.7a7.4 7.4 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.4 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.2 0 3.1-1.9 3.8-3.7 4 .3.3.6.8.6 1.6v2.3c0 .2.1.5.5.4A7.4 7.4 0 0 0 10 2.6Z" fill="currentColor" /></svg>
);
export const SIGN_OUT = <svg {...svg}><path d="M6.8 3.2H4.5A1.7 1.7 0 0 0 2.8 4.9v8.2a1.7 1.7 0 0 0 1.7 1.7h2.3M11.2 5.6 14.6 9l-3.4 3.4M14.6 9H7.3" {...line} /></svg>;
export const PLUG = (
  <svg {...svg}><path d="M7 2.5v3.5M11 2.5v3.5M5 6h8v2.5a4 4 0 0 1-8 0V6ZM9 12.5V16" {...line} /></svg>
);
export const CHEVRON_RIGHT = <svg {...svg}><path d="M7 4l5 5-5 5" {...line} /></svg>;
export const CHEVRON_DOWN = <svg {...svg}><path d="M5 7l4 4 4-4" {...line} /></svg>;
export const CHECK = <svg {...svg}><path d="M4 9.4 7.2 12.6 14 5.4" {...line} /></svg>;
export const GEAR = (
  <svg {...svg}><circle cx="9" cy="9" r="2.3" {...line} /><path d="M9 2.5v1.7M9 13.8v1.7M2.5 9h1.7M13.8 9h1.7M4.4 4.4l1.2 1.2M12.4 12.4l1.2 1.2M4.4 13.6l1.2-1.2M12.4 5.6l1.2-1.2" {...line} /></svg>
);
export const KEY = (
  <svg {...svg}><circle cx="6" cy="9" r="3" {...line} /><path d="M9 9h6.5M13 9v2.5M15.5 9v2" {...line} /></svg>
);
