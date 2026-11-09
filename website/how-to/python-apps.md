---
title: "Python apps and the API key"
description: "Your own Python on the Mac, on the Spark's models: what the API key is, where it lives, worked examples with openai, chatlas and querychat, and how to give an app a key of its own."
---

*(Added 2026-11-09, Dan's decision: "we need to prioritize this so i can actually make some demo
app examples that plug into the spark models".)*

The Spark's front speaks the OpenAI API, so your own Python on the Mac can use its models with any
library that can talk to an OpenAI-compatible server. This page is the setup to come back to: what
the API key is, where it lives, and a worked example with each of `openai`, chatlas and querychat.
[Reaching the Spark](../architecture.qmd#reaching-the-spark) shows the path a request takes.

Any such library needs three things:

| What | Its value on the Mac | Where it comes from |
|---|---|---|
| The base URL | `http://127.0.0.1:9100/v1` | `make tunnel`, which forwards the Spark's front to that address ([Pi's runbook](pi.md) sets it up) |
| The API key | the environment variable `SPARK_API_KEY` | `~/.secrets`, which your shell loads |
| The model | a name such as `qwen3.6-35b-a3b` | the list that section 2 prints |

Every example here ran on the Mac on 2026-11-09 against Qwen3.6-35B-A3B, and answers took 16 to 47
seconds. No other model has been tried with them. Python runs through uv, as everywhere in this
repo: `uv run` installs what each script's header names.

## How the key works

A client key is one long random string, kept in two halves:

- **The Mac holds the value.** It is in `~/.secrets`, as `export SPARK_API_KEY=…`, and your shell
  loads that file, so every new terminal has it. It's the same key Pi uses on the Mac.
- **The Spark holds only a digest.** `/etc/local-ai/secrets/client-keys.sha256` has the key's
  SHA-256 beside a name, and `/etc/local-ai/keys.yaml` gives that name a group and a label.
- **Each request carries the value,** in an `Authorization: Bearer` header. The front hashes it,
  looks for that digest, and either passes the request on or answers `401`.

So your code never holds a key. It reads the variable when it runs, as
`os.environ["SPARK_API_KEY"]`. Never paste the value into a script, a notebook, or anything that
gets committed, and don't print it to see whether it's there: step 1 checks without showing it.

**Don't export it as `OPENAI_API_KEY`.** Other tools read that name and would send the Spark's key
to OpenAI's servers. Pass it explicitly, as the examples do.

## 1. Check the key and the tunnel

**On the Mac**, this prints only `True` or `False`, never the value:

```bash
python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"
```

Expected: `True`. `False` means this terminal didn't load `~/.secrets`, so open a new one.

**On the Mac**, this asks the front without a key, so a refusal is the answer you want:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9100/v1/models
```

Expected: `401`, which means the tunnel is up and the front is answering. `000` means the tunnel is
down.

## 2. With the `openai` package

Save this as `spark_openai.py`. It lists the model names you can use, then asks one of them a
question:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["openai"]
# ///
import os

from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:9100/v1",
    api_key=os.environ["SPARK_API_KEY"],
)

for model in client.models.list():
    print(model.id)

answer = client.chat.completions.create(
    model="qwen3.6-35b-a3b",
    messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
)
print(answer.choices[0].message.content)
```

**On the Mac**, in that folder:

```bash
uv run spark_openai.py
```

Expected: the model names, then `ready`. The list includes the embeddings and speech models, which
don't chat. A model that isn't loaded loads when it's asked for, if it fits beside what's already
loaded; if it doesn't, the request is refused and the message says why.

## 3. With chatlas

