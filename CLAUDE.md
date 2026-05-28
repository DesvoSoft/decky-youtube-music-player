# decky-youtube-music

A standalone Decky Loader plugin for the Steam Deck that plays YouTube Music directly — no external app required.

## Architecture

- **Frontend**: React (TypeScript) UI in the Quick Access Menu panel — thin display layer only
- **Backend**: Python (`main.py`) owns all state — queue, playback, auth, library, ratings
- **Communication**: Decky's `call()` bridge (frontend → backend), pull-based state sync on panel open
- **Streaming URLs**: yt-dlp subprocess (handles YouTube signature deciphering)
- **Metadata/Library**: ytmusicapi (browser cookie auth)
- **Audio Engine**: python-mpv + PipeWire (native SteamOS audio, no CEF overhead)

## Project Structure

```
src/
  index.tsx              - Plugin entry point; defines plugin, TabsContainer (L1/R1 tabs), settings route
  components/
    PlayerView.tsx       - Player tab: album art, track info, playback controls, like/dislike, volume, shuffle/repeat
    QueueView.tsx        - Queue tab: queue list with jump-to and remove buttons
    LibraryView.tsx      - Library tab: Liked Songs + user playlists, click to load
    SettingsPage.tsx     - Full-screen settings page (browser auth setup, sign out)
    Section.tsx          - Simple section wrapper with optional title label
    VolumeSlider.tsx     - Volume slider; volume synced to mpv backend
  context/
    PlayerContext.tsx    - React context; syncs with audioManager and backend state
  services/
    audioManager.ts      - Thin call() wrapper; state + listeners, no DOM element
  types.ts               - Shared TypeScript types (TrackInfo, PlayerState, RepeatMode)
dist/
  index.js               - Compiled frontend bundle (built by rollup)
main.py                  - Python backend: auth, queue, playback, volume, library, ratings
py_modules/              - Bundled Python dependencies (ytmusicapi, yt-dlp, requests, etc.)
plugin.json              - Decky plugin manifest
package.json             - npm manifest; version shown in Decky UI
```

## Tech Stack

- **Frontend**: React (TypeScript), `@decky/ui`, `@decky/api`, `react-icons`
- **Build**: Rollup via `@decky/rollup`
- **Backend**: Python (`main.py`) with `ytmusicapi` and `yt-dlp`
- **Audio**: python-mpv + PipeWire (native SteamOS audio, no CEF overhead)

## Build & Package

### Build only
```bash
npm run build
```

### Install Python dependencies
```bash
pip install ytmusicapi yt-dlp --target py_modules/
```

### Build + package as installable zip
```bash
npm run build
rm -rf /tmp/ym
mkdir -p /tmp/ym/youtube-music/dist
cp dist/index.js /tmp/ym/youtube-music/dist/
cp main.py package.json plugin.json /tmp/ym/youtube-music/
cp -r py_modules /tmp/ym/youtube-music/
cd /tmp/ym && powershell.exe -Command "Compress-Archive -Force -Path 'youtube-music' -DestinationPath 'youtube-music.zip'"
cp /tmp/ym/youtube-music.zip ./youtube-music.zip
```

Output: `youtube-music.zip` in the project root.

## IMPORTANT: After Every Code Change

After making any code change, always:
1. Run `npm run build`
2. Recreate `youtube-music.zip` using the packaging steps above

The zip must have this exact internal structure for Decky Loader's "Install from ZIP" to work:
```
youtube-music/
  main.py
  package.json
  plugin.json
  dist/
    index.js
  py_modules/
    ytmusicapi/
    yt_dlp/
    requests/
    ...
```

## Key Implementation Notes

- **Auth**: OAuth device code flow (primary) with browser cookie auth as fallback. User sets up OAuth client ID in settings, or copies request headers from browser DevTools and transfers to Deck.
- **Streaming URLs**: yt-dlp runs as a subprocess (not imported as library) because Decky's sandboxed Python is missing stdlib modules like `xml.etree`. `LD_LIBRARY_PATH` is stripped to avoid Decky's bundled OpenSSL conflicting with system Python's ssl module.
- **Audio persistence**: python-mpv manages audio in the backend process, so it survives Quick Access panel close/open. The mpv `end_file` callback handles track transitions.
- **Tabs height**: `TabsContainer` measures available panel height at runtime by walking up the DOM to the nearest scrollable ancestor.
- **Fallback UI**: If `Tabs` is not available (older Decky versions), a simple `ButtonItem`-based tab switcher is rendered instead.
- **Playlist cache**: Library playlists are cached in Python after first fetch for instant tab switching.
