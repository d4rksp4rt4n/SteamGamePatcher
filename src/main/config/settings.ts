/**
 * Replaces Python's implicit global state (there was no settings UI at all in the
 * original — auto-apply always ran). This is new: a persisted settings store backing
 * the required "Enable Beta Auto-Install (experimental)" toggle, defaulted OFF for safety
 * per the brief. Also holds an optional cache directory override (Python always used a
 * fixed `data/cache` folder next to the script).
 */
import Store from 'electron-store'
import type { AppSettings } from '@shared/types'

const defaults: AppSettings = {
  betaAutoInstall: false,
  cacheDirOverride: null,
  autoInstallAfterDownload: false,
  viewMode: 'list'
}

let store: Store<AppSettings> | null = null

function getStore(): Store<AppSettings> {
  if (!store) {
    store = new Store<AppSettings>({ name: 'settings', defaults })
  }
  return store
}

export function getSettings(): AppSettings {
  const s = getStore()
  return {
    betaAutoInstall: s.get('betaAutoInstall'),
    cacheDirOverride: s.get('cacheDirOverride'),
    autoInstallAfterDownload: s.get('autoInstallAfterDownload'),
    viewMode: s.get('viewMode')
  }
}

export function updateSettings(patch: Partial<AppSettings>): AppSettings {
  const s = getStore()
  for (const [key, value] of Object.entries(patch)) {
    s.set(key as keyof AppSettings, value as AppSettings[keyof AppSettings])
  }
  return getSettings()
}
