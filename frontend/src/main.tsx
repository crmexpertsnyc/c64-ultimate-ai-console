import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import './styles.css'
import { Layout } from './components/Layout'
import { ToastProvider, useToast } from './components/Toasts'
import { setInputErrorHandler } from './controllers/inputQueue'
import { LiveProvider } from './hooks/useLive'
import { Dashboard } from './pages/Dashboard'
import { Library } from './pages/Library'
import { PlaylistsPage } from './pages/PlaylistsPage'
import { NewsPage } from './pages/NewsPage'
import { ShopPage } from './pages/ShopPage'
import { RepairPage } from './pages/RepairPage'
import { CollectionPage } from './pages/CollectionPage'
import { JukeboxPage } from './pages/JukeboxPage'
import { MagazinesPage } from './pages/MagazinesPage'
import { EventsPage } from './pages/EventsPage'
import { BbsPage } from './pages/BbsPage'
const BbsTerminal = lazy(() => import('./pages/BbsTerminal').then((m) => ({ default: m.BbsTerminal })))
import { TvMode } from './pages/TvMode'
import { NetplayGuest } from './pages/NetplayGuest'
import { CompatPage } from './pages/CompatPage'
import { GameDetail } from './pages/GameDetail'
import { ControllerPage } from './pages/ControllerPage'
import { MenuPage } from './pages/MenuPage'
import { StreamPage } from './pages/StreamPage'
import { SettingsPage } from './pages/SettingsPage'
import { Troubleshooting } from './pages/Troubleshooting'
import { Assembly64Page } from './pages/Assembly64Page'
import { CatalogPage } from './pages/CatalogPage'
import { SetupWizard } from './pages/SetupWizard'
import { StreamView } from './pages/StreamView'
import { AuthGate } from './components/SignIn'
import { EmulatorPage } from './pages/EmulatorPage'
import { EmulatorPicker } from './pages/EmulatorPicker'
import { GalleryPage } from './pages/GalleryPage'
import { api } from './services/api'

function FirstRunGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'loading' | 'setup' | 'ready'>('loading')
  const location = useLocation()
  useEffect(() => {
    api.health()
      .then((h) => setState(h.setupComplete || h.simulated && h.connected ? 'ready' : 'setup'))
      .catch(() => setState('ready'))
  }, [])
  if (state === 'loading') return null
  if (state === 'setup' && location.pathname !== '/setup') return <Navigate to="/setup" replace />
  return <>{children}</>
}

function InputErrors() {
  const toast = useToast()
  useEffect(() => setInputErrorHandler((m) => toast(m, 'error')), [toast])
  return null
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <AuthGate>
      <LiveProvider>
        <InputErrors />
        <BrowserRouter>
          <FirstRunGate>
            <Routes>
              <Route path="/setup" element={<SetupWizard />} />
              <Route path="/stream-view" element={<StreamView />} />
              <Route path="/tv" element={<TvMode />} />
              <Route path="/netplay/:code" element={<NetplayGuest />} />
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="library" element={<Library />} />
                <Route path="catalog" element={<CatalogPage />} />
                <Route path="emulate" element={<EmulatorPicker />} />
                <Route path="emulate/:id" element={<EmulatorPage />} />
                <Route path="gallery" element={<GalleryPage />} />
                <Route path="games/:id" element={<GameDetail />} />
                <Route path="playlists" element={<PlaylistsPage />} />
                <Route path="news" element={<NewsPage />} />
                <Route path="shop" element={<ShopPage />} />
                <Route path="repair" element={<RepairPage />} />
                <Route path="collection" element={<CollectionPage />} />
                <Route path="jukebox" element={<JukeboxPage />} />
                <Route path="magazines" element={<MagazinesPage />} />
                <Route path="events" element={<EventsPage />} />
                <Route path="bbs" element={<BbsPage />} />
                <Route path="bbs/:id/terminal" element={<Suspense fallback={null}><BbsTerminal /></Suspense>} />
                <Route path="compatibility" element={<CompatPage />} />
                <Route path="playlists/:id" element={<PlaylistsPage />} />
                <Route path="controller" element={<ControllerPage />} />
                <Route path="menu" element={<MenuPage />} />
                <Route path="stream" element={<StreamPage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="troubleshooting" element={<Troubleshooting />} />
                <Route path="assembly64" element={<Assembly64Page />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </FirstRunGate>
        </BrowserRouter>
      </LiveProvider>
      </AuthGate>
    </ToastProvider>
  </StrictMode>,
)

// Installable as an app (needs a secure address: https or localhost). The worker caches nothing.
if ('serviceWorker' in navigator && window.isSecureContext && location.port !== '5173') { // not on the Vite dev server
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}) })
}
