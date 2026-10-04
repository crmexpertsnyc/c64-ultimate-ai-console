// Copies the EmulatorJS player and its VICE C64 core from node_modules into public/emulator so the
// console serves them itself (works offline, over Tailscale, and without any third-party CDN).
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const player = join(root, 'node_modules/@emulatorjs/emulatorjs/data')
const core = join(root, 'node_modules/@emulatorjs/core-vice_x64sc')
const out = join(root, 'public/emulator/data')

if (!existsSync(player) || !existsSync(core)) {
  console.warn('[copy-emulator] EmulatorJS is not installed; skipping (run npm install)')
  process.exit(0)
}
rmSync(out, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }) // Windows: files may be briefly locked
cpSync(player, out, { recursive: true })
mkdirSync(join(out, 'cores/reports'), { recursive: true })
// Only the single-threaded builds: threads need cross-origin isolation headers the console does not send.
for (const f of readdirSync(core)) {
  if (f.endsWith('-wasm.data') && !f.includes('-thread')) cpSync(join(core, f), join(out, 'cores', f))
}
cpSync(join(core, 'reports'), join(out, 'cores/reports'), { recursive: true })

// Fix for EmulatorJS 4.2.3: multi-disk games (.m3u) build the Disks menu before the settings store
// exists, so starting them fails with "Cannot set properties of undefined (setting 'disk')".
const emu = join(out, 'src/emulator.js')
const src = readFileSync(emu, 'utf8')
const anchor = 'setupDisksMenu() {'
if (src.includes(anchor)) {
  writeFileSync(emu, src.replace(anchor, anchor + ' this.allSettings ||= {}; this.settings ||= {}; // c64-console fix'))
} else console.warn('[copy-emulator] disk-menu fix not applied: EmulatorJS changed, check multi-disk games')
console.log('[copy-emulator] EmulatorJS + VICE core copied to public/emulator/data')
