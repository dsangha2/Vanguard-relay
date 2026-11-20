/* Phone alerts. The push has no message, so the text comes from /api/push/latest. */
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener('push', e => {
  e.waitUntil((async () => {
    let title = 'Vanguard needs you';
    let body = '';
    let tag = 'vanguard';
    try {
      const r = await fetch('/api/push/latest', {cache: 'no-store', credentials: 'same-origin'});
      if (r.ok) {
        const d = await r.json();
        if (d && d.title) {
          title = d.title;
          body = d.body || '';
          tag = d.goal || d.project || 'vanguard';
        }
      }
    } catch (err) {}
    await self.registration.showNotification(title, {
      body,
      tag,
      icon: '/assets/apple-touch-icon.png',
      data: {url: '/'}
    });
  })());
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    for (const c of list) {
      if ('focus' in c) return c.focus();
    }
    return clients.openWindow('/');
  }));
});
