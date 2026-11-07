/**
 * Folio — the library page for Hermes Desktop.
 *
 * One pane, three states: the library at rest, search results, and a reader that
 * shows the actual page with the matching sentences boxed. Every pixel of colour
 * comes from the app's theme vars, and the pane reads the same SQLite index the
 * agent tools write through the plugin's own `/api/plugins/folio` routes.
 */

import { Button, EmptyState, Input, ScrollArea, StatusDot, cn, haptic, host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const ID = 'folio'

let api = null

function relTime(seconds) {
  if (!seconds || seconds <= 0) return 'never'
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

function Pill({ children, tone }) {
  return jsx('span', {
    className: cn(
      'inline-flex items-center rounded bg-(--ui-fill-secondary) px-1.5 py-0.5 text-[0.6875rem] leading-none',
      tone === 'accent' ? 'text-(--ui-accent)' : 'text-(--ui-text-tertiary)'
    ),
    children
  })
}

function ResultRow({ item, active, onOpen }) {
  return jsxs('button', {
    type: 'button',
    onClick: () => onOpen(item),
    className: cn(
      'group flex w-full flex-col gap-1 rounded px-2 py-2 text-left transition-colors',
      active ? 'bg-(--ui-fill-secondary)' : 'hover:bg-(--chrome-action-hover)'
    ),
    children: [
      jsxs('div', {
        className: 'flex w-full items-center gap-2',
        children: [
          jsx(Pill, { tone: 'accent', children: item.locator || 'start' }),
          jsx('span', {
            className: 'truncate text-[0.8125rem] text-foreground',
            children: item.title
          }),
          jsx('span', {
            className: 'ml-auto shrink-0 text-[0.6875rem] text-(--ui-text-quaternary)',
            children: item.kind
          })
        ]
      }),
      jsx('div', {
        className: 'line-clamp-2 text-[0.75rem] leading-snug text-(--ui-text-tertiary)',
        children: item.snippet
      })
    ]
  })
}

function Reader({ page, onClose }) {
  const doc = page.doc || {}
  const [copied, setCopied] = useState(false)

  // Geometry arrives as 0..1 fractions. Older backends sent PDF points; accept
  // both so a pane and its backend can be upgraded independently.
  const rects = useMemo(() => {
    const raw = page.rects || []
    const width = page.width || 1
    const height = page.height || 1
    return raw.map((rect) =>
      rect.right <= 1.001 && rect.bottom <= 1.001
        ? rect
        : {
            left: rect.left / width,
            right: rect.right / width,
            top: rect.top / height,
            bottom: rect.bottom / height
          }
    )
  }, [page.rects, page.width, page.height])

  const citation = useMemo(() => {
    const author = (doc.author || '').trim()
    const year = (doc.year || '').trim() || 'n.d.'
    const title = (doc.title || '').trim() || '(untitled)'
    const where = page.locator ? `, ${page.locator}` : ''
    return author ? `${author} (${year}). ${title}${where}. ${doc.path}` : `${title} (${year})${where}. ${doc.path}`
  }, [doc, page.locator])

  const copy = useCallback(async () => {
    const ok = await api.os?.writeClipboard?.(citation)
    setCopied(ok !== false)
    haptic('tap')
  }, [citation])

  return jsxs('div', {
    className: 'flex min-h-0 flex-1 flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx('button', {
            type: 'button',
            onClick: onClose,
            className: 'text-[0.75rem] text-(--ui-text-tertiary) hover:text-foreground',
            children: '← results'
          }),
          jsx(Pill, { children: page.locator || 'document' }),
          jsx('span', {
            className: 'truncate text-[0.8125rem] text-foreground',
            children: doc.title
          }),
          jsx('button', {
            type: 'button',
            onClick: copy,
            className: 'ml-auto shrink-0 text-[0.6875rem] text-(--ui-text-tertiary) hover:text-foreground',
            children: copied ? 'copied' : 'copy citation'
          })
        ]
      }),
      jsx(ScrollArea, {
        className: 'min-h-0 flex-1',
        children: jsxs('div', {
          className: 'flex flex-col gap-3 p-3',
          children: [
            page.image
              ? jsxs('div', {
                  className: 'relative overflow-hidden rounded border border-(--ui-stroke-secondary)',
                  children: [
                    jsx('img', {
                      src: page.image,
                      alt: '',
                      className: 'block w-full',
                      draggable: false
                    }),
                    rects.map((rect, index) =>
                      jsx('div', {
                        style: {
                          position: 'absolute',
                          left: `${rect.left * 100}%`,
                          top: `${rect.top * 100}%`,
                          width: `${Math.max(0.4, (rect.right - rect.left) * 100)}%`,
                          height: `${Math.max(0.6, (rect.bottom - rect.top) * 100)}%`,
                          background: 'var(--ui-accent)',
                          opacity: 0.22,
                          borderRadius: '2px',
                          boxShadow: '0 0 0 1px var(--ui-accent)'
                        }
                      }, `rect-${index}`)
                    )
                  ]
                })
              : null,
            jsx('pre', {
              className:
                'whitespace-pre-wrap break-words rounded bg-(--ui-fill-secondary) p-2 text-[0.75rem] leading-relaxed text-(--ui-text-secondary)',
              children: page.text || '(no text stored for this passage)'
            }),
            jsx('div', {
              className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
              children: doc.path
            })
          ]
        })
      })
    ]
  })
}

