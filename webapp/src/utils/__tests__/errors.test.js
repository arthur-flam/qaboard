/**
 * Run with: cd webapp && npm test -- errors
 */
import { errorMessage, isArtifactsError } from '../errors';

const httpError = (status, data) => ({message: `Request failed with status code ${status}`, response: {status, statusText: 'X', data}})

describe('errorMessage', () => {
  it('uses the API error', () => {
    expect(errorMessage(httpError(400, {error: 'Unknown batch: smok'}))).toBe('Unknown batch: smok')
    expect(errorMessage(httpError(400, {errors: ['a.', 'b.']}))).toBe('a. b.')
  })
  it('uses short plain text answers', () => {
    expect(errorMessage(httpError(404, 'Sorry, the commit id was not found'))).toBe('Sorry, the commit id was not found')
  })
  it('never shows HTML pages or JSON dumps', () => {
    expect(errorMessage(httpError(502, '<html><body>Bad Gateway</body></html>'))).toBe('Request failed with status code 502 (502 X)')
    expect(errorMessage(new Error('Network Error'))).toBe('Network Error')
  })
  it('detects missing artifacts', () => {
    expect(isArtifactsError(httpError(409, {error: 'x', artifacts: {ok: false}}))).toBe(true)
    expect(isArtifactsError(httpError(500, {error: 'x'}))).toBe(false)
  })
})
