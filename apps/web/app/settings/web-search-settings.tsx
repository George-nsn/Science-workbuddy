"use client";

import { Eye, EyeOff, Globe2, KeyRound, LoaderCircle, PlugZap, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api";

type SearchConfiguration = {
  configured: boolean;
  provider: "tavily" | null;
  base_url: string | null;
  has_api_key: boolean;
  max_results: number;
  search_depth: "basic" | "advanced";
  source: "environment" | "local" | "none";
};

export function WebSearchSettings() {
  const [configuration, setConfiguration] = useState<SearchConfiguration | null>(null);
  const [baseUrl, setBaseUrl] = useState("https://api.tavily.com");
  const [apiKey, setApiKey] = useState("");
  const [maxResults, setMaxResults] = useState(5);
  const [searchDepth, setSearchDepth] = useState<"basic" | "advanced">("advanced");
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    apiRequest<SearchConfiguration>("/settings/web-search", { cache: "no-store" })
      .then((value) => {
        if (!active) return;
        setConfiguration(value);
        setBaseUrl(value.base_url ?? "https://api.tavily.com");
        setMaxResults(value.max_results);
        setSearchDepth(value.search_depth);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "搜索配置加载失败");
      });
    return () => { active = false; };
  }, []);

  const locked = configuration?.source === "environment";
  const endpointChanged = Boolean(
    configuration?.configured
    && configuration.base_url?.replace(/\/$/, "") !== baseUrl.trim().replace(/\/$/, ""),
  );
  const valid = baseUrl.trim().length > 0
    && (configuration?.has_api_key && !endpointChanged || apiKey.trim().length > 0);

  function payload() {
    return {
      provider: "tavily",
      base_url: baseUrl.trim(),
      api_key: apiKey.trim() || null,
      max_results: maxResults,
      search_depth: searchDepth,
    };
  }

  async function save() {
    setBusy("save"); setError(""); setNotice("");
    try {
      const value = await apiRequest<SearchConfiguration>("/settings/web-search", {
        method: "PUT",
        body: JSON.stringify(payload()),
      });
      setConfiguration(value); setApiKey(""); setNotice("Tavily 搜索配置已加密保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "搜索配置保存失败");
    } finally { setBusy(""); }
  }

  async function test() {
    setBusy("test"); setError(""); setNotice("");
    try {
      await apiRequest("/settings/web-search/test", { method: "POST", body: JSON.stringify(payload()) });
      setNotice("Tavily 连接测试成功；测试配置未自动保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Tavily 连接测试失败");
    } finally { setBusy(""); }
  }

  async function remove() {
    if (!window.confirm("确定移除本机 Tavily 配置吗？")) return;
    setBusy("remove");
    try {
      await apiRequest("/settings/web-search", { method: "DELETE" });
      setConfiguration({ configured: false, provider: null, base_url: null, has_api_key: false, max_results: 5, search_depth: "advanced", source: "none" });
      setApiKey(""); setNotice("Tavily 配置已移除。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "搜索配置移除失败");
    } finally { setBusy(""); }
  }

  return (
    <section className="web-search-settings-card">
      <header><div className="settings-icon"><Globe2 size={22} /></div><div><span className="eyebrow">CONTROLLED WEB SEARCH</span><h2>Tavily 搜索 API</h2></div>{configuration?.configured && <span className="settings-state">已配置</span>}</header>
      <p>网页搜索由后端显式调用，模型没有自由联网工具权限；只把裁剪后的标题、HTTPS 链接和摘要作为不可信上下文。</p>
      {error && <div className="error-banner">{error}</div>}{notice && <div className="success-banner">{notice}</div>}
      <div className="settings-form-grid">
        <label className="settings-wide-field"><span>Base URL</span><input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={locked || !!busy} /></label>
        <label><span>搜索深度</span><select value={searchDepth} onChange={(event) => setSearchDepth(event.target.value as "basic" | "advanced")} disabled={locked || !!busy}><option value="basic">Basic</option><option value="advanced">Advanced</option></select></label>
        <label><span>最大结果数</span><input type="number" min={1} max={10} value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} disabled={locked || !!busy} /></label>
        <label className="settings-wide-field"><span>API Key</span><div className="secret-input"><KeyRound size={16} /><input type={showKey ? "text" : "password"} autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={configuration?.has_api_key ? "已加密保存；留空表示不更换" : "输入 Tavily API Key"} disabled={locked || !!busy} /><button type="button" onClick={() => setShowKey((value) => !value)}>{showKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></label>
      </div>
      <footer><button className="settings-delete" onClick={() => void remove()} disabled={locked || !!busy || !configuration?.configured}><Trash2 size={15} />移除</button><div><button onClick={() => void test()} disabled={!!busy || !valid}>{busy === "test" ? <LoaderCircle className="spin" size={15} /> : <PlugZap size={15} />}测试</button><button className="settings-save" onClick={() => void save()} disabled={locked || !!busy || !valid}>{busy === "save" ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}保存</button></div></footer>
    </section>
  );
}