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
