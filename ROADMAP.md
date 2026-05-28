# Roadmap — decky-youtube-music-player

## Phase 1: Authentication Overhaul (OAuth Device Code Flow)

**Goal:** Replace the current browser-cookie-auth (8 steps, DevTools, file transfer) with OAuth Device Code Flow (2 steps, any device).

### Tasks

- [x] Research OAuth alternatives
- [ ] Create Google Cloud project + OAuth client ID ("TVs and Limited Input devices")
- [ ] Bundle client_id in plugin config (defaults in `main.py`, overridable via `oauth_config.json`)
- [x] `main.py`: Add `start_oauth()` + `check_oauth()` — device code flow via Google API (raw HTTP)
- [x] `main.py`: Persist `oauth.json`, re-init YTMusic on load (tries OAuth first, browser fallback)
- [x] `main.py`: Modify `_try_init_ytmusic()` to try OAuth first, browser fallback
- [x] `main.py`: Modify `sign_out()` to clean OAuth data
- [x] `main.py`: Add `_load_oauth_config()` + `_init_ytmusic_oauth()` helpers
- [x] `main.py`: Add `save_oauth_config()` — frontend-adjustable client_id
- [x] `SettingsPage.tsx`: New OAuth UI — "Sign in with Google" → show code → polling → done
- [x] `SettingsPage.tsx`: Keep browser auth as collapsible "Advanced" fallback
- [x] `types.ts`: Add `AuthState`, `OAuthStartResult`, `OAuthCheckResult` interfaces
- [ ] `PlayerContext.tsx`: React to auth state changes in real-time
- [ ] Build + package + verify (requires Linux/Steam Deck environment)

**Success criteria:**
- User clicks "Sign in", sees a code, enters it on google.com/device on their phone, done
- No file transfers, no DevTools, no Cloud Console for end users
- Fallback browser auth still works for advanced users

---

## Phase 2: Performance Optimization

**Goal:** Eliminate the yt-dlp subprocess bottleneck and redundant system calls.

### Tasks

- [x] `main.py`: Add streaming URL cache (`_url_cache` dict with TTL, 5min expiry)
- [x] `main.py`: Add `_prefetch_urls()` + `prefetch_next()` — pre-fetch next 3 track URLs
- [x] `main.py`: Modify `_get_streaming_url()` to check cache first
- [x] `main.py`: Remove PulseAudio `pactl` calls from `set_volume()` — `<audio>` element only
- [x] `main.py`: Simplify `set_volume()` to only persist value (no subprocess)
- [x] `main.py`: Debounce `_save_settings()` writes (throttle to 500ms)
- [x] `audioManager.ts`: Verified — volume fully handled by `<audio>` element, no changes needed

**Success criteria:**
- Track changes are near-instant (cached URL instead of 3-10s yt-dlp subprocess) ✓
- Volume changes don't spawn subprocesses ✓
- No redundant PulseAudio/audio element conflict ✓

---

## Phase 3: Bug Fixes & Reliability

**Goal:** Fix state sync, error handling, and edge cases.

### Tasks

- [x] `PlayerContext.tsx`: On mount, fetch current track from backend (fresh streaming URL)
- [x] `audioManager.ts`: Better yt-dlp error recovery with exponential backoff (1s, 2s, 4s, max 3 retries)
- [x] `PlayerView.tsx`: Show user-visible error when streaming URL fails
- [x] `main.py`: `get_song_rating()` — query ytmusicapi directly when not in queue cache
- [x] `main.py`: `remove_from_queue()` — preserve shuffle order instead of regenerating
- [x] `main.py`: `get_auth_state()` — now returns `hasCredentials` (was done in Phase 1)
- [x] `index.tsx`: Show "Checking authentication..." loading state until initial check completes

**Success criteria:**
- Panel open/close doesn't show stale track info (backend track fetched on mount) ✓
- yt-dlp failures show a message and auto-retry (exponential backoff + error UI) ✓
- Like/Dislike reflects actual YTM state (live query fallback) ✓
- Removing from queue doesn't reshuffle (shuffle order preserved) ✓

---

## Phase 4: UX Enhancements

**Goal:** Polish the experience with features users expect from a music player.

### Tasks

- [x] `PlayerView.tsx`: Add playback progress bar (seekable via SliderField + seekTo)
- [x] `PlayerView.tsx`: Display current time / total duration
- [x] `main.py`: Add queue persistence — save/restore queue.json across plugin reloads
- [x] `audioManager.ts`: Support Media Session API (media keys on Deck)
- [ ] `PlayerView.tsx`: Add QR code display during OAuth setup (requires QR lib)
- [ ] `LibraryView.tsx`: Add search within library/playlist
- [x] `QueueView.tsx`: Show total queue duration + track count

**Success criteria:**
- User can see and seek track progress ✓
- Queue survives plugin reload ✓
- Media keys on Steam Deck control playback ✓

---

## Appendix: Google Cloud Project Setup (one-time, for maintainer)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g. "decky-ytmusic")
3. Go to **APIs & Services → Library**
4. Search for and enable **YouTube Data API v3**
5. Go to **APIs & Services → Credentials**
6. Click **Create Credentials → OAuth client ID**
7. Application type: **TVs and Limited Input devices**
8. Name: "Decky YouTube Music Player"
9. Copy the **Client ID** (and Client Secret if provided)
10. Set these as defaults in `main.py` (`OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`)
