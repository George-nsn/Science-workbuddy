"use client";

import "@milkdown/crepe/theme/common/style.css";
import "@milkdown/crepe/theme/frame.css";

import type { Crepe } from "@milkdown/crepe";
import type { MutableRefObject } from "react";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

import type { WorkbenchAttachment } from "@/lib/api";

type InsertAction = typeof import("@milkdown/kit/utils").insert;
type ReplaceAllAction = typeof import("@milkdown/kit/utils").replaceAll;
type EditorViewContext = typeof import("@milkdown/kit/core").editorViewCtx;
type HistoryCommand = typeof import("@milkdown/kit/prose/history").undo;
type SetBlockType = typeof import("@milkdown/kit/prose/commands").setBlockType;
type LiftListItem = typeof import("@milkdown/kit/prose/schema-list").liftListItem;

type EditorRuntime = {
  editorViewCtx: EditorViewContext;
  redo: HistoryCommand;
  setBlockType: SetBlockType;
  undo: HistoryCommand;
};

export type MarkdownEditorHandle = {
  getMarkdown: () => string;
  insertMarkdown: (value: string) => void;
  redo: () => boolean;
  setBlockType: (level: 0 | 1 | 2 | 3) => boolean;
  setMarkdown: (value: string) => void;
  undo: () => boolean;
};

type MarkdownEditorProps = {
  initialMarkdown: string;
  onChange: (value: string) => void;
  plotAttachments?: WorkbenchAttachment[];
  onDeletePlot?: (attachment: WorkbenchAttachment) => void;
  onEditPlot?: (attachment: WorkbenchAttachment) => void;
};

type PlotCallbacks = Pick<
  MarkdownEditorProps,
  "onDeletePlot" | "onEditPlot"
>;

