# Migration Plan: HTML5 `<audio>` → python-mpv + PipeWire

## Why Migrate?

| Factor | `<audio>` in CEF | `python-mpv` + PipeWire |
|--------|-----------------|------------------------|
| Audio routing | Chromium → PulseAudio → PipeWire | Native → PipeWire |
| CPU overhead | Full CEF render pipeline (GPU compositing, JS event loop) | Single native thread (libmpv) |
| RAM overhead | ~50-80MB for CEF process | ~5-10MB for libmpv |
| Latency | 200-500ms (CEF audio pipeline) | 20-50ms (direct PipeWire) |
| Buffer control | None (browser-defined) | Configurable (`pipewire_buffer=50`) |
| Track-end detection | `ended` event on `<audio>` | `end_file` callback from libmpv |
| Seeking | `audioElement.currentTime = x` | `player.seek(x)` | 
| Volume control | `audioElement.volume = x` | `player.volume = x` |
| SteamOS integration | Works-through (extra layer) | Native (PipeWire first-class) |
| Stability | Drops audio when CEF process throttles | Stable under load (dedicated thread) |

**Bottom line**: mpv is lighter, more reliable, and more native on SteamOS. The current `<audio>`-in-CEF approach adds an unnecessary browser rendering layer for pure audio playback.

---

## Research Summary

### 1. python-mpv availability on SteamOS
- **libmpv.so** ships with SteamOS as part of the `mpv` package (`/usr/lib/libmpv.so`)
- `python-mpv` (PyPI) is a single-file ctypes wrapper — no compilation needed
- Can be installed via `pip install python-mpv --target py_modules/` or copy `mpv.py` directly
- The mpv Python package on PyPI is just the binding; `libmpv.so` must be present on the system (it is)

### 2. PipeWire configuration for Steam Deck
- SteamOS 3.x uses PipeWire as default audio server
- Optimal mpv config: `ao="pipewire"`, `pipewire_buffer=50` (50ms buffer)
- Known issue: mpv + PipeWire 1.4.3 had stutter at non-44100 rates (fixed in newer versions)
- Direct PipeWire routing avoids PulseAudio compatibility layer

### 3. yt-dlp integration
- Current yt-dlp subprocess approach (`bestaudio[ext=m4a]/bestaudio`) works correctly
- mpv can either:
  - **Option A**: Receive direct audio URLs (like current flow) — keeps existing cache/prefetch
  - **Option B**: Let mpv handle yt-dlp internally (set `ytdl=True`) — loses cache control
- **Decision: Option A** — we pass resolved URLs to mpv, keeping our optimization layer

### 4. AudioLoader coexistence
- `window.AUDIOLOADER_MENUMUSIC` is a global injected by AudioLoader plugin
- Must call `.pause()` on it before starting playback
- Guard with `typeof window.AUDIOLOADER_MENUMUSIC?.pause === 'function'`

---

## Migration Phases

### Phase A: Backend — Integrate python-mpv

**Goal**: Add mpv playback engine to `main.py` alongside existing queue/auth/library/search code.

#### A1. Add mpv import + init in `_main()`

```python
import mpv

class Plugin:
    player = None

    async def _main(self):
        self._load_settings()
        self._load_queue()
        self._try_init_ytmusic()
        self._init_mpv()  # NEW

    def _init_mpv(self):
        """Initialize mpv player with PipeWire backend."""
        try:
            import mpv as mpv_module
            self.player = mpv_module.MPV(
                video=False,
                ytdl=False,       # We resolve URLs ourselves
                ao="pipewire",
                pipewire_buffer=50,
                volume=self.volume * 100,
            )
            # Callbacks
            self.player.register_event_callback(self._on_mpv_event)
            decky.logger.info("mpv initialized with PipeWire")
        except Exception as e:
            decky.logger.error(f"Failed to initialize mpv: {e}")
            self.player = None
```

#### A2. Add `_on_mpv_event` callback

