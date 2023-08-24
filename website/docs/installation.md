---
id: installation
title: Installing QA-Board's client
sidebar_label: Client Installation
---

You need to install QA-Board's CLI client: `qa`. It wraps and runs your code.

```bash
pip install --upgrade git+ssh://git@github.sec.samsung.net/arthur-flam/qaboard
# If this does not work
# => don't hesitate to contact arthur.flam@samsung.com
#
# If you have SSH/clone errors
# => make sure you configured your SSH keys at
#    https://github.sec.samsung.net/settings/keys
# If you have SSL/certificates/trust errors
# => use --trusted-host pypi.python.org --trusted-host pypi.org --trusted-host files.pythonhosted.org
# If you have timeouts, not authorized, proxy errors, or "this is not a git repo error"
# => use --proxy http://12.26.204.100:8080
# If you don't have pip or experience permissions issues
# => install python, we recommend the anaconda distribution.
#    https://www.anaconda.com/distribution/#download-section
```

To make sure the installation was successful, try printing a list of `qa`'s CLI commands:

```bash
qa --help

# If you get errors about not using a utf8 locale, you can likely: 
#   export LC_ALL=C.utf8 LANG=C.utf8
```

## Connecting to a custom QA-Board server
By default `qa` tries to use a QA-Board server running locally (it assumes you used the default config).

If you connect to a remote QA-Board server, you'll need to set those environment variables:

```bash
export QABOARD_HOST=my-server:5151
export QABOARD_PROTOCOL=http
```
