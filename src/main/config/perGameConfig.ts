/**
 * Port of `App._load_configs()`, `App._migrate_old_config()`, and
 * `App.save_per_game_config()` from SteamGamePatcher.py.
 *
 * Per-game state lives in `<install_dir>/patcher_config.json` (unchanged filename/shape
 * from the Python version, so an existing install of the old app and this one share state
 * with zero migration needed for that file). The one thing that *does* need migrating is
 * the old app's single global `data/last_applied.json` — if it's found, we fold its entries
 * into the appropriate per-game config files and delete it, exactly once.
 */
import { existsSync } from 'node:fs'
import { readFile, writeFile, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import log from 'electron-log'
import type { InstalledGamesMap, LastAppliedMap, LastPatchRecord, PerGameConfig } from '@shared/types'
import { CONFIG_FILENAME, getDataDir } from './paths'

async function readConfig(path: string): Promise<PerGameConfig> {
  if (!existsSync(path)) return {}
  try {
    return JSON.parse(await readFile(path, 'utf-8')) as PerGameConfig
  } catch (err) {
    log.warn(`[config/perGame] Failed to parse ${path}: ${err}`)
    return {}
  }
}

async function writeConfig(path: string, cfg: PerGameConfig): Promise<void> {
  await writeFile(path, JSON.stringify(cfg, null, 4), 'utf-8')
}

/**
 * Reads every installed game's patcher_config.json and builds the last-applied-patch map.
 * `gameNameByAppid` should come from the built match list (`by_id`) so the map key matches
 * what the UI displays — falls back to the raw appid if the game isn't in the DB.
 */
export async function loadConfigs(
  installed: InstalledGamesMap,
  gameNameByAppid: Record<string, string>
): Promise<LastAppliedMap> {
  const lastApplied: LastAppliedMap = {}

  for (const [appid, game] of Object.entries(installed)) {
    const cfgPath = join(game.installDir, CONFIG_FILENAME)
    const cfg = await readConfig(cfgPath)
    const lp = cfg.last_patch
    if (lp) {
      const gameName = gameNameByAppid[appid] ?? appid
      ;(lastApplied[appid] ??= {})[gameName] = lp
    }
  }

  return lastApplied
}

/**
 * One-time migration of the old app's global `data/last_applied.json` into per-game
 * config files. No-op if that file doesn't exist. Deletes it on success, same as Python.
 */
export async function migrateOldConfig(installed: InstalledGamesMap): Promise<void> {
  const oldPath = join(getDataDir(), 'last_applied.json')
  if (!existsSync(oldPath)) return

  try {
    const oldData = JSON.parse(await readFile(oldPath, 'utf-8')) as Record<
      string,
      Record<string, LastPatchRecord>
    >

    for (const [appid, games] of Object.entries(oldData)) {
      for (const [, patchRecord] of Object.entries(games)) {
        const game = installed[appid]
        if (!game) continue
        const cfgPath = join(game.installDir, CONFIG_FILENAME)
        const cfg = await readConfig(cfgPath)
        cfg.last_patch = patchRecord
        await writeConfig(cfgPath, cfg)
      }
    }

    await unlink(oldPath)
    log.info('[config/perGame] Migrated old global config to per-game configs')
  } catch (err) {
    log.warn(`[config/perGame] Migration failed: ${err}`)
  }
}

/**
 * Writes the record of a just-applied patch into the game's patcher_config.json.
 * Returns the updated LastAppliedMap entry so callers can update in-memory state
 * without re-reading every config file (mirrors Python mutating `self.last_applied` in place).
 */
export async function savePerGameConfig(
  installed: InstalledGamesMap,
  appid: string,
  gameName: string,
  fileName: string,
  date: string,
  changes: LastPatchRecord['changes']
): Promise<LastPatchRecord | null> {
  const game = installed[appid]
  if (!game) return null

  const cfgPath = join(game.installDir, CONFIG_FILENAME)
  try {
    const cfg = await readConfig(cfgPath)
    const lastPatch: LastPatchRecord = { file: fileName, date, changes }
    cfg.last_patch = lastPatch
    await writeConfig(cfgPath, cfg)
    log.info(`[config/perGame] Saved config: ${fileName}`)
    return lastPatch
  } catch (err) {
    log.error(`[config/perGame] Config save failed: ${err}`)
    return null
  }
}
