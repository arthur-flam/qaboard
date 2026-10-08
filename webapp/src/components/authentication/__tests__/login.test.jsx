/**
 * Logged-out users: actions are disabled with a "Log in" suggestion, and 401 answers show one clear toast.
 * Run with: cd webapp && npm test -- login
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { Provider } from 'react-redux';
import { createStore } from 'redux';

import { RequiresLogin, LoginHint, installLoginInterceptor, registerLoginHandler, LOGIN_REQUIRED_ERROR } from '../login';
import { loggedReducer } from '../../../reducers/users';
import { login } from '../../../actions/users';

const makeStore = is_logged => {
  const store = createStore((state = {user: loggedReducer(undefined, {})}, action) => ({user: loggedReducer(state.user, action)}))
  if (is_logged) store.dispatch(login({user_name: 'alice'}))
  return store
}

// Calls the interceptor's error handler like axios would
const interceptor = (store, toaster) => {
  let onError
  const axios = {interceptors: {response: {use: (ok, ko) => { onError = ko }}}}
  installLoginInterceptor(axios, store, toaster)
  return (url, config = {}) => onError({config: {url, ...config}, response: {status: 401, data: {error: LOGIN_REQUIRED_ERROR}}}).catch(e => e)
}


describe('RequiresLogin', () => {
  const Action = () => <RequiresLogin>{is_logged => <button disabled={!is_logged}>Redo</button>}</RequiresLogin>

  it('disables actions when logged out', () => {
    render(<Provider store={makeStore(false)}><Action/></Provider>)
    expect(screen.getByText('Redo')).toBeDisabled()
  })
  it('enables them when logged in', () => {
    render(<Provider store={makeStore(true)}><Action/></Provider>)
    expect(screen.getByText('Redo')).toBeEnabled()
  })
  it('enables them without a store (we don\'t know)', () => {
    render(<Action/>)
    expect(screen.getByText('Redo')).toBeEnabled()
  })
  it('opens the login dialog', () => {
    const handler = vi.fn()
    const unregister = registerLoginHandler(handler)
    render(<LoginHint/>)
    fireEvent.click(screen.getByText('Log in'))
    expect(handler).toHaveBeenCalled()
    unregister()
  })
})


describe('the 401 interceptor', () => {
  it('shows one toast with a "Log in" button', async () => {
    const toaster = {show: vi.fn()}
    const store = makeStore(false)
    await interceptor(store, toaster)('/api/v1/batch/redo/')
    await interceptor(store, toaster)('/api/v1/output/1/')
    expect(toaster.show).toHaveBeenCalledTimes(2)
    const [props, key] = toaster.show.mock.calls[0]
    expect(props.message).toBe('Log in to do this.')
    expect(props.action.text).toBe('Log in')
    // the same key: the second toast replaces the first
    expect(key).toBe('login-required')
    expect(toaster.show.mock.calls[1][1]).toBe('login-required')
  })

  it('logs out when the session expired', async () => {
    const toaster = {show: vi.fn()}
    const store = makeStore(true)
    await interceptor(store, toaster)('/api/v1/batch/redo/')
    expect(store.getState().user.is_logged).toBe(false)
    expect(toaster.show.mock.calls[0][0].message).toMatch(/session expired/)
  })

  it('is silent for background requests and login requests', async () => {
    const toaster = {show: vi.fn()}
    const store = makeStore(true)
    await interceptor(store, toaster)('/api/v1/commit/abc/batch/check', {qaboardBackground: true})
    expect(toaster.show).not.toHaveBeenCalled()
    expect(store.getState().user.is_logged).toBe(false)
    await interceptor(store, toaster)('/api/v1/user/auth/')
    expect(toaster.show).not.toHaveBeenCalled()
  })

  it('still rejects, so that callers stop waiting', async () => {
    const error = await interceptor(makeStore(false), {show: vi.fn()})('/api/v1/batch/redo/')
    expect(error.response.status).toBe(401)
  })
})