export const MarkdownEditor = forwardRef<MarkdownEditorHandle, MarkdownEditorProps>(
  ({
    initialMarkdown,
    onChange,
    plotAttachments = [],
    onDeletePlot,
    onEditPlot,
  }, ref) => {
    const rootRef = useRef<HTMLDivElement>(null);
    const crepeRef = useRef<Crepe | null>(null);
    const insertActionRef = useRef<InsertAction | null>(null);
    const replaceAllActionRef = useRef<ReplaceAllAction | null>(null);
    const runtimeRef = useRef<EditorRuntime | null>(null);
    const initialMarkdownRef = useRef(initialMarkdown);
    const uninstallPlotCardsRef = useRef<(() => void) | null>(null);
    const plotAttachmentsRef = useRef(plotAttachments);
    const plotCallbacksRef = useRef({ onDeletePlot, onEditPlot });

    plotAttachmentsRef.current = plotAttachments;
    plotCallbacksRef.current = { onDeletePlot, onEditPlot };

    useImperativeHandle(ref, () => ({
      getMarkdown: () => crepeRef.current?.getMarkdown() ?? initialMarkdownRef.current,
      insertMarkdown: (value: string) => {
        const crepe = crepeRef.current;
        const insertAction = insertActionRef.current;
        if (crepe && insertAction) crepe.editor.action(insertAction(value));
      },
      setMarkdown: (value: string) => {
        const crepe = crepeRef.current;
        const replaceAllAction = replaceAllActionRef.current;
        if (crepe && replaceAllAction) crepe.editor.action(replaceAllAction(value, true));
      },
      undo: () => runHistoryCommand(crepeRef.current, runtimeRef.current, "undo"),
      redo: () => runHistoryCommand(crepeRef.current, runtimeRef.current, "redo"),
      setBlockType: (level) => setCurrentBlockType(
        crepeRef.current,
        runtimeRef.current,
        level,
      ),
    }), []);

    useEffect(() => {
      const root = rootRef.current;
      if (!root) return;
      let disposed = false;
      const setup = async () => {
        const vueGlobal = globalThis as typeof globalThis & {
          __VUE_OPTIONS_API__?: boolean;
          __VUE_PROD_DEVTOOLS__?: boolean;
          __VUE_PROD_HYDRATION_MISMATCH_DETAILS__?: boolean;
        };
        vueGlobal.__VUE_OPTIONS_API__ = true;
        vueGlobal.__VUE_PROD_DEVTOOLS__ = false;
        vueGlobal.__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ = false;
        const [
          { Crepe: CrepeConstructor },
          { $prose, insert, replaceAll },
          { editorViewCtx },
          { Plugin, NodeSelection },
          { redo, undo },
          { setBlockType },
          { liftListItem },
        ] = await Promise.all([
          import("@milkdown/crepe"),
          import("@milkdown/kit/utils"),
          import("@milkdown/kit/core"),
          import("@milkdown/kit/prose/state"),
          import("@milkdown/kit/prose/history"),
          import("@milkdown/kit/prose/commands"),
          import("@milkdown/kit/prose/schema-list"),
        ]);
        if (disposed) return;
        const intuitiveBlockKeys = $prose(() => new Plugin({
          props: {
            handleKeyDown(view, event) {
              const modifier = event.ctrlKey || event.metaKey;
              const key = event.key.toLowerCase();
              if (modifier && !event.altKey && key === "z") {
                const handled = (event.shiftKey ? redo : undo)(
                  view.state,
                  view.dispatch,
                  view,
                );
                if (handled) event.preventDefault();
                return handled;
              }
              if (modifier && !event.altKey && key === "y") {
                const handled = redo(view.state, view.dispatch, view);
                if (handled) event.preventDefault();
                return handled;
              }
              if (modifier && !event.altKey && /^[0-3]$/.test(key)) {
                const level = Number(key) as 0 | 1 | 2 | 3;
                const handled = changeBlockType(view, level, setBlockType);
                if (handled) event.preventDefault();
                return handled;
              }
              if (event.key === "Escape") {
                return selectCurrentBlock(view, event, NodeSelection);
              }
              if (
                (event.key === "Backspace" || event.key === "Enter")
                && handleEmptyListItem(view, event, liftListItem)
              ) {
                return true;
              }
              if (event.key !== "Backspace" && event.key !== "Delete") return false;
              return handleHeadingDeletion(view, event, NodeSelection, setBlockType);
            },
          },
        }));
        const crepe = new CrepeConstructor({
          root,
          defaultValue: initialMarkdownRef.current,
          features: {
            [CrepeConstructor.Feature.AI]: false,
            [CrepeConstructor.Feature.TopBar]: false,
          },
        });
        crepe.on((listener) => {
          listener.markdownUpdated((_ctx, value) => onChange(value));
        });
        crepe.editor.use(intuitiveBlockKeys);
        crepeRef.current = crepe;
        insertActionRef.current = insert;
        replaceAllActionRef.current = replaceAll;
        runtimeRef.current = { editorViewCtx, redo, setBlockType, undo };
        await crepe.create();
        const uninstallPlotCards = installPlotCardInteractions(
          root,
          plotAttachmentsRef,
          plotCallbacksRef,
        );
        uninstallPlotCardsRef.current = uninstallPlotCards;
        if (disposed) uninstallPlotCards();
        if (disposed) await crepe.destroy();
      };
      void setup();
      return () => {
        disposed = true;
        uninstallPlotCardsRef.current?.();
        uninstallPlotCardsRef.current = null;
        const crepe = crepeRef.current;
        crepeRef.current = null;
        insertActionRef.current = null;
        replaceAllActionRef.current = null;
        runtimeRef.current = null;
        if (crepe) void crepe.destroy();
      };
    }, [onChange]);

    useEffect(() => {
      if (rootRef.current) {
        syncPlotCards(
          rootRef.current,
          plotAttachmentsRef.current,
        );
      }
    }, [plotAttachments]);

    return <div className="markdown-preview markdown-preview-editable" ref={rootRef} />;
  },
);

MarkdownEditor.displayName = "MarkdownEditor";

function attachmentIdFromImage(image: HTMLImageElement): string | null {
  return image.src.match(/\/workbench\/attachments\/([^/]+)\/content/)?.[1] ?? null;
}

function markdownTableSignature(markdown: string | null): string {
  if (!markdown) return "";
  return markdown
    .split("\n")
    .filter((line, index) => index !== 1 && line.trim().startsWith("|"))
    .flatMap((line) => line.trim().replace(/^\||\|$/g, "").split("|"))
    .map((cell) => cell.trim())
    .join("\u241f");
}

function tableBlockSignature(block: HTMLElement): string {
  return [...block.querySelectorAll("tr")]
    .flatMap((row) => [...row.querySelectorAll("th, td")])
    .map((cell) => cell.textContent?.trim() ?? "")
    .join("\u241f");
}

function associateSourceTable(
  root: HTMLElement,
  plotBlock: HTMLElement,
  attachment: WorkbenchAttachment,
): boolean {
  const tables = [...root.querySelectorAll<HTMLElement>(".milkdown-table-block")];
  const alreadyAssociated = tables.some((table) => (
    table.dataset.plotSourceIds?.split(" ").includes(attachment.id)
  ));
  if (alreadyAssociated) return true;
  const signature = markdownTableSignature(attachment.source_table_markdown);
  if (!signature) return true;
  const candidates = tables.filter((table) => tableBlockSignature(table) === signature);
  const preceding = candidates.filter((table) => (
    Boolean(table.compareDocumentPosition(plotBlock) & Node.DOCUMENT_POSITION_FOLLOWING)
  ));
  const table = preceding.at(-1) ?? candidates[0];
  if (!table) return false;
  const sourceIds = new Set(table.dataset.plotSourceIds?.split(" ").filter(Boolean) ?? []);
  sourceIds.add(attachment.id);
  table.dataset.plotSourceIds = [...sourceIds].join(" ");
  return true;
}

