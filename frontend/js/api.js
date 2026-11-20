// Thin fetch wrapper: adds the login token, turns errors into ApiError with a readable message.
import { formatError } from './utils.js';

const TOKEN_KEY = 'college_timetable_token';

export const session = {
  get token() { return localStorage.getItem(TOKEN_KEY); },
  set token(value) { value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY); },
};

export class ApiError extends Error {
  constructor(status, detail) {
    super(formatError(detail));
    this.status = status;
    this.detail = detail;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

async function request(method, path, body) {
  const headers = {};
  // FormData (file uploads) must NOT get a JSON content type: the browser adds the multipart boundary.
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method, headers, body: body === undefined ? undefined : (isForm ? body : JSON.stringify(body)),
    });
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Is `python run.py` still running?');
  }
  if (res.status === 401 && !path.startsWith('/auth/login') && !path.startsWith('/auth/register')) {
    session.token = null;
    onUnauthorized();
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new ApiError(res.status, data && data.detail !== undefined ? data.detail : data);
  return data;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body = {}) => request('POST', path, body),
  put: (path, body = {}) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
  upload: (path, formData) => request('POST', path, formData),
};

/** Download an authenticated file (the CSV export) through a temporary link. */
export async function download(path, filename) {
  const res = await fetch(`/api${path}`, { headers: { Authorization: `Bearer ${session.token}` } });
  if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail);
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
