import { createReadStream, statSync } from "node:fs";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const AGENTS = fileURLToPath(new URL("../../../agents", import.meta.url));
const TYPES: Record<string, string> = {
  ".json": "application/json", ".svg": "image/svg+xml", ".webp": "image/webp", ".png": "image/png",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".avif": "image/avif",
  ".mp4": "video/mp4", ".webm": "video/webm",
};

// Dev only: production gets the same folder from the Dockerfile.
function agentGuides(): Plugin {
  return {
    name: "agent-guides",
    configureServer(server) {
      server.middlewares.use("/agent-guides", (req, res) => {
        let file = "";
        let isFile = false;
        try {
          file = normalize(join(AGENTS, decodeURIComponent((req.url ?? "/").split("?")[0])));
          isFile = file.startsWith(AGENTS + sep) && statSync(file).isFile();
        } catch {
          isFile = false;
        }
        if (!isFile) {
          res.statusCode = 404;
          res.end();
          return;
        }
        res.setHeader("Content-Type", TYPES[extname(file)] ?? "application/octet-stream");
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig(({ command }) => ({
  plugins: [react(), agentGuides()],
  // The MSW worker lives in dev-public/, so a production build never ships it.
  publicDir: command === "serve" ? "dev-public" : false,
  // The page's CSP refuses data: fonts, so every asset ships as a file.
  build: { assetsInlineLimit: 0 },
}));
