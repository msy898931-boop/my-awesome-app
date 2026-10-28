# goldkit

A tiny, dependency-free toolkit for the plumbing every feed-backed tool needs —
extracted from [GoldBot](../README.md), where each piece was a bug first.

```python
from goldkit import ResilientSource

src = ResilientSource(name="rates")
src.add("real_yield", fetch_real_yield, ttl=6 * 3600)  # a daily series
src.add("spot", fetch_spot, ttl=300)  # a live tick

read = src.read()
if not read:
    print("could not check:", read.error)  # never a silent empty
else:
    print(read.value["real_yield"])
```

## The four problems it solves

Each of these has a wrong answer that looks right until it costs you. Every one
shipped in GoldBot before it became a pattern here.

### 1. A failed fetch rendered as a quiet result

A fetch helper that swallows errors into `[]` makes a `429` indistinguishable
from a genuinely quiet day. GoldBot printed "the window is calm" while a
scheduled rate decision sat unread in the feed.

`Result` keeps `ok` and `error` beside the data, so the two cannot be confused,
and truthiness tracks **health, not content** — `if read:` means "the source
answered", not "the list was non-empty".

### 2. A retry storm against a source that just said no

Retrying a `429` on the next call is what turns a temporary limit into a
permanent one.

`Leg` holds an explicit retry gate set to an **absolute deadline** at the moment
of failure. The subtle bug: gating on the *age* of the cached value
(`now - cached_at < NEGATIVE_TTL`) never fires, because the cached value is
already old. See `cache.py`.

### 3. A cached-but-stale value served as fresh

If the freshness test is only "do we have a value and is it within the TTL", a
value kept through a failure passes it on the next call and is returned with no
error. `Leg` tests `value and not error and within ttl` — the second clause is
the whole point.

### 4. Reading an unbounded response

`read_capped` refuses a body over a ceiling *before* it is in memory, reading one
byte past the limit so it never trusts a `Content-Length` the sender controls.
`guarded_url` refuses any scheme that is not `http`/`https`, in one place, so no
call site can forget.

## What's here

| module | what it is |
|---|---|
| `result.py` | `Result` — data plus `ok`/`error`/`stale`, truthy on health |
| `cache.py` | `Leg` — a cached source with an honest freshness test and a firing retry gate |
| `source.py` | `ResilientSource` — several legs, `ok` only when all answered |
| `http.py` | `guarded_url`, `read_capped` — the guarded door and the cap |

## Design rules

- **Pure standard library.** No runtime dependency, like the project it came from.
- **A failure is reported, never rendered as a calm default.**
- **A partial read is useful but marked** — one dead leg makes the whole read
  `ok=False` and `stale=True`, never silently whole.
- **Every pattern has a test that asserts the refusal.**

## Run the tests

```bash
PYTHONPATH=starter python -m pytest starter/tests -q
```

Or as part of the main project's gate, which is how it is kept honest:

```bash
make check
```
