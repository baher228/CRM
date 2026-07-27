import { useCallback, useEffect, useRef, useState } from "react";

import { api, unwrapPage } from "./api";

export function useResource(path, { enabled = true, list = true, query, pageSize = 50 } = {}) {
  const [state, setState] = useState({
    data: list ? [] : null,
    loading: enabled,
    error: null,
    nextCursor: null,
  });
  const [page, setPage] = useState(1);
  const [cursor, setCursor] = useState(null);
  const cursorHistory = useRef([null]);
  const requestSequence = useRef(0);
  const queryKey = JSON.stringify(query || {});

  const load = useCallback(async (signal, queryOverride = query, cursorOverride = cursor) => {
    if (!enabled) return;
    const requestId = ++requestSequence.current;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const requestQuery = list
        ? {
            ...(queryOverride || {}),
            limit: queryOverride?.limit || pageSize,
            ...(cursorOverride ? { cursor: cursorOverride } : {}),
          }
        : queryOverride;
      const payload = await api.get(path, { signal, query: requestQuery });
      if (requestId === requestSequence.current) {
        const pagePayload = list ? unwrapPage(payload) : null;
        setState({
          data: list ? pagePayload.items : payload,
          loading: false,
          error: null,
          nextCursor: list ? pagePayload.nextCursor : null,
        });
      }
    } catch (error) {
      if (error.name !== "AbortError" && requestId === requestSequence.current) {
        setState((current) => ({ ...current, loading: false, error }));
      }
    }
  }, [path, list, queryKey, cursor, pageSize, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    cursorHistory.current = [null];
    setPage(1);
    setCursor(null);
  }, [path, list, queryKey]);

  useEffect(() => {
    if (!enabled) {
      setState({ data: list ? [] : null, loading: false, error: null, nextCursor: null });
      return undefined;
    }
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [enabled, list, load]);

  const nextPage = useCallback(() => {
    if (!state.nextCursor) return;
    cursorHistory.current[page] = state.nextCursor;
    setPage((current) => current + 1);
    setCursor(state.nextCursor);
  }, [page, state.nextCursor]);

  const previousPage = useCallback(() => {
    if (page <= 1) return;
    const previous = page - 1;
    setPage(previous);
    setCursor(cursorHistory.current[previous - 1] || null);
  }, [page]);

  const mutate = useCallback((updater) => {
    setState((current) => ({
      ...current,
      data: typeof updater === "function" ? updater(current.data) : updater,
    }));
  }, []);

  return {
    ...state,
    page,
    pageSize: Number(query?.limit || pageSize),
    hasNext: Boolean(state.nextCursor),
    hasPrevious: page > 1,
    nextPage,
    previousPage,
    mutate,
    reload: (...args) => load(undefined, args.length ? args[0] : query, cursor),
  };
}

export function useDocumentTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · CRM Workspace` : "CRM Workspace";
  }, [title]);
}
