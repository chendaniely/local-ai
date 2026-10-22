---
title: "The usage meter — questions and answers"
description: "How the Spark's usage meter is being decided: the tokens its models use, what they would have cost at Claude's prices, and how far the box is from paying for itself, shown in each harness and on the Mac's menu bar. Each answer with its reason."
date: 2026-10-22
---

# The usage meter — questions and answers

On 2026-10-22 Dan asked for a way to count the tokens the Spark's models use, what that work would
have cost, and how far the box is from paying for itself, with "a fun little menu bar like we do
for claude on cost". This page keeps the questions and his answers in the order they came, as
[Phase 2a's](phase-2a-qa.md) does. Once Dan approves the design, it goes into [the plan](plan.md).

## Where the work starts

The front, which every request to the models passes through, exists only on `phase-2a` since that
branch's cutover (2026-10-16), and nothing deploys from `main` until Phase 2a merges.

**Dan:** "we should worktree this off phase-2a then, not main. so your context is correct." The
work branches from `phase-2a`.

## What "spend" is priced at

**Q: The Spark charges nothing per token, so its spend is what the same tokens would have cost
where they are charged for. At whose prices?**

**Dan: Claude's.** "we can pin to the claude prices."

## What each place shows

**Dan**, in his words: "in the harness itself, I like everything that I am looking at in claude code
so we should have some parody there so there's things around current session running cost with some
information around sub agent running cost, and then the monthly total running cost along with the
current number of tokens in out and thinking tokens. The current session cost is useful, but in the
menu bar, I really only need the current monthly cost of token spent on the spark, and then we can
have a global cost as a percentage of the cost of the spark as a denominator."

So there are two places:

- **In the harness:** the session's running cost, what its subagents cost, the month's total, and
  the tokens in, out and thinking, as his Claude Code status line shows them for Claude.
- **On the menu bar:** only this month's spend on the Spark, and the spend of all time as a
  percentage of what the Spark cost.

## Which harnesses

**Dan:** "i'd like this for the pi, opencode, posit assistant, hermes, pretty much any harness i
want to use." The harness half can't be built for pi alone: whatever each harness needs has to be
small, and adding one more harness has to be cheap.

## One codebase

**Dan:** "so we really need 1 codebase to calculate everything and then the individual harnesses
are reading/using the same code." The counting, the prices and the sums live in one place, and
each harness only reads from it or calls it; no harness keeps its own copy of the prices or its
own arithmetic.

## Which Claude model a Spark model costs as

**Q: An afternoon in pi with the coder (`qwen3.8-27b`): 4M tokens in, 3M of them from cache, and
200K out. What should the meter say that cost?**

- A: every model that writes text at Sonnet 5.5's prices, $4.60.
- B: every one at Opus 5.5's, $8.60.
- C: each matched by size: the coder at Sonnet 5.5's, the smaller Gemma 4 and Qwen3.6 at Haiku
  5.5's.

**Dan: none of them as asked: a mapping he edits.** In his words: "we should have a way where i can
map a local model to a claude model and price, and if i change it the cost shoudl also be able to
update. we can store this in a yaml/json file of sorts. i pretty much only use opus for all my work,
so there's the simple way of just saying that, but true, not all models are opus level. i know this
is for fun, but we do want to be able to have a somewhat accurate calculation,a nd make
updates/changes to the pricing as needed. also prices might change over time, so i need a way to
update that as well. let's think though how this data collection might work."

So each local model maps to a Claude model in a file Dan edits, and Claude's prices are a file too,
since they change over time. A change to either shows in the costs, so what is stored is tokens, and
the cost is worked out from them each time.

## A lifetime baseline

**Dan:** "once this counter is on the dgx spark and the coutner is working i want to work backwards
on all harnesses i have on this current computer and tally up the model for an accurate lifetime
cost as well so we can use that as a baseline and then it gets updated on the spark." The harnesses'
own session files on the Mac give the tokens from before the counter, and the Spark's counter
carries it on from there.

## When the mapping or a price changes

**Q: In January the coder does $300 of work, charged as Opus 5.5 at $4/$20. In March two things
change: Anthropic cuts Opus to $3/$15, and Dan remaps Gemma from Opus to Sonnet. What happens to the
numbers from before March?**

- A: remaps reprice, and prices are dated. January stays $300, at what Opus cost then; Gemma's past
  is recharged at Sonnet, since a remap corrects a judgement about the model, while a price is a
  fact about its day. Each price line carries the date it took effect.
- B: everything at today's prices. January becomes $225, and Gemma's past is recharged too; the
  lifetime figure, and the payback bar with it, moves whenever Anthropic changes a price.
- C: everything dated. January stays $300 and Gemma's past stays at Opus; a remap applies only from
  March on.

**Dan: A**, the recommendation, for the reason it gave.

## What the baseline covers

**Q: The counter goes live, and the baseline tallies everything the Spark served before it. Besides
the Mac's pi, OpenCode, Hermes and Posit Assistant sessions, the box has Dan's Open WebUI chats and
`agent`'s pi sessions since 2026-09-28. What goes into the baseline?**

- A: all of it, wherever it kept token counts. The lifetime figure is the box's, so everything it
  served counts; `agent`'s sessions are read by one command Dan runs with `sudo -u agent`.
- B: the Mac's harnesses only, as first asked; the web chats and `agent`'s work before the counter
  are left out.
- C: the Mac's first, and Open WebUI and `agent` later, once reading up shows what they kept.

**Dan: A**, the recommendation, for the reason it gave. What has no file — scripts, raw `curl`
calls, llama-swap's activity, which each restart wipes — can't be counted either way.
