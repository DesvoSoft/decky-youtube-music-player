import { ButtonItem, TextField, DialogButton, Focusable, SidebarNavigation, PanelSection } from '@decky/ui';
import { call } from '@decky/api';
import { useEffect, useState, useRef } from 'react';
import type { AuthState, OAuthStartResult, OAuthCheckResult } from '../types';
import { usePlayer } from '../context/PlayerContext';

const AuthContent = () => {
  const [authState, setAuthState] = useState<AuthState | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const { refreshAuth } = usePlayer();

  // OAuth state
  const [oauthStarted, setOauthStarted] = useState(false);
  const [oauthUserCode, setOauthUserCode] = useState('');
  const [oauthUrl, setOauthUrl] = useState('');
  const [oauthPending, setOauthPending] = useState(false);
  const [oauthSlowDown, setOauthSlowDown] = useState(false);
  const oauthDeviceCode = useRef('');
  const oauthInterval = useRef(5);

  // OAuth client ID config
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [hasClientId, setHasClientId] = useState(false);
  const [showClientIdForm, setShowClientIdForm] = useState(false);
  const [showCloudGuide, setShowCloudGuide] = useState(false);
  const [oauthConfigSaved, setOauthConfigSaved] = useState(false);

  // Browser auth state
  const [showBrowserAuth, setShowBrowserAuth] = useState(false);
  const [filePath, setFilePath] = useState('/home/deck/headers.txt');

  const getState = async () => {
    const state = await call<[], AuthState>('get_auth_state');
    setAuthState(state);
    void refreshAuth();
  };

  const getOauthConfig = async () => {
    try {
      const cfg = await call<[], { client_id: string; has_client_id: boolean }>('get_oauth_config');
      setHasClientId(cfg.has_client_id);
      if (cfg.client_id) setClientId(cfg.client_id);
    } catch {}
  };

  useEffect(() => {
    void getState();
    void getOauthConfig();
  }, []);

  // ── OAuth Client ID config ──

  const handleSaveOauthConfig = async () => {
    if (!clientId.trim()) return;
    setError('');
    setSaving(true);
    setOauthConfigSaved(false);
    try {
      const result = await call<[string, string], { success?: boolean; error?: string }>(
        'save_oauth_config', clientId.trim(), clientSecret.trim()
      );
      if (result.error) {
        setError(result.error);
      } else {
        setHasClientId(true);
        setOauthConfigSaved(true);
        setShowClientIdForm(false);
      }
    } catch (e) {
      setError(String(e));
    }
    setSaving(false);
  };

  // ── OAuth ──

  const handleStartOAuth = async () => {
    setError('');
    setSaving(true);
    try {
      const result = await call<[], OAuthStartResult>('start_oauth');
      if (result.error) {
        setError(result.error);
        return;
      }
      setOauthStarted(true);
      setOauthUserCode(result.user_code ?? '');
      setOauthUrl(result.verification_url ?? 'https://google.com/device');
      oauthInterval.current = result.interval ?? 5;
      setOauthPending(true);
      setOauthSlowDown(false);
      // Begin polling
      void pollOAuth(result.user_code ?? '');
    } catch (e) {
      setError(String(e));
    }
    setSaving(false);
  };

  const pollOAuth = async (code: string) => {
    const poll = async (): Promise<void> => {
      if (!oauthPending) return;
      try {
        const result = await call<[string], OAuthCheckResult>('check_oauth', code);
        if (result.status === 'success') {
          setOauthPending(false);
          setOauthStarted(false);
          void getState();
          return;
        }
        if (result.status === 'error') {
          setOauthPending(false);
          setError(result.error ?? 'OAuth failed');
          return;
        }
        // pending - schedule next poll
        const delay = (oauthSlowDown ? oauthInterval.current + 5 : oauthInterval.current) * 1000;
        setTimeout(() => void poll(), delay);
      } catch (e) {
        setOauthPending(false);
        setError(`OAuth poll error: ${String(e)}`);
      }
    };
    void poll();
  };

  // Clean up polling on unmount
  useEffect(() => {
    return () => {
      setOauthPending(false);
    };
  }, []);

  // ── Browser auth ──

  const handleLoadFile = async () => {
    if (!filePath.trim()) {
      setError('Please enter a file path.');
      return;
    }
    setError('');
    setSaving(true);
    try {
      const result = await call<[string], { success?: boolean; error?: string }>(
        'load_headers_from_file', filePath.trim()
      );
      if (result.error) {
        setError(result.error);
      } else {
        void getState();
      }
    } catch (e) {
      setError(String(e));
    }
    setSaving(false);
  };

  const handleSignOut = async () => {
    try {
      await call<[], { success: boolean }>('sign_out');
      setOauthStarted(false);
      setOauthPending(false);
      setShowBrowserAuth(false);
      void getState();
    } catch (e) {
      setError(`Sign out failed: ${String(e)}`);
    }
  };

  if (!authState) {
    return <div style={{ padding: '16px', color: 'var(--gpSystemLighterGrey)' }}>Loading...</div>;
  }

  return (
    <div>
      {error && (
        <div style={{ padding: '8px 0', color: '#ff6b6b', fontSize: '12px' }}>{error}</div>
      )}

      {authState.authenticated ? (
        <PanelSection>
          <Focusable flow-children="horizontal" style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0'
          }}>
            <span style={{ color: '#4caf50', fontSize: '14px' }}>Authenticated ✓</span>
            <DialogButton
              style={{ width: 'auto', minWidth: '100px', padding: '8px 16px', fontSize: '13px' }}
              onClick={() => void handleSignOut()}
            >
              Sign Out
            </DialogButton>
          </Focusable>
        </PanelSection>
      ) : (
        <PanelSection>
          {/* OAuth flow */}
          {!oauthStarted ? (
            <div>
              <div style={{
                fontSize: '13px', color: 'var(--gpSystemLighterGrey)', lineHeight: '1.6', marginBottom: '16px'
              }}>
                Connect your YouTube Music account using Google's secure login.
                No file transfers needed.
              </div>

              {/* OAuth Client ID configuration */}
              {!hasClientId || showClientIdForm ? (
                <div style={{ marginBottom: '16px', padding: '12px', background: 'rgba(255,255,255,0.05)', borderRadius: '4px' }}>
                  <div style={{ fontSize: '12px', color: '#ffa500', marginBottom: '8px' }}>
                    {hasClientId ? 'Update your' : 'First, set up your'} Google OAuth Client ID:
                  </div>
                  <TextField
                    placeholder="Client ID (required)"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                  />
                  <div style={{ height: '8px' }} />
                  <TextField
                    placeholder="Client Secret (optional)"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                  />
                  <div style={{ marginTop: '8px', display: 'flex', gap: '8px' }}>
                    <DialogButton
                      style={{ flex: 1, fontSize: '12px', padding: '6px 12px', minWidth: 0 }}
                      onClick={() => void handleSaveOauthConfig()}
                      disabled={saving || !clientId.trim()}
                    >
                      {saving ? 'Saving...' : 'Save'}
                    </DialogButton>
                    {hasClientId && (
                      <DialogButton
                        style={{ fontSize: '12px', padding: '6px 12px', minWidth: 0 }}
                        onClick={() => setShowClientIdForm(false)}
                      >
                        Cancel
                      </DialogButton>
                    )}
                  </div>
                  {oauthConfigSaved && (
                    <div style={{ color: '#4caf50', fontSize: '11px', marginTop: '6px' }}>
                      ✓ Client ID saved
                    </div>
                  )}

                  {/* Google Cloud guide */}
                  <Focusable
                    flow-children="horizontal"
                    style={{ cursor: 'pointer', marginTop: '12px', padding: '4px 0' }}
                    onClick={() => setShowCloudGuide(!showCloudGuide)}
                  >
                    <span style={{ fontSize: '11px', color: showCloudGuide ? '#aaa' : '#888', userSelect: 'none' }}>
                      {showCloudGuide ? '▼' : '▶'} How to get a Client ID
                    </span>
                  </Focusable>
                  {showCloudGuide && (
                    <div style={{ fontSize: '11px', color: '#888', lineHeight: '1.6', marginTop: '4px', padding: '8px', background: 'rgba(0,0,0,0.2)', borderRadius: '4px' }}>
                      <div>1. Go to <span style={{ color: '#66c0ff' }}>console.cloud.google.com</span></div>
                      <div>2. Create a project (e.g. "Decky YTMusic")</div>
                      <div>3. Go to <strong>APIs & Services → Library</strong></div>
                      <div>4. Enable <strong>YouTube Data API v3</strong></div>
                      <div>5. Go to <strong>Credentials → Create Credentials → OAuth client ID</strong></div>
                      <div>6. Application type: <strong>TVs and Limited Input devices</strong></div>
                      <div>7. Name: "Decky YouTube Music Player"</div>
                      <div>8. Copy the <strong>Client ID</strong> and paste above</div>
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ marginBottom: '16px' }}>
                  <div style={{ fontSize: '12px', color: '#4caf50', marginBottom: '8px' }}>
                    ✓ OAuth Client ID configured
                  </div>
                  <DialogButton
                    style={{ fontSize: '11px', padding: '4px 8px', minWidth: 0, width: 'auto', background: 'rgba(255,255,255,0.1)' }}
                    onClick={() => setShowClientIdForm(true)}
                  >
                    Change
                  </DialogButton>
                </div>
              )}

              {hasClientId ? (
                <ButtonItem onClick={() => void handleStartOAuth()}>
                  {saving ? 'Starting...' : 'Sign in with Google'}
                </ButtonItem>
              ) : (
                <div style={{ fontSize: '11px', color: '#888', marginTop: '6px' }}>
                  Configure a Client ID above to enable Google sign-in.
                </div>
              )}
            </div>
          ) : (
            <div>
              <div style={{
                fontSize: '13px', color: 'var(--gpSystemLighterGrey)', lineHeight: '1.6', marginBottom: '16px'
              }}>
                {oauthPending ? (
                  <>
                    <div style={{ marginBottom: '12px', fontSize: '14px', color: 'white' }}>
                      1. Go to{' '}
                      <span style={{ color: '#66c0ff', fontWeight: 'bold' }}>{oauthUrl}</span>
                    </div>
                    <div style={{ marginBottom: '12px', fontSize: '14px', color: 'white' }}>
                      2. Enter code:{' '}
                      <span style={{
                        fontSize: '22px', fontWeight: 'bold', letterSpacing: '4px',
                        color: '#ff0', fontFamily: 'monospace',
                        background: 'rgba(255,255,255,0.1)', padding: '4px 12px',
                        borderRadius: '4px'
                      }}>
                        {oauthUserCode}
                      </span>
                    </div>
                    {oauthSlowDown && (
                      <div style={{ color: '#ffa500', fontSize: '12px', marginBottom: '8px' }}>
                        Waiting for server...
                      </div>
                    )}
                    <div style={{ fontSize: '12px', color: '#888' }}>
                      Waiting for authorization...
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: '13px', color: '#ff6b6b' }}>
                    Authorization expired or failed. Try again.
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Browser auth fallback */}
          {!oauthStarted && (
            <div style={{ marginTop: '24px', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '16px' }}>
              <Focusable
                flow-children="horizontal"
                style={{ cursor: 'pointer', marginBottom: showBrowserAuth ? '12px' : '0' }}
                onClick={() => setShowBrowserAuth(!showBrowserAuth)}
              >
                <span style={{
                  fontSize: '12px', color: showBrowserAuth ? '#aaa' : '#888',
                  userSelect: 'none'
                }}>
                  {showBrowserAuth ? '▼' : '▶'} Advanced: Browser cookie auth
                </span>
              </Focusable>

              {showBrowserAuth && (
                <div>
                  <div style={{
                    fontSize: '12px', color: 'var(--gpSystemLighterGrey)', lineHeight: '1.6', marginBottom: '12px'
                  }}>
                    Use browser cookies from your PC when OAuth is unavailable.
                    Requires copying request headers from browser DevTools.
                  </div>

                  <div style={{ fontSize: '11px', color: '#888', marginBottom: '12px' }}>
                    <div>1. Open music.youtube.com in a browser on your PC</div>
                    <div>2. Log in and open DevTools (F12) → Network tab</div>
                    <div>3. Find a POST to <span style={{ color: '#aaa' }}>/browse</span> (status 200)</div>
                    <div>4. Right-click → Copy Request Headers</div>
                    <div>5. Paste to a text file, transfer to your Deck</div>
                    <div>6. Enter the file path below</div>
                  </div>

                  <TextField
                    value={filePath}
                    onChange={(e) => setFilePath(e.target.value)}
                  />
                  <div style={{ marginTop: '8px' }}>
                    <ButtonItem onClick={() => void handleLoadFile()}>
                      {saving ? 'Loading...' : 'Load & Connect'}
                    </ButtonItem>
                  </div>
                </div>
              )}
            </div>
          )}
        </PanelSection>
      )}
    </div>
  );
};

export const SettingsPage = () => {
  return (
    <SidebarNavigation
      title="YouTube Music Player"
      pages={[
        {
          title: "Settings",
          content: <AuthContent />,
          route: "/youtube-music-settings/auth",
          visible: true,
        },
      ]}
    />
  );
};
