/**
 * Port of `get_steam_path()` from SteamGamePatcher.py.
 *
 * Python used `winreg` to read HKLM\SOFTWARE\WOW6432Node\Valve\Steam, then fell back to
 * common install paths, then OS-specific candidates for Linux/macOS. We reproduce the same
 * search order here so behavior is identical to the original app.
 */
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import log from 'electron-log'

/**
 * Reads a Windows registry string value.
 * Uses `registry-js`, a native-free (WinAPI via N-API) reader — no shelling out to reg.exe.
 * Wrapped defensively: registry-js throws on non-Windows platforms, and the key may not exist.
 */
async function readSteamInstallPathFromRegistry(): Promise<string | null> {
  if (process.platform !== 'win32') return null
  try {
    // Lazy-imported so this module can be unit-tested on non-Windows CI without a native dep failure.
    const { enumerateValues, HKEY, RegistryValueType } = await import('registry-js')
    const values = enumerateValues(HKEY.HKEY_LOCAL_MACHINE, 'SOFTWARE\\WOW6432Node\\Valve\\Steam')
    const installPath = values.find(
      (v) => v.name === 'InstallPath' && v.type === RegistryValueType.REG_SZ
    )
    if (installPath && typeof installPath.data === 'string' && installPath.data.length > 0) {
      return installPath.data
    }
  } catch (err) {
    // registry-js is an optional dependency — if it's not installed (npm skips optional
    // deps that fail their own install step), this is expected and the folder-based
    // fallback below covers it. Log a one-liner instead of the full stack for that
    // specific case so it doesn't look like a real failure on every launch.
    const isModuleNotFound =
      err instanceof Error && 'code' in err && err.code === 'ERR_MODULE_NOT_FOUND'
    if (isModuleNotFound) {
      log.debug('[steam/discovery] registry-js not installed (optional) — using folder fallback')
    } else {
      log.debug('[steam/discovery] registry lookup failed (non-fatal):', err)
    }
  }
  return null
}

function windowsFallbackCandidates(): string[] {
  const candidates: string[] = []
  const pf86 = process.env['ProgramFiles(x86)']
  const pf = process.env['ProgramFiles']
  if (pf86) candidates.push(join(pf86, 'Steam'))
  if (pf) candidates.push(join(pf, 'Steam'))
  candidates.push('C:/Program Files (x86)/Steam')
  return candidates
}

function linuxCandidates(): string[] {
  const home = homedir()
  return [
    join(home, '.steam', 'steam'),
    join(home, '.local', 'share', 'Steam'),
    join(home, '.steam', 'debian-installation')
  ]
}

function macCandidates(): string[] {
  return [join(homedir(), 'Library', 'Application Support', 'Steam')]
}

/**
 * Locate the local Steam installation directory.
 * Returns null if Steam cannot be found (equivalent to Python returning None + a warning log).
 */
export async function getSteamPath(): Promise<string | null> {
  log.info('[steam/discovery] Searching for Steam installation...')

  if (process.platform === 'win32') {
    const fromRegistry = await readSteamInstallPathFromRegistry()
    if (fromRegistry && existsSync(fromRegistry)) {
      log.info(`[steam/discovery] Steam found (registry): ${fromRegistry}`)
      return fromRegistry
    }
    for (const candidate of windowsFallbackCandidates()) {
      if (existsSync(candidate)) {
        log.info(`[steam/discovery] Steam found (fallback): ${candidate}`)
        return candidate
      }
    }
  } else if (process.platform === 'linux') {
    for (const candidate of linuxCandidates()) {
      if (existsSync(candidate)) {
        log.info(`[steam/discovery] Steam found (Linux): ${candidate}`)
        return candidate
      }
    }
  } else if (process.platform === 'darwin') {
    for (const candidate of macCandidates()) {
      if (existsSync(candidate)) {
        log.info(`[steam/discovery] Steam found (macOS): ${candidate}`)
        return candidate
      }
    }
  }

  log.warn('[steam/discovery] Steam installation not found')
  return null
}
