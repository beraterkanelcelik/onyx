import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  setCursorToEnd as setCursorToEndUtil,
  setCursorAfterNode,
  insertTextAtCursor as insertTextAtCursorUtil,
  insertNodeAtCursor as insertNodeAtCursorUtil,
  createRichInputTileNode,
  getAdjacentPasteTile,
  getTextContent,
  shouldCreatePasteTile,
} from "@/lib/contentEditable";

export interface UseContentEditableOptions {
  initialContent?: string;
  wrapperRef: React.RefObject<HTMLDivElement | null>;
  minHeight?: number;
  maxHeight?: number;
  onContentChange?: (text: string) => void;
}

export interface UseContentEditableReturn {
  ref: React.RefObject<HTMLDivElement | null>;
  message: string;
  isEmpty: boolean;
  setContent: (text: string) => void;
  clearContent: () => void;
  handleInput: (event: React.FormEvent<HTMLDivElement>) => void;
  handleCompositionStart: () => void;
  handleCompositionEnd: () => void;
  insertTextAtCursor: (text: string) => void;
  insertTileAtCursor: (text: string) => void;
  pasteText: (text: string) => void;
  handleCopy: (event: React.ClipboardEvent<HTMLDivElement>) => void;
  handleCut: (event: React.ClipboardEvent<HTMLDivElement>) => void;
  setCursorToEnd: () => void;
  resize: () => void;
  handleTileMouseDown: (event: React.MouseEvent<HTMLDivElement>) => void;
  handleTileClick: (event: React.MouseEvent<HTMLDivElement>) => void;
  handleTileKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => boolean;
  tilePopover: { text: string; rect: DOMRect; tile: HTMLElement } | null;
  dismissTilePopover: () => void;
  updateTileText: (newText: string) => void;
}

