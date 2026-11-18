// @ts-nocheck
// NOTE: The @ts-nocheck above silences editor-only TS errors (missing @types/node:
// `node:*` imports, `Buffer`). These are cosmetic — Bun runs this .ts directly and
// provides all Node built-ins at runtime, so the errors have no runtime effect.
// DO NOT "fix" them by adding a tsconfig.json or @types/* to this directory
// (opencode and skills/opencode/, symlinked to ~/.config/opencode). Bun reads any
// tsconfig found here when loading the plugin and it can break OpenCode startup.
// See PLAN.md §4 "Gotchas / lessons" and the "Migration log" section. Type-check in the fork instead.
//
// opencode-vision — live plugin loaded by OpenCode from ~/.config/opencode/plugins/
//
// SOURCE OF TRUTH: github.com/bankenichi/opencode-vision (AGPL-3.0). This file is a
// vendored copy. No build step is required: OpenCode runs on Bun, which executes
// TypeScript directly, and this plugin's only imports are Node built-ins — zero
// runtime npm dependencies. (Deliberately no `@opencode/plugin` import: the plugin
// definition object is hand-built below, so nothing has to be installed for the
// config dir.)
//
// If you edit the plugin, do it in the fork, then re-copy here (and into
// ../../opencode-vision/plugin/src-fork/src/index.ts). See opencode-vision/plugin/README.md.
//
// OPENCODE V2 PLUGIN API (rewritten 2026-11-18 — see PLAN.md "Migration log"):
//   OpenCode 2.x validates the module's default export as a *definition object*:
//
//       interface Plugin { id: string; setup: (ctx) => Promise<Cleanup|void> | Cleanup | void }
//
//   The V1 shape ("default export is an async function returning a hooks object") no
//   longer runs; it fails at startup with
//   `PluginModule.LoadError: Plugin must export a default definition with an id and an
//   effect or setup function. (cause: SchemaError(Expected object at ["default"]))`.
//   Docs: https://opencode.ai/v2/docs/build/plugins  (migrate: /build/plugins/migrate-v1)
//
//   V1 → V2 mapping used here:
//     - `client.app.log(...)`                 → `console.log(...)` (lands in opencode.log)
//     - `experimental.chat.messages.transform` → `ctx.session.hook("context", cb)`
//     - `event` handler on `session.idle`      → `ctx.session.hook("experimental.idle", cb)`
//     - `server.instance.disposed`             → the `Cleanup` fn returned from `setup`
//     - model on the message (`info.modelID`)  → `event.model` = { providerID, id, variant? }
//     - `parts[]` / `FilePart{mime,url}`       → `message.content[]` / `MediaPart{media:{source}}`
//
// Pairs with: the `vision` MCP server (opencode-vision/mcp/vision_mcp.py) which exposes
// the `vision_analyze` tool, and config/opencode-vision.json which sets the model patterns
// and imageAnalysisTool.

import { tmpdir, homedir } from "node:os";
import { join } from "node:path";
import {
  mkdir,
  writeFile,
  readFile,
  unlink,
  readdir,
  stat,
} from "node:fs/promises";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

// Constants

const PLUGIN_NAME = "opencode-vision";
const CONFIG_FILENAME = "opencode-vision.json";
const TEMP_DIR_NAME = "opencode-vision";
const MAX_TOOL_NAME_LENGTH = 256;

/**
 * Temp images are also swept when they are older than this, so files left behind by a
 * crashed/aborted session (idle hook never fired) do not pile up forever.
 */
const STALE_IMAGE_MAX_AGE_MS = 12 * 60 * 60 * 1000;

const PROMPT_TEMPLATE_VARIABLES = [
  "{imageList}",
  "{imageCount}",
  "{toolName}",
  "{userText}",
] as const;

const DEFAULT_MODEL_PATTERNS: readonly string[] = ["*/qwen3-coder-next-mlx"];
const DEFAULT_IMAGE_ANALYSIS_TOOL = "local_vision";

const SUPPORTED_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/webp",
]);

const MIME_TO_EXTENSION: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/webp": "webp",
};

/** Reverse of MIME_TO_EXTENSION: used when a media part carries only a filename. */
const EXTENSION_TO_MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
};

