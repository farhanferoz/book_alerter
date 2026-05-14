// Typed fetch wrapper over the OpenAPI-generated `paths` type.
// Returns are typed against the 2xx JSON response shape for each path+method.
// Throws ApiError on non-2xx; the parsed body is attached when JSON.

import type { paths } from "./schema";

type Method = "get" | "post" | "patch" | "put" | "delete";

// Path keys that have an operation for method M.
type PathsFor<M extends Method> = {
  [P in keyof paths]: paths[P] extends { [K in M]: unknown } ? P : never;
}[keyof paths];

// 2xx response JSON shape, or unknown when the operation has no JSON response.
type JsonResponse<P, M extends Method> = P extends keyof paths
  ? paths[P] extends { [K in M]: infer Op }
    ? Op extends { responses: infer R }
      ? R extends Record<number, { content: { "application/json": infer J } }>
        ? J
        : unknown
      : unknown
    : unknown
  : unknown;

// JSON request body shape, or undefined when the operation has none.
type JsonBody<P, M extends Method> = P extends keyof paths
  ? paths[P] extends { [K in M]: infer Op }
    ? Op extends { requestBody: { content: { "application/json": infer B } } }
      ? B
      : Op extends { requestBody?: { content: { "application/json": infer B } } }
        ? B | undefined
        : undefined
    : undefined
  : undefined;

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  method: Method,
  path: string,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  const hasBody = body !== undefined;
  if (hasBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, {
    ...init,
    method: method.toUpperCase(),
    headers,
    body: hasBody ? JSON.stringify(body) : init?.body,
  });
  const text = await res.text();
  const parsed: unknown = text ? safeJson(text) : undefined;
  if (!res.ok) {
    throw new ApiError(res.status, parsed, `HTTP ${res.status} ${method.toUpperCase()} ${path}`);
  }
  return parsed as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function apiGet<P extends PathsFor<"get">>(
  path: P,
  init?: RequestInit,
): Promise<JsonResponse<P, "get">> {
  return request<JsonResponse<P, "get">>("get", path as string, undefined, init);
}

export function apiPost<P extends PathsFor<"post">>(
  path: P,
  body?: JsonBody<P, "post">,
  init?: RequestInit,
): Promise<JsonResponse<P, "post">> {
  return request<JsonResponse<P, "post">>("post", path as string, body, init);
}

export function apiPatch<P extends PathsFor<"patch">>(
  path: P,
  body?: JsonBody<P, "patch">,
  init?: RequestInit,
): Promise<JsonResponse<P, "patch">> {
  return request<JsonResponse<P, "patch">>("patch", path as string, body, init);
}

export function apiPut<P extends PathsFor<"put">>(
  path: P,
  body?: JsonBody<P, "put">,
  init?: RequestInit,
): Promise<JsonResponse<P, "put">> {
  return request<JsonResponse<P, "put">>("put", path as string, body, init);
}

export function apiDelete<P extends PathsFor<"delete">>(
  path: P,
  init?: RequestInit,
): Promise<JsonResponse<P, "delete">> {
  return request<JsonResponse<P, "delete">>("delete", path as string, undefined, init);
}