export function useContentEditable({
  initialContent = "",
  wrapperRef,
  minHeight = 44,
  maxHeight = 200,
  onContentChange,
}: UseContentEditableOptions): UseContentEditableReturn {
  const ref = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState(initialContent);
  const isEmpty = useMemo(() => !message, [message]);
  const isComposingRef = useRef(false);
  const onContentChangeRef = useRef(onContentChange);
  const rafRef = useRef<number | null>(null);
  const wrapperPaddingYRef = useRef(0);
  const selectedTileRef = useRef<HTMLElement | null>(null);
  const [tilePopover, setTilePopover] = useState<{
    text: string;
    rect: DOMRect;
    tile: HTMLElement;
  } | null>(null);

  useEffect(() => {
    onContentChangeRef.current = onContentChange;
  }, [onContentChange]);

  useEffect(() => {
    if (wrapperRef.current) {
      const cs = getComputedStyle(wrapperRef.current);
      wrapperPaddingYRef.current =
        parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    }
  }, [wrapperRef]);

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
    };
  }, []);

  // Track text selection to highlight tiles within the selection range.
  useEffect(() => {
    function handleSelectionChange() {
      const el = ref.current;
      if (!el || !el.contains(document.activeElement ?? null)) return;

      const sel = window.getSelection();
      const tiles = el.querySelectorAll("[data-paste-tile]");
      tiles.forEach((tile) => {
        const htmlTile = tile as HTMLElement;
        if (
          sel &&
          sel.rangeCount > 0 &&
          !sel.isCollapsed &&
          sel.getRangeAt(0).intersectsNode(tile)
        ) {
          htmlTile.classList.add("rich-input-tile-in-selection");
        } else {
          htmlTile.classList.remove("rich-input-tile-in-selection");
        }
      });
    }

    document.addEventListener("selectionchange", handleSelectionChange);
    return () =>
      document.removeEventListener("selectionchange", handleSelectionChange);
  }, []);

  const clearTileSelection = useCallback(() => {
    if (selectedTileRef.current) {
      selectedTileRef.current.classList.remove("rich-input-tile-selected");
      selectedTileRef.current = null;
    }
  }, []);

  const resize = useCallback(() => {
    const wrapper = wrapperRef.current;
    const div = ref.current;
    if (!wrapper || !div) return;

    wrapper.style.height = `${minHeight}px`;
    const clamped = Math.min(
      Math.max(div.scrollHeight + wrapperPaddingYRef.current, minHeight),
      maxHeight
    );
    wrapper.style.height = `${clamped}px`;
  }, [wrapperRef, minHeight, maxHeight]);

  const updateEmpty = useCallback((el: HTMLElement, text: string) => {
    if (text) {
      el.removeAttribute("data-empty");
    } else {
      el.setAttribute("data-empty", "");
    }
  }, []);

  const syncFromDOM = useCallback(() => {
    const el = ref.current;
    if (!el) return;

    if (!isComposingRef.current && !el.textContent && el.innerHTML) {
      el.innerHTML = "";
    }

    const text = getTextContent(el);
    setMessage(text);
    updateEmpty(el, text);
    onContentChangeRef.current?.(text);
  }, [updateEmpty]);

  const handleInput = useCallback(
    (_event: React.FormEvent<HTMLDivElement>) => {
      if (isComposingRef.current) return;
      clearTileSelection();

      syncFromDOM();
      resize();
    },
    [syncFromDOM, resize, clearTileSelection]
  );

  const handleCompositionStart = useCallback(() => {
    isComposingRef.current = true;
    if (ref.current) {
      ref.current.removeAttribute("data-empty");
    }
  }, []);

  const handleCompositionEnd = useCallback(() => {
    isComposingRef.current = false;
    syncFromDOM();
    resize();
  }, [syncFromDOM, resize]);

  const setContent = useCallback(
    (text: string) => {
      if (!ref.current) return;

      clearTileSelection();
      setTilePopover(null);

      ref.current.textContent = text;
      setMessage(text);
      updateEmpty(ref.current, text);
      resize();
      onContentChangeRef.current?.(text);

      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        if (ref.current) {
          ref.current.focus();
          setCursorToEndUtil(ref.current);
        }
      });
    },
    [resize, updateEmpty]
  );

  const clearContent = useCallback(() => {
    if (!ref.current) return;

    clearTileSelection();
    setTilePopover(null);

    ref.current.innerHTML = "";
    setMessage("");
    updateEmpty(ref.current, "");
    resize();
    onContentChangeRef.current?.("");
  }, [resize, updateEmpty, clearTileSelection]);

  const insertTextAtCursor = useCallback(
    (text: string) => {
      if (!ref.current) return;
      insertTextAtCursorUtil(ref.current, text);
      syncFromDOM();
      resize();
    },
    [syncFromDOM, resize]
  );

  const insertTileAtCursor = useCallback(
    (text: string) => {
      if (!ref.current) return;
      const tile = createRichInputTileNode(text);
      insertNodeAtCursorUtil(ref.current, tile);

      const space = document.createTextNode(" ");
      tile.after(space);
      setCursorAfterNode(space);

      syncFromDOM();
      resize();
    },
    [syncFromDOM, resize]
  );

  const pasteText = useCallback(
    (text: string) => {
      if (shouldCreatePasteTile(text)) {
        insertTileAtCursor(text);
      } else {
        insertTextAtCursor(text);
      }
    },
    [insertTileAtCursor, insertTextAtCursor]
  );

  const handleTileMouseDown = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      clearTileSelection();

      const target = event.target as HTMLElement;
      const removeBtn = target.closest("[data-paste-tile-remove]");
      if (!removeBtn) return;

      event.preventDefault();
      const tile = removeBtn.closest("[data-paste-tile]");
      if (tile) {
        tile.remove();
        setTilePopover(null);
        syncFromDOM();
        resize();
      }
    },
    [syncFromDOM, resize, clearTileSelection]
  );

  const handleTileClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      const target = event.target as HTMLElement;
      if (target.closest("[data-paste-tile-remove]")) return;

      const tile = target.closest("[data-paste-tile]") as HTMLElement | null;
      if (tile) {
        const text = tile.getAttribute("data-text") ?? "";
        const rect = tile.getBoundingClientRect();
        setTilePopover({ text, rect, tile });
      } else {
        setTilePopover(null);
      }
    },
    []
  );

  const dismissTilePopover = useCallback(() => {
    setTilePopover(null);
    ref.current?.focus();
    if (selectedTileRef.current) {
      const s = window.getSelection();
      if (s) {
        const r = document.createRange();
        r.selectNode(selectedTileRef.current);
        s.removeAllRanges();
        s.addRange(r);
      }
    }
  }, []);

  const updateTileText = useCallback(
    (newText: string) => {
      if (!tilePopover?.tile || !ref.current?.contains(tilePopover.tile))
        return;
      const { tile } = tilePopover;
      tile.setAttribute("data-text", newText);
      tile.title = newText.length > 200 ? newText.slice(0, 200) + "…" : newText;

      const preview = tile.querySelector(".rich-input-tile-preview");
      if (preview) {
        const firstLine = newText.split("\n")[0]?.trim() ?? "";
        preview.textContent =
          firstLine.length > 20 ? firstLine.slice(0, 20) + "…" : firstLine;
      }
      const meta = tile.querySelector(".rich-input-tile-meta");
      if (meta) {
        const lines = newText.split("\n");
        meta.textContent =
          lines.length > 1
            ? `${lines.length} lines`
            : `${newText.length} chars`;
      }

      syncFromDOM();
    },
    [tilePopover, syncFromDOM]
  );

  const handleTileKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>): boolean => {
      const isNav = event.key === "ArrowLeft" || event.key === "ArrowRight";
      const isDelete = event.key === "Backspace" || event.key === "Delete";

      // Enter on selected tile → open popover
      if (event.key === "Enter" && selectedTileRef.current) {
        event.preventDefault();
        const tile = selectedTileRef.current;
        const text = tile.getAttribute("data-text") ?? "";
        const rect = tile.getBoundingClientRect();
        setTilePopover({ text, rect, tile });
        return true;
      }

      // Unrelated keys clear selection
      if (!isNav && !isDelete) {
        setTilePopover(null);
        clearTileSelection();
        return false;
      }

      setTilePopover(null);

      // If a tile is already selected, handle second press
      if (selectedTileRef.current) {
        const selected = selectedTileRef.current;

        if (isNav) {
          // Arrow on selected tile → deselect and move cursor past it
          event.preventDefault();
          clearTileSelection();
          if (event.key === "ArrowRight") {
            setCursorAfterNode(selected);
            const next = selected.nextSibling;
            if (next?.nodeType === Node.TEXT_NODE && next.textContent === " ") {
              setCursorAfterNode(next);
            }
          } else {
            const s = window.getSelection();
            if (s) {
              const r = document.createRange();
              r.setStartBefore(selected);
              r.collapse(true);
              s.removeAllRanges();
              s.addRange(r);
            }
          }
          return true;
        }

        if (isDelete) {
          const direction = event.key === "Backspace" ? "before" : "after";
          const adjacent = getAdjacentPasteTile(
            window.getSelection()!.getRangeAt(0),
            direction
          );
          if (adjacent === selected) {
            // Second delete press on same tile → remove it
            event.preventDefault();
            selected.remove();
            selectedTileRef.current = null;
            syncFromDOM();
            resize();
            return true;
          }
        }

        clearTileSelection();
        return false;
      }

      // No tile selected — check if cursor is adjacent to a tile
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) {
        return false;
      }

      const range = sel.getRangeAt(0);
      let direction: "before" | "after";
      if (isDelete) {
        direction = event.key === "Backspace" ? "before" : "after";
      } else {
        direction = event.key === "ArrowLeft" ? "before" : "after";
      }

      let tile = getAdjacentPasteTile(range, direction);

      // For arrow keys, skip whitespace-only text nodes (e.g. trailing space)
      if (!tile && isNav) {
        const { startContainer } = range;
        if (
          startContainer.nodeType === Node.TEXT_NODE &&
          startContainer.textContent?.trim() === ""
        ) {
          const sibling =
            direction === "before"
              ? startContainer.previousSibling
              : startContainer.nextSibling;
          if (
            sibling?.nodeType === Node.ELEMENT_NODE &&
            (sibling as HTMLElement).hasAttribute("data-paste-tile")
          ) {
            tile = sibling as HTMLElement;
          }
        }
      }

      if (!tile) return false;

      // First press: highlight the tile and select it to hide the caret
      event.preventDefault();
      tile.classList.add("rich-input-tile-selected");
      selectedTileRef.current = tile;
      const s = window.getSelection();
      if (s) {
        const r = document.createRange();
        r.selectNode(tile);
        s.removeAllRanges();
        s.addRange(r);
      }
      return true;
    },
    [syncFromDOM, resize, clearTileSelection]
  );

  const handleCopy = useCallback(
    (event: React.ClipboardEvent<HTMLDivElement>) => {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;

      const range = sel.getRangeAt(0);
      if (!ref.current?.contains(range.commonAncestorContainer)) return;

      event.preventDefault();
      const fragment = range.cloneContents();
      const temp = document.createElement("div");
      temp.appendChild(fragment);
      event.clipboardData.setData("text/plain", getTextContent(temp));
    },
    []
  );

  const handleCut = useCallback(
    (event: React.ClipboardEvent<HTMLDivElement>) => {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;

      const range = sel.getRangeAt(0);
      if (!ref.current?.contains(range.commonAncestorContainer)) return;

      event.preventDefault();
      const fragment = range.cloneContents();
      const temp = document.createElement("div");
      temp.appendChild(fragment);
      event.clipboardData.setData("text/plain", getTextContent(temp));

      range.deleteContents();
      syncFromDOM();
      resize();
    },
    [syncFromDOM, resize]
  );

  const setCursorToEnd = useCallback(() => {
    if (!ref.current) return;
    setCursorToEndUtil(ref.current);
  }, []);

  return {
    ref,
    message,
    isEmpty,
    setContent,
    clearContent,
    handleInput,
    handleCompositionStart,
    handleCompositionEnd,
    insertTextAtCursor,
    insertTileAtCursor,
    pasteText,
    handleCopy,
    handleCut,
    setCursorToEnd,
    resize,
    handleTileMouseDown,
    handleTileClick,
    handleTileKeyDown,
    tilePopover,
    dismissTilePopover,
    updateTileText,
  };
}
