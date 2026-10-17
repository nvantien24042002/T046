import { describe, expect, it, vi } from 'vitest'
import { createLatestSelection, type SelectionState } from '../api/latestSelection'
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const first = { datasetId: 'dataset-a', requestFingerprint: 'a'.repeat(64) }

describe('latest dataset/configuration selection', () => {
  it.each([
    { datasetId: 'dataset-b', requestFingerprint: first.requestFingerprint },
    { datasetId: first.datasetId, requestFingerprint: 'b'.repeat(64) },
  ])('ignores late success or failure after selection changes', async (second) => {
    for (const outcome of ['success', 'failure']) {
      const states: SelectionState<string>[] = []
      const gate = createLatestSelection<string>((state) => states.push(state))
      const old = deferred<string>()
      let oldSignal!: AbortSignal
      const oldRun = gate.run(first, (_selection, signal) => { oldSignal = signal; return old.promise })
      await gate.run(second, async () => 'current')
      if (outcome === 'success') old.resolve('obsolete'); else old.reject(Error('obsolete failure'))
      await oldRun
      expect(oldSignal.aborted).toBe(true)
      expect(states.map((state) => state.kind)).toEqual(['loading', 'loading', 'ready'])
      expect(states.at(-1)).toMatchObject({ kind: 'ready', selection: second, data: 'current' })
    }
  })
  it('clears old visible data during retry, reports errors and preserves empty values', async () => {
    const states: SelectionState<string[]>[] = []
    const gate = createLatestSelection<string[]>((state) => states.push(state))
    await gate.run(first, async () => { throw Error('failed') })
    const retry = deferred<string[]>()
    const run = gate.run(first, () => retry.promise)
    expect(states.at(-1)).toEqual({ kind: 'loading', selection: first })
    retry.resolve([]); await run
    expect(states.map((state) => state.kind)).toEqual(['loading', 'error', 'loading', 'ready'])
    expect(states.at(-1)).toMatchObject({ data: [] })
  })
  it('cancels without publishing a late result', async () => {
    const publish = vi.fn()
    const gate = createLatestSelection<string>(publish)
    const old = deferred<string>()
    const run = gate.run(first, () => old.promise)
    gate.cancel(); old.resolve('obsolete'); await run
    expect(publish).toHaveBeenCalledTimes(1)
  })
  it('invalidates older requests even when retrying the exact same selection', async () => {
    const states: SelectionState<string>[] = []
    const gate = createLatestSelection<string>((state) => states.push(state))
    const old = deferred<string>()
    const run = gate.run(first, () => old.promise)
    await gate.run(first, async () => 'retry')
    old.resolve('old'); await run
    expect(states.at(-1)).toMatchObject({ data: 'retry' })
    expect(states.filter((state) => state.kind === 'ready')).toHaveLength(1)
  })
})
