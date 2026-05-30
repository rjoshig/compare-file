/** @type {import('next').NextConfig} */
const API_TARGET = process.env.SEGCMP_API_URL || "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  // TypeScript still type-checks the build; ESLint is optional here (no eslint
  // config shipped) so we don't fail the build on a missing linter.
  eslint: { ignoreDuringBuilds: true },
  // Proxy /api/* to the FastAPI backend so the browser never hits CORS in dev
  // (mirrors the Vite proxy used by ui/). Override the target with SEGCMP_API_URL.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_TARGET}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
