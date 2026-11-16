import vinext from "vinext";
import { defineConfig, type Plugin } from "vite";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { readExecutionProfile } from "./scripts/execution-profile.mjs";

const SITE_CREATOR_PLACEHOLDER_DATABASE_ID =
  "00000000-0000-4000-8000-000000000000";

// hosting.json 与 sites-vite-plugin 是托管环境注入的文件，本地/树莓派
// 部署时可能不存在；缺失时退化为无 D1/R2 绑定、无托管插件。
function loadHostingConfig(): { d1?: string; r2?: string } {
  try {
    const path = fileURLToPath(new URL("./.openai/hosting.json", import.meta.url));
    if (!existsSync(path)) return {};
    return JSON.parse(readFileSync(path, "utf8")) as { d1?: string; r2?: string };
  } catch {
    return {};
  }
}

const hostingConfig = loadHostingConfig();
const { d1, r2 } = hostingConfig;

async function loadSitesPlugin(): Promise<Plugin[]> {
  try {
    // @ts-expect-error -- ./build/sites-vite-plugin 由托管环境注入，本地/树莓派不存在，运行时 try/catch 兜底
    const mod = (await import("./build/sites-vite-plugin")) as {
      sites?: (opts: { mockAuth: boolean }) => Plugin;
    };
    return mod.sites ? [mod.sites({ mockAuth: true })] : [];
  } catch {
    return [];
  }
}

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";
const managedLinux = readExecutionProfile() === "managed-linux";

const localBindingConfig = {
  main: "vinext/server/fetch-handler",
  compatibility_flags: ["nodejs_compat"],
  d1_databases: d1
    ? [
        {
          binding: d1,
          database_name: "site-creator-d1",
          database_id: SITE_CREATOR_PLACEHOLDER_DATABASE_ID,
        },
      ]
    : [],
  r2_buckets: r2
    ? [
        {
          binding: r2,
          bucket_name: "site-creator-r2",
        },
      ]
    : [],
};

export default defineConfig(async () => {
  // Use Miniflare's local Request.cf placeholder unless fetching is requested.
  process.env.CLOUDFLARE_CF_FETCH_ENABLED ??= "false";
  process.env.WRANGLER_SEND_METRICS ??= "false";

  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.WRANGLER_REGISTRY_PATH ??= ".wrangler/dev-registry";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    server: {
      ...(managedLinux ? { host: "0.0.0.0", allowedHosts: ["terminal.local"] } : {}),
      ...(isCodexSeatbeltSandbox ? { watch: { useFsEvents: false, usePolling: true } } : {}),
    },
    plugins: [
      vinext(),
      ...(await loadSitesPlugin()),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        inspectorPort: false,
        config: localBindingConfig,
      }),
    ],
  };
});