[chatlas](https://posit-dev.github.io/chatlas/) needs `ChatOpenAICompletions` here, not
`ChatOpenAI`. `ChatOpenAI` uses OpenAI's Responses API, and chatlas's own docs warn that most other
servers won't work with it. Pass the key explicitly, since chatlas otherwise looks for
`OPENAI_API_KEY`.

Save this as `spark_chatlas.py`. It also registers a tool, to show that tool calls work:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["chatlas", "openai"]
# ///
import os

from chatlas import ChatOpenAICompletions

chat = ChatOpenAICompletions(
    base_url="http://127.0.0.1:9100/v1",
    api_key=os.environ["SPARK_API_KEY"],
    model="qwen3.6-35b-a3b",
)


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


chat.register_tool(add)
print(chat.chat("What is 1234 + 4321? Use the add tool.", echo="none"))
```

**On the Mac**, in that folder:

```bash
uv run spark_chatlas.py
```

Expected: an answer that says `5555`.

## 4. With querychat

[querychat](https://posit-dev.github.io/querychat/py/index.html) puts a chat beside a table and
answers by running SQL, so it needs a model with tool calling. Give it the chatlas object from
section 3. **Always pass `client=chat`:** left out, querychat uses OpenAI itself, and your questions
and your table's schema go there.

Save this as `app.py`:

```python
import os

from chatlas import ChatOpenAICompletions
from querychat import QueryChat
from querychat.data import titanic

chat = ChatOpenAICompletions(
    base_url="http://127.0.0.1:9100/v1",
    api_key=os.environ["SPARK_API_KEY"],
    model="qwen3.6-35b-a3b",
)
qc = QueryChat(titanic(), "titanic", client=chat)
app = qc.app()
```

**On the Mac**, in that folder:

```bash
uv run --with chatlas --with querychat --with pandas shiny run app.py
```

Then open the address it prints, `http://127.0.0.1:8000`, and ask how many passengers survived. The
answer is 342. Asked through querychat's own chat object, it gave that on 2026-11-09; the page itself
was loaded but not clicked through. A warning that visualization tools need `querychat[viz]` is
harmless: querychat carries on without its `visualize` tool, and `--with "querychat[viz]"` adds it.

## A key of its own

The key above is fine for demos you run yourself. Give an app its own before you share it, so you
can cancel that key without touching Pi's. Once the usage meter is deployed, its rows will tell the
app's requests apart from Pi's as well.

*(Not yet run for an app's key, as of 2026-11-09. These are [Secret files](secret-files.md)' steps
for a new client key on another machine, applied to the Mac.)* `SPARK_DEMO_KEY` and
`example-demo-apps` are placeholders. The key list's names are private, so your real one goes in
the vault, not in this repo.

**On the Mac**, make sure the variable isn't already set. Go on only if this prints `0`:

```bash
grep -c '^export SPARK_DEMO_KEY=' ~/.secrets
```

**On the Mac**, generate the key. Nothing is displayed:

```bash
printf 'export SPARK_DEMO_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets
```

**On the Mac**, send a copy to the Spark. Nothing is displayed here either:

```bash
( . ~/.secrets; printf 'SPARK_DEMO_KEY=%s\n' "$SPARK_DEMO_KEY" ) | ssh brightroar 'umask 077; cat > ~/.spark-key-in'
```

**On the Spark**, follow [*A new client key, after 2a*](secret-files.md#a-new-client-key-after-2a):

1. Step 19's command for another machine, with `name` set to your key's name. It stores the digest
   and deletes the copy.
2. Step 20, adding this entry to the key list:

   ```yaml
   example-demo-apps:
     group: dan
     label: demo apps
   ```

3. Steps 17 and 18, the checks, then `make restart-front` in the clone that deploys (`phase-2a`
   until Phase 2a merges). It restarts the gate and the front once nothing has been in flight for a
   minute.

**On the Mac**, open a new terminal so it has the variable, and use `SPARK_DEMO_KEY` in place of
`SPARK_API_KEY` in your code. Then record the key in the vault by reference, as step 21 says: which
variable, and where its value lives, never the value. To cancel a key later, the same section of
[Secret files](secret-files.md#a-new-client-key-after-2a) says how to retire one.

## When it doesn't work

| What you see | What it means | What to do |
|---|---|---|
| `KeyError: 'SPARK_API_KEY'` | This process doesn't have the variable | Run it from a new terminal. An app started from the Dock may not have your shell's variables |
| A connection error, or `000` in step 1 | The tunnel is down | Start it with `make tunnel` ([Pi's runbook](pi.md)) |
| `401`, `invalid_api_key` | The Spark doesn't know that key | Check which variable your code reads. A new key needs `make restart-front` first |
| `409`, `no_fit` | The model doesn't fit beside what's loaded | The message says what it needs, what's free and what to do. Pick a loaded model, or make room |
