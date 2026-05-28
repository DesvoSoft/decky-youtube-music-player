import { call } from '@decky/api';

let trackChangeListeners: Array<(track: TrackInfo | null) => void> = [];
let playStateListeners: Array<(playing: boolean) => void> = [];

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

export async function playTrack(track: TrackInfo) {
  updateMediaSession(track);
  notifyTrackChange(track);
  notifyPlayState(true);
}

export function pausePlayback() {
  isPlaying = false;
  notifyPlayState(false);
  void call('pause');
}

export async function resumePlayback() {
  try {
    await call('resume');
    isPlaying = true;
    notifyPlayState(true);
  } catch (e) {
    console.error('[YTM] resume failed:', e);
    notifyPlayState(false);
  }
}

export function togglePlayback() {
  if (isPlaying) {
    pausePlayback();
  } else {
    void resumePlayback();
  }
}

export async function playNext() {
  const result = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('next_track');
  if (result.stopped) {
    notifyPlayState(false);
    notifyTrackChange(null);
    return;
  }
  if (result.error || !result.url) return;
  updateMediaSession(result);
  notifyTrackChange(result);
  notifyPlayState(true);
}

export async function playPrevious() {
  const result = await call<[], TrackInfo & { stopped?: boolean; error?: string }>('previous_track');
  if (result.stopped) return;
  if (result.error || !result.url) return;
  updateMediaSession(result);
  notifyTrackChange(result);
  notifyPlayState(true);
}
