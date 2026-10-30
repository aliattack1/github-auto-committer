# GitHub Auto Committer

A small Android app written in Python with [Flet](https://flet.dev) that takes
a zip file of your project and pushes its contents to a GitHub repository as
a single commit — automatically, with `.gitignore` rules respected.

## Features

- **Sign in once, stay signed in.** Credentials (GitHub username + Personal
  Access Token) are stored on-device in a JSON file under
  `$HOME/.ghcommitter/` (on Android this resolves to the app's private
  files sandbox — already OS-encrypted and inaccessible to other apps).
- **Pick an existing repo or create a new one.** On launch the app fetches
  your repo list via the GitHub REST API and shows them in a dropdown. A
  "New repo" button opens a dialog to create a new private/public repo inline.
- **Strict `.gitignore` support.** Every `.gitignore` file found inside the
  zip is honored with git's "later-patterns-win" rule. A built-in exclude
  list also drops `node_modules/`, `__pycache__/`, `.venv/`, `build/`,
  `dist/`, `.git/`, OS junk, IDE configs, etc.
- **Single-commit push.** All surviving files are uploaded as blobs and
  folded into one new commit using the `tree → commit → update-ref`
  pipeline. No git binary required — works on stock Android.
- **Auto commit message.** Defaults to `chore: auto-commit export <ts>`,
  fully editable before pushing.
- **In-app history.** Last 50 commits are kept locally with timestamp,
  repo, branch, file count, SHA, and status — visible on the History tab.
- **GitHub Action included.** `.github/workflows/build-apk.yml` builds a
  debug APK automatically when you push to `main`, on tags `v*`, or via
  manual dispatch.

## Why a Personal Access Token instead of a real password?

GitHub disabled password-based authentication for the API and git
operations in August 2021. A Personal Access Token (PAT) is the only
viable credential that still lets the app act on your behalf. The login
form labels the field "Password (Personal Access Token)" to make this
explicit. Create one at
https://github.com/settings/tokens with the following scopes:

- `repo` — push to public and private repos
- `workflow` — only if you want the app to be able to commit GitHub
  Actions workflow files (e.g. `.github/workflows/*.yml`)

## Project layout

```
github-auto-committer/
├── main.py                       # Flet app entry
├── requirements.txt
├── pyproject.toml                # Flet build config (package id, app name)
├── README.md
├── .gitignore
├── .github/
│   └── workflows/
│       └── build-apk.yml         # APK build workflow
├── assets/
│   └── icon.png                  # Replace with your own icon
├── src/
│   ├── __init__.py
│   ├── github_client.py          # GitHub REST API client (auth, repos, commit pipeline)
│   ├── gitignore_filter.py       # .gitignore matcher (pathspec-based)
│   ├── zip_processor.py          # Safe zip extraction + summarize
│   ├── storage.py                # JSON-file-backed creds + history in $HOME/.ghcommitter
│   └── commit_pipeline.py       # End-to-end orchestrator with progress callbacks
└── ui/
    ├── __init__.py
    ├── theme.py                  # Material 3 dark + GitHub purple tokens
    ├── login_view.py             # Login screen
    ├── main_view.py              # File picker + repo dropdown + new-repo dialog + progress log
    └── history_view.py           # In-app commit history
```

## Run on desktop (for development)

```bash
pip install -r requirements.txt
python main.py
```

A Flet window will open. The flow is identical to the Android app —
handy for tweaking the UI before building the APK.

## Build the Android APK locally

The Flet CLI wraps Buildozer + Python-for-Android under the hood. You
need Java 17, the Android SDK, and a few system packages. The fastest
way is via Docker:

```bash
# One-liner with the official Flet build image
docker run --rm -v "$(pwd)":/app -w /app fletdev/flet:latest \
    flet build apk --debug
```

Or natively:

```bash
pip install "flet>=0.23.0"
flet build apk --debug
```

The signed debug APK lands in `build/`. Copy it to your phone and
install with `adb install build/app-debug.apk` or sideload directly.

## Build the APK via GitHub Actions

The included `.github/workflows/build-apk.yml` uses the official
[`flet-dev/flet-build-action`](https://github.com/flet-dev/flet-build-action)
to build a debug APK:

- On every push to `main` / `master`
- On every git tag matching `v*` (also creates a GitHub Release)
- On manual dispatch via the Actions tab

The APK is uploaded as a downloadable artifact named
`github-auto-committer-apk` (30-day retention). On tag pushes, the APK
is also attached to the auto-created GitHub Release.

> **Note:** The workflow uses `flet-dev/flet-build-action@v1` (the
> official Flet build action). An earlier version of this workflow
> mistakenly referenced `flet-app/flet-build-action` which does not
> exist — if you copied the source before this fix, re-copy the file
> at `.github/workflows/build-apk.yml`.

To trigger a build right now:

1. Push the source to a new GitHub repo:
   ```
   git init
   git add .
   git commit -m "init: GitHub Auto Committer"
   git remote add origin <your-repo-url>
   git push -u origin main
   ```
2. Open the repo's **Actions** tab in your browser.
3. Select **Build Android APK** → **Run workflow**.
4. When the run finishes (typically ~20-40 minutes for the first run
   because the action downloads Flutter + Android SDK + NDK), scroll
   down to the **Artifacts** section and download
   `github-auto-committer-apk`.

## How a commit actually happens

1. **Verify creds** — `GET /user` with HTTP Basic auth (username + PAT).
2. **List repos** — `GET /user/repos?affiliation=owner,collaborator`.
3. **(Optional) Create new repo** — `POST /user/repos` with
   `auto_init=true` so the default branch exists.
4. **Resolve branch HEAD** — `GET /repos/{owner}/{repo}/git/refs/heads/{branch}`,
   falling back to creating the branch from the default branch if needed.
5. **Upload blobs** — `POST /repos/{owner}/{repo}/git/blobs` once per
   surviving file, getting back a SHA per blob.
6. **Create tree** — `POST /repos/{owner}/{repo}/git/trees` with
   `base_tree` set to the current HEAD's tree SHA and one entry per
   blob. This preserves any existing files in the repo.
7. **Create commit** — `POST /repos/{owner}/{repo}/git/commits` with
   `tree` = new tree SHA and `parents` = [HEAD SHA].
8. **Update ref** — `PATCH /repos/{owner}/{repo}/git/refs/heads/{branch}`
   to point at the new commit SHA.

Net effect: one new commit on the chosen branch, containing all
surviving files from the zip, with a clean audit trail.

## Security notes

- Credentials live only on the device. They never leave it except
  directly to GitHub's API over HTTPS.
- `pathspec` and `httpx` are the only non-Flet runtime dependencies —
  both pure-Python and Android-friendly.
- No external `git` binary is invoked, so there is no shell injection
  surface.
- The `.git/` directory inside your zip is always excluded so that the
  project's own history doesn't clash with the target repo's.

## Roadmap ideas

- Per-upload exclude pattern editor
- Branch picker for non-default branches
- "Draft commit" mode that creates a branch instead of pushing to main
- OAuth device flow as an alternative to PAT entry

Pull requests welcome.
