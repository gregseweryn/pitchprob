import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image (ADR 0009):
  // .next/standalone runs with node alone, no node_modules copy.
  output: "standalone",
};

export default nextConfig;
