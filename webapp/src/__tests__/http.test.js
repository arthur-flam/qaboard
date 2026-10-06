// Errors shown to users, and the background jobs started by "Redo"
import axios from 'axios';
import { errorMessage, waitForJob, redo, redoToast } from '../utils/http';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const httpError = (status, data, statusText = '') => ({ isAxiosError: true, request: {}, response: { status, data, statusText } });

describe('errorMessage', () => {
  test('prefers the error sent by the server', () => {
    expect(errorMessage(httpError(500, { error: 'Some runs failed' }))).toBe('Some runs failed');
  });

  test('explains timeouts and unavailable servers instead of dumping the error', () => {
    const html = '<html><body><h1>504 Gateway Time-out</h1></body></html>';
    expect(errorMessage(httpError(504, html))).toMatch(/took too long/);
    expect(errorMessage(httpError(502, html))).toMatch(/unavailable/);
    expect(errorMessage(httpError(401, {}))).toMatch(/log in/);
  });

  test('shows short plain-text errors, not HTML pages', () => {
    expect(errorMessage(httpError(404, '404 ERROR:\n Not found'))).toBe('Error 404: 404 ERROR:\n Not found');
    expect(errorMessage(httpError(500, '<html>oops</html>', 'Internal Server Error'))).toBe('Error 500 (Internal Server Error)');
  });

  test('network errors and exceptions', () => {
    expect(errorMessage({ isAxiosError: true, request: {}, message: 'Network Error' })).toMatch(/reach the server/);
    expect(errorMessage(new TypeError('x is undefined'))).toBe('x is undefined');
  });
});

describe('waitForJob', () => {
  beforeEach(() => vi.mocked(axios.get).mockReset());

  test('polls until the job is done, through deploys', async () => {
    vi.mocked(axios.get)
      .mockResolvedValueOnce({ data: { state: 'PENDING' } })
      .mockRejectedValueOnce(httpError(502, ''))
      .mockResolvedValueOnce({ data: { state: 'STARTED' } })
      .mockResolvedValueOnce({ data: { state: 'SUCCESS', result: { started: 2, failed: [] } } });
    await expect(waitForJob('abc', { interval: 0 })).resolves.toEqual({ started: 2, failed: [] });
    expect(axios.get).toHaveBeenCalledTimes(4);
    expect(axios.get).toHaveBeenCalledWith('/api/v1/jobs/abc/');
  });

  test('reports failed jobs and real errors', async () => {
    vi.mocked(axios.get).mockResolvedValueOnce({ data: { state: 'FAILURE', error: 'ValueError: boom' } });
    await expect(waitForJob('abc', { interval: 0 })).rejects.toThrow('ValueError: boom');
    vi.mocked(axios.get).mockRejectedValueOnce(httpError(401, {}));
    await expect(waitForJob('abc', { interval: 0 })).rejects.toMatchObject({ response: { status: 401 } });
  });

  test('gives up eventually', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { state: 'STARTED' } });
    await expect(waitForJob('abc', { interval: 0, timeout: -1 })).rejects.toThrow(/still not done/);
  });
});

describe('redo', () => {
  beforeEach(() => {
    vi.mocked(axios.get).mockReset();
    vi.mocked(axios.post).mockReset();
  });

  test('waits for the runs to be submitted', async () => {
    vi.mocked(axios.post).mockResolvedValueOnce({ data: { status: 'queued', outputs: 3, job_id: 'j1' } });
    vi.mocked(axios.get).mockResolvedValueOnce({ data: { state: 'SUCCESS', result: { started: 2, failed: [{ id: 7, error: 'see log.txt' }] } } });
    const onQueued = vi.fn();
    const result = await redo('/api/v1/batch/redo/', { id: 1, only_failed: true }, { onQueued });
    expect(axios.post).toHaveBeenCalledWith('/api/v1/batch/redo/', { id: 1, only_failed: true });
    expect(onQueued).toHaveBeenCalledWith({ status: 'queued', outputs: 3, job_id: 'j1' });
    expect(result).toEqual({ started: 2, failed: [{ id: 7, error: 'see log.txt' }] });
  });

  test('nothing to redo', async () => {
    vi.mocked(axios.post).mockResolvedValueOnce({ data: { status: 'OK', outputs: 0 } });
    await expect(redo('/api/v1/batch/redo/', { id: 1 })).resolves.toEqual({ started: 0, failed: [] });
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe('redoToast', () => {
  test('summarizes the result', () => {
    expect(redoToast({ started: 0, failed: [] }).message).toBe('There was nothing to redo.');
    expect(redoToast({ started: 1, failed: [] })).toEqual({ message: 'Started 1 run again.', intent: 'success' });
    expect(redoToast({ started: 3, failed: [] }).message).toBe('Started 3 runs again.');
    const partial = redoToast({ started: 1, failed: [{ id: 2, error: 'see log.txt' }] });
    expect(partial.message).toBe('1 of 2 runs failed to start: see log.txt');
    expect(partial.intent).toBe('warning');
    expect(redoToast({ started: 0, failed: [{ id: 2, error: 'x' }] }).intent).toBe('danger');
  });
});