// Types

interface PluginConfig {
  models?: string[];
  imageAnalysisTool?: string;
  promptTemplate?: string;
}

/**
 * An image that has been made available to the MCP vision tool.
 *
 * `part` is the *identity* of the content part that produced it — V2 media parts have no
 * id field, so the transform below drops processed parts by object identity.
 * `temporary` is true only for files this plugin wrote into the temp dir.
 */
interface SavedImage {
  path: string;
  mime: string;
  part: unknown;
  temporary: boolean;
}

interface ModelInfo {
  providerID: string;
  modelID: string;
}

type Logger = (msg: string) => void;

// Plugin State

let pluginConfig: PluginConfig = {};

/** sessionID → set of temp file paths created for that session. */
const sessionTempImages = new Map<string, Set<string>>();

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// Config: Path Resolution

function getUserConfigPath(): string {
  return join(homedir(), ".config", "opencode", CONFIG_FILENAME);
}

function getProjectConfigPath(directory: string): string {
  return join(directory, ".opencode", CONFIG_FILENAME);
}

// Config: File Parsing

function parseModelsArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const models = value.filter((m): m is string => typeof m === "string");
  return models.length > 0 ? models : undefined;
}

function parseImageAnalysisTool(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  if (value.trim() === "") return undefined;
  if (value.length > MAX_TOOL_NAME_LENGTH) return undefined;
  return value;
}

function parsePromptTemplate(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  if (!PROMPT_TEMPLATE_VARIABLES.some((v) => trimmed.includes(v)))
    return undefined;
  return trimmed;
}

function parseConfigObject(raw: unknown): PluginConfig {
  if (!raw || typeof raw !== "object") return {};

  const obj = raw as Record<string, unknown>;
  return {
    models: parseModelsArray(obj.models),
    imageAnalysisTool: parseImageAnalysisTool(obj.imageAnalysisTool),
    promptTemplate: parsePromptTemplate(obj.promptTemplate),
  };
}

async function readConfigFile(
  configPath: string,
): Promise<PluginConfig | null> {
  if (!existsSync(configPath)) return null;

  try {
    const content = await readFile(configPath, "utf-8");
    const parsed = JSON.parse(content) as unknown;
    return parseConfigObject(parsed);
  } catch {
    return null;
  }
}

// Config: Precedence & Merging (project > user > defaults)

function selectWithPrecedence<T>(
  projectValue: T | undefined,
  userValue: T | undefined,
  defaultValue: T,
): { value: T; source: "project" | "user" | "default" } {
  if (projectValue !== undefined) {
    return { value: projectValue, source: "project" };
  }
  if (userValue !== undefined) {
    return { value: userValue, source: "user" };
  }
  return { value: defaultValue, source: "default" };
}

async function loadPluginConfig(directory: string, log: Logger): Promise<void> {
  const userConfig = await readConfigFile(getUserConfigPath());
  const projectConfig = await readConfigFile(getProjectConfigPath(directory));

  // Resolve models with precedence
  const modelsResult = selectWithPrecedence(
    projectConfig?.models,
    userConfig?.models,
    undefined,
  );
  if (modelsResult.source !== "default") {
    log(
      `Loaded models from ${modelsResult.source} config: ${modelsResult.value?.join(", ")}`,
    );
  } else {
    log(`Using default models: ${DEFAULT_MODEL_PATTERNS.join(", ")}`);
  }

  // Resolve imageAnalysisTool with precedence
  const toolResult = selectWithPrecedence(
    projectConfig?.imageAnalysisTool,
    userConfig?.imageAnalysisTool,
    undefined,
  );
  if (toolResult.source !== "default") {
    log(
      `Using imageAnalysisTool from ${toolResult.source} config: ${toolResult.value}`,
    );
  } else {
    log(`Using default imageAnalysisTool: ${DEFAULT_IMAGE_ANALYSIS_TOOL}`);
  }

  // Resolve promptTemplate with precedence
  const templateResult = selectWithPrecedence(
    projectConfig?.promptTemplate,
    userConfig?.promptTemplate,
    undefined,
  );
  if (templateResult.source !== "default") {
    log(
      `Using promptTemplate from ${templateResult.source} config (${templateResult.value?.length ?? 0} chars)`,
    );
  } else {
    log("Using default (hardcoded) injection prompt template");
  }

  pluginConfig = {
    models: modelsResult.value,
    imageAnalysisTool: toolResult.value,
    promptTemplate: templateResult.value,
  };
}

