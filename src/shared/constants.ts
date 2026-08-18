/**
 * Mirrors the module-level constants at the top of SteamGamePatcher.py.
 */

export const APP_VERSION = '2.0.0-electron'

export const DB_URL =
  'https://raw.githubusercontent.com/d4rksp4rt4n/SteamGamePatcher/refs/heads/main/database/data/patches_database.json'

/** Repo the app itself is published from — used for both the update-check API call and
 *  the "View on GitHub" link in the About screen. Change this in one place if the repo
 *  ever moves. */
export const GITHUB_REPO = 'd4rksp4rt4n/SteamGamePatcher'
export const GITHUB_REPO_URL = `https://github.com/${GITHUB_REPO}`
export const GITHUB_RELEASES_URL = `${GITHUB_REPO_URL}/releases`
export const GITHUB_LATEST_RELEASE_API_URL = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`

export const NUKIGE_STEAM_GROUP_URL = 'https://steamcommunity.com/groups/Nukige'

/** Filename written into each game's install directory (unchanged from the Python version,
 *  so existing configs from the old app are read/written in place without migration). */
export const CONFIG_FILENAME = 'patcher_config.json'

/** Steam-inspired color palette (Python class `C`). Consumed as Tailwind CSS variables. */
export const STEAM_PALETTE = {
  bgDarkest: '#171a21',
  bgDark: '#1b2838',
  bgCard: '#2a475e',
  bgHover: '#3d6b8e',
  bgInput: '#1e2a3a',
  accent: '#66c0f4',
  accentDim: '#417a9b',
  text: '#c6d4df',
  textDim: '#8f98a0',
  textBright: '#ffffff',
  red: '#b52f2f',
  redHover: '#d44040',
  green: '#00ff88',
  greenDim: '#4CAF50',
  orange: '#e67e22',
  link: '#64B5F6',
  viewable: '#66bb6a',
  viewableHover: '#90CAF9',
  favGold: '#f5c542'
} as const

/** Shown in warning/confirmation copy wherever the app needs to describe where patch
 *  files come from. Edit this if the database source or its description changes. */
export const PATCH_SOURCE_LABEL = "the Nukige group's curated patch database"
