export type RepeatMode = 'NONE' | 'ALL' | 'ONE';

export interface TrackInfo {
  videoId: string;
  title: string;
  artist: string;
  album: string;
  albumArt: string;
  duration: number;
  url?: string;
  queuePosition?: number;
  queueLength?: number;
}

export interface AuthState {
  authenticated: boolean;
  hasCredentials: boolean;
}

export interface OAuthStartResult {
  user_code?: string;
  verification_url?: string;
  interval?: number;
  expires_in?: number;
  error?: string;
}

export interface OAuthCheckResult {
  status: 'success' | 'pending' | 'error';
  slow_down?: boolean;
  error?: string;
}

export interface PlayerState {
  track: TrackInfo | null;
  isPlaying: boolean;
  volume: number;
  repeat: RepeatMode;
  shuffle: boolean;
  authenticated: boolean;
  hasCredentials: boolean;
}
