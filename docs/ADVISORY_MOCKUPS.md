# Independent perspectives and grounded mockups

Multicoders exposes two versioned JSON commands for Hornero and other
coordinators. Both default to official provider CLI authentication and remove
provider API keys from child environments, so a subscription-backed session
cannot silently switch to API billing.

## Perspective protocol

```bash
python -m multicoders perspective \
  --request advisory-request.json \
  --repo /path/to/repo \
  --output result.json
```

The request declares mode (`review`, `brainstorm`, `research` or
`design-opinion`), requirement, neutral question, content-hashed evidence,
verbatim constraints, providers and minimum quorum.

Phase A starts every provider concurrently with byte-equivalent logical
content and a read-only sandbox. A provider never sees a peer answer or the
coordinator's preferred conclusion. Phase B starts only after every slot
completes, fails or times out. It preserves originals, groups exact agreements,
shows divergent positions and initializes every finding as `ESCALATE`.

Operational failures are values in `provider_runs`; one provider cannot abort
the others. Synthesis is withheld when quorum or a required provider fails.
`resume: true` continues the latest Codex session with the sandbox supplied as
a configuration override.

Use `--dry-run` for deterministic protocol and integration tests without a
model or network.

## Mockup protocol

```bash
python -m multicoders mockup \
  --request mockup-request.json \
  --repo /path/to/repo \
  --artifact-dir /path/to/repo/.hornero/artifacts/mockups/mockup-000001 \
  --output result.json
```

The request contains one screenshot (unless `from_scratch` is explicit), named
views with exact `.png` filenames, approved constraints, product context and
the visual language observed by the coordinator.

The Codex adapter:

- copies and hashes the reference image;
- attaches it to a workspace-write Codex session whose cwd is only the output
  directory;
- requests exactly one file per named view;
- validates PNG signatures, dimensions, byte limits and undeclared files;
- records session, reference and artifact metadata in `manifest.json`;
- supports immutable refinement runs through Codex resume.

Mockup write access is the exception to advisory read-only execution. It is
contained to a caller-created artifact directory and never grants source-tree
editing authority.

## Telegram delivery

`TelegramBot.send_photo(Path(...))` uploads a local PNG/JPEG with multipart
form data, preserves captions/topic IDs and rejects symlinks, unsupported
types and oversize photos before network access. URL and Telegram file-ID
delivery remain supported.

## Provider environment

`provider_environment()` copies the parent environment and removes only the
selected provider's API billing variables in the child copy. The parent
`os.environ` is never mutated, which keeps concurrent providers isolated.
API credentials can be forwarded only through an explicit
`allow_api_keys=True` call made by a separately governed API mode.