function Library() {
  const [status, setStatus] = useState(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [page, setPage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('search')
  const [changes, setChanges] = useState([])
  const inputRef = useRef(null)

  const loadStatus = useCallback(async () => {
    try {
      const data = await api.rest('/status')
      setStatus(data)
      setError('')
    } catch (err) {
      setStatus(null)
      setError('Folio backend is not available. Enable the plugin in Settings → Plugins.')
    }
  }, [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const runSearch = useCallback(async () => {
    const text = query.trim()
    if (!text) return
    setBusy(true)
    try {
      const data = await api.rest(`/search?q=${encodeURIComponent(text)}&limit=20`)
      setResults(data.results || [])
      setPage(null)
      setTab('search')
      setError('')
    } catch (err) {
      setError(String(err && err.message ? err.message : err))
    } finally {
      setBusy(false)
    }
  }, [query])

  const open = useCallback(
    async (item) => {
      setBusy(true)
      try {
        const data = await api.rest(
          `/page?doc_id=${item.doc_id}&locator=${encodeURIComponent(item.locator || '')}&q=${encodeURIComponent(query)}`
        )
        if (data.ok) setPage(data)
      } catch (err) {
        setError(String(err && err.message ? err.message : err))
      } finally {
        setBusy(false)
      }
    },
    [query]
  )

  const refresh = useCallback(async () => {
    setBusy(true)
    try {
      await api.rest('/index', { method: 'POST', body: {} })
      await loadStatus()
    } catch (err) {
      setError(String(err && err.message ? err.message : err))
    } finally {
      setBusy(false)
    }
  }, [loadStatus])

  const loadChanges = useCallback(async () => {
    setBusy(true)
    try {
      const data = await api.rest('/changes?since=30d&limit=30')
      setChanges(data.changes || [])
      setTab('changes')
      setPage(null)
    } catch (err) {
      setError(String(err && err.message ? err.message : err))
    } finally {
      setBusy(false)
    }
  }, [])

  const stats = status && status.stats ? status.stats : null
  const scan = status && status.scan ? status.scan : null

  if (error && !stats) {
    return jsx('div', {
      className: 'flex h-full flex-col p-3',
      children: jsx(EmptyState, { title: 'Folio unavailable', description: error })
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col text-sm',
    children: [
      jsxs('div', {
        className: 'flex flex-col gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx('span', { className: 'text-[0.8125rem] font-medium text-foreground', children: 'Folio' }),
              stats
                ? jsx('span', {
                    className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: `${stats.documents.toLocaleString()} documents · ${stats.chunks.toLocaleString()} passages · ${(stats.chars / 1e6).toFixed(1)}M chars`
                  })
                : null,
              jsx('button', {
                type: 'button',
                onClick: refresh,
                disabled: busy,
                className: 'ml-auto text-[0.6875rem] text-(--ui-text-tertiary) hover:text-foreground disabled:opacity-40',
                children: scan && scan.running ? 'scanning…' : 'refresh'
              }),
              jsx('button', {
                type: 'button',
                onClick: loadChanges,
                className: 'text-[0.6875rem] text-(--ui-text-tertiary) hover:text-foreground',
                children: 'changes'
              })
            ]
          }),
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(Input, {
                ref: inputRef,
                value: query,
                placeholder: 'Ask your documents — “retry budget”, “termination clause”',
                onChange: (event) => setQuery(event.target.value),
                onKeyDown: (event) => {
                  if (event.key === 'Enter') runSearch()
                },
                className: 'h-7 text-[0.8125rem]'
              }),
              jsx(Button, {
                size: 'sm',
                variant: 'secondary',
                onClick: runSearch,
                disabled: busy || !query.trim(),
                children: busy ? '…' : 'search'
              })
            ]
          })
        ]
      }),

      page
        ? jsx(Reader, { page, onClose: () => setPage(null) })
        : jsx(ScrollArea, {
            className: 'min-h-0 flex-1',
            children: jsxs('div', {
              className: 'flex flex-col gap-0.5 p-2',
              children: [
                error
                  ? jsx('div', { className: 'px-2 py-1 text-[0.6875rem] text-(--ui-text-quaternary)', children: error })
                  : null,
                tab === 'search'
                  ? results.length
                    ? results.map((item, index) =>
                        jsx(
                          ResultRow,
                          {
                            item,
                            active: false,
                            onOpen: open
                          },
                          `r-${item.doc_id}-${item.locator}-${index}`
                        )
                      )
                    : jsx(EmptyState, {
                        title: query ? 'No passage matched' : 'Search your library',
                        description: query
                          ? 'Try fewer, more distinctive words, or a phrase in quotes.'
                          : stats && stats.documents
                            ? 'Every hit names the document, the page, and the sentence.'
                            : 'Nothing indexed yet — ask Hermes to run folio_index on a folder.'
                      })
                  : changes.length
                    ? changes.map((entry, index) =>
                        jsxs('div', {
                          className: 'flex flex-col gap-0.5 rounded px-2 py-1.5 hover:bg-(--chrome-action-hover)',
                          children: [
                            jsxs('div', {
                              className: 'flex items-center gap-2',
                              children: [
                                jsx(Pill, {
                                  tone: entry.version_count > 1 ? 'accent' : undefined,
                                  children: entry.version_count > 1 ? `rev ${entry.version_count}` : 'new'
                                }),
                                jsx('span', {
                                  className: 'truncate text-[0.8125rem] text-foreground',
                                  children: entry.title
                                })
                              ]
                            }),
                            jsx('div', {
                              className: 'truncate text-[0.6875rem] text-(--ui-text-quaternary)',
                              children: entry.path
                            })
                          ]
                        }, `c-${entry.doc_id}-${index}`)
                      )
                    : jsx(EmptyState, { title: 'Nothing changed', description: 'No document was added or revised in the last 30 days.' })
              ]
            })
          }),

      jsxs('div', {
        className: 'flex items-center gap-2 border-t border-(--ui-stroke-secondary) px-3 py-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: [
          jsx(StatusDot, { tone: scan && scan.running ? 'warning' : 'success' }),
          jsx('span', {
            children: stats
              ? `refreshed ${relTime(Math.max(0, Date.now() / 1000 - (stats.last_indexed || 0)))} · ${(stats.db_bytes / 1e6).toFixed(1)} MB local`
              : 'local index'
          }),
          jsx('span', {
            className: 'ml-auto',
            children: status && status.auto_scan_minutes ? `auto ${status.auto_scan_minutes}m` : 'manual refresh'
          })
        ]
      })
    ]
  })
}

function StatusChip() {
  const [stats, setStats] = useState(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const data = await api.rest('/status')
        if (alive) setStats(data.stats)
      } catch (err) {
        if (alive) setStats(null)
      }
    }
    tick()
    const timer = setInterval(tick, 300000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  if (!stats) return null
  return jsx('button', {
    type: 'button',
    onClick: () => host.notify({ kind: 'info', message: `Folio: ${stats.documents} documents, ${stats.chunks} passages indexed` }),
    className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
    children: `folio ${stats.documents}`
  })
}

export default {
  id: ID,
  name: 'Folio',
  defaultEnabled: false,
  register(ctx) {
    api = ctx

    ctx.register({
      id: 'folio-pane',
      area: 'panes',
      title: 'Folio',
      order: 40,
      data: { placement: 'right', width: '460px' },
      render: Library
    })

    ctx.register({
      id: 'folio-chip',
      area: 'statusBar.right',
      render: StatusChip
    })
  }
}
