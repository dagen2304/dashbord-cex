import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { websocketUrl } from './api'

export type LiveState = 'connecting' | 'live' | 'offline'

/**
 * Écoute les notifications des workers (WebSocket) et rafraîchit les données concernées.
 * Reconnexion automatique avec délai croissant. Si le WebSocket est indisponible,
 * les requêtes continuent de se rafraîchir périodiquement (refetchInterval).
 */
export function useLiveUpdates(enabled: boolean): LiveState {
  const queryClient = useQueryClient()
  const [state, setState] = useState<LiveState>('connecting')

  useEffect(() => {
    if (!enabled) return
    let ws: WebSocket | null = null
    let retry = 0
    let timer: number | undefined
    let ping: number | undefined
    let stopped = false
    let pending: number | undefined

    const invalidate = () => {
      // Regroupe les rafales (une notification par équipement) en un seul rafraîchissement.
      window.clearTimeout(pending)
      pending = window.setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['realtime'] })
        queryClient.invalidateQueries({ queryKey: ['device'] })
        queryClient.invalidateQueries({ queryKey: ['alerts'] })
        queryClient.invalidateQueries({ queryKey: ['inventory'] })
      }, 1500)
    }

    const connect = () => {
      const url = websocketUrl()
      if (!url || stopped) return
      setState('connecting')
      ws = new WebSocket(url)
      ws.onopen = () => {
        retry = 0
        setState('live')
        ping = window.setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send('ping'), 30_000)
      }
      ws.onmessage = invalidate
      ws.onclose = () => {
        window.clearInterval(ping)
        if (stopped) return
        setState('offline')
        retry = Math.min(retry + 1, 6)
        timer = window.setTimeout(connect, 1000 * 2 ** retry)
      }
    }
    connect()
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.clearTimeout(pending)
      window.clearInterval(ping)
      ws?.close()
    }
  }, [enabled, queryClient])

  return state
}