function relatedTableBlock(plotBlock: HTMLElement): HTMLElement | null {
  const attachmentId = plotBlock.dataset.plotAttachmentId;
  const root = plotBlock.closest<HTMLElement>(".ProseMirror");
  if (attachmentId && root) {
    const associated = [...root.querySelectorAll<HTMLElement>(".milkdown-table-block")]
      .find((table) => table.dataset.plotSourceIds?.split(" ").includes(attachmentId));
    if (associated) return associated;
  }
  let sibling = plotBlock.previousElementSibling;
  while (sibling) {
    if (sibling instanceof HTMLElement && sibling.classList.contains("milkdown-table-block")) {
      return sibling;
    }
    if (
      sibling instanceof HTMLElement
      && (sibling.dataset.plotAttachmentId || sibling.textContent?.trim())
    ) break;
    sibling = sibling.previousElementSibling;
  }
  return null;
}

function setPlotDisplay(plotBlock: HTMLElement, showTable: boolean): void {
  const tableBlock = relatedTableBlock(plotBlock);
  if (
    tableBlock
    && tableBlock.classList.contains("plot-source-table-hidden") === showTable
  ) {
    tableBlock.classList.toggle("plot-source-table-hidden", !showTable);
  }
  const showTableValue = String(showTable);
  if (plotBlock.dataset.showTable !== showTableValue) {
    plotBlock.dataset.showTable = showTableValue;
  }
  const toggle = plotBlock.querySelector<HTMLButtonElement>("[data-plot-action='table']");
  if (toggle) {
    const label = showTable ? "仅显示图" : "图 + 表";
    if (toggle.textContent !== label) toggle.textContent = label;
    if (toggle.getAttribute("aria-pressed") !== showTableValue) {
      toggle.setAttribute("aria-pressed", showTableValue);
    }
  }
}

function createPlotToolbar(attachment: WorkbenchAttachment): HTMLDivElement {
  const toolbar = document.createElement("div");
  toolbar.className = "notebook-plot-toolbar";
  toolbar.setAttribute("contenteditable", "false");
  toolbar.draggable = false;
  toolbar.innerHTML = [
    `<span>绘图 Agent · V${attachment.render_revision}</span>`,
    '<button type="button" data-plot-action="table" aria-pressed="false">图 + 表</button>',
    '<button type="button" data-plot-action="edit">编辑图形</button>',
    '<button type="button" data-plot-action="delete">删除</button>',
  ].join("");
  for (const button of toolbar.querySelectorAll("button")) button.draggable = false;
  return toolbar;
}

function syncPlotCards(
  root: HTMLElement,
  attachments: WorkbenchAttachment[],
): void {
  const byId = new Map(attachments.map((attachment) => [attachment.id, attachment]));
  for (const image of root.querySelectorAll<HTMLImageElement>("img")) {
    const attachmentId = attachmentIdFromImage(image);
    if (!attachmentId) continue;
    const attachment = byId.get(attachmentId);
    if (!attachment || attachment.attachment_kind !== "generated_plot") continue;
    const block = image.closest<HTMLElement>(".milkdown-image-block");
    const wrapper = image.closest<HTMLElement>(".image-wrapper");
    if (!block || !wrapper) continue;
    block.dataset.plotAttachmentId = attachment.id;
    block.classList.add("notebook-plot-card");
    // Preserve ProseMirror's native block drag/drop so moving the card also
    // updates the Markdown document order instead of applying a visual offset.
    block.draggable = true;
    block.title = "按住拖动图卡；双击打开图形编辑";
    if (
      block.dataset.plotSourceRevision !== String(attachment.render_revision)
      && associateSourceTable(root, block, attachment)
    ) {
      block.dataset.plotSourceRevision = String(attachment.render_revision);
    }
    if (
      block.dataset.plotRevision === String(attachment.render_revision)
      && block.querySelector(".notebook-plot-toolbar")
    ) {
      setPlotDisplay(block, block.dataset.showTable === "true");
      continue;
    }
    wrapper.classList.add("notebook-plot-resizable");
    const storedWidth = window.localStorage.getItem(`science-buddy.plot-width.${attachment.id}`);
    if (storedWidth) wrapper.style.width = storedWidth;
    image.src = image.src.replace(/(?:\?v=\d+)?$/, `?v=${attachment.render_revision}`);
    block.querySelector(".notebook-plot-toolbar")?.remove();
    block.prepend(createPlotToolbar(attachment));
    setPlotDisplay(block, block.dataset.showTable === "true");
    block.dataset.plotRevision = String(attachment.render_revision);
  }
}

