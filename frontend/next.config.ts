import type { NextConfig } from "next";

// Static export for GitHub Pages. BASE_PATH is the repo name there (e.g. "/santa");
// it is empty for local development.
const nextConfig: NextConfig = {
  output: "export",
  basePath: process.env.BASE_PATH || undefined,
  trailingSlash: true,
};

export default nextConfig;
