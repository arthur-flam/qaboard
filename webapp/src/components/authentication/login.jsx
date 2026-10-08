// Actions that change things (redo, delete, tuning...) need a logged-in user.
// When logged out, the web app disables them and suggests to log in,
// and if the server still answers 401 (e.g. the session expired), it says so once, with a "Log in" button.
import React, { useContext } from "react";
import { ReactReduxContext, useSelector } from "react-redux";
import { Button, Intent, MenuItem, MenuDivider, Tooltip } from "@blueprintjs/core";

import { logout } from "../../actions/users";


// What the backend answers (api/auth.py login_required): the toaster skips toasts showing it,
// since the interceptor below already showed one with a "Log in" button.
export const LOGIN_REQUIRED_ERROR = "You need to be logged-in to do this.";

// The login button (Auth.jsx) registers how to open its dialog, or the SAML redirect
let loginHandler = null;
export const registerLoginHandler = handler => {
  loginHandler = handler;
  return () => { if (loginHandler === handler) loginHandler = null; };
};

export const requestLogin = () => {
  if (loginHandler) loginHandler();
  else window.scrollTo({top: 0}); // the login button is at the top of the sidebar
};

// For class components: <RequiresLogin>{is_logged => <Button disabled={!is_logged} .../>}</RequiresLogin>
// Without a redux store (e.g. in tests) we don't know: nothing is disabled.
export const RequiresLogin = ({ children }) => {
  const context = useContext(ReactReduxContext);
  return context?.store ? <WithStore>{children}</WithStore> : children(true);
};

const WithStore = ({ children }) => {
  const is_logged = useSelector(state => state.user?.is_logged ?? false);
  return children(is_logged);
};

// First entry of the menus whose actions need a login
export const LoginMenuItem = ({ text = "Log in to redo, rename or delete" }) => <>
  <MenuItem icon="log-in" text={text} intent={Intent.PRIMARY} onClick={requestLogin} data-testid="login-menu-item"/>
  <MenuDivider/>
</>

// Next to a button that needs a login
export const LoginHint = ({ text = "Log in to do this" }) =>
  <Tooltip content={text}>
    <Button icon="log-in" text="Log in" minimal small intent={Intent.PRIMARY} onClick={requestLogin} style={{marginLeft: '5px'}}/>
  </Tooltip>


// One toast for all the 401 answers, and we tell the store we are logged out
export const installLoginInterceptor = (axios, store, toaster) => axios.interceptors.response.use(
  response => response,
  error => {
    const url = error?.config?.url ?? '';
    const is_login_request = url.includes('/api/v1/user/');
    if (error?.response?.status === 401 && !is_login_request) {
      const was_logged = store.getState().user?.is_logged;
      if (was_logged) store.dispatch(logout());
      // requests users didn't ask for (status polling, pre-flight checks...) fail silently
      if (error.config?.qaboardBackground) return Promise.reject(error);
      toaster.show({
        message: was_logged ? "Your session expired: log in again to do this." : "Log in to do this.",
        intent: Intent.WARNING,
        icon: "log-in",
        action: { text: "Log in", onClick: requestLogin },
        timeout: 10000,
      }, "login-required");
    }
    return Promise.reject(error);
  },
);
