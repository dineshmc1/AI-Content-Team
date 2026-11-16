import type {NextConfig} from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  distDir: ".next-build",
  // Next 16's parallel static-page workers intermittently collide over generated
  // directories on this Windows filesystem. One worker keeps local builds reliable.
  experimental: {cpus: 1},
};

export default nextConfig;
