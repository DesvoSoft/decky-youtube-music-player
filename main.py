import decky
import json
import os
import random
import threading
import time as _time

_PY_MODULES = os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules")

try:
    import mpv as _mpv
    _MPV_AVAILABLE = True
except ImportError:
    _MPV_AVAILABLE = False
    decky.logger.warning("python-mpv not installed — mpv audio engine unavailable")
BROWSER_AUTH_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "browser.json")
OAUTH_AUTH_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "oauth.json")
OAUTH_CONFIG_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "oauth_config.json")
SETTINGS_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
QUEUE_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "queue.json")

# Default OAuth client credentials - override by setting OAUTH_CONFIG_FILE
OAUTH_CLIENT_ID = ""
OAUTH_CLIENT_SECRET = ""

class Plugin:
    authenticated = False
    ytmusic = None
    _oauth_creds = None

    _pending_device_code = None

    # Streaming URL cache
    _url_cache = {}              # video_id -> {"url": str, "cached_at": float}
    _url_cache_ttl = 300         # 5 minutes
    _last_cache_cleanup = 0.0

    # Settings debounce
    _last_save_time = 0.0
    _save_debounce_ms = 500

    # Queue / playback state
    queue = []
    queue_position = 0
    is_playing = False
    shuffle = False
    shuffle_order = []
    repeat = "NONE"         # NONE | ALL | ONE
    volume = 1.0

    # mpv audio engine
    player = None

    # ── Authentication (OAuth + browser fallback) ──────────────────

    def _load_oauth_config(self):
        global OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
        if os.path.exists(OAUTH_CONFIG_FILE):
            try:
                with open(OAUTH_CONFIG_FILE, "r") as f:
                    cfg = json.load(f)
                if cfg.get("client_id"):
                    OAUTH_CLIENT_ID = cfg["client_id"]
                if cfg.get("client_secret"):
                    OAUTH_CLIENT_SECRET = cfg["client_secret"]
            except Exception as e:
                decky.logger.error(f"Failed to load OAuth config: {e}")

    def _init_ytmusic_oauth(self):
        """Initialize ytmusicapi with OAuth credentials."""
        if not os.path.exists(OAUTH_AUTH_FILE):
            return False
        try:
            from ytmusicapi import YTMusic
            from ytmusicapi.auth.oauth import OAuthCredentials
            creds = OAuthCredentials(
                client_id=OAUTH_CLIENT_ID,
                client_secret=OAUTH_CLIENT_SECRET,
            )
            self._oauth_creds = creds
            self.ytmusic = YTMusic(OAUTH_AUTH_FILE, oauth_credentials=creds)
            self.authenticated = True
            decky.logger.info("ytmusicapi initialized with OAuth")
            return True
        except ImportError:
            decky.logger.warning("OAuth not available in this ytmusicapi version")
            return False
        except Exception as e:
            decky.logger.error(f"Failed to init ytmusicapi with OAuth: {e}")
            return False

    def _try_init_ytmusic(self):
        """Try to initialize ytmusicapi with saved credentials.
        Tries OAuth first, then falls back to browser cookies."""
        self._load_oauth_config()

        # Try OAuth first
        if os.path.exists(OAUTH_AUTH_FILE):
            if self._init_ytmusic_oauth():
                return

        # Fall back to browser cookies
        if os.path.exists(BROWSER_AUTH_FILE):
            try:
                from ytmusicapi import YTMusic
                self.ytmusic = YTMusic(BROWSER_AUTH_FILE)
                self.authenticated = True
                decky.logger.info("ytmusicapi initialized with browser auth")
                return
            except Exception as e:
                decky.logger.error(f"Failed to init ytmusicapi with browser auth: {e}")
                self.authenticated = False
                self.ytmusic = None

    def _load_settings(self):
        """Load persisted settings (volume, etc.) from disk."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                self.volume = data.get("volume", 1.0)
            except Exception as e:
                decky.logger.error(f"Failed to load settings: {e}")

    def _save_settings(self):
        """Save persisted settings to disk with debounce."""
        now = _time.time()
        if now - self._last_save_time < self._save_debounce_ms / 1000:
            return
        self._last_save_time = now
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"volume": self.volume}, f)

    def _save_queue(self):
        """Persist current queue and position to disk."""
        try:
            os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
            with open(QUEUE_FILE, "w") as f:
                json.dump({
                    "queue": self.queue,
                    "queue_position": self.queue_position,
                    "shuffle": self.shuffle,
                    "shuffle_order": self.shuffle_order,
                    "repeat": self.repeat,
                }, f)
        except Exception as e:
            decky.logger.error(f"Failed to save queue: {e}")

    def _load_queue(self):
        """Restore queue from disk."""
        if not os.path.exists(QUEUE_FILE):
            return
        try:
            with open(QUEUE_FILE, "r") as f:
                data = json.load(f)
            self.queue = data.get("queue", [])
            self.queue_position = data.get("queue_position", 0)
            self.shuffle = data.get("shuffle", False)
            self.shuffle_order = data.get("shuffle_order", [])
            self.repeat = data.get("repeat", "NONE")
            decky.logger.info(f"Restored queue with {len(self.queue)} tracks")
        except Exception as e:
            decky.logger.error(f"Failed to load queue: {e}")

    # ── mpv audio engine ───────────────────────────────────────────

    def _init_mpv(self):
        if not _MPV_AVAILABLE:
            decky.logger.warning("Cannot initialize mpv: python-mpv not installed")
            return
        try:
            self.player = _mpv.MPV(
                video=False,
                ytdl=False,
                ao="pipewire",
                pipewire_buffer=50,
                volume=self.volume * 100,
            )
            self.player.register_event_callback(self._on_mpv_event)
            decky.logger.info("mpv initialized with PipeWire backend")
        except Exception as e:
            decky.logger.error(f"Failed to initialize mpv: {e}")
            self.player = None

    def _on_mpv_event(self, event):
        if not _MPV_AVAILABLE:
            return
        if event.event_id == _mpv.MpvEventID.END_FILE:
            reason = event.event_data.get("reason")
            if reason == "eof":
                decky.logger.info("Track ended naturally — advancing queue")
                threading.Thread(target=self._handle_track_end, daemon=True).start()
            elif reason == "error":
                decky.logger.error(f"mpv playback error: {event.event_data}")
                threading.Thread(target=self._handle_playback_error, daemon=True).start()

    def _handle_track_end(self):
        try:
            result = self._advance_queue(1)
            if result is None or result.get("stopped"):
                self.is_playing = False
        except Exception as e:
            decky.logger.error(f"Error handling track end: {e}")

    def _handle_playback_error(self):
        decky.logger.error("mpv playback error — skipping to next track")
        try:
            result = self._advance_queue(1)
            if result is None or result.get("stopped"):
                self.is_playing = False
        except Exception as e:
            decky.logger.error(f"Error recovering from playback error: {e}")

    async def _main(self):
        decky.logger.info("YouTube Music plugin loaded")
        self._load_settings()
        self._load_queue()
        self._try_init_ytmusic()
        self._init_mpv()

    async def _unload(self):
        decky.logger.info("YouTube Music plugin unloaded")
        if self.player:
            try:
                self.player.stop()
                self.player.terminate()
            except Exception:
                pass
            self.player = None

    async def get_auth_state(self):
        """Return current auth status."""
        has_oauth = os.path.exists(OAUTH_AUTH_FILE)
        has_browser = os.path.exists(BROWSER_AUTH_FILE)
        return {
            "authenticated": self.authenticated,
            "hasCredentials": has_oauth or has_browser,
        }

    async def get_oauth_config(self):
        """Return current OAuth client config (client_id only, never secret)."""
        return {
            "client_id": OAUTH_CLIENT_ID,
            "has_client_id": bool(OAUTH_CLIENT_ID),
        }

    async def save_oauth_config(self, client_id: str, client_secret: str = ""):
        """Save OAuth client credentials."""
        try:
            os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
            with open(OAUTH_CONFIG_FILE, "w") as f:
                json.dump({"client_id": client_id, "client_secret": client_secret}, f)
            global OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
            OAUTH_CLIENT_ID = client_id
            OAUTH_CLIENT_SECRET = client_secret
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    async def start_oauth(self):
        """Start the OAuth device code flow.
        Returns verification URL + user code for the user to visit."""
        if not OAUTH_CLIENT_ID:
            return {"error": "No OAuth client ID configured. Set it in Settings first."}

        try:
            import requests
            resp = requests.post(
                "https://oauth2.googleapis.com/device/code",
                data={
                    "client_id": OAUTH_CLIENT_ID,
                    "scope": "https://www.googleapis.com/auth/youtube",
                },
                timeout=10,
            )
            data = resp.json()

            if "error" in data:
                return {"error": data.get("error_description", data["error"])}

            self._pending_device_code = data["device_code"]

            return {
                "user_code": data["user_code"],
                "verification_url": data.get("verification_url", "https://google.com/device"),
                "interval": data.get("interval", 5),
                "expires_in": data.get("expires_in", 1800),
            }
        except Exception as e:
            decky.logger.error(f"Failed to start OAuth: {e}")
            return {"error": str(e)}

    async def check_oauth(self, device_code: str):
        """Poll Google's token endpoint for OAuth authorization.
        Call this every `interval` seconds until status is 'success' or 'error'."""
        if not device_code:
            return {"error": "No device code provided"}

        try:
            import requests
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": OAUTH_CLIENT_ID,
                    "client_secret": OAUTH_CLIENT_SECRET,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=10,
            )
            data = resp.json()

            if "access_token" in data:
                token = {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", ""),
                    "expires_in": data.get("expires_in", 3600),
                    "scope": data.get("scope", ""),
                    "token_type": data.get("token_type", "Bearer"),
                    "expires_at": int(_time.time()) + data.get("expires_in", 3600),
                }
                os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
                with open(OAUTH_AUTH_FILE, "w") as f:
                    json.dump(token, f)

                if self._init_ytmusic_oauth():
                    return {"status": "success"}
                else:
                    return {"status": "error", "error": "Failed to initialize with OAuth token"}

            error = data.get("error", "")
            if error == "authorization_pending":
                return {"status": "pending"}
            elif error == "slow_down":
                return {"status": "pending", "slow_down": True}
            elif error == "expired_token":
                return {"status": "error", "error": "Code expired. Please try again."}
            elif error == "access_denied":
                return {"status": "error", "error": "Access denied by user."}
            else:
                desc = data.get("error_description", error)
                return {"status": "error", "error": desc}
        except Exception as e:
            decky.logger.error(f"OAuth poll failed: {e}")
            return {"status": "error", "error": str(e)}

    async def load_headers_from_file(self, file_path: str):
        """Read browser request headers from a text file on the Deck.
        Uses ytmusicapi.setup() to parse raw headers into browser.json."""
        try:
            if not os.path.exists(file_path):
                return {"error": f"File not found: {file_path}"}

            with open(file_path, "r") as f:
                headers_raw = f.read()

            if not headers_raw.strip():
                return {"error": "File is empty"}

            from ytmusicapi import setup
            os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
            setup(filepath=BROWSER_AUTH_FILE, headers_raw=headers_raw)
            decky.logger.info(f"Browser headers loaded from {file_path}")

            # Re-initialize ytmusicapi
            self._try_init_ytmusic()

            if self.authenticated:
                return {"success": True}
            else:
                return {"error": "Headers saved but initialization failed. Check that the headers are correct."}
        except Exception as e:
            decky.logger.error(f"Failed to load headers from file: {e}")
            return {"error": str(e)}

    async def sign_out(self):
        """Sign out — delete all auth files and reset state."""
        self.authenticated = False
        self.ytmusic = None
        self._oauth_creds = None
        self._pending_device_code = None
        self.queue = []
        self.queue_position = 0
        self.is_playing = False
        self.shuffle = False
        self.shuffle_order = []
        self.repeat = "NONE"
        self._cached_playlists = None
        for f in [BROWSER_AUTH_FILE, OAUTH_AUTH_FILE]:
            if os.path.exists(f):
                os.remove(f)
        decky.logger.info("Signed out")
        return {"success": True}

    # ── Streaming URL (cached) ─────────────────────────────────────

    def _clean_url_cache(self):
        """Remove expired entries from the URL cache."""
        now = _time.time()
        if now - self._last_cache_cleanup < 60:
            return
        self._last_cache_cleanup = now
        expired = [k for k, v in self._url_cache.items()
                   if now - v["cached_at"] > self._url_cache_ttl]
        for k in expired:
            del self._url_cache[k]

    def _get_streaming_url(self, video_id, skip_cache=False):
        """Fetch the best audio streaming URL using yt-dlp.
        Results are cached for _url_cache_ttl seconds."""
        self._clean_url_cache()

        # Check cache first
        if not skip_cache and video_id in self._url_cache:
            cached = self._url_cache[video_id]
            if _time.time() - cached["cached_at"] < self._url_cache_ttl:
                decky.logger.debug(f"Cache hit for {video_id}")
                return cached["url"]

        import subprocess
        try:
            env = os.environ.copy()
            env['PYTHONPATH'] = _PY_MODULES + ':' + env.get('PYTHONPATH', '')
            env.pop('LD_LIBRARY_PATH', None)

            result = subprocess.run(
                [
                    'python3', '-m', 'yt_dlp',
                    '--print', 'urls',
                    '-f', 'bestaudio[ext=m4a]/bestaudio',
                    '--no-warnings',
                    '-q',
                    f'https://music.youtube.com/watch?v={video_id}',
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            url = result.stdout.strip()
            if result.returncode != 0 or not url or not url.startswith('http'):
                decky.logger.warning(f"yt-dlp failed for {video_id}. rc={result.returncode} stderr: {result.stderr[-500:]}")
                return None

            # Cache the result
            self._url_cache[video_id] = {
                "url": url,
                "cached_at": _time.time(),
            }
            decky.logger.info(f"Got streaming URL for {video_id}")
            return url
        except subprocess.TimeoutExpired:
            decky.logger.error(f"yt-dlp timed out for {video_id}")
            return None
        except Exception as e:
            decky.logger.error(f"Failed to get streaming URL for {video_id}: {e}")
            return None

    def _prefetch_urls(self, from_index=0, count=3):
        """Pre-fetch streaming URLs for upcoming tracks in the background."""
        if not self.queue:
            return
        for i in range(from_index, min(from_index + count, len(self.queue))):
            tid = self.queue[i].get("videoId", "")
            if tid and tid not in self._url_cache:
                decky.logger.debug(f"Pre-fetching URL for {tid}")
                self._get_streaming_url(tid)

    def _play_url_sync(self, url: str):
        """Synchronous mpv playback start. Called internally after URL resolution."""
        if not self.player or not url:
            return False
        try:
            self.player.play(url)
            self.player.pause = False
            self.is_playing = True
            return True
        except Exception as e:
            decky.logger.error(f"mpv play failed: {e}")
            return False

    def _current_track_with_url(self):
        """Return current track metadata + fresh streaming URL.
        Feeds the resolved URL to mpv for playback."""
        if not self.queue or self.queue_position >= len(self.queue):
            return None

        track = self.queue[self.queue_position]
        url = self._get_streaming_url(track["videoId"])
        self._play_url_sync(url)

        return {
            "videoId": track["videoId"],
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "album": track.get("album", ""),
            "albumArt": track.get("albumArt", ""),
            "duration": track.get("duration", 0),
            "url": url,
            "queuePosition": self.queue_position,
            "queueLength": len(self.queue),
        }

    # ── Playback controls ──────────────────────────────────────────

    async def get_current_track(self):
        """Return current track with fresh streaming URL."""
        result = self._current_track_with_url()
        if result is None:
            return {"error": "No track in queue"}
        if result["url"] is None:
            return {"error": "Failed to get streaming URL"}
        return result

    async def play_url(self, url: str):
        """Feed a resolved streaming URL to mpv for playback."""
        if not self.player:
            return {"error": "mpv not initialized"}
        ok = self._play_url_sync(url)
        return {"success": ok} if ok else {"error": "mpv play failed"}

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

    async def seek(self, position: float):
        """Seek to a specific position in seconds."""
        if self.player:
            self.player.seek(position, reference="absolute")
        return {"success": True}

    async def get_playback_position(self):
        """Return current playback position and duration for the frontend progress bar."""
        if not self.player:
            return {"position": 0, "duration": 0}
        try:
            return {
                "position": self.player.time_pos or 0,
                "duration": self.player.duration or 0,
            }
        except Exception as e:
            decky.logger.debug(f"Failed to get playback position: {e}")
            return {"position": 0, "duration": 0}

    def _advance_queue(self, direction=1):
        if not self.queue:
            return None

        if self.repeat == "ONE":
            return self._current_track_with_url()

        if self.shuffle and self.shuffle_order:
            try:
                shuffle_idx = self.shuffle_order.index(self.queue_position)
            except ValueError:
                shuffle_idx = 0
            shuffle_idx += direction

            if shuffle_idx >= len(self.shuffle_order):
                if self.repeat == "ALL":
                    shuffle_idx = 0
                else:
                    self.is_playing = False
                    return None
            elif shuffle_idx < 0:
                if self.repeat == "ALL":
                    shuffle_idx = len(self.shuffle_order) - 1
                else:
                    shuffle_idx = 0

            self.queue_position = self.shuffle_order[shuffle_idx]
        else:
            self.queue_position += direction

            if self.queue_position >= len(self.queue):
                if self.repeat == "ALL":
                    self.queue_position = 0
                else:
                    self.queue_position = len(self.queue) - 1
                    self.is_playing = False
                    return None
            elif self.queue_position < 0:
                if self.repeat == "ALL":
                    self.queue_position = len(self.queue) - 1
                else:
                    self.queue_position = 0

        self.is_playing = True
        return self._current_track_with_url()

    async def next_track(self):
        result = self._advance_queue(1)
        if result is None:
            return {"stopped": True}
        if result.get("url") is None:
            return {"error": "Failed to get streaming URL"}
        self._prefetch_urls(self.queue_position + 1)
        return result

    async def previous_track(self):
        result = self._advance_queue(-1)
        if result is None:
            return {"stopped": True}
        if result.get("url") is None:
            return {"error": "Failed to get streaming URL"}
        self._prefetch_urls(self.queue_position + 1)
        return result

    async def get_playback_state(self):
        track = None
        if self.queue and self.queue_position < len(self.queue):
            track = self.queue[self.queue_position]
        return {
            "is_playing": self.is_playing,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "volume": self.volume,
            "queue_position": self.queue_position,
            "queue_length": len(self.queue),
            "current_track": track,
        }

    # ── Volume ─────────────────────────────────────────────────────

    async def set_volume(self, value):
        """Set volume. value is 0-100 from frontend. Also syncs to mpv."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return {"error": f"Invalid volume value: {value!r}"}
        self.volume = max(0, min(100, value)) / 100.0
        if self.player:
            self.player.volume = value
        self._save_settings()
        return {"volume": value}

    async def get_volume(self):
        """Return current volume (0-100 for frontend)."""
        return {"volume": self.volume * 100}

    # ── Like / Dislike ──────────────────────────────────────────────

    async def rate_song(self, video_id, rating):
        if not self.ytmusic:
            return {"error": "Not authenticated"}
        try:
            self.ytmusic.rate_song(video_id, rating)
            # Update the cached likeStatus in the queue
            for t in self.queue:
                if t.get("videoId") == video_id:
                    t["likeStatus"] = rating
            return {"rating": rating}
        except Exception as e:
            decky.logger.error(f"Failed to rate song {video_id}: {e}")
            error_msg = str(e)
            if "Sign in" in error_msg or "sign in" in error_msg:
                return {"error": "Session expired. Please re-authenticate in Settings."}
            return {"error": error_msg}

    async def get_song_rating(self, video_id):
        # Check queue cache first
        for t in self.queue:
            if t.get("videoId") == video_id:
                cached = t.get("likeStatus", "INDIFFERENT")
                if cached != "INDIFFERENT":
                    return {"rating": cached}

        # Fall back to live query
        if self.ytmusic:
            try:
                rating = self.ytmusic.get_rating(video_id)
                return {"rating": rating.get("rating", "INDIFFERENT")}
            except Exception as e:
                decky.logger.warning(f"Failed to fetch live rating for {video_id}: {e}")

        return {"rating": "INDIFFERENT"}

    # ── Shuffle / Repeat ───────────────────────────────────────────

    async def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        if self.shuffle and self.queue:
            self.shuffle_order = list(range(len(self.queue)))
            random.shuffle(self.shuffle_order)
            if self.queue_position in self.shuffle_order:
                self.shuffle_order.remove(self.queue_position)
                self.shuffle_order.insert(0, self.queue_position)
        else:
            self.shuffle_order = []
        return {"shuffle": self.shuffle}

    async def toggle_repeat(self):
        cycle = {"NONE": "ALL", "ALL": "ONE", "ONE": "NONE"}
        self.repeat = cycle.get(self.repeat, "NONE")
        return {"repeat": self.repeat}

    # ── Queue management ─────────────────────────────────────────────

    async def get_queue(self):
        return {
            "tracks": self.queue,
            "position": self.queue_position,
        }

    async def remove_from_queue(self, index):
        if index < 0 or index >= len(self.queue):
            return {"error": "Invalid index"}

        self.queue.pop(index)

        if index < self.queue_position:
            self.queue_position -= 1
        elif index == self.queue_position:
            if self.queue_position >= len(self.queue):
                self.queue_position = max(0, len(self.queue) - 1)

        if self.shuffle and self.shuffle_order:
            try:
                self.shuffle_order.remove(index)
            except ValueError:
                pass
            self.shuffle_order = [i - 1 if i > index else i for i in self.shuffle_order]

        self._save_queue()
        return {"success": True, "queue_length": len(self.queue)}

    async def jump_to_queue(self, index):
        if index < 0 or index >= len(self.queue):
            return {"error": "Invalid index"}

        self.queue_position = index
        self._save_queue()
        result = self._current_track_with_url()
        if result is None or result.get("url") is None:
            return {"error": "Failed to get streaming URL"}
        return result

    # ── Library ─────────────────────────────────────────────────────

    _cached_playlists = None

    async def get_library_playlists(self, refresh=False):
        if not self.ytmusic:
            return {"error": "Not authenticated"}
        if self._cached_playlists and not refresh:
            return {"playlists": self._cached_playlists}
        try:
            playlists = self.ytmusic.get_library_playlists(limit=None)
            result = []
            # Liked Songs first
            result.append({
                "playlistId": "LM",
                "title": "Liked Songs",
                "count": None,
                "thumbnail": None,
            })
            for p in playlists:
                pid = p.get("playlistId", "")
                if pid == "LM":
                    continue
                thumbnails = p.get("thumbnails", [])
                thumb = thumbnails[0]["url"] if thumbnails else None
                result.append({
                    "playlistId": pid,
                    "title": p.get("title", "Unknown Playlist"),
                    "count": p.get("count"),
                    "thumbnail": thumb,
                })
            self._cached_playlists = result
            return {"playlists": result}
        except Exception as e:
            decky.logger.error(f"Failed to get library playlists: {e}")
            error_msg = str(e)
            if "Sign in" in error_msg or "sign in" in error_msg or "twoColumnBrowseResultsRenderer" in error_msg:
                return {"error": "Session expired. Please re-authenticate with fresh browser headers in Settings."}
            return {"error": error_msg}

    # ── Search ─────────────────────────────────────────────────────

    async def search_songs(self, query):
        if not self.ytmusic:
            return {"error": "Not authenticated"}
        try:
            results = self.ytmusic.search(query, filter="songs", limit=20)
            songs = []
            for r in results:
                thumbnails = r.get("thumbnails", [])
                album_art = thumbnails[-1]["url"] if thumbnails else ""
                artists = r.get("artists", [])
                artist_name = ", ".join(a.get("name", "") for a in artists) if artists else ""
                songs.append({
                    "videoId": r.get("videoId", ""),
                    "title": r.get("title", "Unknown"),
                    "artist": artist_name,
                    "albumArt": album_art,
                    "duration": r.get("duration", ""),
                })
            return {"results": [s for s in songs if s["videoId"]]}
        except Exception as e:
            decky.logger.error(f"Search failed: {e}")
            return {"error": str(e)}

    async def play_song_radio(self, video_id):
        if not self.ytmusic:
            return {"error": "Not authenticated"}
        try:
            self.is_playing = False

            watch = self.ytmusic.get_watch_playlist(videoId=video_id, radio=True)
            tracks = watch.get("tracks", [])
            if not tracks:
                return {"error": "No radio tracks found"}

            self.queue = []
            for t in tracks:
                thumbnails = t.get("thumbnail", [])
                album_art = thumbnails[-1]["url"] if thumbnails else ""
                artists = t.get("artists", [])
                artist_name = ", ".join(a.get("name", "") for a in artists) if artists else ""
                album = t.get("album")
                album_name = album.get("name", "") if album else ""

                duration_str = t.get("length", "0:00")
                duration_seconds = 0
                parts = duration_str.split(":")
                try:
                    if len(parts) == 2:
                        duration_seconds = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except ValueError:
                    pass

                self.queue.append({
                    "videoId": t.get("videoId", ""),
                    "title": t.get("title", "Unknown"),
                    "artist": artist_name,
                    "album": album_name,
                    "albumArt": album_art,
                    "duration": duration_seconds,
                    "likeStatus": t.get("likeStatus", "INDIFFERENT"),
                })

            self.queue = [t for t in self.queue if t["videoId"]]
            if not self.queue:
                return {"error": "No playable tracks in radio"}

            # Put the selected song first if it's not already
            for i, t in enumerate(self.queue):
                if t["videoId"] == video_id:
                    if i != 0:
                        self.queue.insert(0, self.queue.pop(i))
                    break

            self.queue_position = 0
            if self.shuffle:
                self.shuffle_order = list(range(len(self.queue)))
                random.shuffle(self.shuffle_order)
                self.shuffle_order.remove(0)
                self.shuffle_order.insert(0, 0)
            else:
                self.shuffle_order = []

            result = self._current_track_with_url()
            if result is None or result.get("url") is None:
                return {"error": "Failed to get streaming URL"}

            self.is_playing = True
            self._save_queue()
            return result
        except Exception as e:
            decky.logger.error(f"Failed to start song radio for {video_id}: {e}")
            return {"error": str(e)}

    # ── Playlist loading ─────────────────────────────────────────────

    async def load_playlist(self, playlist_id):
        if not self.ytmusic:
            return {"error": "Not authenticated"}

        try:
            self.is_playing = False  # stop old playback state before rebuilding queue

            if playlist_id == "LM":
                playlist_data = self.ytmusic.get_liked_songs(limit=50)
            else:
                playlist_data = self.ytmusic.get_playlist(playlist_id, limit=50)

            tracks = playlist_data.get("tracks", [])
            if not tracks:
                return {"error": "Playlist is empty. If this is Liked Songs, try re-authenticating with fresh browser headers."}

            self.queue = []
            for t in tracks:
                thumbnails = t.get("thumbnails", [])
                album_art = thumbnails[-1]["url"] if thumbnails else ""

                artists = t.get("artists", [])
                artist_name = ", ".join(a.get("name", "") for a in artists) if artists else ""

                album = t.get("album")
                album_name = album.get("name", "") if album else ""

                duration_seconds = t.get("duration_seconds", 0)
                if not duration_seconds:
                    duration_str = t.get("duration", "0:00")
                    parts = duration_str.split(":")
                    try:
                        if len(parts) == 2:
                            duration_seconds = int(parts[0]) * 60 + int(parts[1])
                        elif len(parts) == 3:
                            duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    except ValueError:
                        duration_seconds = 0

                self.queue.append({
                    "videoId": t.get("videoId", ""),
                    "title": t.get("title", "Unknown"),
                    "artist": artist_name,
                    "album": album_name,
                    "albumArt": album_art,
                    "duration": duration_seconds,
                    "likeStatus": t.get("likeStatus", "INDIFFERENT"),
                })

            self.queue = [t for t in self.queue if t["videoId"]]

            if not self.queue:
                return {"error": "No playable tracks in playlist"}

            self.queue_position = 0

            if self.shuffle:
                self.shuffle_order = list(range(len(self.queue)))
                random.shuffle(self.shuffle_order)
                self.shuffle_order.remove(0)
                self.shuffle_order.insert(0, 0)
            else:
                self.shuffle_order = []

            result = self._current_track_with_url()
            if result is None or result.get("url") is None:
                return {"error": "Failed to get streaming URL for first track"}

            # Pre-fetch upcoming tracks in background
            self._prefetch_urls(1)

            self.is_playing = True
            self._save_queue()
            return result
        except Exception as e:
            decky.logger.error(f"Failed to load playlist {playlist_id}: {e}")
            error_msg = str(e)
            if "Sign in" in error_msg or "sign in" in error_msg or "twoColumnBrowseResultsRenderer" in error_msg:
                return {"error": "Session expired. Please re-authenticate with fresh browser headers in Settings."}
            return {"error": error_msg}