function installPlotCardInteractions(
  root: HTMLElement,
  attachmentsRef: MutableRefObject<WorkbenchAttachment[]>,
  callbacksRef: MutableRefObject<PlotCallbacks>,
): () => void {
  let syncFrame: number | null = null;
  let disposed = false;
  const observedWrappers = new WeakSet<HTMLElement>();

  const resizeObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const wrapper = entry.target;
      if (!(wrapper instanceof HTMLElement)) continue;
      const block = wrapper.closest<HTMLElement>("[data-plot-attachment-id]");
      if (!block?.dataset.plotAttachmentId || !wrapper.style.width) continue;
      const storageKey = `science-buddy.plot-width.${block.dataset.plotAttachmentId}`;
      if (window.localStorage.getItem(storageKey) !== wrapper.style.width) {
        window.localStorage.setItem(storageKey, wrapper.style.width);
      }
    }
  });

  const syncAndObserve = () => {
    syncFrame = null;
    if (disposed) return;
    syncPlotCards(root, attachmentsRef.current);
    for (const wrapper of root.querySelectorAll<HTMLElement>(".notebook-plot-resizable")) {
      if (observedWrappers.has(wrapper)) continue;
      observedWrappers.add(wrapper);
      resizeObserver.observe(wrapper);
    }
  };

  const scheduleSync = () => {
    if (disposed || syncFrame !== null) return;
    syncFrame = window.requestAnimationFrame(syncAndObserve);
  };

  const observer = new MutationObserver(scheduleSync);
  observer.observe(root, { childList: true, subtree: true });
  syncAndObserve();

  const click = (event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const button = target.closest<HTMLButtonElement>("[data-plot-action]");
    if (!button) return;
    const block = button.closest<HTMLElement>("[data-plot-attachment-id]");
    const attachment = attachmentsRef.current.find(
      (item) => item.id === block?.dataset.plotAttachmentId,
    );
    if (!block || !attachment) return;
    event.preventDefault();
    event.stopPropagation();
    if (button.dataset.plotAction === "table") {
      setPlotDisplay(block, block.dataset.showTable !== "true");
    } else if (button.dataset.plotAction === "edit") {
      callbacksRef.current.onEditPlot?.(attachment);
    } else if (button.dataset.plotAction === "delete") {
      callbacksRef.current.onDeletePlot?.(attachment);
    }
  };

  const doubleClick = (event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const directBlock = target.closest<HTMLElement>("[data-plot-attachment-id]");
    const coordinateBlock = event instanceof MouseEvent
      ? [...root.querySelectorAll<HTMLElement>("[data-plot-attachment-id]")].find((item) => {
        const bounds = item.getBoundingClientRect();
        return event.clientX >= bounds.left
          && event.clientX <= bounds.right
          && event.clientY >= bounds.top
          && event.clientY <= bounds.bottom;
      })
      : undefined;
    const block = directBlock ?? coordinateBlock;
    const attachment = attachmentsRef.current.find(
      (item) => item.id === block?.dataset.plotAttachmentId,
    );
    if (!block || !attachment) return;
    event.preventDefault();
    event.stopPropagation();
    callbacksRef.current.onEditPlot?.(attachment);
  };

  const mouseDown = (event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.closest(".notebook-plot-toolbar")) return;
    event.stopImmediatePropagation();
  };

  const dragStart = (event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const block = target.closest<HTMLElement>("[data-plot-attachment-id]");
    if (
      !block
      || target.closest(".notebook-plot-toolbar")
      || target.closest(".notebook-plot-resize-zone")
    ) return;
    block.classList.add("dragging");
  };

  const dragEnd = () => {
    for (const block of root.querySelectorAll(".notebook-plot-card.dragging")) {
      block.classList.remove("dragging");
    }
  };

  root.addEventListener("click", click, true);
  root.addEventListener("dblclick", doubleClick, true);
  root.addEventListener("mousedown", mouseDown, true);
  root.addEventListener("pointerdown", mouseDown, true);
  root.addEventListener("dragstart", dragStart, true);
  root.addEventListener("dragend", dragEnd, true);
  return () => {
    disposed = true;
    observer.disconnect();
    if (syncFrame !== null) window.cancelAnimationFrame(syncFrame);
    resizeObserver.disconnect();
    root.removeEventListener("click", click, true);
    root.removeEventListener("dblclick", doubleClick, true);
    root.removeEventListener("mousedown", mouseDown, true);
    root.removeEventListener("pointerdown", mouseDown, true);
    root.removeEventListener("dragstart", dragStart, true);
    root.removeEventListener("dragend", dragEnd, true);
  };
}

