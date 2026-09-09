"use client";

import { BookPlus, Check, ExternalLink, LoaderCircle, Search, ShieldCheck } from "lucide-react";
import { FormEvent, useState } from "react";

import {
  apiRequest,
  type LiteratureItem,
  type LiteratureSearchResponse,
  type SourceReference,
} from "@/lib/api";

const providerLabels = {
  pubmed: "PubMed",
  europe_pmc: "Europe PMC",
  openalex: "OpenAlex",
  crossref: "Crossref",
} as const;
type ProviderName = keyof typeof providerLabels;

export function SearchWorkbench() {
  const [query, setQuery] = useState("");
  const [providers, setProviders] = useState<ProviderName[]>([
    "pubmed", "europe_pmc", "openalex", "crossref",
  ]);
  const [results, setResults] = useState<LiteratureItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);
  const [imported, setImported] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  function toggleProvider(provider: ProviderName) {
    setProviders((current) =>
      current.includes(provider)
        ? current.length === 1
          ? current
          : current.filter((value) => value !== provider)
        : [...current, provider],
    );
  }

  async function searchLiterature(event: FormEvent) {
    event.preventDefault();
    if (query.trim().length < 2) return;
    setSearching(true);
    setError(null);
    try {
      const response = await apiRequest<LiteratureSearchResponse>("/literature/search", {
        method: "POST",
        body: JSON.stringify({ query: query.trim(), providers, limit: 20 }),
      });
      setResults(response.items);
    } catch (value) {
      setError(value instanceof Error ? value.message : "文献检索失败");
    } finally {
      setSearching(false);
    }
  }

  async function importPaper(key: string, references: SourceReference[]) {
    setImporting(key);
    setError(null);
    try {
      await apiRequest("/literature/import", {
        method: "POST",
        body: JSON.stringify({ references }),
      });
      setImported((current) => new Set(current).add(key));
    } catch (value) {
      setError(value instanceof Error ? value.message : "文献入库失败");
    } finally {
      setImporting(null);
    }
  }

  return (
    <>
      <header className="page-header">
        <div>
          <span className="eyebrow">LITERATURE DISCOVERY</span>
          <h1>跨来源文献检索</h1>
          <p>检索 PubMed、Europe PMC、OpenAlex 与 Crossref，并在入库前合并相同 PMID/DOI 的记录。</p>
        </div>
        <div className="boundary-badge"><ShieldCheck size={16} /> 摘要与全文证据明确分层</div>
      </header>

      <section className="search-panel">
        <form onSubmit={searchLiterature}>
          <div className="search-box">
            <Search size={20} />
            <input
              aria-label="科研问题或 PubMed 查询式"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="例如：BRAF V600E AND papillary thyroid carcinoma prognosis"
              value={query}
            />
            <button disabled={searching || query.trim().length < 2} type="submit">
              {searching ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />}
              {searching ? "检索中" : "检索"}
            </button>
          </div>
          <div className="source-options">
            {(Object.keys(providerLabels) as ProviderName[]).map((provider) => (
              <button
                className={providers.includes(provider) ? "selected" : ""}
                key={provider}
                onClick={() => toggleProvider(provider)}
                type="button"
              >
                <span className="check-box">{providers.includes(provider) && <Check size={12} />}</span>
                {providerLabels[provider]}
              </button>
            ))}
            <span>四个来源独立限流；相同 PMID/DOI 自动合并。</span>
          </div>
        </form>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="results-section">
        <div className="results-heading">
          <div><span className="eyebrow">SEARCH RESULTS</span><h2>候选文献</h2></div>
          <span>{results.length ? `${results.length} 条去重结果` : "输入问题开始检索"}</span>
        </div>
        <div className="result-list">
          {results.map((item) => {
            const isImported = imported.has(item.key);
            const isImporting = importing === item.key;
            return (
              <article className="paper-card" key={item.key}>
                <div className="paper-meta">
                  <span className={`depth-pill ${item.is_open_access ? "fulltext" : "abstract"}`}>
                    {item.is_open_access ? "开放全文" : "摘要证据"}
                  </span>
                  <span>{item.publication_year ?? "年份未知"}</span>
                  <span>{item.journal ?? "期刊未知"}</span>
                </div>
                <h3>{item.title}</h3>
                <p className="authors">
                  {item.authors.slice(0, 5).map((author) => author.full_name).join(" · ") || "作者未知"}
                </p>
                <p className="abstract-preview">
                  {item.abstract ?? "当前来源没有提供摘要。入库后只能作为元数据记录，不能作为全文证据。"}
                </p>
                <div className="paper-identifiers">
                  {item.pmid && <span>PMID {item.pmid}</span>}
                  {item.pmcid && <span>{item.pmcid}</span>}
                  {item.doi && <span>DOI {item.doi}</span>}
                </div>
                <footer>
                  <div className="source-pills">
                    {item.sources.map((source) => (
                      <span key={`${source.provider}:${source.source_id}`}>{providerLabels[source.provider]}</span>
                    ))}
                  </div>
                  <div className="paper-actions">
                    {item.full_text_url && (
                      <a href={item.full_text_url} rel="noreferrer" target="_blank">
                        来源 <ExternalLink size={14} />
                      </a>
                    )}
                    <button
                      disabled={isImported || isImporting}
                      onClick={() => importPaper(item.key, item.sources)}
                    >
                      {isImporting ? <LoaderCircle className="spin" size={15} /> : isImported ? <Check size={15} /> : <BookPlus size={15} />}
                      {isImporting ? "入库中" : isImported ? "已入库" : "导入文献库"}
                    </button>
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      </section>
    </>
  );
}