// Config: Accessors

function getConfiguredModels(): readonly string[] {
  return pluginConfig.models ?? DEFAULT_MODEL_PATTERNS;
}

function getImageAnalysisTool(): string {
  return pluginConfig.imageAnalysisTool ?? DEFAULT_IMAGE_ANALYSIS_TOOL;
}

function getPromptTemplate(): string | undefined {
  return pluginConfig.promptTemplate;
}

// Temp File Tracking & Cleanup
//
// Images decoded from a request are written to %TEMP%/opencode-vision/ so the MCP tool can
// open them by path. They are deleted when the session goes idle; the shutdown Cleanup and
// the stale sweep cover sessions that never reach idle (interrupted/failed runs).

function trackTemporaryImages(sessionID: string, images: SavedImage[]): void {
  const tempPaths = images
    .filter((img) => img.temporary)
    .map((img) => img.path);
  if (tempPaths.length === 0) return;

  const existing = sessionTempImages.get(sessionID) ?? new Set<string>();
  for (const tempPath of tempPaths) {
    existing.add(tempPath);
  }
  sessionTempImages.set(sessionID, existing);
}

async function cleanupSessionTempImages(
  sessionID: string,
  log: Logger,
): Promise<void> {
  const tempPaths = sessionTempImages.get(sessionID);
  if (!tempPaths || tempPaths.size === 0) return;

  sessionTempImages.delete(sessionID);

  for (const tempPath of tempPaths) {
    try {
      await unlink(tempPath);
      log(`Deleted temp image: ${tempPath}`);
    } catch (err) {
      log(`Failed to delete temp image ${tempPath}: ${errorMessage(err)}`);
    }
  }
}

async function cleanupAllTempImages(log: Logger): Promise<void> {
  const sessionIDs = Array.from(sessionTempImages.keys());
  for (const sessionID of sessionIDs) {
    await cleanupSessionTempImages(sessionID, log);
  }
}

/**
 * Deletes temp images this plugin created more than STALE_IMAGE_MAX_AGE_MS ago.
 *
 * Covers files that were never tracked (or never cleaned) because the owning session was
 * interrupted, failed, or the process died. Only touches files inside our own temp
 * sub-directory; anything missing/vanished in the meantime is ignored.
 */
async function cleanupStaleTempImages(log: Logger): Promise<void> {
  const dir = join(tmpdir(), TEMP_DIR_NAME);
  if (!existsSync(dir)) return;

  let entries: string[];
  try {
    entries = await readdir(dir);
  } catch (err) {
    log(`Temp dir sweep skipped: ${errorMessage(err)}`);
    return;
  }

  const now = Date.now();
  for (const entry of entries) {
    const filePath = join(dir, entry);
    try {
      const info = await stat(filePath);
      if (!info.isFile()) continue;
      if (now - info.mtimeMs <= STALE_IMAGE_MAX_AGE_MS) continue;
      await unlink(filePath);
      log(`Deleted stale temp image: ${filePath}`);
    } catch {
      // File raced away or is not ours to delete; the next sweep will try again.
    }
  }
}

// Pattern Matching (supports wildcards: *, prefix*, *suffix, *contains*)

function matchesWildcardPattern(pattern: string, value: string): boolean {
  const p = pattern.toLowerCase();
  const v = value.toLowerCase();

  // Global wildcard
  if (p === "*") return true;

  // Contains: *text*
  if (p.startsWith("*") && p.endsWith("*") && p.length > 2) {
    return v.includes(p.slice(1, -1));
  }

  // Prefix: text*
  if (p.endsWith("*")) {
    return v.startsWith(p.slice(0, -1));
  }

  // Suffix: *text
  if (p.startsWith("*")) {
    return v.endsWith(p.slice(1));
  }

  // Exact match
  return v === p;
}

