import { api, errorMessage } from '../services/api'

/**
 * Serialises input requests so a quick press → release can never arrive out of
 * order, which could otherwise leave a direction held on the C64.
 */
let chain: Promise<unknown> = Promise.resolve()
let onError: (msg: string) => void = () => {}

export function setInputErrorHandler(fn: (msg: string) => void) {
  onError = fn
}

function enqueue(fn: () => Promise<unknown>) {
  chain = chain.then(fn).catch((e) => onError(errorMessage(e)))
  return chain
}

export const input = {
  joy: (inputs: string[], transition: 'tap' | 'press' | 'release', port?: number) =>
    enqueue(() => api.joystick(inputs, transition, port)),
  key: (key: string, transition: 'tap' | 'press' | 'release') => enqueue(() => api.key(key, transition)),
  releaseAll: () => enqueue(() => api.releaseAll()),
}
