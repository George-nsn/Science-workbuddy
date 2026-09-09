"use client";

import { Maximize2, Minimize2, Move, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useId, useRef, useState, type MouseEvent } from "react";

export function MermaidDiagram({ code }: Readonly<{ code: string }>) {
  const elementRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "");

  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [posStart, setPosStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    let active = true;
    const element = elementRef.current;
    const render = async () => {
      if (!element) return;
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: {
            background: "transparent",
            primaryColor: "#e5f2e9",
            primaryTextColor: "#18342b",
            primaryBorderColor: "#3a8065",
            secondaryColor: "#eaf0f5",
            secondaryTextColor: "#24404a",
            secondaryBorderColor: "#668594",
            tertiaryColor: "#f7f1df",
            tertiaryTextColor: "#5e502a",
            tertiaryBorderColor: "#aa9660",
            lineColor: "#668177",
            textColor: "#273a33",
            edgeLabelBackground: "#fbfcf8",
            clusterBkg: "#f5f8f4",
            clusterBorder: "#c4d4ca",
            fontFamily: "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif",
            fontSize: "15px",
          },
          themeCSS: `
            .node rect, .node polygon, .node circle, .node ellipse {
              filter: drop-shadow(0 4px 10px rgba(31, 68, 55, .12));
              stroke-width: 1.8px;
            }
            .nodeLabel { font-weight: 600; font-size: 14px; }
            .edgeLabel { color: #496157; font-size: 13px; font-weight: 500; }
            .cluster rect { rx: 12px; ry: 12px; stroke-dasharray: 4 3; stroke-width: 1.5px; }
            .cluster-label { color: #246b52; font-weight: 700; font-size: 15px; }
            .flowchart-link { stroke-width: 2px; }
          `,
          flowchart: {
            htmlLabels: false,
            useMaxWidth: false,
            curve: "basis",
            nodeSpacing: 48,
            rankSpacing: 68,
            padding: 20,
            diagramPadding: 20,
          },
        });
        const result = await mermaid.render(`brainstorm-${id}`, code);
        if (active) {
          element.innerHTML = result.svg;
          const svgEl = element.querySelector("svg");
          if (svgEl) {
            svgEl.style.maxWidth = "none";
          }
        }
      } catch {
        if (active) {
          element.textContent = "技术路线图无法渲染，请查看文本方案。";
        }
      }
    };
    void render();
    return () => {
      active = false;
      if (element) element.textContent = "";
    };
  }, [code, id]);

  function handleZoomIn() {
    setScale((prev) => Math.min(3.0, +(prev + 0.25).toFixed(2)));
  }

  function handleZoomOut() {
    setScale((prev) => Math.max(0.4, +(prev - 0.25).toFixed(2)));
  }

  function handleReset() {
    setScale(1);
    setPosition({ x: 0, y: 0 });
  }

  function handleMouseDown(event: MouseEvent<HTMLDivElement>) {
    // Only drag with left click
    if (event.button !== 0) return;
    setIsDragging(true);
    setDragStart({ x: event.clientX, y: event.clientY });
    setPosStart({ x: position.x, y: position.y });
  }

  function handleMouseMove(event: MouseEvent<HTMLDivElement>) {
    if (!isDragging) return;
    const dx = event.clientX - dragStart.x;
    const dy = event.clientY - dragStart.y;
    setPosition({ x: posStart.x + dx, y: posStart.y + dy });
  }

  function handleMouseUp() {
    setIsDragging(false);
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const onWheel = (e: globalThis.WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY < 0 ? 0.15 : -0.15;
      setScale((prev) => Math.min(3.0, Math.max(0.4, +(prev + delta).toFixed(2))));
    };
    container.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      container.removeEventListener("wheel", onWheel);
    };
  }, []);

  return (
    <figure className={`mermaid-figure ${isFullscreen ? "fullscreen" : ""}`}>
      <figcaption className="mermaid-figcaption">
        <div className="mermaid-meta">
          <span>END-TO-END WORKFLOW</span>
          <strong>研究技术路线</strong>
          <small>假设 → 验证 → 分析 → 决策 → 复现</small>
        </div>
        <div className="mermaid-controls" role="toolbar" aria-label="路线图缩放与平移控制">
          <span className="mermaid-scale-badge">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            className="mermaid-ctrl-btn"
            onClick={handleZoomIn}
            title="放大 (Zoom In)"
            aria-label="放大"
          >
            <ZoomIn size={14} />
            <span>放大</span>
          </button>
          <button
            type="button"
            className="mermaid-ctrl-btn"
            onClick={handleZoomOut}
            title="缩小 (Zoom Out)"
            aria-label="缩小"
          >
            <ZoomOut size={14} />
            <span>缩小</span>
          </button>
          <button
            type="button"
            className="mermaid-ctrl-btn"
            onClick={handleReset}
            title="移动 / 还原视图 (Reset & Pan)"
            aria-label="移动/还原"
          >
            <Move size={14} />
            <span>移动/还原</span>
          </button>
          <button
            type="button"
            className="mermaid-ctrl-btn icon-only"
            onClick={() => setIsFullscreen((prev) => !prev)}
            title={isFullscreen ? "退出全屏" : "全屏查看"}
            aria-label={isFullscreen ? "退出全屏" : "全屏查看"}
          >
            {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </figcaption>
      <div
        ref={containerRef}
        className={`mermaid-viewport ${isDragging ? "dragging" : ""}`}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onDoubleClick={handleReset}
        title="按住鼠标左键可自由拖拽移动，滚轮可缩放，双击复位"
      >
        <div
          aria-label="技术路线图"
          className="mermaid-canvas"
          ref={elementRef}
          style={{
            transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
            transformOrigin: "center center",
            transition: isDragging ? "none" : "transform 0.12s ease-out",
          }}
        />
      </div>
    </figure>
  );
}