function matchesSinglePattern(pattern: string, model: ModelInfo): boolean {
  // Global wildcard matches everything
  if (pattern === "*") return true;

  const slashIndex = pattern.indexOf("/");

  // No slash: match against both provider and model
  if (slashIndex === -1) {
    return (
      matchesWildcardPattern(pattern, model.modelID) ||
      matchesWildcardPattern(pattern, model.providerID)
    );
  }

  // With slash: match provider/model separately
  const providerPattern = pattern.slice(0, slashIndex);
  const modelPattern = pattern.slice(slashIndex + 1);

  return (
    matchesWildcardPattern(providerPattern, model.providerID) &&
    matchesWildcardPattern(modelPattern, model.modelID)
  );
}

function modelMatchesAnyPattern(model: ModelInfo | undefined): boolean {
  if (!model) return false;

  const patterns = getConfiguredModels();
  return patterns.some((pattern) => matchesSinglePattern(pattern, model));
}

// Type Guards (V2 request messages)
//
// A V2 request message is `{ role, content: ContentPart[] }`, and a user turn's content
// parts are:
//   - TextPart:  { type: "text", text }
//   - MediaPart: { type: "media", media, filename?, cache?, providerMetadata? }
// `media` is a media Asset: it carries `source` (a tagged union — bytes | base64 | url |
// ref) plus a resolved `mediaType`. The older V1 shape was `parts[]` with `FilePart`.

function isTextPart(part: unknown): boolean {
  return (part as { type?: string })?.type === "text";
}

/** Returns the Asset carrying a media part's payload, or undefined if absent. */
function getMediaAsset(part: unknown): any {
  if ((part as { type?: string })?.type !== "media") return undefined;
  return (part as { media?: any }).media;
}

function mimeFromFilename(filename: unknown): string | undefined {
  if (typeof filename !== "string") return undefined;
  const dot = filename.lastIndexOf(".");
  if (dot === -1 || dot === filename.length - 1) return undefined;
  const ext = filename.slice(dot + 1).toLowerCase();
  return EXTENSION_TO_MIME[ext];
}

/**
 * Resolves the media type of a media part.
 *
 * Preference order: the Asset's own resolved `mediaType` (OpenCode sniffs magic bytes, so
 * this is the most reliable), then the source's declared `mediaType`, then the part's
 * filename extension. Returns "" when nothing is declared, so callers can skip the part.
 */
function resolveMediaMime(part: unknown): string {
  const asset = getMediaAsset(part);
  if (!asset) return "";

  const candidates: unknown[] = [
    asset.mediaType,
    asset.source?.mediaType,
    mimeFromFilename((part as { filename?: unknown }).filename),
    mimeFromFilename(asset.filename),
  ];

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate !== "") {
      return candidate.toLowerCase();
    }
  }
  return "";
}

/** True when the part is a media part whose type is one of the supported image mimetypes. */
function isImageMediaPart(part: unknown): boolean {
  return SUPPORTED_MIME_TYPES.has(resolveMediaMime(part));
}

// Image Processing: Media Sources
//
// V2 tags where the bytes live instead of encoding it in a URL scheme:
//   base64 / bytes → inline in the request; must be decoded and saved to a temp file
//   url            → `file:` (already on disk), `http(s)` (pass through to the MCP tool)
//                    or a `data:` URL (defensive; treated like base64)
//   ref            → an id in the provider's own storage; no local bytes, so it is skipped

function getExtensionForMime(mime: string): string {
  return MIME_TO_EXTENSION[mime.toLowerCase()] ?? "png";
}

async function ensureTempDir(): Promise<string> {
  const dir = join(tmpdir(), TEMP_DIR_NAME);
  await mkdir(dir, { recursive: true });
  return dir;
}

async function saveImageToTemp(data: Buffer, mime: string): Promise<string> {
  const tempDir = await ensureTempDir();
  const filename = `${randomUUID()}.${getExtensionForMime(mime)}`;
  const filepath = join(tempDir, filename);
  await writeFile(filepath, data);
  return filepath;
}

