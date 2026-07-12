import type { NextConfig } from "next";

const backendBase = (
  process.env.BACKEND_API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8123"
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendBase}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
