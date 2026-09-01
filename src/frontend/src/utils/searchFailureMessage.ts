import { isApiResponseError, isTimeoutError } from '../services/api';

// Shown when we genuinely have nothing better: the request failed without the server
// saying why, which really can mean blocked mirrors or a restricted network.
export const UNREACHABLE_SOURCE_MESSAGE =
  'Unable to reach download source. Network may be restricted or mirrors blocked.';

// Shown when the client's own abort fired. It is the backstop for a server that never
// answered at all, and it says nothing about mirrors - the budget the client waited out
// is the server's own, which the user can raise. See issue #1285.
export const CLIENT_TIMEOUT_MESSAGE =
  'The search took longer than the server said it would. Raise the release search ' +
  'timeout if your setup is simply slow.';

/**
 * The sentence to show for a failed direct-mode search.
 *
 * The server knows why a search failed - a spent search budget, an unsolved protection
 * challenge - and blanket-replacing that with the mirrors line told users their network
 * was broken when it was not. So: the server's own words when it explained itself, the
 * timeout line when we gave up before it answered, and the mirrors line only when
 * neither applies. See issue #1285.
 */
export const describeSearchFailure = (error: unknown): string => {
  if (isTimeoutError(error)) {
    return CLIENT_TIMEOUT_MESSAGE;
  }

  if (isApiResponseError(error) && error.serverMessage) {
    return error.serverMessage;
  }

  const message = error instanceof Error ? error.message : '';
  if (message.includes('Network restricted') || message.includes('Unable to reach')) {
    return message;
  }

  return UNREACHABLE_SOURCE_MESSAGE;
};
