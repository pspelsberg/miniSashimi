import React, { useState, useEffect, useRef } from 'react';
import { 
  Activity, Settings, ShieldCheck, Key, Cpu, AlertTriangle, 
  Play, Square, DollarSign, RefreshCw, Layers, CheckCircle2, 
  XCircle, Clock, Database, User, Terminal, Monitor
} from 'lucide-react';
import './App.css';

const API_BASE = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";

const formatWorkflowDateTime = (dateTimeStr) => {
  if (!dateTimeStr) return '';
  try {
    const date = new Date(dateTimeStr);
    if (isNaN(date.getTime())) return '';
    const now = new Date();
    
    const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    
    if (date.toDateString() === now.toDateString()) {
      return `Heute, ${timeStr}`;
    }
    
    const yesterday = new Date();
    yesterday.setDate(now.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
      return `Gestern, ${timeStr}`;
    }
    
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    return `${day}.${month}.${year}, ${timeStr}`;
  } catch (e) {
    console.error("Fehler beim Formatieren des Datums:", e);
    return '';
  }
};

function App() {
  const [activeTab, setActiveTab] = useState('workflows'); // 'workflows' | 'settings'
  const [workflows, setWorkflows] = useState({});
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(null);
  const [wsConnected, setWsConnected] = useState(false);
  
  // Authentication State
  const [csrfToken, setCsrfToken] = useState('');
  const [tokenValid, setTokenValid] = useState(false);
  const [showTokenModal, setShowTokenModal] = useState(true);
  const [tokenInput, setTokenInput] = useState('');
  const [authError, setAuthError] = useState('');

  // API Keys State
  const [apiKeys, setApiKeys] = useState({
    openai: { configured: false, masked: '' },
    anthropic: { configured: false, masked: '' },
    mistral: { configured: false, masked: '' },
    openrouter: { configured: false, masked: '' },
    deepseek: { configured: false, masked: '' },
    google: { configured: false, masked: '' }
  });
  const [inputKeys, setInputKeys] = useState({
    openai: '',
    anthropic: '',
    mistral: '',
    openrouter: '',
    deepseek: '',
    google: ''
  });

  // Settings State
  const [settings, setSettings] = useState({
    model_thinker: '',
    model_conductor: '',
    model_worker_backend: '',
    model_worker_frontend: '',
    model_test_architect: '',
    model_worker_integration: '',
    model_pre_verifier: '',
    model_security_auditor: '',
    model_performance_scout: '',
    model_dependency_watchdog: '',
    model_visual_verifier: '',
    model_runtime_executor: '',
    model_repository_janitor: '',
    pii_masking_enabled: 'true',
    cost_limit_usd: '10.0',
    llm_timeout_seconds: '',
    test_mode_enabled: 'false',
    lmstudio_base_url: '',
    ollama_base_url: '',
    llama_cpp_base_url: '',
    token_limit: '100000',
    workflow_timeout_seconds: '300.0'
  });

  // Saved Settings State (for tracking server-side confirmed settings)
  const [savedSettings, setSavedSettings] = useState({
    model_thinker: '',
    model_conductor: '',
    model_worker_backend: '',
    model_worker_frontend: '',
    model_test_architect: '',
    model_worker_integration: '',
    model_pre_verifier: '',
    model_security_auditor: '',
    model_performance_scout: '',
    model_dependency_watchdog: '',
    model_visual_verifier: '',
    model_runtime_executor: '',
    model_repository_janitor: '',
    pii_masking_enabled: 'true',
    cost_limit_usd: '10.0',
    llm_timeout_seconds: '',
    test_mode_enabled: 'false',
    lmstudio_base_url: '',
    ollama_base_url: '',
    llama_cpp_base_url: '',
    token_limit: '100000',
    workflow_timeout_seconds: '300.0'
  });

  const wsRef = useRef(null);
  const terminalBodyRef = useRef(null);
  
  // Live Logger State
  const [logs, setLogs] = useState([]);
  const [consoleExpanded, setConsoleExpanded] = useState(false);

  // Auto-scroll terminal to bottom
  useEffect(() => {
    if (consoleExpanded && terminalBodyRef.current) {
      terminalBodyRef.current.scrollTop = terminalBodyRef.current.scrollHeight;
    }
  }, [logs, consoleExpanded]);

  // Verify session on mount
  useEffect(() => {
    const verifySession = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/verify`, {
          credentials: 'include'
        });
        if (res.ok) {
          const data = await res.json();
          setCsrfToken(data.csrf_token);
          setTokenValid(true);
          setShowTokenModal(false);
        } else {
          setShowTokenModal(true);
        }
      } catch (e) {
        console.error("Session verification failed:", e);
        setShowTokenModal(true);
      }
    };
    verifySession();
  }, []);

  // Main Effect when token becomes valid
  useEffect(() => {
    if (!tokenValid) return;

    connectWebSocket();
    fetchKeys();
    fetchSettings();

    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, [tokenValid]);

  const handleAuthError = () => {
    setCsrfToken('');
    setTokenValid(false);
    setShowTokenModal(true);
    setAuthError("Sitzung abgelaufen oder ungültiger Token.");
  };

  const handleLogin = async (e) => {
    if (e) e.preventDefault();
    if (!tokenInput.trim()) return;

    try {
      const res = await fetch(`${API_BASE}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: tokenInput.trim() }),
        credentials: 'include'
      });
      if (res.ok) {
        const data = await res.json();
        setCsrfToken(data.csrf_token);
        setTokenValid(true);
        setShowTokenModal(false);
        setAuthError('');
      } else {
        setAuthError("Ungültiger Access Token.");
      }
    } catch (e) {
      setAuthError("Verbindung zum Server fehlgeschlagen.");
    }
  };

  const authedFetch = async (url, options = {}) => {
    const headers = {
      ...options.headers,
      'Content-Type': 'application/json'
    };
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    const res = await fetch(url, { 
      ...options, 
      headers,
      credentials: 'include'
    });
    if (res.status === 401 || res.status === 403) {
      handleAuthError();
      throw new Error("Unauthorized");
    }
    return res;
  };

  // WebSocket Verbindung
  const connectWebSocket = async () => {
    try {
      const res = await authedFetch(`${API_BASE}/api/ws-ticket`, { method: 'POST' });
      if (!res.ok) throw new Error("Ticket-Abruf fehlgeschlagen");
      const { ticket } = await res.json();
      
      const ws = new WebSocket(`${WS_URL}?ticket=${ticket}`);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
        console.log("WebSocket verbunden.");
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'error' && msg.message === 'Unauthorized') {
          handleAuthError();
          return;
        }
        if (msg.type === 'init_workflows') {
          setWorkflows(msg.data);
          const keys = Object.keys(msg.data);
          if (keys.length > 0) {
            setSelectedWorkflowId(keys[keys.length - 1]);
          }
        } else if (msg.type === 'workflow_update') {
          setWorkflows(prev => {
            const updated = { ...prev, [msg.data.workflow_id]: msg.data };
            return updated;
          });
          setSelectedWorkflowId(msg.data.workflow_id);
        } else if (msg.type === 'log_event') {
          setLogs(prev => {
            const updated = [...prev, msg.data];
            return updated.slice(-300); // Max 300 logs in frontend memory
          });
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        console.log("WebSocket getrennt. Reconnect in 3s...");
        setTimeout(() => {
          if (tokenValid) {
            connectWebSocket();
          }
        }, 3000);
      };
    } catch (e) {
      console.error("Fehler beim Herstellen der WebSocket-Verbindung:", e);
      setWsConnected(false);
      setTimeout(() => {
        if (tokenValid) {
          connectWebSocket();
        }
      }, 3000);
    }
  };

  const fetchKeys = async () => {
    try {
      const res = await authedFetch(`${API_BASE}/api/keys`);
      const data = await res.json();
      setApiKeys(data);
    } catch (e) {
      console.error("Fehler beim Laden der API Keys:", e);
    }
  };

  const fetchSettings = async () => {
    try {
      const res = await authedFetch(`${API_BASE}/api/settings`);
      const data = await res.json();
      setSettings(data);
      setSavedSettings(data);
    } catch (e) {
      console.error("Fehler beim Laden der Settings:", e);
    }
  };

  const saveKey = async (provider) => {
    const key = inputKeys[provider];
    if (!key) return;

    try {
      const res = await authedFetch(`${API_BASE}/api/keys/${provider}`, {
        method: 'POST',
        body: JSON.stringify({ api_key: key })
      });
      if (res.ok) {
        window.alert(`API Key fuer ${provider} erfolgreich gespeichert!`);
        setInputKeys(prev => ({ ...prev, [provider]: '' }));
        fetchKeys();
      }
    } catch (e) {
      window.alert("Fehler beim Speichern des Keys.");
    }
  };

  const deleteKey = async (provider) => {
    if (!window.confirm(`Möchten Sie den API Key für ${provider} wirklich löschen?`)) return;

    try {
      const res = await authedFetch(`${API_BASE}/api/keys/${provider}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        window.alert(`API Key fuer ${provider} erfolgreich gelöscht!`);
        fetchKeys();
      } else {
        window.alert("Fehler beim Löschen des Keys.");
      }
    } catch (e) {
      window.alert("Fehler beim Löschen des Keys.");
    }
  };

  const saveAllSettings = async () => {
    try {
      const res = await authedFetch(`${API_BASE}/api/settings`, {
        method: 'POST',
        body: JSON.stringify(settings)
      });
      if (res.ok) {
        window.alert("Systemeinstellungen erfolgreich gespeichert!");
        fetchSettings();
      }
    } catch (e) {
      window.alert("Fehler beim Speichern der Einstellungen.");
    }
  };

  const abortWorkflow = async (workflowId) => {
    try {
      const res = await authedFetch(`${API_BASE}/api/workflows/${workflowId}/abort`, {
        method: 'POST'
      });
      if (res.ok) {
        window.alert("Abbruch-Signal gesendet!");
      }
    } catch (e) {
      console.error("Fehler beim Abbrechen des Workflows:", e);
    }
  };

  const clearWorkflowHistory = async () => {
    if (!window.confirm("Möchten Sie die gesamte Workflow-Historie wirklich unwiderruflich löschen? Laufende Workflows werden nicht gelöscht.")) {
      return;
    }
    try {
      const res = await authedFetch(`${API_BASE}/api/workflows`, {
        method: 'DELETE'
      });
      if (res.ok) {
        window.alert("Workflow-Historie erfolgreich gelöscht!");
        setSelectedWorkflowId(null);
        setWorkflows(prev => {
          const updated = {};
          Object.entries(prev).forEach(([wid, wf]) => {
            if (wf.status === 'RUNNING' || wf.status === 'PENDING') {
              updated[wid] = wf;
            }
          });
          return updated;
        });
      } else {
        const errData = await res.json().catch(() => ({}));
        window.alert(`Fehler beim Löschen: ${errData.detail || 'Unbekannter Fehler'}`);
      }
    } catch (e) {
      window.alert("Fehler beim Verbinden mit der API.");
    }
  };

  const selectedWorkflow = workflows[selectedWorkflowId];

  // Hilfsfunktion zur Formatierung des Status
  const getStatusBadge = (status) => {
    switch (status) {
      case 'COMPLETED': return <span className="badge completed">Completed</span>;
      case 'RUNNING': return <span className="badge running">Running</span>;
      case 'FAILED': return <span className="badge failed">Failed</span>;
      case 'ABORTED': return <span className="badge aborted">Aborted</span>;
      default: return <span className="badge pending">Pending</span>;
    }
  };

  const resolveModelRouting = (modelString) => {
    if (!modelString) return { status: 'none', label: 'Kein Modell', details: 'Bitte Modell eintragen' };
    
    const parts = modelString.split('/');
    let provider = '';
    let model = '';
    
    if (parts.length >= 2) {
      provider = parts[0].toLowerCase();
      model = parts.slice(1).join('/');
    } else {
      provider = 'openai';
      model = modelString;
    }
    
    const subProviderMap = {
      'mistralai': 'mistral',
      'anthropic': 'anthropic',
      'openai': 'openai',
      'google': 'google',
      'deepseek': 'deepseek'
    };

    // 1. If provider is openrouter and it has a sub-model (like openrouter/anthropic/claude-4.8)
    if (provider === 'openrouter' && model.includes('/')) {
      const subParts = model.split('/');
      const subProvider = subParts[0].toLowerCase();
      const mappedProvider = subProviderMap[subProvider] || subProvider;
      
      if (apiKeys[mappedProvider] && apiKeys[mappedProvider].configured) {
        return {
          status: 'native',
          provider: mappedProvider,
          label: `Nativ: ${mappedProvider.toUpperCase()}`,
          details: `Spezifischer Key vorhanden. Verbindung direkt (nativ) zu ${mappedProvider.toUpperCase()} (OpenRouter umgangen)`
        };
      } else if (apiKeys['openrouter'] && apiKeys['openrouter'].configured) {
        return {
          status: 'openrouter',
          provider: 'openrouter',
          label: 'OpenRouter',
          details: `Verwendet OpenRouter API-Key`
        };
      }
    } else {
      // Direct provider like openai/gpt-4o or anthropic/claude-3
      const mappedProvider = subProviderMap[provider] || provider;
      if (apiKeys[mappedProvider] && apiKeys[mappedProvider].configured) {
        return {
          status: 'native',
          provider: mappedProvider,
          label: `Nativ: ${mappedProvider.toUpperCase()}`,
          details: `Direkte native Verbindung`
        };
      } else if (apiKeys['openrouter'] && apiKeys['openrouter'].configured) {
        return {
          status: 'fallback',
          provider: 'openrouter',
          label: 'OpenRouter Fallback',
          details: `Kein Key für ${provider.toUpperCase()}, weicht auf OpenRouter aus`
        };
      }
    }

    // Check if this is a local provider
    if (['lmstudio', 'ollama', 'llama.cpp', 'llama.ccp', 'llamacpp', 'llamaccp'].includes(provider)) {
      const displayProviderName = provider === 'lmstudio' ? 'LM Studio' : provider === 'ollama' ? 'Ollama' : 'llama.cpp';
      return {
        status: 'local',
        provider: provider,
        label: `${displayProviderName} (Lokal)`,
        details: `Lokales Modell via ${displayProviderName} – kein API-Key benötigt`
      };
    }

    // Direct check of whatever provider was parsed
    if (apiKeys[provider] && apiKeys[provider].configured) {
      return {
        status: 'native',
        provider: provider,
        label: `Nativ: ${provider.toUpperCase()}`,
        details: `Direkte native Verbindung`
      };
    }

    // If we have an openrouter key and nothing else, and provider doesn't match a direct key
    if (apiKeys['openrouter'] && apiKeys['openrouter'].configured) {
      return {
        status: 'fallback',
        provider: 'openrouter',
        label: 'OpenRouter Fallback',
        details: `Kein Key für ${provider.toUpperCase()}, weicht auf OpenRouter aus`
      };
    }

    return {
      status: 'missing',
      label: 'Kein Key',
      details: 'Weder spezifischer Key noch OpenRouter-Key konfiguriert!'
    };
  };

  const getRoutingBadge = (modelString) => {
    const routing = resolveModelRouting(modelString);
    if (routing.status === 'native') {
      return (
        <span className="routing-badge routing-native" title={routing.details}>
          <CheckCircle2 size={12} className="inline-icon" /> {routing.label}
        </span>
      );
    }
    if (routing.status === 'openrouter') {
      return (
        <span className="routing-badge routing-openrouter" title={routing.details}>
          <Cpu size={12} className="inline-icon" /> {routing.label}
        </span>
      );
    }
    if (routing.status === 'fallback') {
      return (
        <span className="routing-badge routing-fallback" title={routing.details}>
          <AlertTriangle size={12} className="inline-icon" /> {routing.label}
        </span>
      );
    }
    if (routing.status === 'local') {
      return (
        <span className="routing-badge routing-local" title={routing.details}>
          <Monitor size={12} className="inline-icon" /> {routing.label}
        </span>
      );
    }
    if (routing.status === 'missing') {
      return (
        <span className="routing-badge routing-missing" title={routing.details}>
          <XCircle size={12} className="inline-icon" /> {routing.label}
        </span>
      );
    }
    return null;
  };

  if (showTokenModal) {
    return (
      <div className="login-container">
        <div className="login-card">
          <div className="login-logo">🍣</div>
          <h1 className="login-title">miniSashimi</h1>
          <p className="login-subtitle">Multi-Agent Proxy Dashboard</p>
          
          <form onSubmit={handleLogin}>
            <div className="login-input-group">
              <label>Dashboard Access Token</label>
              <input 
                type="password"
                className="login-input"
                placeholder="Token eingeben..."
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                autoFocus
              />
            </div>
            {authError && <div className="login-error">{authError}</div>}
            <button type="submit" className="login-btn" style={{ marginTop: '1rem' }}>
              Dashboard entsperren
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      <header>
        <div className="logo">
          <span>🍣</span> miniSashimi Orchestrator
        </div>
        <div className="header-status">
          <div className={`status-dot ${wsConnected ? '' : 'disconnected'}`} />
          {wsConnected ? 'Proxy Online' : 'Proxy Offline'}
          {savedSettings?.test_mode_enabled === 'true' && (
            <span className="test-active-badge" style={{
              marginLeft: '0.5rem',
              backgroundColor: 'rgba(245, 158, 11, 0.15)',
              border: '1px solid rgba(245, 158, 11, 0.4)',
              color: 'var(--warning-color)',
              padding: '0.2rem 0.5rem',
              borderRadius: '6px',
              fontSize: '0.75rem',
              fontWeight: 600
            }}>
              Test aktiv
            </span>
          )}
        </div>
      </header>

      <div className="dashboard-grid">
        {/* SIDEBAR */}
        <div className="sidebar">
          <div className="tabs">
            <button 
              className={`tab-btn ${activeTab === 'workflows' ? 'active' : ''}`}
              onClick={() => setActiveTab('workflows')}
            >
              <Activity size={16} style={{ marginRight: '8px', verticalAlign: 'middle' }} />
              Workflows
            </button>
            <button 
              className={`tab-btn ${activeTab === 'settings' ? 'active' : ''}`}
              onClick={() => setActiveTab('settings')}
            >
              <Settings size={16} style={{ marginRight: '8px', verticalAlign: 'middle' }} />
              Settings
            </button>
          </div>

          {activeTab === 'workflows' ? (
            <div className="list-container">
              {Object.values(workflows).length === 0 ? (
                <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                  Keine aktiven Workflows. Starte einen Client (z. B. PI Coding Agent).
                </div>
              ) : (
                Object.values(workflows).reverse().map((wf) => (
                  <div 
                    key={wf.workflow_id}
                    className={`workflow-item ${selectedWorkflowId === wf.workflow_id ? 'active' : ''}`}
                    onClick={() => setSelectedWorkflowId(wf.workflow_id)}
                  >
                    <div className="workflow-header">
                      <span>{formatWorkflowDateTime(wf.started_at)}</span>
                      {getStatusBadge(wf.status)}
                    </div>
                    <div className="workflow-prompt">{wf.client_prompt}</div>
                    <div className="workflow-footer">
                      <span style={{ color: 'var(--accent-color)', fontWeight: 600 }}>
                        ${wf.accumulated_cost_usd.toFixed(4)}
                      </span>
                      <span style={{ color: 'var(--text-muted)' }}>
                        {wf.tasks.length} Tasks
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : (
            <div className="settings-panel">
              {/* API Keys */}
              <div className="settings-group">
                <h3><Key size={14} style={{ marginRight: '6px' }} /> API Keys</h3>
                {Object.keys(apiKeys).map((provider) => (
                  <div className="key-input-wrapper" key={provider}>
                    <label>{provider} API Key</label>
                    <div className="input-row">
                      <input 
                        type="password" 
                        placeholder={apiKeys[provider].configured ? apiKeys[provider].masked : "Eintragen..."}
                        value={inputKeys[provider]}
                        onChange={(e) => setInputKeys(prev => ({ ...prev, [provider]: e.target.value }))}
                      />
                      <button className="save-btn" onClick={() => saveKey(provider)}>Save</button>
                      {apiKeys[provider].configured && (
                        <button className="delete-btn" onClick={() => deleteKey(provider)}>Delete</button>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Security settings */}
              <div className="settings-group">
                <h3><ShieldCheck size={14} style={{ marginRight: '6px' }} /> Compliance & Security</h3>
                <div className="key-input-wrapper">
                  <label>Boundary PII-Anonymisierung</label>
                  <select 
                    value={settings.pii_masking_enabled}
                    onChange={(e) => setSettings(prev => ({ ...prev, pii_masking_enabled: e.target.value }))}
                  >
                    <option value="true">Aktiviert (Empfohlen)</option>
                    <option value="false">Deaktiviert</option>
                  </select>
                </div>
                <div className="key-input-wrapper">
                  <label>Kostenlimit pro Workflow ($)</label>
                  <input 
                    type="text" 
                    value={settings.cost_limit_usd}
                    onChange={(e) => setSettings(prev => ({ ...prev, cost_limit_usd: e.target.value }))}
                  />
                </div>
                <div className="key-input-wrapper">
                  <label>LLM-Request Timeout (Sekunden)</label>
                  <input 
                    type="text" 
                    value={settings.llm_timeout_seconds}
                    onChange={(e) => setSettings(prev => ({ ...prev, llm_timeout_seconds: e.target.value }))}
                  />
                </div>
                <div className="key-input-wrapper">
                  <label>Gesamt-Tokenlimit pro Workflow</label>
                  <input 
                    type="text" 
                    value={settings.token_limit || ''}
                    onChange={(e) => setSettings(prev => ({ ...prev, token_limit: e.target.value }))}
                  />
                </div>
                <div className="key-input-wrapper">
                  <label>Gesamt-Workflow Timeout (Sekunden)</label>
                  <input 
                    type="text" 
                    value={settings.workflow_timeout_seconds || ''}
                    onChange={(e) => setSettings(prev => ({ ...prev, workflow_timeout_seconds: e.target.value }))}
                  />
                </div>
                <div className="key-input-wrapper">
                  <label>Testmodus (Mistral Small)</label>
                  <select 
                    value={settings.test_mode_enabled || 'false'}
                    onChange={(e) => setSettings(prev => ({ ...prev, test_mode_enabled: e.target.value }))}
                  >
                    <option value="false">Deaktiviert (Normalbetrieb)</option>
                    <option value="true">Aktiviert (Alle Tasks via Mistral Small)</option>
                  </select>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem', display: 'block' }}>
                    Leitet alle Tasks kostengünstig an `mistral/mistral-small-latest` um, um Token zu sparen. Benötigt Mistral API-Key.
                  </span>
                  {settings.test_mode_enabled === 'true' && !apiKeys.mistral?.configured && (
                    <span style={{ color: 'var(--danger-color)', fontSize: '0.8rem', display: 'block', marginTop: '0.4rem', fontWeight: 'bold' }}>
                      ⚠️ Mistral API-Key fehlt! Bitte oben eintragen und speichern, damit der Testmodus funktioniert.
                    </span>
                  )}
                </div>
                <div className="key-input-wrapper">
                  <label><Monitor size={12} style={{ marginRight: '4px', verticalAlign: 'middle' }} /> LM Studio Base-URL</label>
                  <input 
                    type="text" 
                    placeholder="http://127.0.0.1:1234/v1"
                    value={settings.lmstudio_base_url}
                    onChange={(e) => setSettings(prev => ({ ...prev, lmstudio_base_url: e.target.value }))}
                  />
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem', display: 'block' }}>
                    Standard: <code>http://127.0.0.1:1234/v1</code> — Trage bei Modell-Routing z.B. <code>lmstudio/dein-modell</code> ein.
                  </span>
                </div>
                <div className="key-input-wrapper">
                  <label><Monitor size={12} style={{ marginRight: '4px', verticalAlign: 'middle' }} /> Ollama Base-URL</label>
                  <input 
                    type="text" 
                    placeholder="http://127.0.0.1:11434/v1"
                    value={settings.ollama_base_url}
                    onChange={(e) => setSettings(prev => ({ ...prev, ollama_base_url: e.target.value }))}
                  />
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem', display: 'block' }}>
                    Standard: <code>http://127.0.0.1:11434/v1</code> — Trage bei Modell-Routing z.B. <code>ollama/llama3</code> ein.
                  </span>
                </div>
                <div className="key-input-wrapper">
                  <label><Monitor size={12} style={{ marginRight: '4px', verticalAlign: 'middle' }} /> llama.cpp Base-URL</label>
                  <input 
                    type="text" 
                    placeholder="http://127.0.0.1:8080/v1"
                    value={settings.llama_cpp_base_url}
                    onChange={(e) => setSettings(prev => ({ ...prev, llama_cpp_base_url: e.target.value }))}
                  />
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem', display: 'block' }}>
                    Standard: <code>http://127.0.0.1:8080/v1</code> — Trage bei Modell-Routing z.B. <code>llama.cpp/dein-modell</code> ein.
                  </span>
                </div>
              </div>

              {/* Unsaved changes indicator */}
              {JSON.stringify(settings) !== JSON.stringify(savedSettings) && (
                <div style={{
                  backgroundColor: 'rgba(245, 158, 11, 0.1)',
                  border: '1px solid rgba(245, 158, 11, 0.3)',
                  color: 'var(--warning-color)',
                  padding: '0.75rem',
                  borderRadius: '6px',
                  fontSize: '0.85rem',
                  marginTop: '1rem',
                  marginBottom: '-0.5rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px'
                }}>
                  <AlertTriangle size={16} style={{ flexShrink: 0 }} />
                  <span>Sie haben ungespeicherte Änderungen! Bitte speichern, um sie zu aktivieren.</span>
                </div>
              )}

              {/* Save Settings Button */}
              <button 
                className="save-btn" 
                style={{ width: '100%', padding: '0.75rem', marginTop: '1rem' }}
                onClick={saveAllSettings}
              >
                Systemeinstellungen speichern
              </button>
            </div>
          )}
        </div>

        {/* WORKFLOW DETAILS */}
        <div className="main-content">
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {activeTab === 'workflows' && savedSettings?.test_mode_enabled === 'true' && (
              <div className="testmode-banner">
                <AlertTriangle size={16} className="testmode-banner-icon" />
                <div>
                  <strong>Testmodus aktiv:</strong> Alle Tasks dieses Workflows werden kostengünstig über <code>mistral/mistral-small-latest</code> ausgeführt, um Token/Kosten zu sparen.
                </div>
              </div>
            )}
            {activeTab === 'workflows' && selectedWorkflow ? (
              <div className="detail-view">
                <div className="detail-header">
                  <div className="detail-title">
                    <h2>Workflow Details</h2>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.2rem' }}>
                      ID: <code style={{ color: 'var(--accent-color)' }}>{selectedWorkflow.workflow_id}</code>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
                    <div className="meta-stats">
                      {selectedWorkflow.total_tokens > 0 && (
                        <div className="meta-stat">
                          Tokens: <span>{selectedWorkflow.total_tokens.toLocaleString()}</span>
                        </div>
                      )}
                      <div className="meta-stat">
                        Kosten: <span>${selectedWorkflow.accumulated_cost_usd.toFixed(4)}</span>
                      </div>
                      <div className="meta-stat">
                        Status: {getStatusBadge(selectedWorkflow.status)}
                      </div>
                    </div>
                    {selectedWorkflow.status === 'RUNNING' && (
                      <button 
                        className="kill-btn"
                        onClick={() => abortWorkflow(selectedWorkflow.workflow_id)}
                      >
                        <Square size={14} style={{ marginRight: '6px', verticalAlign: 'middle' }} />
                        Kill Loop
                      </button>
                    )}
                  </div>
                </div>

                {selectedWorkflow.total_tokens > 0 && (
                  <div className="token-banner">
                    <div className="token-banner-item">
                      <span className="token-label">Input/Prompt:</span>
                      <span className="token-value">{(selectedWorkflow.prompt_tokens || 0).toLocaleString()}</span>
                    </div>
                    <div className="token-banner-item">
                      <span className="token-label">Output/Completion:</span>
                      <span className="token-value">{(selectedWorkflow.completion_tokens || 0).toLocaleString()}</span>
                    </div>
                    {selectedWorkflow.cache_read_tokens > 0 && (
                      <div className="token-banner-item">
                        <span className="token-label">Cache Hit/Read:</span>
                        <span className="token-value">{(selectedWorkflow.cache_read_tokens || 0).toLocaleString()}</span>
                      </div>
                    )}
                    <div className="token-banner-item total">
                      <span className="token-label">Total:</span>
                      <span className="token-value">{(selectedWorkflow.total_tokens || 0).toLocaleString()}</span>
                    </div>
                  </div>
                )}

                <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '1rem', borderRadius: '12px', marginBottom: '1.5rem', border: '1px solid var(--border-color)' }}>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
                    Ursprünglicher Client-Prompt
                  </div>
                  <div style={{ fontSize: '0.9rem', color: 'var(--text-primary)', fontStyle: 'italic' }}>
                    "{selectedWorkflow.client_prompt}"
                  </div>
                </div>

                <div className="tasks-timeline">
                  {selectedWorkflow.tasks.map((task, idx) => (
                    <div key={task.id || idx} className={`task-node ${task.status.toLowerCase()}`}>
                      <div className="task-dot" />
                      <div className="task-card">
                        <div className="task-card-header">
                          <span className="task-title">{task.title}</span>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                            <span className="task-model">{task.assigned_model}</span>
                            {task.total_tokens > 0 && (
                              <span className="task-tokens">
                                {(task.prompt_tokens || 0).toLocaleString()} in / {(task.completion_tokens || 0).toLocaleString()} out
                                {task.cache_read_tokens > 0 && ` (cached: ${(task.cache_read_tokens || 0).toLocaleString()})`}
                              </span>
                            )}
                          </div>
                        </div>
                        
                        {getStatusBadge(task.status)}
                        
                        {task.output && (
                          <details style={{ marginTop: '0.75rem' }}>
                            <summary style={{ cursor: 'pointer', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                              Output anzeigen ({task.output.split('\n').length} Zeilen)
                            </summary>
                            <pre className="task-output">
                              {task.output}
                            </pre>
                          </details>
                        )}

                        {task.error && (
                          <div style={{ marginTop: '0.75rem', padding: '0.75rem', backgroundColor: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--danger-color)', borderRadius: '8px', color: 'var(--danger-color)', fontSize: '0.85rem' }}>
                            <strong>Fehler:</strong> {task.error}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              activeTab === 'workflows' && (
                <div className="detail-placeholder">
                  <span>🍣</span>
                  Wähle einen aktiven Workflow aus der Liste aus, um die Live-Schritte der Agenten zu sehen.
                </div>
              )
            )}

            {activeTab === 'settings' && (
              <div className="detail-view" style={{ overflowY: 'auto' }}>
                <div className="detail-header">
                  <h2>Modell-Routing pro Phase</h2>
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
                  Definiere, welches Modell für welche Phase der multi-agentischen Verarbeitung und Verifikation aufgerufen werden soll.
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
                  <div className="key-input-wrapper routing-wrapper">
                    <div className="routing-label-row">
                      <label>Phase 1: Systemarchitektur (Thinker)</label>
                      {getRoutingBadge(settings.model_thinker)}
                    </div>
                    <input 
                      type="text" 
                      value={settings.model_thinker}
                      onChange={(e) => setSettings(prev => ({ ...prev, model_thinker: e.target.value }))}
                    />
                    {settings.model_thinker && (
                      <div className="routing-details-text">
                        {resolveModelRouting(settings.model_thinker).details}
                      </div>
                    )}
                  </div>
                  <div className="key-input-wrapper routing-wrapper">
                    <div className="routing-label-row">
                      <label>Phase 1: Workflow-Konstruktion (Conductor)</label>
                      {getRoutingBadge(settings.model_conductor)}
                    </div>
                    <input 
                      type="text" 
                      value={settings.model_conductor}
                      onChange={(e) => setSettings(prev => ({ ...prev, model_conductor: e.target.value }))}
                    />
                    {settings.model_conductor && (
                      <div className="routing-details-text">
                        {resolveModelRouting(settings.model_conductor).details}
                      </div>
                    )}
                  </div>
                  <div className="key-input-wrapper routing-wrapper">
                    <div className="routing-label-row">
                      <label>Phase 2: Backend-Worker</label>
                      {getRoutingBadge(settings.model_worker_backend)}
                    </div>
                    <input 
                      type="text" 
                      value={settings.model_worker_backend}
                      onChange={(e) => setSettings(prev => ({ ...prev, model_worker_backend: e.target.value }))}
                    />
                    {settings.model_worker_backend && (
                      <div className="routing-details-text">
                        {resolveModelRouting(settings.model_worker_backend).details}
                      </div>
                    )}
                  </div>
                  <div className="key-input-wrapper routing-wrapper">
                    <div className="routing-label-row">
                      <label>Phase 2: Frontend-Worker</label>
                      {getRoutingBadge(settings.model_worker_frontend)}
                    </div>
                    <input 
                      type="text" 
                      value={settings.model_worker_frontend}
                      onChange={(e) => setSettings(prev => ({ ...prev, model_worker_frontend: e.target.value }))}
                    />
                    {settings.model_worker_frontend && (
                      <div className="routing-details-text">
                        {resolveModelRouting(settings.model_worker_frontend).details}
                      </div>
                    )}
                  </div>
                  <div className="key-input-wrapper routing-wrapper">
                    <div className="routing-label-row">
                      <label>Phase 3: Syntax-Prüfung (Pre-Verifier)</label>
                      {getRoutingBadge(settings.model_pre_verifier)}
                    </div>
                    <input 
                      type="text" 
                      value={settings.model_pre_verifier}
                      onChange={(e) => setSettings(prev => ({ ...prev, model_pre_verifier: e.target.value }))}
                    />
                    {settings.model_pre_verifier && (
                      <div className="routing-details-text">
                        {resolveModelRouting(settings.model_pre_verifier).details}
                      </div>
                    )}
                  </div>
                  <div className="key-input-wrapper routing-wrapper">
                    <div className="routing-label-row">
                      <label>Phase 3: Security Auditor</label>
                      {getRoutingBadge(settings.model_security_auditor)}
                    </div>
                    <input 
                      type="text" 
                      value={settings.model_security_auditor}
                      onChange={(e) => setSettings(prev => ({ ...prev, model_security_auditor: e.target.value }))}
                    />
                    {settings.model_security_auditor && (
                      <div className="routing-details-text">
                        {resolveModelRouting(settings.model_security_auditor).details}
                      </div>
                    )}
                  </div>
                </div>

                <button 
                  className="save-btn" 
                  style={{ alignSelf: 'flex-start', padding: '0.75rem 2rem', marginTop: '2rem' }}
                  onClick={saveAllSettings}
                >
                  Routing-Einstellungen speichern
                </button>

                <hr style={{ margin: '2rem 0', borderColor: 'var(--border-color)', opacity: 0.3 }} />

                <div className="detail-header">
                  <h2>System-Aktionen</h2>
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
                  Verwalte deine temporären Systemdaten und Verläufe.
                </p>

                <div className="key-input-wrapper routing-wrapper" style={{ padding: '1.5rem', background: 'rgba(255, 255, 255, 0.02)', borderRadius: '12px', border: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ flex: 1, paddingRight: '1.5rem' }}>
                    <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1rem', color: 'var(--text-primary)' }}>Workflow-Historie löschen</h3>
                    <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                      Entfernt alle abgeschlossenen, fehlgeschlagenen und abgebrochenen Workflows permanent aus der Datenbank. Laufende Workflows bleiben erhalten.
                    </p>
                  </div>
                  <button 
                    className="kill-btn"
                    onClick={clearWorkflowHistory}
                  >
                    Historie löschen
                  </button>
                </div>
              </div>
            )}
          </div>

          {activeTab === 'workflows' && (
            <div className={`terminal-panel ${consoleExpanded ? 'expanded' : ''}`}>
              <div className="terminal-header" onClick={() => setConsoleExpanded(!consoleExpanded)}>
                <div className="terminal-title-row">
                  <Terminal size={14} style={{ marginRight: '6px' }} />
                  <span>Live System-Logs</span>
                  <span className="terminal-count-badge">{logs.length} Einträge</span>
                </div>
                <div className="terminal-controls" onClick={(e) => e.stopPropagation()}>
                  <button className="terminal-btn clear" onClick={() => setLogs([])} title="Logs leeren">
                    Leeren
                  </button>
                  <button className="terminal-btn toggle" onClick={() => setConsoleExpanded(!consoleExpanded)}>
                    {consoleExpanded ? 'Ausblenden' : 'Einblenden'}
                  </button>
                </div>
              </div>
              {consoleExpanded && (
                <div className="terminal-body" ref={terminalBodyRef}>
                  {logs.length === 0 ? (
                    <div className="terminal-empty">Keine Log-Einträge vorhanden. Warte auf Events...</div>
                  ) : (
                    logs.map((log, index) => (
                      <div key={index} className={`terminal-line ${log.level.toLowerCase()}`}>
                        <span className="log-time">[{log.timestamp}]</span>
                        <span className="log-logger">[{log.logger}]</span>
                        <span className="log-level">[{log.level}]</span>
                        <span className="log-msg">{log.message}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
