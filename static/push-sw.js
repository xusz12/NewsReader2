const kinds = new Set(["daily_report", "collection_error", "personal_reply", "article_reply"]);

function notificationURL(kind, target) {
  if (!kinds.has(kind) || typeof target?.id !== "string" || !/^[a-f0-9-]{32,36}$/.test(target.id)) return null;
  const url = new URL("/", self.location.origin);
  url.searchParams.set("nr_push", kind);
  url.searchParams.set("id", target.id);
  if (kind === "collection_error" && /^\d{4}-\d\d-\d\d$/.test(target.day)) url.searchParams.set("day", target.day);
  if (kind === "personal_reply" && Number.isSafeInteger(target.message_id)) url.searchParams.set("message", String(target.message_id));
  return url;
}

self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("push", event => {
  let payload;
  try { payload = event.data?.json(); } catch {}
  const url = notificationURL(payload?.kind, payload?.target);
  event.waitUntil(self.registration.showNotification(url && payload.title || "News Reader", {
    body: url && payload.body || "打开 News Reader 查看通知。",
    tag: /^[a-f0-9]{32}$/.test(payload?.tag) ? payload.tag : "nr-notification",
    icon: "/static/brand/app-icon-192.png",
    data: { url: url?.href || new URL("/", self.location.origin).href },
  }));
});

function openInClient(client, url) {
  return new Promise(resolve => {
    const channel = new MessageChannel();
    const finish = accepted => {
      clearTimeout(timer);
      channel.port1.close();
      resolve(accepted);
    };
    const timer = setTimeout(() => finish(false), 500);
    channel.port1.onmessage = event => finish(event.data === true);
    try { client.postMessage({ type: "nr:push-open", url: url.href }, [channel.port2]); }
    catch { finish(false); }
  });
}

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    let url;
    try { url = new URL(event.notification.data?.url); } catch { return; }
    if (url.origin !== self.location.origin || url.pathname !== "/") return;
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clients) {
      if (new URL(client.url).pathname !== "/") continue;
      if (await openInClient(client, url)) { await client.focus(); return; }
    }
    await self.clients.openWindow(url.href);
  })());
});
