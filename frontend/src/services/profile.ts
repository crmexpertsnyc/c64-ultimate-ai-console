/** 👪 Who is playing on this device (family profiles). Sent with every request as X-C64-Profile. */
const KEY = 'c64.profile'

export function currentProfile(): string {
  try { return localStorage.getItem(KEY) || '1' } catch { return '1' }
}

export function setCurrentProfile(id: number): void {
  try { localStorage.setItem(KEY, String(id)) } catch { /* storage unavailable: stays on profile 1 */ }
}

/** Whether this device has picked a profile yet (TV mode asks "Who's playing?" when it hasn't). */
export function profileChosen(): boolean {
  try { return !!localStorage.getItem(KEY) } catch { return false }
}
