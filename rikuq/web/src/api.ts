export async function health(base = "/api", request: typeof fetch = fetch): Promise<boolean> {
  const response = await request(`${base}/health`);
  if (!response.ok) throw new Error("El servidor de Rikuq no responde.");
  const body = await response.json();
  return body.status === "ok" && body.app === "rikuq";
}