```python
def _on_mpv_event(self, event):
    """Handle mpv events: track end, errors."""
    if event.event_id == mpv.MpvEventID.END_FILE:
        reason = event.event_data.get("reason")
        if reason == "eof":
            decky.logger.info("Track ended naturally")
            # Auto-advance to next track
            import threading
            threading.Thread(target=self._handle_track_end, daemon=True).start()
        elif reason == "error":
            decky.logger.error(f"mpv playback error: {event.event_data}")
            import threading
            threading.Thread(target=self._handle_playback_error, daemon=True).start()
```

#### A3. Add playback control methods

Replace the current `resume()`/`pause()` which only toggle `is_playing`:

```python
async def resume(self):
    self.is_playing = True
    if self.player:
        self.player.pause = False
    return {"success": True}

async def pause(self):
    self.is_playing = False
    if self.player:
        self.player.pause = True
    return {"success": True}

async def stop(self):
    """Stop playback entirely."""
    self.is_playing = False
    if self.player:
        self.player.stop()
    return {"success": True}
```

#### A4. Add `play_url()` — the core playback method

```python
async def play_url(self, url: str):
    """Play a direct audio URL through mpv."""
    if not self.player:
        return {"error": "mpv not initialized"}
    try:
        self.player.play(url)
        self.player.pause = False
        self.is_playing = True
        return {"success": True}
    except Exception as e:
        decky.logger.error(f"mpv play_url failed: {e}")
        return {"error": str(e)}
```

#### A5. Modify `get_current_track()` to call `play_url()`

```python
async def get_current_track(self):
    result = self._current_track_with_url()
    if result is None:
        return {"error": "No track in queue"}
    if result["url"] is None:
        return {"error": "Failed to get streaming URL"}
    # Feed URL to mpv
    await self.play_url(result["url"])
    return result
```

#### A6. Add seek support

```python
async def seek(self, position: float):
    """Seek to position in seconds."""
    if self.player:
        self.player.seek(position, reference="absolute")
    return {"success": True}

async def get_playback_position(self):
    """Return current playback position for frontend progress bar."""
    if not self.player:
        return {"position": 0, "duration": 0}
    try:
        pos = self.player.time_pos or 0
        dur = self.player.duration or 0
        return {"position": pos, "duration": dur}
    except:
        return {"position": 0, "duration": 0}
```

#### A7. Update `set_volume()` to control mpv

```python
async def set_volume(self, value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return {"error": f"Invalid volume: {value!r}"}
    self.volume = max(0, min(100, value)) / 100.0
    if self.player:
        self.player.volume = value  # mpv uses 0-100
    self._save_settings()
    return {"volume": value}
```

#### A8. Update `_unload()` to clean up mpv

```python
async def _unload(self):
    decky.logger.info("YouTube Music plugin unloaded")
    if self.player:
        try:
            self.player.terminate()
        except:
            pass
        self.player = None
```

#### A9. Handle `track_ended()` via mpv callback

The `_handle_track_end()` method (called from `_on_mpv_event`) should:

```python
def _handle_track_end(self):
    """Called when mpv finishes a track. Auto-advance queue."""
    import asyncio
    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(self._advance_and_play(), loop)
    else:
        # Fallback: create new loop
        asyncio.run(self._advance_and_play())

async def _advance_and_play(self):
    """Advance queue and play next track via mpv."""
    result = self._advance_queue(1)
    if result is None or result.get("stopped"):
        self.is_playing = False
        return
    if result.get("url"):
        await self.play_url(result["url"])
```

---

### Phase B: Frontend — Simplify `audioManager.ts`

**Goal**: Remove all `<audio>` element management. `audioManager.ts` becomes a thin `call()` wrapper.

#### B1. Remove `initAudio()` and `destroyAudio()`

These functions create/remove the `<audio>` element. After migration, there is no `<audio>` element.

#### B2. Remove `getAudioElement()` and `seekTo()`

The frontend no longer directly touches the audio element. Seeking goes through `call('seek', time)`.

#### B3. Simplify `playTrack()`

```typescript
export async function playTrack(track: TrackInfo) {
  resetErrorRetries();
  // Backend already receives URL via get_current_track
  // Just notify listeners
  notifyTrackChange(track);
  notifyPlayState(true);
}
```

