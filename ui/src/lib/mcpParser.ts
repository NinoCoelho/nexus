export interface UrlMissing {
  param: string;
  credName: string;
}

export interface ParsedServer {
  name: string;
  transport: "stdio" | "sse" | "streamable-http";
  command: string[];
  url: string;
  env: Record<string, string>;
  headers: Record<string, string>;
  missing: string[];
  urlMissing: UrlMissing[];
}

const PLACEHOLDER_RE = /<[^>]+>|YOUR_|PLACEHOLDER|_HERE$|insert_|\[.*\]|^$/i;

function serverNameFromCommand(cmd: string[]): string {
  const joined = cmd.join(" ");
  const m = joined.match(/@modelcontextprotocol\/server-([a-z0-9-]+)/);
  if (m) return m[1];
  const m2 = joined.match(/@[^/]+\/server-([a-z0-9-]+)/);
  if (m2) return m2[1];
  const m3 = joined.match(/\/([a-z0-9-]+)(?:\s|$)/);
  if (m3) return m3[1];
  return "server";
}

function serverNameFromUrl(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.hostname.split(".");
    if (parts[0] === "mcp" || parts[0] === "api") return parts[1] || parts[0];
    return parts[0];
  } catch {
    return "server";
  }
}

function makeCredName(serverName: string, paramName: string): string {
  const prefix = serverName.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const norm = paramName.toLowerCase().replace(/-/g, "_");
  if (["apikey", "api_key", "key", "api-key"].includes(norm))
    return `${prefix}_API_KEY`;
  if (["token", "access_token", "auth_token", "bearer_token"].includes(norm))
    return `${prefix}_TOKEN`;
  if (["secret", "client_secret", "api_secret"].includes(norm))
    return `${prefix}_SECRET`;
  return `${prefix}_${norm.toUpperCase().replace(/[^A-Z0-9]/g, "_")}`;
}

function extractUrlPlaceholders(
  url: string,
  serverName: string,
): { url: string; urlMissing: UrlMissing[] } {
  const urlMissing: UrlMissing[] = [];
  try {
    const u = new URL(url);
    for (const [key, value] of [...u.searchParams.entries()]) {
      if (!value || PLACEHOLDER_RE.test(value)) {
        const credName = makeCredName(serverName, key);
        u.searchParams.set(key, `$${credName}`);
        urlMissing.push({ param: key, credName });
      }
    }
    if (urlMissing.length) return { url: u.toString(), urlMissing };
  } catch {
    /* not a valid URL */
  }
  return { url, urlMissing };
}

function parseEnvBlock(env: Record<string, string>): {
  env: Record<string, string>;
  missing: string[];
} {
  const missing: string[] = [];
  const resolved: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    if (!v || PLACEHOLDER_RE.test(v)) {
      missing.push(k);
      resolved[k] = "";
    } else {
      resolved[k] = v;
    }
  }
  return { env: resolved, missing };
}

function parseSingleServer(
  name: string,
  obj: Record<string, unknown>,
): ParsedServer {
  const command = obj.command
    ? [String(obj.command), ...(Array.isArray(obj.args) ? obj.args.map(String) : [])]
    : [];
  let url = String(obj.url || "");
  const rawEnv =
    obj.env && typeof obj.env === "object"
      ? (obj.env as Record<string, string>)
      : {};
  const headers =
    obj.headers && typeof obj.headers === "object"
      ? (obj.headers as Record<string, string>)
      : {};
  const { env, missing } = parseEnvBlock(rawEnv);

  const resolvedName =
    name === "server" && command.length
      ? serverNameFromCommand(command)
      : name === "server" && url
        ? serverNameFromUrl(url)
        : name;

  let urlMissing: UrlMissing[] = [];
  if (url) {
    const extracted = extractUrlPlaceholders(url, resolvedName);
    url = extracted.url;
    urlMissing = extracted.urlMissing;
  }

  let transport: ParsedServer["transport"] = "stdio";
  if (
    obj.type === "http" ||
    obj.type === "sse" ||
    obj.type === "streamable-http"
  ) {
    transport = obj.type as ParsedServer["transport"];
  } else if (url && !command.length) {
    transport = "streamable-http";
  }

  return {
    name: resolvedName,
    transport,
    command,
    url,
    env,
    headers,
    missing,
    urlMissing,
  };
}

function parseJsonObj(obj: Record<string, unknown>): ParsedServer[] {
  const results: ParsedServer[] = [];

  let serversObj: Record<string, unknown> | undefined;
  if (obj.mcpServers && typeof obj.mcpServers === "object") {
    serversObj = obj.mcpServers as Record<string, unknown>;
  } else if (obj.servers && typeof obj.servers === "object") {
    serversObj = obj.servers as Record<string, unknown>;
  } else if (
    obj.mcp &&
    typeof obj.mcp === "object" &&
    (obj.mcp as Record<string, unknown>).servers
  ) {
    serversObj = (obj.mcp as Record<string, unknown>).servers as Record<
      string,
      unknown
    >;
  }

  if (serversObj) {
    for (const [name, val] of Object.entries(serversObj)) {
      if (val && typeof val === "object") {
        results.push(parseSingleServer(name, val as Record<string, unknown>));
      }
    }
    return results;
  }

  if (obj.command || obj.url || obj.type) {
    return [parseSingleServer("server", obj)];
  }

  return results;
}

export function parseMcpConfig(input: string): ParsedServer[] {
  const trimmed = input.trim();

  try {
    const obj = JSON.parse(trimmed);
    return parseJsonObj(obj);
  } catch {
    // not JSON, try other formats
  }

  if (/^https?:\/\//i.test(trimmed)) {
    const serverName = serverNameFromUrl(trimmed);
    const { url, urlMissing } = extractUrlPlaceholders(trimmed, serverName);
    const transport = /\/sse\/?$/.test(trimmed) ? "sse" : "streamable-http";
    return [
      {
        name: serverName,
        transport: transport as "sse" | "streamable-http",
        command: [],
        url,
        env: {},
        headers: {},
        missing: [],
        urlMissing,
      },
    ];
  }

  if (/^(npx|uvx|docker|node|python|bun)\s/i.test(trimmed)) {
    const parts = trimmed.split(/\s+/);
    return [
      {
        name: serverNameFromCommand(parts),
        transport: "stdio",
        command: parts,
        url: "",
        env: {},
        headers: {},
        missing: [],
        urlMissing: [],
      },
    ];
  }

  return [];
}

export function looksLikeAuthError(error: string | null): boolean {
  if (!error) return false;
  const lower = error.toLowerCase();
  return [
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "authentication",
    "invalid api",
    "invalid key",
    "api key required",
    "access denied",
    "not authenticated",
    "auth failed",
    "credential",
  ].some((p) => lower.includes(p));
}

const CRED_PARAM_NAMES = new Set([
  "apikey",
  "api_key",
  "key",
  "token",
  "access_token",
  "secret",
  "auth",
  "password",
  "api-key",
  "bearer",
]);

export function detectUrlCredParams(
  url: string,
  serverName: string,
): UrlMissing[] {
  const result: UrlMissing[] = [];
  try {
    const u = new URL(url);
    for (const [key, value] of [...u.searchParams.entries()]) {
      const normKey = key.toLowerCase().replace(/-/g, "_");
      if (CRED_PARAM_NAMES.has(normKey) && !value.startsWith("$")) {
        const credName = makeCredName(serverName, key);
        result.push({ param: key, credName });
      }
    }
  } catch {
    /* not a valid URL */
  }
  return result;
}
