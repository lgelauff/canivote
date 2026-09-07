# Deploying canivote to Toolforge

The service is stateless: no database, no OAuth, no secrets, no scheduled jobs,
nothing to back up. Deployment is a clone, a virtualenv and a webservice.

What follows is mostly a list of things that do not work the obvious way. None
of it is guessable from the code, which is why it is written down.

## First time

```bash
# The tool account is created at toolsadmin.wikimedia.org, not from the CLI.
become canivote
git clone https://github.com/lgelauff/canivote.git ~/canivote

# ~/www/python must be a REAL directory. Symlinking it to the repo silently
# breaks every symlink below it.
mkdir -p ~/www/python
ln -s ~/canivote ~/www/python/src
ln -s ~/canivote/ops/uwsgi.ini ~/www/python/uwsgi.ini
```

The `uwsgi.ini` symlink is deliberate. Toolforge reads `~/www/python/uwsgi.ini`,
not the copy in the repo, so copying it means a change in git has no effect
until somebody remembers to copy it again.

## The virtualenv, which is where this goes wrong

The venv must be built **inside the webservice shell**. One built on the bastion
does not work with the running service, and `pip install` from the bastion has
no effect on it either.

```bash
toolforge webservice <image> shell
```

Then, inside that shell — **`python3 -m venv` hangs**, because `ensurepip` does
not work in the pod. Build it without pip and fetch pip separately:

```bash
python3 -m venv --without-pip ~/www/python/venv
curl -sS https://bootstrap.pypa.io/get-pip.py | ~/www/python/venv/bin/python3
~/www/python/venv/bin/pip install -r ~/canivote/requirements.txt
exit
```

`requirements.txt` is the production install path — a `pip freeze` of a working
environment, so the whole dependency graph is pinned, not just the four
top-level packages. `pyproject.toml` exists for the test and lint tooling and is
not what production installs.

## Starting it

```bash
cd ~                     # webservice commands fail if run from inside the repo
toolforge webservice <image> start
```

Check `toolforge webservice --list-image-types` for the image name rather than
assuming `python3.13`; `pyproject.toml`'s `requires-python` should agree with
whatever is actually offered.

## Updating

```bash
cd ~/canivote && git pull
# If requirements.txt changed, reinstall inside the webservice shell first —
# the venv lives outside the repo, so a restart alone will not pick it up and
# the service comes back up with an ImportError.
cd ~ && toolforge webservice <image> restart
```

## Then actually check it

A clean `restart` is not evidence the deployment works. Hit the live service and
compare a real response against the example in `README.md`:

```bash
curl -s https://canivote.toolforge.org/health
curl -s 'https://canivote.toolforge.org/check?user=Effeietsanders&policy=frwiki-sondage'
```

## Still to settle on first deploy

The rate limiter keys on `request.remote_addr`, which behind Toolforge's front
proxy is likely the proxy for every caller — one shared bucket rather than one
per client. Measure the real hop count on the deployed service, then pin
`ProxyFix(x_for=N)` to it in `app.py`. Until that is done the limit is a global
throttle, which is safe for Wikimedia but unfair between callers.
