# How to set up ingestion credentials

Sync scripts (`kb sync`) read credentials only from the environment — they
have no opinion on how those get set. Sync is the only part of this system
that touches the network; search and editing always work offline. See
[reference: ingestion pipelines](../reference/ingestion.md) for the full
per-source contract.

## Option 1: export directly

- `PKB_MEMOS_URL` / `PKB_MEMOS_TOKEN` (usememos)
- `PKB_GITLAB_PROJECT` (GitLab, via an authenticated `glab`)

## Option 2: sops + age

A data repo can commit `.sops.yaml` + `.pkb/secrets.enc.env` (ciphertext, safe
to commit) using [sops](https://github.com/getsops/sops) and
[age](https://github.com/FiloSottile/age):

```bash
kb secrets                                        # opens the encrypted file in $EDITOR
sops exec-env .pkb/secrets.enc.env 'kb sync memos'  # runs a sync with decrypted values
                                                     # injected only into that one subprocess
```

`kb secrets` is resolved-repo-aware, like every other `kb` command — no need
to remember the path. See
[templates/secrets.env.example](../../templates/secrets.env.example) for the
expected keys.

## beads

Requires the `bd` CLI on `PATH`. By default `kb` points `bd` at your resolved
data repo (`~/.pkb`, or whichever repo you're standing inside) rather than
relying on `bd`'s own cwd-based auto-discovery, which wouldn't know about
`kb`'s home-directory fallback.

- Initialize one in your data repo with `bd init --stealth` to keep it out of git.
- `PKB_BEADS_DIR` points at a different project's store instead.
- `PKB_BEADS_GLOBAL=1` uses `bd`'s shared cross-project store.
