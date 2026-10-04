// Minimal service worker: makes the console installable as an app ("Add to Home Screen" / "Install").
// It deliberately caches nothing — the console is live (your C64, streams, library), and the emulator
// already keeps its own files and saves fresh — so every request simply goes to the network.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