/** Saves decoded image bytes into the temp dir and returns the SavedImage record. */
async function saveInlineBytes(
  data: Buffer,
  mime: string,
  part: unknown,
  log: Logger,
): Promise<SavedImage | null> {
  try {
    const savedPath = await saveImageToTemp(data, mime);
    log(`Saved image to: ${savedPath}`);
    return { path: savedPath, mime, part, temporary: true };
  } catch (err) {
    log(`Failed to save image: ${errorMessage(err)}`);
    return null;
  }
}

function parseBase64DataUrl(
  dataUrl: string,
): { mime: string; data: Buffer } | null {
  const match = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
  if (!match) return null;

  try {
    return { mime: match[1], data: Buffer.from(match[2], "base64") };
  } catch {
    return null;
  }
}

/**
 * Turns a `file:` URL into a local path.
 *
 * `fileURLToPath` is used first because it handles the Windows
 * `file:///C:/dir/file.png` form correctly (a naive strip of `file://` leaves a leading
 * slash and the path will not open). Falls back to the naive strip only if the URL is
 * malformed.
 */
function fileUrlToLocalPath(url: string, log: Logger): string {
  try {
    return fileURLToPath(url);
  } catch (err) {
    const stripped = url.replace(/^file:\/\/\/?/, "");
    log(
      `fileURLToPath failed (${errorMessage(err)}); using stripped path: ${stripped}`,
    );
    return stripped;
  }
}

/** Handles a media part whose source is `{ type: "url", url }`. */
async function handleUrlSource(
  url: string,
  mime: string,
  part: unknown,
  log: Logger,
): Promise<SavedImage | null> {
  if (url.startsWith("file:")) {
    // Image is already saved on disk; the MCP tool can read it directly.
    const localPath = fileUrlToLocalPath(url, log);
    log(`Image already on disk: ${localPath}`);
    return { path: localPath, mime, part, temporary: false };
  }

  if (url.startsWith("http://") || url.startsWith("https://")) {
    // Remote URLs are passed directly to the MCP tool, which can fetch them itself.
    // This avoids unnecessary network requests and disk I/O.
    log(`Image is remote URL: ${url}`);
    return { path: url, mime, part, temporary: false };
  }

  if (url.startsWith("data:")) {
    // Pasted clipboard images may still arrive as base64 data URLs.
    const parsed = parseBase64DataUrl(url);
    if (!parsed) {
      log("Failed to parse data URL for a media part");
      return null;
    }
    return saveInlineBytes(parsed.data, parsed.mime || mime, part, log);
  }

  log(`Unsupported URL scheme for a media part: ${url.slice(0, 50)}...`);
  return null;
}

/**
 * Makes one media part available to the vision tool.
 * Returns the SavedImage record, or null when the part cannot be used
 * (unsupported source, provider-side `ref`, write failure).
 */
async function processMediaPart(
  part: unknown,
  log: Logger,
): Promise<SavedImage | null> {
  const mime = resolveMediaMime(part);
  const asset = getMediaAsset(part);
  const source = asset?.source;

  if (!source || typeof source !== "object") {
    // No tagged source; fall back to the Asset's synchronous inline view if present.
    const inline =
      typeof asset?.inline === "function" ? asset.inline() : undefined;
    if (inline?.base64) {
      return saveInlineBytes(
        Buffer.from(inline.base64, "base64"),
        inline.mime || mime,
        part,
        log,
      );
    }
    log("Skipping media part: no source payload");
    return null;
  }

  switch (source.type) {
    case "base64":
      if (typeof source.data !== "string") return null;
      return saveInlineBytes(
        Buffer.from(source.data, "base64"),
        source.mediaType || mime,
        part,
        log,
      );

    case "bytes":
      if (!source.data) return null;
      // `data` is a Uint8Array; Buffer.from accepts it without copying semantics we care about.
      return saveInlineBytes(
        Buffer.from(source.data),
        source.mediaType || mime,
        part,
        log,
      );

    case "url":
      if (typeof source.url !== "string") return null;
      return handleUrlSource(source.url, source.mediaType || mime, part, log);

    case "ref":
      // Provider-hosted asset: there are no local bytes to hand to the MCP tool.
      log(
        `Skipping media part: provider-hosted ref (${String(source.provider ?? "unknown")})`,
      );
      return null;

    default:
      log(`Skipping media part: unknown source type ${String(source.type)}`);
      return null;
  }
}