#### B4. Simplify controls

```typescript
export function pausePlayback() {
  isPlaying = false;
  notifyPlayState(false);
  void call('pause');
}

export function resumePlayback() {
  void call('resume').then(() => {
    isPlaying = true;
    notifyPlayState(true);
  }).catch((e) => {
    console.error('[YTM] resume failed:', e);
    notifyPlayState(false);
  });
}

export function togglePlayback() {
  if (isPlaying) {
    pausePlayback();
  } else {
    resumePlayback();
  }
}
```

#### B5. Remove `handleTrackEnded()` and `handleError()`

Track-end detection is now handled by mpv's `end_file` callback in the backend.

#### B6. Remove `playNext()` and `playPrevious()` local logic

These should just call the backend. The backend handles queue advancement AND feed to mpv:

```typescript
export async function playNext() {
  const result = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('next_track');
  if (result.stopped) {
    notifyPlayState(false);
    notifyTrackChange(null);
    return;
  }
  if (result.error) return;
  notifyTrackChange(result);
  notifyPlayState(true);
}
```

#### B7. Remove `setAudioVolume()` and `getAudioElement()`

Volume is now handled by the backend through mpv.

#### B8. Updated `audioManager.ts` final size: ~50 lines (was 265)

New file structure:
- Module-scoped state (`currentTrack`, `isPlaying`)
- Listener management (`addTrackChangeListener`, `addPlayStateListener`)
- Thin wrappers: `pausePlayback()`, `resumePlayback()`, `togglePlayback()`, `playNext()`, `playPrevious()`, `playTrack()`
- No `<audio>` element creation or management
- No `initAudio()` / `destroyAudio()`
- No `getAudioElement()` / `seekTo()` / `setAudioVolume()`

---

### Phase C: Frontend — Update `PlayerView.tsx`

**Goal**: Replace direct `audioElement.currentTime` reads with backend polling.

#### C1. Remove direct audio element access

Current code reads `getAudioElement()` directly. Replace with polling via `call('get_playback_position')`.

```typescript
// Before:
const audio = getAudioElement();
audio.addEventListener('timeupdate', onTime);

// After:
useEffect(() => {
  if (!isPlaying || !track) return;
  const interval = setInterval(async () => {
    try {
      const pos = await call<[], { position: number; duration: number }>('get_playback_position');
      if (!seeking) setCurrentTime(pos.position);
      if (pos.duration) setDuration(pos.duration);
    } catch {}
  }, 1000); // Update every second
  return () => clearInterval(interval);
}, [track?.videoId, isPlaying, seeking]);
```

#### C2. Update `handleSeek` to call backend

```typescript
const handleSeek = useCallback(async (val: number) => {
  setCurrentTime(val);
  await call('seek', val);
}, []);
```

#### C3. Remove `import { getAudioElement, seekTo } from ...`

These no longer exist in the simplified `audioManager.ts`.

---

### Phase D: Frontend — Update `index.tsx`

**Goal**: Remove `initAudio()` / `destroyAudio()` calls, add AudioLoader coexistence.

#### D1. Remove `initAudio()` call from `definePlugin`

```typescript
// Before:
export default definePlugin(() => {
  initAudio();
  ...
  return {
    ...
    onDismount() {
      destroyAudio();
      ...
    },
  };
});

// After:
export default definePlugin(() => {
  // AudioLoader coexistence: pause any running AudioLoader playback
  if (typeof window !== 'undefined' && (window as any).AUDIOLOADER_MENUMUSIC?.pause) {
    (window as any).AUDIOLOADER_MENUMUSIC.pause();
  }
  ...
  return {
    ...
    onDismount() {
      // No audio cleanup needed — mpv lives in backend
      ...
    },
  };
});
```

#### D2. Remove import

```typescript
// Remove:
import { initAudio, destroyAudio } from './services/audioManager';
```

---

### Phase E: Cleanup — Update Documentation

**Goal**: Remove stale references to PulseAudio and CEF audio architecture.

#### E1. `VolumeSlider.tsx:66`
```typescript
// Before:
    // Debounce the backend + PulseAudio call
// After:
    // Debounce the backend volume call
```

