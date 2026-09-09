import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The stable Windows launcher opens this loopback host in development.
  allowedDevOrigins: ["127.0.0.1"],
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
