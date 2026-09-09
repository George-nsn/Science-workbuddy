"use client";

import {
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  PlugZap,
  Save,
  ServerCog,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { apiRequest } from "@/lib/api";

type ModelOption = {
  id: string;
  label: string;
  category: string;
};

type ModelProvider = {
  id: string;
  label: string;
  protocol: "openai_compatible" | "anthropic" | "github_copilot";
  description: string;
  default_base_url: string;
  api_key_required: boolean;
  models: ModelOption[];
};

type ModelCatalog = { providers: ModelProvider[] };

type ModelConfiguration = {
  configured: boolean;
  provider: string | null;
  model: string | null;
  base_url: string | null;
  has_api_key: boolean;
  source: "environment" | "local" | "none";
};

type ConnectionResult = {
  ok: true;
  provider: string;
  model: string;
  detail: string;
};

const FALLBACK_PROVIDER: ModelProvider = {
  id: "openai_compatible",
  label: "其他 OpenAI 兼容接口",
  protocol: "openai_compatible",
  description: "自定义兼容接口。",
  default_base_url: "https://api.openai.com/v1",
  api_key_required: true,
  models: [],
};

export function ModelSettingsWorkbench() {
  const [configuration, setConfiguration] = useState<ModelConfiguration | null>(null);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      apiRequest<ModelConfiguration>("/settings/model", { cache: "no-store" }),
      apiRequest<ModelCatalog>("/settings/model/catalog", { cache: "no-store" }),
    ])
      .then(([value, catalog]) => {
        if (!active) return;
        setProviders(catalog.providers);
        const nextProvider =
          catalog.providers.find((item) => item.id === value.provider) ??
          catalog.providers[0] ??
          FALLBACK_PROVIDER;
        setConfiguration(value);
        setProvider(nextProvider.id);
        setModel(value.model ?? nextProvider.models[0]?.id ?? "");
        setBaseUrl(value.base_url ?? nextProvider.default_base_url);
        setApiKey("");
      })
      .catch((value: unknown) => {
        if (active) setError(value instanceof Error ? value.message : "模型配置加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const providerInfo =
    providers.find((item) => item.id === provider) ?? FALLBACK_PROVIDER;
  const selectedModel = providerInfo.models.some((item) => item.id === model)
    ? model
    : "__custom__";
  const environmentLocked = configuration?.source === "environment";
  const endpointChanged = Boolean(
    configuration?.configured &&
      (configuration.provider !== provider ||
        configuration.base_url?.replace(/\/$/, "") !== baseUrl.trim().replace(/\/$/, "")),
  );
  const keyRequired =
    providerInfo.api_key_required && (!configuration?.has_api_key || endpointChanged);
  const isGitHubCopilot = providerInfo.protocol === "github_copilot";
  const formValid =
    model.trim().length > 0 &&
    baseUrl.trim().length > 0 &&
    (!keyRequired || apiKey.trim().length > 0);
  const busy = loading || saving || testing || removing;

  const statusText = useMemo(() => {
    if (loading) return "正在读取配置";
    if (!configuration?.configured) return "尚未配置模型";
    return configuration.source === "environment"
      ? "由后端环境变量管理"
      : "本机加密配置已启用";
  }, [configuration, loading]);

  function applyConfiguration(value: ModelConfiguration) {
    setConfiguration(value);
    const nextProvider =
      providers.find((item) => item.id === value.provider) ??
      providers[0] ??
      FALLBACK_PROVIDER;
    setProvider(nextProvider.id);
    setModel(value.model ?? nextProvider.models[0]?.id ?? "");
    setBaseUrl(value.base_url ?? nextProvider.default_base_url);
    setApiKey("");
  }

  function changeProvider(value: string) {
    const nextProvider =
      providers.find((item) => item.id === value) ?? FALLBACK_PROVIDER;
    setProvider(value);
    setModel(nextProvider.models[0]?.id ?? "");
    setBaseUrl(nextProvider.default_base_url);
    setNotice(null);
    setError(null);
  }

  function payload() {
    return {
      provider,
      model: model.trim(),
      base_url: baseUrl.trim(),
      api_key: apiKey.trim() || null,
    };
  }

  async function saveConfiguration(event: FormEvent) {
    event.preventDefault();
    if (!formValid || environmentLocked) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const value = await apiRequest<ModelConfiguration>("/settings/model", {
        method: "PUT",
        body: JSON.stringify(payload()),
      });
      applyConfiguration(value);
      setNotice("模型配置已加密保存，新的科研问答与课题 Agent 请求会立即使用该配置。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "模型配置保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    if (!formValid) return;
    setTesting(true);
    setError(null);
    setNotice(null);
    try {
      const value = await apiRequest<ConnectionResult>("/settings/model/test", {
        method: "POST",
        body: JSON.stringify(payload()),
      });
      setNotice(`${value.provider} · ${value.model} 连接成功。`);
    } catch (value) {
      setError(value instanceof Error ? value.message : "模型连接测试失败");
    } finally {
      setTesting(false);
    }
  }

  async function removeConfiguration() {
    if (environmentLocked || !configuration?.configured) return;
    if (!window.confirm("确定移除本机保存的模型配置吗？后续模型功能将不可用。")) return;
    setRemoving(true);
    setError(null);
    setNotice(null);
    try {
      await apiRequest<void>("/settings/model", { method: "DELETE" });
      applyConfiguration({
        configured: false,
        provider: null,
        model: null,
        base_url: null,
        has_api_key: false,
        source: "none",
      });
      setNotice("本机模型配置和加密 API Key 已移除。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "模型配置移除失败");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <>
      <header className="page-header settings-page-header">
        <div>
          <span className="eyebrow">MODEL &amp; PRIVACY CONTROL</span>
          <h1>模型与隐私</h1>
          <p>配置科研问答和课题多 Agent 使用的模型接口；API Key 仅在本机后端加密保存。</p>
        </div>
        <div className={`model-badge ${configuration?.configured ? "configured" : ""}`}>
          <span /> {statusText}
        </div>
      </header>

      {error && <div aria-live="assertive" className="error-banner" role="alert">{error}</div>}
      {notice && <div aria-live="polite" className="success-banner" role="status">{notice}</div>}

      <section className="settings-layout">
        <form className="model-settings-card" onSubmit={saveConfiguration}>
          <header>
            <div className="settings-icon"><ServerCog size={22} /></div>
            <div>
              <span className="eyebrow">ACTIVE MODEL</span>
              <h2>模型 API 配置</h2>
            </div>
            {configuration?.configured && (
              <span className="settings-state"><CheckCircle2 size={14} />已配置</span>
            )}
          </header>

          {environmentLocked && (
            <div className="environment-lock">
              <LockKeyhole size={17} />
              <div><strong>环境变量配置正在生效</strong><span>为避免部署配置被界面覆盖，当前字段只读；请在后端环境中修改。</span></div>
            </div>
          )}

          <div className="settings-form-grid">
            <label>
              <span>模型厂家 / 接入方式</span>
              <select
                disabled={busy || environmentLocked || !providers.length}
                onChange={(event) => changeProvider(event.target.value)}
                value={provider}
              >
                {providers.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
              <small>
                {providerInfo.description} · 协议：
                {providerInfo.protocol === "anthropic"
                  ? "Anthropic Messages"
                  : isGitHubCopilot
                    ? "官方 Copilot SDK / CLI"
                    : "OpenAI Chat Completions"}
              </small>
            </label>

            <label>
              <span>模型</span>
              <select
                disabled={busy || environmentLocked}
                onChange={(event) => {
                  const value = event.target.value;
                  setModel(value === "__custom__" ? "" : value);
                }}
                value={selectedModel}
              >
                {providerInfo.models.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} · {item.category}
                  </option>
                ))}
                <option value="__custom__">自定义模型名称…</option>
              </select>
              {selectedModel === "__custom__" && (
                <input
                  aria-label="自定义模型名称"
                  disabled={busy || environmentLocked}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder="输入接口支持的精确模型 ID"
                  value={model}
                />
              )}
            </label>

            <label className="settings-wide-field">
              <span>API Base URL</span>
              <input
                disabled={busy || environmentLocked || isGitHubCopilot}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder={providerInfo.default_base_url}
                spellCheck={false}
                type="url"
                value={baseUrl}
              />
              <small>
                {isGitHubCopilot
                  ? "Copilot SDK 自动管理服务连接，此地址仅用于标识且不可编辑。"
                  : "厂家预设会自动填入正确地址；远程服务必须使用 HTTPS，本机模型可使用 HTTP localhost。"}
              </small>
            </label>

            <label className="settings-wide-field">
              <span>
                {isGitHubCopilot
                  ? "GitHub OAuth Token（可选）"
                  : `API Key${providerInfo.api_key_required ? "" : "（本机服务可选）"}`}
              </span>
              <div className="secret-input">
                <KeyRound size={16} />
                <input
                  autoComplete="new-password"
                  disabled={busy || environmentLocked}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={
                    endpointChanged && providerInfo.api_key_required
                      ? "服务端点已变化，请重新输入 API Key"
                      : isGitHubCopilot
                        ? "留空使用本机 Copilot CLI 已登录账号"
                        : !providerInfo.api_key_required
                        ? "本机服务通常无需 API Key"
                        : configuration?.has_api_key
                        ? "已安全保存；留空表示不更换"
                        : "输入 API Key"
                  }
                  type={showKey ? "text" : "password"}
                  value={apiKey}
                />
                <button
                  aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
                  disabled={environmentLocked}
                  onClick={() => setShowKey((current) => !current)}
                  type="button"
                >
                  {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <small>
                {isGitHubCopilot
                  ? "需要有效 GitHub Copilot 订阅；可留空复用 Copilot CLI 登录，或填写具备 Copilot 权限的 GitHub OAuth Token。"
                  : "页面不会读取或回显已保存的 Key；更新其他设置时可留空。"}
              </small>
            </label>
          </div>

          <footer>
            <button
              className="settings-delete"
              disabled={busy || environmentLocked || !configuration?.configured}
              onClick={removeConfiguration}
              type="button"
            >
              {removing ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}移除配置
            </button>
            <div>
              <button disabled={busy || !formValid} onClick={testConnection} type="button">
                {testing ? <LoaderCircle className="spin" size={15} /> : <PlugZap size={15} />}
                {testing ? "连接中" : "测试连接"}
              </button>
              <button className="settings-save" disabled={busy || !formValid || environmentLocked} type="submit">
                {saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
                {saving ? "保存中" : "保存配置"}
              </button>
            </div>
          </footer>
        </form>

        <aside className="privacy-settings-card">
          <div className="privacy-shield"><ShieldCheck size={28} /></div>
          <span className="eyebrow">PRIVACY BOUNDARY</span>
          <h2>密钥与科研数据边界</h2>
          <ul>
            <li><strong>加密存储</strong><span>API Key 使用本机派生密钥加密后写入 SQLite，不以明文保存。</span></li>
            <li><strong>不回传密钥</strong><span>配置接口仅返回“是否已保存”，浏览器无法读取已有 Key。</span></li>
            <li><strong>按会话授权</strong><span>课题头脑风暴仍需逐会话勾选模型处理授权，配置模型不会自动发送内容。</span></li>
            <li><strong>直连服务商</strong><span>模型请求由本机 FastAPI 直接发送至这里配置的 Base URL。</span></li>
          </ul>
          <div className="privacy-warning">
            保存前请确认所选服务商的数据保留与隐私政策；不要将临床身份信息或未获授权的数据发送到云模型。
          </div>
        </aside>
      </section>
    </>
  );
}
