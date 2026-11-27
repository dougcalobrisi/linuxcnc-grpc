# Changelog

All notable changes to linuxcnc-grpc (the server and the Python, Go, Node.js and Rust clients) are listed here. Versions follow [semantic versioning](https://semver.org).

## Unreleased

### Fixed
- **`scripts/wait-for-linuxcnc.py` could wait forever.** If its first attempt came before LinuxCNC was up, no later attempt in the same process could succeed. Each attempt now runs in its own process. `--timeout` is a deadline for the whole run: the first check (that the `linuxcnc` module can be imported), every attempt and every pause between attempts stop at it.

### Maintainer tooling
These do not affect users of the packages.
- **Publishing:** `publish-node.sh` applies the npm dist-tag of a pre-release and refuses a pre-release it cannot derive a tag for; the artifact version checks match the version exactly; `publish-all.sh` refuses a tree with uncommitted changes unless given `--allow-dirty`, creates the tag only after every package was published, and checks an existing local or remote tag first.
- **`sync-versions.sh`** reports a lock file it could not update and stops before any commit, changes only the project's own entry in `uv.lock`, also sets the versions of the example projects, and refuses `--tag` without `--commit`.
- **Release workflow:** it checks the tag on the remote before publishing, runs one release at a time, and fails if a registry already has the version. Only a run started with the input `resume_partial_release` set to true (default false) skips a version that is already published; that input is for running a release that failed part-way again on the same commit.
- **CI:** GitHub Actions in the workflows are pinned to commit SHAs (each at its latest release, which moved checkout, cache, setup-go, setup-node, upload-pages-artifact, setup-uv and action-gh-release up a major version), and Dependabot keeps the pins current. Hugo, the docs theme, uv, the protoc plugins and the LinuxCNC source used by the e2e job are pinned too. The release job installed `npm@latest`, which no longer runs on the Node version the job uses; it now installs a fixed npm 11. The e2e job fails if no test passed or a test was skipped unexpectedly; the stream examples must still be streaming when their timeout stops them, where every exit status used to be ignored; the protoc download is verified against a SHA-256; workflow permissions are narrowed to what each job needs. The e2e job's LinuxCNC build cache is keyed by the pinned commit and the checkout is verified on every run. A release dry run now works for a version that is already on npm and for a pre-release.