// Prompt Generation
//
// Since the target model doesn't natively understand image attachments,
// we replace them with text instructions that tell the model to use a
// configured MCP image tool (e.g. local_vision / vision_analyze) with the
// file path or URL. The user's original text is preserved as "User's request: ...".

function applyPromptTemplate(
  template: string,
  vars: {
    imageList: string;
    imageCount: number;
    toolName: string;
    userText: string;
  },
): string {
  return template
    .replace(/\{imageList\}/g, vars.imageList)
    .replace(/\{imageCount\}/g, String(vars.imageCount))
    .replace(/\{userText\}/g, vars.userText)
    .replace(/\{toolName\}/g, vars.toolName);
}

function generateInjectionPrompt(
  images: SavedImage[],
  userText: string,
  toolName: string,
): string {
  if (images.length === 0) return userText;

  const imageList = images
    .map((img, idx) => `- Image ${idx + 1}: ${img.path}`)
    .join("\n");

  const customTemplate = getPromptTemplate();
  if (customTemplate !== undefined) {
    return applyPromptTemplate(customTemplate, {
      imageList,
      imageCount: images.length,
      toolName,
      userText,
    });
  }

  const isSingle = images.length === 1;

  const imageCountText = isSingle ? "an image" : `${images.length} images`;
  const imagePlural = isSingle ? "image is" : "images are";
  const analyzeText = isSingle ? "this image" : "each image";

  return `The user has shared ${imageCountText}. The ${imagePlural} saved at:
${imageList}

Use the \`${toolName}\` tool to analyze ${analyzeText}.

User's request: ${userText || "(analyze the image)"}`;
}

// Message Transformation
//
// The transformation flow (inside the `context` hook, which sees the outbound request):
// 1. Find the last user-role message in `event.messages`
// 2. Extract and save any image media parts from its `content`
// 3. Drop those media parts (the matched model can't interpret raw images)
// 4. Put the injection instructions in the message's text part
//
// Decoded schema values may be immutable class instances, so the transform builds NEW
// objects instead of mutating the ones it was handed: the message is rebuilt as a plain
// `{ ...message, content: newContent }` and swapped into the messages array. That also
// keeps the stored session transcript untouched — the pasted image stays in the user's
// history and only the copy sent to the model changes.

function findLastUserMessageIndex(messages: unknown[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if ((messages[i] as { role?: string })?.role === "user") return i;
  }
  return -1;
}

/**
 * Builds the replacement `content` array for a processed message.
 *
 * Processed media parts are dropped by object identity (V2 media parts carry no id).
 * The injection text replaces the first remaining text part; if the message had no text
 * part, one is prepended.
 */
function buildTransformedContent(
  content: unknown[],
  processedParts: Set<unknown>,
  transformedText: string,
): unknown[] {
  const kept = content.filter((part) => !processedParts.has(part));

  const textIndex = kept.findIndex(isTextPart);
  if (textIndex !== -1) {
    const copy = kept.slice();
    copy[textIndex] = {
      ...(kept[textIndex] as Record<string, unknown>),
      text: transformedText,
    };
    return copy;
  }

  return [{ type: "text", text: transformedText }, ...kept];
}

/**
 * Replaces the message at `index` without mutating the original object.
 *
 * Tries the least destructive option that actually takes effect: swap the array element
 * in place, else reassign `event.messages`, else (last resort) write `content` on the
 * original message object.
 */
function applyTransformedMessage(
  event: any,
  messages: unknown[],
  index: number,
  updated: unknown,
  log: Logger,
): boolean {
  try {
    messages[index] = updated;
    if (messages[index] === updated) return true;
  } catch {
    // Frozen array: fall through to reassigning event.messages.
  }

  try {
    event.messages = [...messages.slice(0, index), updated, ...messages.slice(index + 1)];
    if (event.messages?.[index] === updated) return true;
  } catch {
    // Not writable: fall through to mutating content in place.
  }

  try {
    (messages[index] as { content?: unknown }).content = (
      updated as { content?: unknown }
    ).content;
    return true;
  } catch (err) {
    log(`Could not apply the transformed message: ${errorMessage(err)}`);
    return false;
  }
}

