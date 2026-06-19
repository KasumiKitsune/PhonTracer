let engineConnection = null;

export function setEngineConnection(connection) {
  engineConnection = connection || null;
}

export function getEngineConnection() {
  return engineConnection;
}

export async function apiFetch(path, options = {}) {
  if (!engineConnection) {
    throw new Error('PhonTracer 分析引擎尚未就绪');
  }
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const headers = new Headers(options.headers || {});
  headers.set('Authorization', `Bearer ${engineConnection.token}`);
  return fetch(`${engineConnection.api_base}${normalizedPath}`, {
    ...options,
    headers,
  });
}
