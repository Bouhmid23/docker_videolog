import { auth } from "./firebase";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { skipAuth?: boolean } = {}
): Promise<T> {
  const { skipAuth, headers, ...rest } = options;
  const finalHeaders = new Headers(headers);
  if (
    !finalHeaders.has("Content-Type") &&
    rest.body &&
    !(rest.body instanceof FormData)
  ) {
    finalHeaders.set("Content-Type", "application/json");
  }

  if (!skipAuth && auth.currentUser) {
    const token = await auth.currentUser.getIdToken();
    finalHeaders.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`/api${path}`, {
    ...rest,
    headers: finalHeaders,
  });

  if (!response.ok) {
    let detail = `Erreur HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") {
        detail = data.detail;
      } else if (Array.isArray(data?.detail) && data.detail[0]?.msg) {
        detail = data.detail[0].msg;
      }
    } catch {
      // ignore JSON parse errors
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function apiDownloadBlob(
  path: string,
  fallbackFilename: string
): Promise<void> {
  const headers = new Headers();
  if (auth.currentUser) {
    const token = await auth.currentUser.getIdToken();
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`/api${path}`, { headers });
  if (!response.ok) {
    let detail = `Erreur HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      // ignore
    }
    throw new ApiError(response.status, detail);
  }
  const blob = await response.blob();
  const cd = response.headers.get("Content-Disposition") ?? "";
  const match = cd.match(/filename="?([^"]+)"?/i);
  const filename = match?.[1] || fallbackFilename;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