/**
 * `context` hook callback: rewrites image parts for models that cannot see them.
 *
 * Never throws — a plugin bug must not break the model request. On any unexpected problem
 * the request is left untouched and the problem is logged.
 */
async function handleContextEvent(event: any, log: Logger): Promise<void> {
  try {
    const modelRef = event?.model;
    const model: ModelInfo | undefined = modelRef
      ? { providerID: modelRef.providerID, modelID: modelRef.id }
      : undefined;

    if (!modelMatchesAnyPattern(model)) return;
    log("Model matched, checking for images...");

    const messages = Array.isArray(event.messages) ? event.messages : [];
    const index = findLastUserMessageIndex(messages);
    if (index === -1) return;

    const message = messages[index] as { content?: unknown };
    const content = Array.isArray(message.content) ? message.content : [];
    const imageParts = content.filter(isImageMediaPart);
    if (imageParts.length === 0) return;

    log("Found images in message, processing...");

    // Opportunistic: reclaim leftovers from aborted sessions while we are here.
    await cleanupStaleTempImages(log);

    const savedImages: SavedImage[] = [];
    for (const part of imageParts) {
      const saved = await processMediaPart(part, log);
      if (saved) savedImages.push(saved);
    }

    if (savedImages.length === 0) {
      log("No images were successfully saved");
      return;
    }

    trackTemporaryImages(String(event.sessionID), savedImages);

    log(`Saved ${savedImages.length} image(s), transforming message...`);

    const userText =
      (content.find(isTextPart) as { text?: string } | undefined)?.text ?? "";

    const transformedText = generateInjectionPrompt(
      savedImages,
      userText,
      getImageAnalysisTool(),
    );

    const processedParts = new Set(savedImages.map((img) => img.part));
    const newContent = buildTransformedContent(
      content,
      processedParts,
      transformedText,
    );

    const applied = applyTransformedMessage(
      event,
      messages,
      index,
      { ...(message as Record<string, unknown>), content: newContent },
      log,
    );

    if (applied) log("Successfully injected image path instructions");
  } catch (err) {
    log(`Context transform skipped after error: ${errorMessage(err)}`);
  }
}

// Plugin Definition (V2: a plain `{ id, setup }` object, no npm import needed)

export const OpencodeVisionPlugin = {
  id: PLUGIN_NAME,

  /**
   * Registers the plugin's hooks. `ctx` is the V2 plugin context; only
   * `ctx.location.directory` and `ctx.session.hook` are used.
   *
   * Returns a Cleanup that disposes the hook registrations and deletes any temp
   * images still pending (the V2 replacement for the V1 `server.instance.disposed` event).
   */
  setup: async (ctx: any) => {
    const directory: string = ctx?.location?.directory ?? process.cwd();

    // V1 used client.app.log(); the V2 plugin API has no log endpoint, so stdout is
    // captured into OpenCode's log file instead.
    const log: Logger = (msg) => {
      console.log(`[${PLUGIN_NAME}] ${msg}`);
    };

    await loadPluginConfig(directory, log);
    log("Plugin initialized");

    const registrations: Array<{ dispose: () => Promise<void> }> = [];

    // Rewrites image media parts for models that cannot accept them.
    try {
      registrations.push(
        await ctx.session.hook("context", (event: any) =>
          handleContextEvent(event, log),
        ),
      );
    } catch (err) {
      log(
        `Could not register the "context" hook (${errorMessage(err)}); image injection is disabled.`,
      );
    }

    // Deletes this session's temp images once it finishes model work.
    try {
      registrations.push(
        await ctx.session.hook("experimental.idle", (event: any) => {
          cleanupSessionTempImages(String(event?.sessionID), log).catch((err) =>
            log(`Idle cleanup failed: ${errorMessage(err)}`),
          );
        }),
      );
    } catch (err) {
      log(
        `Could not register the "experimental.idle" hook (${errorMessage(err)}); temp images rely on the stale sweep.`,
      );
    }

    return async () => {
      for (const registration of registrations) {
        try {
          await registration.dispose();
        } catch {
          // Shutting down; nothing useful to do.
        }
      }
      await cleanupAllTempImages(log);
    };
  },
};

export default OpencodeVisionPlugin;