function runHistoryCommand(
  crepe: Crepe | null,
  runtime: EditorRuntime | null,
  direction: "undo" | "redo",
): boolean {
  if (!crepe || !runtime) return false;
  return crepe.editor.action((ctx) => {
    const view = ctx.get(runtime.editorViewCtx);
    return runtime[direction](view.state, view.dispatch, view);
  });
}

function setCurrentBlockType(
  crepe: Crepe | null,
  runtime: EditorRuntime | null,
  level: 0 | 1 | 2 | 3,
): boolean {
  if (!crepe || !runtime) return false;
  return crepe.editor.action((ctx) => {
    const view = ctx.get(runtime.editorViewCtx);
    return changeBlockType(view, level, runtime.setBlockType);
  });
}

function changeBlockType(
  view: Parameters<HistoryCommand>[2],
  level: 0 | 1 | 2 | 3,
  setBlockType: SetBlockType,
): boolean {
  if (!view) return false;
  const nodeType = level === 0
    ? view.state.schema.nodes.paragraph
    : view.state.schema.nodes.heading;
  if (!nodeType) return false;
  return setBlockType(nodeType, level === 0 ? undefined : { level })(
    view.state,
    view.dispatch,
    view,
  );
}

function handleHeadingDeletion(
  view: NonNullable<Parameters<HistoryCommand>[2]>,
  event: KeyboardEvent,
  NodeSelection: typeof import("@milkdown/kit/prose/state").NodeSelection,
  setBlockType: SetBlockType,
): boolean {
  const { state } = view;
  const { selection } = state;
  const selectedBlock = selection instanceof NodeSelection;
  if (selectedBlock) {
    event.preventDefault();
    return removeBlock(view, selection.from, selection.to);
  }

  const { $from, $to } = selection;
  const heading = $from.parent;
  if (heading.type.name !== "heading" || !$from.sameParent($to)) return false;
  const emptyHeading = heading.content.size === 0;
  const backspaceAtStart = event.key === "Backspace"
    && selection.empty
    && $from.parentOffset === 0;
  if (emptyHeading || backspaceAtStart) {
    const paragraph = state.schema.nodes.paragraph;
    if (!paragraph) return false;
    event.preventDefault();
    return setBlockType(paragraph)(state, view.dispatch, view);
  }
  return false;
}

function handleEmptyListItem(
  view: NonNullable<Parameters<HistoryCommand>[2]>,
  event: KeyboardEvent,
  liftListItem: LiftListItem,
): boolean {
  const { state } = view;
  const { selection } = state;
  if (!selection.empty) return false;
  const { $from } = selection;
  if (!$from.parent.isTextblock || $from.parent.content.size !== 0) return false;
  if (event.key === "Backspace" && $from.parentOffset !== 0) return false;
  let listItemType = null;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const node = $from.node(depth);
    if (node.type.name === "list_item") {
      listItemType = node.type;
      break;
    }
  }
  if (!listItemType) return false;
  const handled = liftListItem(listItemType)(state, view.dispatch, view);
  if (handled) event.preventDefault();
  return handled;
}

function selectCurrentBlock(
  view: NonNullable<Parameters<HistoryCommand>[2]>,
  event: KeyboardEvent,
  NodeSelection: typeof import("@milkdown/kit/prose/state").NodeSelection,
): boolean {
  const { $from } = view.state.selection;
  if (!view.state.selection.empty || $from.depth !== 1 || !$from.parent.isTextblock) {
    return false;
  }
  event.preventDefault();
  view.dispatch(
    view.state.tr.setSelection(NodeSelection.create(view.state.doc, $from.before())),
  );
  return true;
}

function removeBlock(
  view: NonNullable<Parameters<HistoryCommand>[2]>,
  from: number,
  to: number,
): boolean {
  const { state } = view;
  const paragraph = state.schema.nodes.paragraph;
  if (!paragraph) return false;
  const containingBlock = state.doc.resolve(from).parent;
  const transaction = containingBlock.childCount === 1
    ? state.tr.replaceWith(from, to, paragraph.create())
    : state.tr.delete(from, to);
  view.dispatch(transaction.scrollIntoView());
  return true;
}