#### E2. `README.md` — Update architecture description
- Replace "HTML5 `<audio>` element for playback" with "python-mpv + PipeWire for native audio"
- Remove PulseAudio references

#### E3. `CLAUDE.md` — Update architecture section
- Replace `<audio>` references with mpv
- Update file descriptions

#### E4. `YT-Music_Decky.md` — Already recommends python-mpv, no changes needed

---

### Phase F: Remove Deferred Code (Optional)

**Goal**: Remove the `<audio>` element CSS/hacks if they exist.

- `index.tsx` CSS for `#ytm-container` — keep (it's for layout, not audio)
- No other `<audio>`-specific CSS exists

---

## Rollout Plan

### Step 1: Phase A (Backend) — Safe, no frontend changes
- Add mpv init, control methods, event callbacks
- Old `<audio>` still works in parallel
- Both systems coexist — `is_playing` state shared
- Test: `call('play_url', url)` via Decky dev console

### Step 2: Phase B + C + D (Frontend) — Switchover
- Remove `<audio>` element
- Update PlayerView to poll backend for position
- Remove initAudio/destroyAudio
- AudioLoader coexistence
- **Moment of truth**: After deploy, audio comes from mpv, not CEF

### Step 3: Phase E (Cleanup)
- Fix stale comments, update docs

### Step 4: Rollback if needed
- Revert `main.py` to previous version (before Phase A changes)
- Revert frontend files to pre-Phase-B state
- Old `<audio>` code path still exists in git history

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `libmpv.so` not available on all SteamOS versions | Low | High | Fallback to `<audio>` element; detect mpv availability at init |
| PipeWire buffer underrun during gaming | Medium | Medium | Default `pipewire_buffer=50`; make configurable in settings |
| mpv callback threading issues (Python GIL) | Medium | Medium | Use `asyncio.run_coroutine_threadsafe()` for cross-thread calls |
| Decky sandbox blocks `mpv.py` ctypes calls | Low | High | Test on Deck first; alternative: use `mpv` CLI as subprocess |
| AudioLoader conflict not resolved by `.pause()` | Low | Medium | Acceptable — user has one music player active at a time |

---

## Success Criteria

- [ ] Plugin plays audio on Steam Deck without `<audio>` element
- [ ] PipeWire is used as audio backend (verify via `pactl info`)
- [ ] Track transitions (next/prev/end) work identically to before
- [ ] Seeking works with progress bar in UI
- [ ] Volume control works identically
- [ ] Shuffle/repeat modes work identically
- [ ] AudioLoader music pauses when YTM starts playing
- [ ] CPU/RAM usage is lower than before (verify via `htop`)
- [ ] Backend queue/auth/library/search all unchanged
- [ ] Panel open/close doesn't affect playback

---

## Files Changed Summary

| File | Change |
|------|--------|
| `main.py` | Add mpv init, `_on_mpv_event`, `play_url`, `seek`, `get_playback_position`, `stop`; update `resume`, `pause`, `set_volume`, `_unload`, `get_current_track` |
| `src/services/audioManager.ts` | Remove `<audio>` management; simplify to ~50 lines of thin `call()` wrappers |
| `src/components/PlayerView.tsx` | Replace `audioElement` reads with `get_playback_position` polling; update seek to use `call('seek')` |
| `src/components/VolumeSlider.tsx` | Fix stale PulseAudio comment (line 66) |
| `src/index.tsx` | Remove `initAudio()`/`destroyAudio()`; add AudioLoader pause |
| `CLAUDE.md` | Update architecture docs |
| `README.md` | Update architecture docs |

---

## Verification Checklist (Post-Migration)

```bash
# On Steam Deck:
# 1. Check mpv library is loaded
lsof -p $(pgrep -f python3) | grep libmpv

# 2. Check PipeWire is audio backend
pactl info | grep "Server Name"

# 3. Check CPU usage of python process (should be <5% when idle)
htop

# 4. Check audio buffer settings
pactl list sinks | grep -A5 "Name:"
```
