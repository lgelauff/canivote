# Deploying canivote to Toolforge

The service is stateless: no database, no OAuth, no secrets, no scheduled jobs,
nothing to back up. Deployment is a clone, a virtualenv and a webservice.

What follows is mostly a list of things that do not work the obvious way. None
of it is guessable from the code, which is why it is written down.

## First time

```bash
# Tool accounts are created through the web UI at toolsadmin.wikimedia.org.
# There is no CLI equivalent — `toolforge tools create` is not a command
# (confirmed 2026-09-07; wiki-polis's own deployment guide is wrong on this).
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
toolforge webservice python3.13 shell
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
toolforge webservice python3.13 start --health-check-path /health
```

`python3.13` is offered (confirmed 2026-09-07 — `toolforge webservice --help`
lists the supported types; there is no `--list-image-types` flag and no
`toolforge images list` command). `pyproject.toml` and CI are pinned to match.

`--health-check-path /health` is worth passing. Without it Toolforge only does a
TCP check, which cannot tell a listening socket from a working service; with it
the pod is restarted when `/health` stops answering. It requires the endpoint to
return 200 to any `Host` header, which ours does.

## Updating

```bash
cd ~/canivote && git pull
# If requirements.txt changed, reinstall inside the webservice shell first —
# the venv lives outside the repo, so a restart alone will not pick it up and
# the service comes back up with an ImportError.
cd ~ && toolforge webservice python3.13 restart
```

## Then actually check it

A clean `restart` is not evidence the deployment works. Hit the live service and
compare a real response against the example in `README.md`:

```bash
curl -s https://canivote.toolforge.org/health
curl -s 'https://canivote.toolforge.org/check?user=Effeietsanders&policy=frwiki-sondage'
```

## Still to settle on first deploy

Some values can only be read off a running deployment, and a couple of them
describe how the service behaves under load — so they are kept out of this file.
See `.claude/deploy-notes.md` in a local checkout, which is gitignored.
