import { call } from '@decky/api';

const AUDIO_ID = 'ytm-audio-player';

let audioElement: HTMLAudioElement | null = null;
let trackChangeListeners: Array<(track: TrackInfo | null) => void> = [];
let playStateListeners: Array<(playing: boolean) => void> = [];

// Named handler references for proper addEventListener/removeEventListener pairing
function onAudioEnded() { void handleTrackEnded(); }
function onAudioError() { void handleError(); }
function onAudioPause() {
  // Detect system-initiated pause (e.g. sleep/wake) and sync state
  if (isPlaying) {
    isPlaying = false;
    notifyPlayState(false);
    void call('pause');
  }
}

export interface TrackInfo {
  videoId: string;
  title: string;
  artist: string;
  album: string;
  albumArt: string;
  duration: number;
  url: string;
  queuePosition: number;
  queueLength: number;
}

// Current track state — module-scoped, survives panel close/open
let currentTrack: TrackInfo | null = null;
let isPlaying = false;

export function getCurrentTrack(): TrackInfo | null {
  return currentTrack;
}

export function getIsPlaying(): boolean {
  return isPlaying;
}

function notifyTrackChange(track: TrackInfo | null) {
  currentTrack = track;
  trackChangeListeners.forEach((fn) => fn(track));
}

function notifyPlayState(playing: boolean) {
  isPlaying = playing;
  playStateListeners.forEach((fn) => fn(playing));
}

export function addTrackChangeListener(fn: (track: TrackInfo | null) => void): () => void {
  trackChangeListeners.push(fn);
  return () => { trackChangeListeners = trackChangeListeners.filter((l) => l !== fn); };
}

export function addPlayStateListener(fn: (playing: boolean) => void): () => void {
  playStateListeners.push(fn);
  return () => { playStateListeners = playStateListeners.filter((l) => l !== fn); };
}

async function handleTrackEnded() {
  try {
    const result = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('track_ended');
    if (result.stopped) {
      notifyPlayState(false);
      notifyTrackChange(null);
      return;
    }
    if (result.error || !result.url) {
      notifyPlayState(false);
      return;
    }
    await loadAndPlay(result);
  } catch (e) {
    console.error('[YTM] track_ended error:', e);
    notifyPlayState(false);
  }
}

let errorRetryCount = 0;
const MAX_ERROR_RETRIES = 3;

async function handleError() {
  if (errorRetryCount >= MAX_ERROR_RETRIES) {
    console.error('[YTM] Max error retries reached');
    errorRetryCount = 0;
    notifyPlayState(false);
    return;
  }

  const delay = Math.pow(2, errorRetryCount) * 1000; // 1s, 2s, 4s
  errorRetryCount++;

  await new Promise((r) => setTimeout(r, delay));

  try {
    const result = await call<[], TrackInfo & { error?: string }>('get_current_track');
    if (result.error || !result.url) {
      const next = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('next_track');
      if (next.stopped || next.error) {
        errorRetryCount = 0;
        notifyPlayState(false);
        return;
      }
      errorRetryCount = 0;
      await loadAndPlay(next);
      return;
    }
    errorRetryCount = 0;
    await loadAndPlay(result);
  } catch (e) {
    console.error('[YTM] error recovery failed:', e);
    notifyPlayState(false);
  }
}

// Reset retry counter on successful loads
export function resetErrorRetries() {
  errorRetryCount = 0;
}

function updateMediaSession(track: TrackInfo) {
  if (!('mediaSession' in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.title,
    artist: track.artist,
    album: track.album,
    artwork: track.albumArt ? [{ src: track.albumArt, sizes: '512x512', type: 'image/jpeg' }] : [],
  });
  navigator.mediaSession.setActionHandler('play', () => void resumePlayback());
  navigator.mediaSession.setActionHandler('pause', () => pausePlayback());
  navigator.mediaSession.setActionHandler('previoustrack', () => void playPrevious());
  navigator.mediaSession.setActionHandler('nexttrack', () => void playNext());
}

async function loadAndPlay(track: TrackInfo) {
  if (!audioElement || !track.url) return;
  currentTrack = track;
  audioElement.src = track.url;
  updateMediaSession(track);
  try {
    await audioElement.play();
    isPlaying = true;
    notifyTrackChange(track);
    notifyPlayState(true);
    void call('resume');
  } catch (e) {
    console.error('[YTM] play failed:', e);
    notifyPlayState(false);
  }
}

/** Initialize the persistent <audio> element. Call once in definePlugin(). */
export function initAudio() {
  if (document.getElementById(AUDIO_ID)) {
    audioElement = document.getElementById(AUDIO_ID) as HTMLAudioElement;
    return;
  }

  audioElement = document.createElement('audio');
  audioElement.id = AUDIO_ID;
  audioElement.style.display = 'none';
  audioElement.addEventListener('ended', onAudioEnded);
  audioElement.addEventListener('error', onAudioError);
  audioElement.addEventListener('pause', onAudioPause);
  document.body.appendChild(audioElement);
}

/** Cleanup on plugin unload. */
export function destroyAudio() {
  if (audioElement) {
    audioElement.pause();
    audioElement.src = '';
    audioElement.removeEventListener('ended', onAudioEnded);
    audioElement.removeEventListener('error', onAudioError);
    audioElement.removeEventListener('pause', onAudioPause);
    audioElement.remove();
    audioElement = null;
  }
  currentTrack = null;
  isPlaying = false;
  trackChangeListeners = [];
  playStateListeners = [];
}

/** Play a track by loading its URL into the <audio> element. */
export async function playTrack(track: TrackInfo) {
  errorRetryCount = 0;
  await loadAndPlay(track);
}

/** Pause playback. */
export function pausePlayback() {
  // Set isPlaying false BEFORE pause() so onAudioPause listener doesn't double-notify
  isPlaying = false;
  notifyPlayState(false);
  void call('pause');
  audioElement?.pause();
}

/** Resume playback. */
export function resumePlayback() {
  if (audioElement && audioElement.src) {
    void audioElement.play().then(() => {
      isPlaying = true;
      notifyPlayState(true);
      void call('resume');
    }).catch((e) => {
      console.error('[YTM] resume failed:', e);
      notifyPlayState(false);
    });
  }
}

/** Toggle play/pause. */
export function togglePlayback() {
  if (isPlaying) {
    pausePlayback();
  } else {
    resumePlayback();
  }
}

/** Play next track via backend. */
export async function playNext() {
  const result = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('next_track');
  if (result.stopped) {
    notifyPlayState(false);
    notifyTrackChange(null);
    return;
  }
  if (result.error || !result.url) return;
  await loadAndPlay(result);
}

/** Play previous track via backend. */
export async function playPrevious() {
  const result = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('previous_track');
  if (result.stopped) return;
  if (result.error || !result.url) return;
  await loadAndPlay(result);
}

/** Set volume on the <audio> element. */
export function setAudioVolume(value: number) {
  if (audioElement) {
    audioElement.volume = Math.max(0, Math.min(1, value));
  }
}

/** Get the <audio> element for direct access if needed. */
export function getAudioElement(): HTMLAudioElement | null {
  return audioElement;
}

/** Seek the <audio> element to a specific time (seconds). */
export function seekTo(time: number) {
  if (audioElement) {
    audioElement.currentTime = time;
  }
}
